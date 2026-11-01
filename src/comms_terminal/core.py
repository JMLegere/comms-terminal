"""Comms Terminal's Local core (ADR 0013): the laptop copy, the write queue and the sync with the Service, served to
the App as the Local API.

The core reuses the Console's machinery unchanged: `remote.Link`, `remote.Remote` and `remote.Syncer` keep the copy
current (ADR 0012), and `remote.proxy` applies each write to the copy at once and queues it for the Service. The core
plays the part the Console plays for them (`call_from_thread`, `notify`, `apply_snapshot`, `check_copy`,
`set_online`), running all database work on one worker thread, as the Console runs it on its app thread.

The Local API is JSON-RPC 2.0, one object per line, on a user-only Unix socket. Values use `remote.encode`'s tags.
Every call names its Origin: "view" (the App's own screens, on Jeremy's input) or "panel" (Interface-file scripts,
which agents may write). Panels may run queries and the commands in `agent.COMMANDS`; sending, scheduling, Task
completion, undo, Respond, reactions and upload links stay with views. `rpc.discover` returns the OpenRPC document;
`schema/local-api.openrpc.json` is a copy that tests keep current and grow-only.
"""

import base64
import inspect
import json
import logging
import os
import queue
import socketserver
import threading
import urllib.request
import uuid
from concurrent.futures import Future
from importlib import resources
from pathlib import Path

from . import agent, files, inbox, pipeline, remote, render, sequences, writers

LOG = logging.getLogger("comms_terminal.core")
GENERATION = 1  # the Local API's protocol generation: a breaking change starts the next one (ADR 0013)
FILES = ("127.0.0.1", 8789)  # the Service's file server, through the link's forward (remote.FILES_FORWARD)
HEARTBEAT = 15  # seconds between heartbeat events
BACKLOG = 1000  # events a slow subscriber may fall behind before it gets "missed"
MAX_IMAGE = 5 * 1024 * 1024  # a remote image larger than this stays as its alt text
ORIGINS = ("view", "panel")
VIEW_ONLY = set(agent.CONSOLE_ONLY)  # commands a Panel may not run (sending, Tasks, undo, upload links)
DOMAIN_ERRORS = tuple(remote.ERRORS.values())


def default_listen() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/comms-terminal-{os.getuid()}"
    return Path(base) / "comms-terminal" / "core.sock"


class Refused(Exception):
    """A call the Origin may not make."""


class ApiError(Exception):
    def __init__(self, code: int, message: str, data=None):
        super().__init__(message)
        self.code, self.data = code, data


# --- queries: one per view (PRD Phase 0.5) ---------------------------------------------------------------------------

QUERIES: dict = {}  # name -> (fn(core, db, **params), {param: json type}, summary)


def query(method, **params):
    def mark(fn):
        QUERIES[method] = (fn, params, " ".join((fn.__doc__ or "").split()))
        return fn
    return mark


@query("rail.summary")
def _rail(core, db):
    """Counts and lists the rail shows: Status views, Cases, People, Today, Connections, Sequences, due Tasks."""
    return {"counts": inbox.view_counts(db), "cases": inbox.case_queue(db), "people": inbox.people_count(db),
            "connections": inbox.connections(db), "sequences": sequences.sequences(db),
            "tasks": sequences.tasks_due(db)}


@query("today.page")
def _today(core, db):
    """Today: Todo Threads by Case, due Tasks, and Drafts not shown through them."""
    return inbox.today(db)


@query("threads.queue", view="string")
def _queue(core, db, view):
    """The rows of one Status view: Todo, Snoozed, Done, Ignored or All."""
    if view not in inbox.VIEWS:
        raise ApiError(-32602, f"view must be one of {', '.join(inbox.VIEWS)}")
    return inbox.queue(db, view)


@query("thread.view", thread_id="string")
def _thread(core, db, thread_id):
    """One Thread: timeline, senders, its Reply Draft, files staged on it, and the Cases it may join."""
    return {"view": inbox.thread_view(db, thread_id, files=FILES), "senders": inbox.thread_senders(db, thread_id),
            "reply_draft": inbox.reply_draft(db, thread_id), "staged": inbox.staged(db, thread_id=thread_id),
            "cases": inbox.cases(db)}


@query("cases.list")
def _cases(core, db):
    """Cases with their rolled-up Status."""
    return inbox.case_queue(db)


@query("case.page", case_id="string")
def _case(core, db, case_id):
    """One Case: its Activity feed and its Threads."""
    return {"feed": inbox.case_feed(db, case_id), "threads": inbox.case_threads(db, case_id)}


@query("drafts.list")
def _drafts(core, db):
    """Every Draft."""
    return inbox.list_drafts(db)


@query("draft.view", draft_id="string")
def _draft(core, db, draft_id):
    """One Draft and the files staged on it."""
    return {"draft": inbox.get_draft(db, draft_id), "staged": inbox.staged(db, draft_id=draft_id)}


@query("tasks.list")
def _tasks(core, db):
    """Due Tasks."""
    return sequences.tasks_due(db)


@query("sequences.list")
def _sequences(core, db):
    """Every Sequence."""
    return sequences.sequences(db)


@query("sequence.page", sequence_id="string")
def _sequence(core, db, sequence_id):
    """One Sequence: its Steps, their outline, and what would stop it running."""
    steps = sequences.sequence_steps(db, sequence_id)
    return {"sequence": sequences.get_sequence(db, sequence_id), "steps": steps,
            "outline": sequences.outline(steps), "problems": sequences.validate(db, sequence_id)}


@query("paused.list")
def _paused(core, db):
    """Paused Enrollments."""
    return sequences.paused(db)


@query("people.list")
def _people(core, db):
    """Every Person."""
    return inbox.people(db)


@query("people.find", text="string")
def _find(core, db, text):
    """People whose name or Identity matches `text`, as [id, name] pairs."""
    return inbox.find_people(db, text)


@query("person.page", person_id="string")
def _person(core, db, person_id):
    """One Person: Identities, Threads, Organizations and Enrollments."""
    return {"person": inbox.person_view(db, person_id),
            "organizations": inbox.person_organizations(db, person_id),
            "enrollments": sequences.enrollments(db, person_id)}


@query("organizations.list")
def _organizations(core, db):
    """Every Organization, with each Organization's Pipeline Stages."""
    return {"organizations": inbox.organizations(db), "states": pipeline.states(db)}


@query("organization.page", domain="string")
def _organization(core, db, domain):
    """One Organization by its domain."""
    return inbox.organization_view(db, domain)


@query("pipeline.page", name="string")
def _pipeline(core, db, name):
    """One Pipeline (revenue, fundraising or talent): its math and its board as Markdown."""
    p = pipeline.parse_pipeline(name)
    return {"math": pipeline.math(db, p) if p in pipeline.MATH else None, "board": pipeline.board_markdown(db, p)}


@query("pipeline.unattended")
def _unattended(core, db):
    """Organizations in an open Stage with nothing bringing them back."""
    return pipeline.unattended(db)


@query("connections.list")
def _connections(core, db):
    """Every Connection."""
    return inbox.connections(db)


@query("channels.list", connection_id="string")
def _channels(core, db, connection_id):
    """One Connection's channel rules."""
    return inbox.channel_rules(db, connection_id)


# --- the core ------------------------------------------------------------------------------------------------------

class Subscriber:
    """One connection's event stream: sent in order on its own thread; a full backlog becomes one "missed"."""

    def __init__(self, write):
        self.write, self.q, self.alive = write, queue.Queue(maxsize=BACKLOG), True
        threading.Thread(target=self._run, daemon=True).start()

    def push(self, message):
        try:
            self.q.put_nowait(message)
        except queue.Full:
            while True:
                try:
                    self.q.get_nowait()
                except queue.Empty:
                    break
            self.q.put_nowait({"jsonrpc": "2.0", "method": "event", "params": {"kind": "missed", "data": None,
                                                                               "seq": message["params"]["seq"]}})

    def _run(self):
        while self.alive:
            try:
                self.write(self.q.get())
            except OSError:
                self.alive = False


class Core:
    """The Local core. With `remote` and `syncer` it works on the laptop copy (ADR 0012); without them, on the
    database file directly, as the Console does on the Service's host."""

    def __init__(self, db_path: str, socket=None, remote_=None, syncer=None):
        self.db_path, self.socket, self.remote, self.syncer = db_path, socket, remote_, syncer
        self.jobs = queue.Queue()
        self.worker = threading.Thread(target=self._work, daemon=True)
        self.lock, self.seq, self.subscribers = threading.Lock(), 0, []
        self.proxies = {m: remote.proxy(remote_, mod, m) for m, mod in remote.MODULES.items()} if remote_ else None
        self.worker.start()
        self.db = self.call_from_thread(inbox.connect, db_path)
        self.raw = self.call_from_thread(files.raw_dir, self.db)
        if syncer is not None and syncer.base is not None:  # loaded from base.db on the caller's thread
            self.call_from_thread(_rebase, syncer)
        if remote_ is not None:
            remote_.app = self
        threading.Thread(target=self._heartbeat, daemon=True).start()

    # The interface Remote and Syncer report to, as the Console's app does.

    def _work(self):
        while True:
            fn, args, kwargs, done = self.jobs.get()
            try:
                done.set_result(fn(*args, **kwargs))
            except BaseException as e:  # noqa: BLE001 - handed back to the caller's thread
                done.set_exception(e)

    def call_from_thread(self, fn, *args, **kwargs):
        if threading.current_thread() is self.worker:
            return fn(*args, **kwargs)
        done = Future()
        self.jobs.put((fn, args, kwargs, done))
        return done.result()

    def notify(self, message, severity="information", timeout=None):
        self.publish("toast", {"message": message, "severity": severity})

    def apply_snapshot(self, syncer, body):
        if syncer.apply(self.db, body):
            self.check_copy()
            changed = syncer.changed
            self.publish("rows", {"changed": None if changed is None else [list(c) for c in changed]})

    def check_copy(self):
        """Writes stay off while the copy's schema or the Service's build differ from this core's code."""
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        service, mine = getattr(self.syncer, "service_build", None), build()
        if version != inbox.SCHEMA_VERSION or (service and mine and service != mine):
            self.set_online("Update ready · restart Comms Terminal to load the service's version; read-only.")
            if service and mine and service != mine:
                self.publish("update", {"service_build": service, "build": mine})
        elif self.syncer is not None and self.syncer.confirmed and self.remote.link.ready():
            self.set_online(None)

    def set_online(self, problem):
        r = self.remote
        if r is None or (r.problem == problem and r.online == (problem is None)):
            return
        r.online, r.problem = problem is None, problem
        self.publish("status", self.status())

    # Events.

    def publish(self, kind, data):
        with self.lock:
            self.seq += 1
            message = {"jsonrpc": "2.0", "method": "event", "params": {"seq": self.seq, "kind": kind, "data": data}}
            self.subscribers = [s for s in self.subscribers if s.alive]
            for s in self.subscribers:
                s.push(message)

    def subscribe(self, write) -> dict:
        with self.lock:
            self.subscribers.append(Subscriber(write))
            seq = self.seq
        return {"seq": seq, "status": self.status()}

    def _heartbeat(self):
        while True:
            threading.Event().wait(HEARTBEAT)
            self.publish("heartbeat", None)

    def status(self) -> dict:
        r = self.remote
        return {"generation": GENERATION, "online": True if r is None else r.online,
                "problem": None if r is None else r.problem, "build": build(),
                "service_build": getattr(self.syncer, "service_build", None), "schema": inbox.SCHEMA_VERSION,
                "pending": 0 if r is None else r.busy, "seq": self.seq}

    # The Local API.

    def handle(self, req: dict, write=None) -> dict | None:
        rid = req.get("id")
        try:
            if req.get("jsonrpc") != "2.0" or not isinstance(req.get("method"), str):
                raise ApiError(-32600, "invalid request")
            params = req.get("params") or {}
            if not isinstance(params, dict):
                raise ApiError(-32602, "params must be an object")
            result = self.dispatch(req["method"], dict(params), write)
            return None if rid is None else {"jsonrpc": "2.0", "id": rid, "result": result}
        except ApiError as e:
            error = {"code": e.code, "message": str(e)} | ({"data": e.data} if e.data is not None else {})
        except Refused as e:
            error = {"code": -32012, "message": str(e)}
        except remote.Offline as e:
            error = {"code": -32011, "message": str(e)}
        except DOMAIN_ERRORS as e:
            kind = next(n for n, c in remote.ERRORS.items() if isinstance(e, c))
            error = {"code": -32010, "message": str(e.args[0]) if e.args else kind, "data": {"type": kind}}
        except Exception as e:  # noqa: BLE001 - every call gets an answer
            LOG.info("call %s failed: %s", req.get("method"), e)
            error = {"code": -32000, "message": f"{type(e).__name__}: {e}"}
        return None if rid is None else {"jsonrpc": "2.0", "id": rid, "error": error}

    def dispatch(self, method: str, params: dict, write=None):
        origin = params.pop("origin", "panel")  # least privilege: only the App's views say "view"
        if origin not in ORIGINS:
            raise ApiError(-32602, "origin must be view or panel")
        if method == "rpc.discover":
            return openrpc()
        if method == "core.status":
            return self.status()
        if method == "events.subscribe":
            if write is None:
                raise ApiError(-32601, "events.subscribe needs a connection")
            return self.subscribe(write)
        if method in QUERIES:
            fn, spec, _ = QUERIES[method]
            _require(params, spec)
            return remote.encode(self.call_from_thread(fn, self, self.db, **params))
        if method in ("message.body", "message.load_images"):
            raw = self._raw(params.get("raw"))
            return render.safe_html(raw, fetch=fetch_image if method == "message.load_images" else None)
        if method.startswith("command."):
            return self.command(method.removeprefix("command."), params, origin)
        if method in SERVICE:
            if origin != "view":
                raise Refused(f"{method} runs only from the App's views")
            fn, spec, _ = SERVICE[method]
            missing = {p for p, (_, req) in spec.items() if req} - set(params)
            if missing or set(params) - set(spec):
                raise ApiError(-32602, f"expected params {sorted(spec)}, got {sorted(params)}")
            return fn(self, **params)
        raise ApiError(-32601, f"no method {method}")

    def command(self, name: str, params: dict, origin: str):
        if name not in writers.WRITERS:
            raise ApiError(-32601, f"no command {name}")
        if origin == "panel" and name in VIEW_ONLY:
            raise Refused(f"{name} runs only from the App's views: {agent.CONSOLE_ONLY[name]}")
        module, fn = name.split(".")
        real = getattr(remote.MODULES[module], fn)
        kwargs = remote.decode(params)
        try:
            inspect.signature(real).bind(None, **kwargs)
        except TypeError as e:
            raise ApiError(-32602, str(e)) from None
        target = getattr(self.proxies[module], fn) if self.proxies else real

        def run():
            result = target(self.db, **kwargs)
            if self.proxies is None:
                self.db.commit()
            return result
        result = self.call_from_thread(run)
        self.publish("rows", {"changed": None, "cause": name})
        return remote.encode(result)

    def _raw(self, name) -> bytes:
        if not isinstance(name, str) or len(name) != 64 or any(c not in "0123456789abcdef" for c in name):
            raise ApiError(-32602, "raw must be a message's sha256")
        if self.raw is None:
            raise ApiError(-32000, "no raw messages beside an in-memory database")
        try:
            return (self.raw / f"{name}.eml").read_bytes()
        except FileNotFoundError:
            raise ApiError(-32004, "raw message not on this machine") from None

    # View-only operations that reach the Service directly, never queued or retried (ADR 0011).

    def send_draft(self, draft_id, revision, again=False):
        return remote.socket_send_draft(self._socket())(draft_id, revision, again)

    def send_reply(self, thread_id, text, to=None, cc=None):
        return remote.socket_reply(self._socket())(thread_id, text, to, cc)

    def respond(self, payload):
        return remote.socket_respond(self._socket())(payload)

    def slack_reactions(self, payload):
        return remote.socket_slack_reactions(self._socket())(payload)

    def upload(self, owner_kind, owner_id, path):
        """Stage a file from this machine's disk on a Draft or Thread, through the Service's upload page (FR-A9)."""
        if owner_kind not in ("draft", "thread"):
            raise ApiError(-32602, "owner_kind must be draft or thread")
        data, name = Path(path).read_bytes(), Path(path).name
        url = self.call_from_thread(self._upload_url, (owner_kind, owner_id))
        boundary = uuid.uuid4().hex
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
                f"Content-Type: application/octet-stream\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(url, body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            out = json.loads(resp.read())
        self.publish("rows", {"changed": None, "cause": "upload"})
        return out

    def _upload_url(self, owner):
        target = self.proxies["files"] if self.proxies else files
        return target.upload_url(self.db, owner, *FILES)

    def _socket(self):
        return self.remote.link.command() if self.remote is not None else self.socket


def _rebase(syncer):
    """Load the Syncer's base again on the worker thread: SQLite refuses a connection from another thread, and a
    restarted core's Syncer read base.db on the main thread."""
    import sqlite3
    base = remote.mirror()
    saved = sqlite3.connect(syncer.base_path)
    try:
        saved.backup(base)
    finally:
        saved.close()
    syncer.base = base


SERVICE = {  # method -> (Core method, {param: (json type, required)}, summary)
    "send.draft": (Core.send_draft, {"draft_id": ("string", True), "revision": ("integer", True),
                                     "again": ("boolean", False)}, "Send a Draft now (Jeremy only)."),
    "send.reply": (Core.send_reply, {"thread_id": ("string", True), "text": ("string", True),
                                     "to": ("array", False), "cc": ("array", False)}, "Send a reply (Jeremy only)."),
    "respond": (Core.respond, {"payload": ("object", True)}, "Respond to an invite (FR-13e; Jeremy only)."),
    "slack.reactions": (Core.slack_reactions, {"payload": ("object", True)},
                        "Read, add or remove Jeremy's own Slack reaction (FR-R)."),
    "upload": (Core.upload, {"owner_kind": ("string", True), "owner_id": ("string", True), "path": ("string", True)},
               "Stage a file from this machine on a Draft or Thread."),
}


def _require(params: dict, spec: dict):
    missing, extra = set(spec) - set(params), set(params) - set(spec)
    if missing or extra:
        raise ApiError(-32602, f"expected params {sorted(spec)}, got {sorted(params)}")


def build() -> str:
    """The commit baked in at install (build_hook.py); '' when running from source."""
    try:
        return (resources.files("comms_terminal") / "BUILD").read_text().split()[1]
    except (OSError, IndexError):
        return ""


def fetch_image(url: str) -> str | None:
    """A remote image as a data: URI, fetched on Jeremy's request ("Load images"); None if it is not one."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": files.USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as resp:
            ctype = resp.headers.get_content_type()
            data = resp.read(MAX_IMAGE + 1)
    except (OSError, ValueError):
        return None
    if not ctype.startswith("image/") or len(data) > MAX_IMAGE:
        return None
    return f"data:{ctype};base64,{base64.b64encode(data).decode()}"


# --- the OpenRPC document ------------------------------------------------------------------------------------------

def _param(name, schema, required, summary=None):
    return {"name": name, "required": required, "schema": schema} | ({"summary": summary} if summary else {})


ORIGIN = _param("origin", {"enum": list(ORIGINS)}, False, "view or panel; panel when omitted")


def openrpc() -> dict:
    methods = [
        {"name": "rpc.discover", "summary": "This document.", "params": []},
        {"name": "core.status", "summary": "Online or read-only, builds, schema, pending writes, event sequence.",
         "params": []},
        {"name": "events.subscribe", "summary": "Status now, then events on this connection: rows, status, toast,"
                                                " update, heartbeat, missed.", "params": []},
        {"name": "message.body", "summary": "A message's HTML with every remote fetch removed (FR-7).",
         "params": [_param("raw", {"type": "string"}, True)]},
        {"name": "message.load_images", "summary": "The same, with remote images fetched by the core as data: URIs.",
         "params": [_param("raw", {"type": "string"}, True)]},
    ]
    for name, (_, spec, summary) in QUERIES.items():
        methods.append({"name": name, "summary": summary,
                        "params": [_param(p, {"type": t}, True) for p, t in spec.items()]})
    for name in writers.WRITERS:
        module, fn = name.split(".")
        params = []
        for p in list(inspect.signature(getattr(remote.MODULES[module], fn)).parameters.values())[1:]:
            if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
                continue
            params.append(_param(p.name, {}, p.default is p.empty))
        methods.append({"name": f"command.{name}", "summary": f"The {name} writer ({writers.WRITERS[name]}).",
                        "params": params, "x-panel": name not in VIEW_ONLY})
    for name, (_, spec, summary) in SERVICE.items():
        methods.append({"name": name, "summary": summary, "x-panel": False,
                        "params": [_param(p, {"type": t}, req) for p, (t, req) in spec.items()]})
    for m in methods:
        m["params"].append(ORIGIN)
        m["result"] = {"name": "result", "schema": {}}
    return {"openrpc": "1.3.2", "info": {"title": "Comms Terminal Local API", "version": f"{GENERATION}.0.0",
                                         "description": "ADR 0013. Values use remote.encode's tags ($dt, $dc, $tuple,"
                                                        " $bytes, $dict)."},
            "methods": sorted(methods, key=lambda m: m["name"])}


def breaks(released: dict, current: dict) -> list[str]:
    """What `current` breaks for Clients of `released`: a removed method, a removed parameter, or a parameter
    that is new and required or became required. Empty when current only grows (NFR-6)."""
    now = {m["name"]: {p["name"]: p["required"] for p in m["params"]} for m in current["methods"]}
    out = []
    for m in released["methods"]:
        if m["name"] not in now:
            out.append(f"{m['name']} removed")
            continue
        was = {p["name"]: p["required"] for p in m["params"]}
        out += [f"{m['name']} lost {p}" for p in was if p not in now[m["name"]]]
        out += [f"{m['name']} requires {p}" for p, req in now[m["name"]].items() if req and not was.get(p, False)]
    return out


# --- the socket ----------------------------------------------------------------------------------------------------

class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def serve(core: Core, path: Path) -> _Server:
    """The Local API on a user-only socket: the directory 0700, the socket 0600."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.unlink(missing_ok=True)
    old = os.umask(0o177)
    try:
        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                lock = threading.Lock()

                def write(message):
                    with lock:
                        self.wfile.write(json.dumps(message).encode() + b"\n")
                        self.wfile.flush()
                for line in self.rfile:
                    try:
                        req = json.loads(line)
                    except ValueError:
                        write({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
                        continue
                    answer = core.handle(req, write) if isinstance(req, dict) else \
                        {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
                    if answer is not None:
                        write(answer)
        return _Server(str(path), Handler)
    finally:
        os.umask(old)


def run(db_path: str, socket_path: str, ssh_args: str | None, listen: Path):
    """`comms-terminal core`: on a laptop copy with --remote (ADR 0012), else on the database file directly."""
    rem = syncer = None
    if ssh_args:
        from .main import start_remote
        _, rem, syncer = start_remote(ssh_args, db_path, console=False)
    core = Core(db_path, socket_path, rem, syncer)
    if syncer is not None:
        syncer.start()
    server = serve(core, listen)
    print(f"Comms Terminal Local core on {listen}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        listen.unlink(missing_ok=True)
        if rem is not None:
            rem.drain(30)  # writes made just before a quit still reach the Service
            rem.link.stop()
