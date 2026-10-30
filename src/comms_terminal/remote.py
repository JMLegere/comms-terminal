"""Comms Terminal's local console (ADR 0004): the console runs on Jeremy's laptop against a copy of the service's
database and raw messages, so a keypress never waits on the network. The service stays the only writer.

Service side: POST /call runs one allowlisted inbox/sequences/pipeline/files function; POST /sync answers with a
consistent database snapshot once the database has changed since the caller's copy; POST /raw returns raw
messages by name. Laptop side: one SSH master connection (started with the console) carries each request to the
service socket through a tiny relay; `install` swaps the console's inbox/sequences/pipeline/files for proxies that apply
a write locally at once and send it in order, and `Syncer` keeps the copy current.

`files/` and `files-secret` stay on the service: Attachment links come signed in each snapshot.
"""

import base64
import dataclasses
import gzip
import http.client
import inspect
import itertools
import json
import logging
import os
import queue
import socket
import sqlite3
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from . import clock, files, inbox, pipeline, sequences, writers

LOG = logging.getLogger("comms_terminal.remote")

CALLS = writers.WRITERS  # name -> how the console runs it (writers.KINDS); each writer declares its own
MODULES = {"inbox": inbox, "sequences": sequences, "pipeline": pipeline, "files": files}


class ReplyError(Exception):
    """A service call that did not succeed. `status`: the service's HTTP status, 503 when the service was not
    reachable (nothing sent), 504 when the response was lost (a send may have gone out)."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=30)
        self.unix_path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.unix_path)


class _CommandConnection(http.client.HTTPConnection):
    """HTTP over a command's stdin/stdout: Comms Terminal's SSH relay to the service socket (remote.Link)."""

    def __init__(self, command):
        super().__init__("localhost", timeout=30)
        self.command, self.proc = command, None

    def connect(self):
        mine, theirs = socket.socketpair()
        try:
            self.proc = subprocess.Popen(self.command, stdin=theirs, stdout=theirs, stderr=subprocess.DEVNULL,
                                         start_new_session=True)
        finally:
            theirs.close()
        mine.settimeout(self.timeout)
        self.sock = mine

    def reap(self):
        """After the body is read (http.client closes an HTTP/1.0 connection before that, so close() must not
        wait on the relay still writing it)."""
        if self.proc is not None:
            try:
                self.proc.wait(5)
            except Exception:  # noqa: BLE001 - never leave a relay running
                self.proc.kill()
                self.proc.wait()
            self.proc = None


def post(socket_path, path, payload, timeout=30) -> tuple[int, dict]:
    """POST JSON to the service: `socket_path` is its Unix socket, or a relay command (a list) to run."""
    conn = _CommandConnection(socket_path) if isinstance(socket_path, list) else _UnixConnection(socket_path)
    conn.timeout = timeout
    try:
        conn.connect()
    except OSError as e:
        raise ReplyError(f"service not reachable: {e}", 503) from e
    try:
        conn.request("POST", path, json.dumps(payload), {"Content-Type": "application/json"})
        r = conn.getresponse()
        raw = r.read()
    except (OSError, http.client.HTTPException) as e:  # the request may have reached the service
        raise ReplyError(f"no response from the service: {e}", 504) from e
    finally:
        conn.close()
        if isinstance(conn, _CommandConnection):
            conn.reap()
    try:
        return r.status, json.loads(raw or b"{}")
    except ValueError:  # an error page, not JSON (an older service has no such route)
        return r.status, {"error": f"HTTP {r.status}"}


class DraftSendError(ReplyError):
    pass


def socket_send_draft(socket_path):
    """Send function that calls POST /send-draft; a lost response means the outcome is unknown (504)."""
    def send(draft_id, revision, again=False):
        try:
            status, body = post(socket_path, "/send-draft", {"draft_id": draft_id, "revision": revision,
                                                           "again": again}, timeout=90)
        except ReplyError as e:
            raise DraftSendError(str(e), e.status) from e
        if status != 200:
            raise DraftSendError(body.get("error", f"HTTP {status}"), status)
        return body
    return send


def socket_slack_conversations(socket_path):
    """Jeremy's Slack conversations, [{id, kind, label}], from POST /slack-conversations (Slack Draft picker)."""
    def fetch(connection_id):
        status, body = post(socket_path, "/slack-conversations", {"connection_id": connection_id}, timeout=120)
        if status != 200:
            raise ReplyError(body.get("error", f"HTTP {status}"))
        return body["conversations"]
    return fetch


def socket_slack_reactions(socket_path):
    """One private source request, over the existing Unix socket or SSH command; never queued or retried."""
    def fetch(payload):
        changing = payload["op"] != "read"
        try:
            status, body = post(socket_path, "/slack-reactions", payload)
        except ReplyError as e:
            outcome = "unknown" if changing and e.status != 503 else "unavailable"
            return {"outcome": outcome, "error": "Reaction service unavailable."}
        if isinstance(body, dict) and body.get("outcome") in (
                "ready", "confirmed", "unavailable", "refused", "unknown", "review_required"):
            return body
        outcome = ("review_required" if status == 409 else "refused" if status == 400 else
                   "unknown" if changing and status != 404 else "unavailable")
        return {"outcome": outcome, "error": "Reaction service unavailable."}
    return fetch


def socket_respond(socket_path):
    """Jeremy's invite Response (FR-13e) over the Unix socket or SSH command; never queued or retried."""
    def respond(payload):
        try:
            status, body = post(socket_path, "/respond", payload)
        except ReplyError as e:
            return {"outcome": "unavailable" if e.status == 503 else "unknown", "error": "Service unavailable."}
        if isinstance(body, dict) and body.get("outcome") in ("confirmed", "refused", "unknown", "unavailable"):
            return body
        return {"outcome": "refused" if status in (400, 404) else "unknown", "error": "Service unavailable."}
    return respond


def socket_reply(socket_path):
    """Reply function that calls POST /reply on the service's local socket; a lost response means the outcome
    is unknown (504)."""
    def reply(thread_id, text, to, cc):
        status, body = post(socket_path, "/reply", {"thread_id": thread_id, "text": text, "to": to, "cc": cc})
        if status != 200:
            raise ReplyError(body.get("error", f"HTTP {status}"), status)
        return body
    return reply


def socket_context(socket_path, source):
    """A Context source's answer (FR-13a, FR-13b) from POST /<source> on the service's local socket; None when
    that source is not connected."""
    def fetch(emails, domains):
        status, body = post(socket_path, f"/{source}", {"emails": emails, "domains": domains}, timeout=120)
        if status in (404, 503):  # not connected, or a service from before this source (404)
            return None
        if status != 200:
            raise ReplyError(body.get("error", f"HTTP {status}"))
        return body
    return fetch


# --- values over the wire ------------------------------------------------------

DATACLASSES = {c.__name__: c for c in vars(inbox).values()  # the Action dataclasses and Thread, by name
               if isinstance(c, type) and dataclasses.is_dataclass(c) and c.__module__ == inbox.__name__}
ERRORS = {f"{n}.{c.__name__}": c for n, c in sorted(  # every domain error by module, most specific first
    {(n, c) for n, m in MODULES.items() for c in vars(m).values()
     if isinstance(c, type) and issubclass(c, Exception) and c.__module__ == m.__name__},
    key=lambda nc: (-len(nc[1].__mro__), nc[0], nc[1].__name__))}
ERRORS |= {"ValueError": ValueError, "KeyError": KeyError}


def encode(v):
    """JSON for call arguments and results: datetimes, the action and Thread dataclasses, tuples, bytes, and
    dicts with non-string keys are tagged so `decode` gives them back."""
    if isinstance(v, datetime):
        return {"$dt": v.isoformat()}
    if dataclasses.is_dataclass(v) and type(v).__name__ in DATACLASSES:
        return {"$dc": type(v).__name__, "f": {f.name: encode(getattr(v, f.name)) for f in dataclasses.fields(v)}}
    if isinstance(v, sqlite3.Row):
        v = dict(v)
    if isinstance(v, dict):
        if all(isinstance(k, str) and not k.startswith("$") for k in v):
            return {k: encode(x) for k, x in v.items()}
        return {"$dict": [[encode(k), encode(x)] for k, x in v.items()]}
    if isinstance(v, tuple):
        return {"$tuple": [encode(x) for x in v]}
    if isinstance(v, list):
        return [encode(x) for x in v]
    if isinstance(v, bytes):
        return {"$bytes": base64.b64encode(v).decode()}
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    raise TypeError(f"cannot send {type(v).__name__}")


def decode(v):
    if isinstance(v, list):
        return [decode(x) for x in v]
    if not isinstance(v, dict):
        return v
    if len(v) == 1 and "$dt" in v:
        return datetime.fromisoformat(v["$dt"])
    if len(v) == 1 and "$tuple" in v:
        return tuple(decode(x) for x in v["$tuple"])
    if len(v) == 1 and "$bytes" in v:
        return base64.b64decode(v["$bytes"])
    if len(v) == 1 and "$dict" in v:
        return {decode(k): decode(x) for k, x in v["$dict"]}
    if set(v) == {"$dc", "f"}:
        return DATACLASSES[v["$dc"]](**{k: decode(x) for k, x in v["f"].items()})
    return {k: decode(x) for k, x in v.items()}


# --- service side ----------------------------------------------------------------

class Held(sqlite3.Connection):
    """A connection whose writers' own commits wait while `held` (ADR 0012): handle_call commits a laptop write and
    its applied_mutations row together, so a crash between them cannot apply a resent write twice."""
    held = False

    def commit(self):
        if not self.held:
            super().commit()

    def __exit__(self, kind, value, tb):  # `with db:` in a writer
        return False if self.held else super().__exit__(kind, value, tb)  # held: an error reaches the caller


def handle_call(db_path: str, body: bytes) -> tuple[int, dict]:
    """POST /call {fn, args, kwargs, client?, mutation?}: one allowlisted function on the service's database. A call
    that names its laptop console (client) and its place in that console's queue (mutation) is applied once: sent
    again, it gets the answer it got the first time (ADR 0012)."""
    try:
        req = json.loads(body)
        name = req["fn"]
        if name not in CALLS:
            return 403, {"error": f"{name} is not callable"}
        module, fn = name.split(".")
        args, kwargs = decode(req.get("args", [])), decode(req.get("kwargs", {}))
        client, mutation = req.get("client"), req.get("mutation")
        if (client is None) != (mutation is None) or (client is not None and (
                not inbox.is_id(client) or type(mutation) is not int or mutation <= 0)):
            raise ValueError("client and mutation go together")
    except (ValueError, KeyError, TypeError):
        return 400, {"error": "bad call"}
    db = inbox.connect(db_path, factory=Held)
    try:
        if client is not None:
            first = db.execute("SELECT status, answer FROM applied_mutations WHERE client_id = ? AND mutation_id = ?",
                               (client, mutation)).fetchone()
            if first is not None:
                return first["status"], json.loads(first["answer"])
        if client is None:
            return _apply(db, name, module, fn, args, kwargs)
        db.held = True
        db.execute("BEGIN IMMEDIATE")
        try:
            with inbox.minting(f"{client}:{mutation}", kwargs.get("now") or clock.now()):  # the copy's new ids
                status, answer = _apply(db, name, module, fn, args, kwargs)
            if status != 200:  # refused or failed: nothing it wrote stays
                db.rollback()
                db.execute("BEGIN IMMEDIATE")
            if status in (200, 409):  # a definite outcome, kept with the write; a 500 may be tried again
                db.execute("INSERT OR IGNORE INTO applied_mutations"
                           " (client_id, mutation_id, status, answer, applied_at) VALUES (?, ?, ?, ?, ?)",
                           (client, mutation, status, json.dumps(answer), clock.iso(clock.now())))
            db.held = False
            db.commit()
        except BaseException:
            db.held = False
            db.rollback()
            raise
        return status, answer
    finally:
        db.close()


def _apply(db, name, module, fn, args, kwargs) -> tuple[int, dict]:
    try:
        result = getattr(MODULES[module], fn)(db, *args, **kwargs)
        return 200, {"result": encode(result)}
    except tuple(ERRORS.values()) as e:
        kind = next(n for n, c in ERRORS.items() if isinstance(e, c))  # most specific first
        return 409, {"error": str(e.args[0]) if e.args else kind, "type": kind}
    except Exception as e:  # noqa: BLE001 - answer, so the console reverts instead of waiting
        LOG.info("call %s failed: %s", name, e)
        return 500, {"error": f"{type(e).__name__}: {e}"}


class Changes:
    """A generation number that moves whenever the database changes (any connection's commit), so a /sync
    request waits for news instead of polling the laptop's copy."""

    def __init__(self, db_path: str, every: float = 0.1):
        self.db_path, self.every = db_path, every
        self.boot, self.gen = os.urandom(4).hex(), 0
        self.cond = threading.Condition()
        self.watcher = None  # started by the first /sync: a service with no laptop copy never polls

    def token(self) -> str:
        return f"{self.boot}:{self.gen}"

    def _watch(self):
        db = inbox.connect(self.db_path)
        seen = None
        while True:
            v = db.execute("PRAGMA data_version").fetchone()[0]
            if v != seen:
                seen = v
                with self.cond:
                    self.gen += 1
                    self.cond.notify_all()
            time.sleep(self.every)

    def wait(self, since: str | None, timeout: float) -> str:
        with self.cond:
            if self.watcher is None:
                self.watcher = threading.Thread(target=self._watch, daemon=True)
                self.watcher.start()
                self.cond.wait_for(lambda: self.gen > 0, 5)
            self.cond.wait_for(lambda: since != self.token(), timeout)
            return self.token()


def snapshot_bytes(db_path: str) -> bytes:
    src, mem = inbox.connect(db_path), sqlite3.connect(":memory:")
    try:
        src.backup(mem)
        data = bytearray(mem.serialize())
        data[18] = data[19] = 1  # rollback-journal header: a WAL image does not deserialize
        return gzip.compress(bytes(data), 5)
    finally:
        src.close()
        mem.close()


def build() -> str:
    """This install's short commit (tui.build_version's BUILD file); '' for a source run."""
    from importlib import resources
    try:
        return (resources.files("comms_terminal") / "BUILD").read_text().split()[1]
    except (OSError, IndexError):
        return ""


def cursor(db) -> int:
    """The newest change-log sequence number (0 before the first change); pruning never lowers it."""
    r = db.execute("SELECT seq FROM sqlite_sequence WHERE name = 'changes'").fetchone()
    return r[0] if r else 0


def change_set(db, since: int) -> list[dict] | None:
    """Each row changed after change-log entry `since`, once, as it stands now (None: deleted); None when `since`
    is older than the kept log or ahead of it, so the caller sends a whole Snapshot (ADR 0012)."""
    now, oldest = cursor(db), db.execute("SELECT min(seq) FROM changes").fetchone()[0]
    if since > now or since < (now + 1 if oldest is None else oldest) - 1:
        return None
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type = 'table'")}
    out = []
    for tbl, row_id in db.execute("SELECT tbl, row_id FROM changes WHERE seq > ? GROUP BY tbl, row_id"
                                  " ORDER BY max(seq)", (since,)).fetchall():
        if tbl in tables:  # a table a later migration dropped has nothing left to send
            key = "id" if any(c[1] == "id" for c in db.execute(f'PRAGMA table_info("{tbl}")')) else "rowid"
            row = db.execute(f"SELECT rowid AS rowid, * FROM {tbl} WHERE {key} = ?", (row_id,)).fetchone()
            out.append({"tbl": tbl, "row_id": row_id, "row": dict(row) if row else None})
    return out


def handle_sync(db_path: str, changes: Changes, body: bytes, wait: float = 25) -> tuple[int, dict]:
    """POST /sync {since, cursor?, schema?}: once the database differs from `since` (at once for none), what
    changed. A caller that sends its change-log `cursor` and runs this schema gets a Change set: the rows changed
    since then, the raw messages and Attachment capabilities they add, and the new cursor (ADR 0012). Anyone else,
    or a cursor older than the kept log, gets the whole snapshot with every raw message name and capability. Both
    carry the service's build and, for a caller that names its console (client), the last of its writes applied,
    whose effects the answer already holds."""
    try:
        req = json.loads(body or b"{}")
        since, wait = req.get("since"), min(wait, max(0.0, float(req.get("wait", wait))))
        mine = req.get("cursor")
        mine = int(mine) if mine is not None and req.get("schema") == inbox.SCHEMA_VERSION else None
        client = req.get("client")
        if client is not None and not inbox.is_id(client):
            raise ValueError("client")
    except (ValueError, AttributeError, TypeError):
        return 400, {"error": "bad sync"}
    token = changes.wait(since, wait)
    if token == since:
        return 200, {"since": token}  # nothing new
    db = inbox.connect(db_path)
    try:
        done = {} if client is None else {"last_mutation": db.execute(  # first: its writes' rows are in what follows
            "SELECT coalesce(max(mutation_id), 0) FROM applied_mutations WHERE client_id = ?", (client,)).fetchone()[0]}
        at = cursor(db)  # before reading rows: a row changed meanwhile comes again next time, harmlessly
        rows = change_set(db, mine) if mine is not None else None
        secret = files.files_secret(db)
        if rows is not None:
            kept = [(c["tbl"], c["row"]) for c in rows if c["row"] is not None]
            names = sorted({f"{raw}.eml" for tbl, r in kept if tbl == "activities"
                            and (raw := json.loads(r["data"]).get("raw"))})
            caps = {r["sha"]: files.file_cap(secret, r["sha"]) for tbl, r in kept if tbl == "attachments" and r["sha"]}
            return 200, {"since": token, "cursor": at, "changes": rows, "raw": names, "caps": caps,
                         "build": build()} | done
        raw = files.raw_dir(db)
        names = sorted(p.name for p in raw.iterdir()) if raw is not None and raw.is_dir() else []
        caps = {r[0]: files.file_cap(secret, r[0])
                for r in db.execute("SELECT DISTINCT sha FROM attachments WHERE sha IS NOT NULL")}
    finally:
        db.close()
    data = snapshot_bytes(db_path)
    return 200, {"since": token, "cursor": at, "db": base64.b64encode(data).decode(), "raw": names, "caps": caps,
                 "build": build()} | done


RAW_BATCH = 4 * 1024 * 1024


def handle_raw(db_path: str, body: bytes) -> tuple[int, dict]:
    """POST /raw {names}: raw messages by name, up to about RAW_BATCH bytes; the caller asks again for the rest."""
    try:
        names = json.loads(body)["names"]
    except (ValueError, KeyError, TypeError):
        return 400, {"error": "bad raw"}
    db = inbox.connect(db_path)
    try:
        raw = files.raw_dir(db)
    finally:
        db.close()
    out, size = {}, 0
    for name in names:
        path = raw / name if raw is not None else None
        if path is None or path.name != name or not name.endswith(".eml") or not path.is_file():
            continue
        data = path.read_bytes()
        out[name] = base64.b64encode(data).decode()
        size += len(data)
        if size >= RAW_BATCH:
            break
    return 200, {"raw": out}


# --- laptop side -----------------------------------------------------------------

SERVICE_SOCKET = "/run/comms-terminal/inbox.sock"  # the container's socket directory, bind-mounted on the host
RELAY = f"""import os,socket,sys,threading
s=socket.socket(socket.AF_UNIX)
s.connect({SERVICE_SOCKET!r})
def up():
    while b:=os.read(0,65536):
        s.sendall(b)
    s.shutdown(socket.SHUT_WR)
threading.Thread(target=up,daemon=True).start()
while b:=s.recv(65536):
    sys.stdout.buffer.write(b)
    sys.stdout.buffer.flush()
"""
# Root on the host reaches the container's 0600 socket; the administrator's sudo is the existing access.
RELAY_COMMAND = ("sudo -n /usr/bin/python3 -I -c "
                 f"\"exec(__import__('base64').b64decode('{base64.b64encode(RELAY.encode()).decode()}'))\"")
# The master carries Attachment links and upload pages to the service's loopback file server (tui.FILE_SERVER).
# A port already taken on the laptop only warns: writes keep working (ExitOnForwardFailure stays off).
FILES_FORWARD = "127.0.0.1:8789:127.0.0.1:8789"


def _die_with_parent():
    """In the SSH master's child: SIGTERM when the console exits by any route (window closed, crash, kill)."""
    import ctypes
    import signal
    ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG


class Link:
    """The SSH route to the service: one master connection, and a relay session per request over it."""

    def __init__(self, ssh_args: list[str], control: str):
        self.ssh_args, self.control, self.master = ssh_args, control, None

    def start(self):
        if self.master is None or self.master.poll() is not None:
            Path(self.control).unlink(missing_ok=True)
            self.master = subprocess.Popen(
                ["ssh", "-M", "-S", self.control, "-N", "-o", "ControlPersist=no", "-o", "ServerAliveInterval=15",
                 "-o", "ServerAliveCountMax=2", "-L", FILES_FORWARD, *self.ssh_args],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True, preexec_fn=_die_with_parent)

    def ready(self) -> bool:
        return self.master is not None and self.master.poll() is None and os.path.exists(self.control)

    def stop(self):
        if self.master is not None and self.master.poll() is None:
            self.master.terminate()
            try:
                self.master.wait(5)
            except subprocess.TimeoutExpired:
                self.master.kill()
        Path(self.control).unlink(missing_ok=True)

    def command(self) -> list[str]:
        """One request's relay: what post runs in place of connecting to a local socket."""
        return ["ssh", "-S", self.control, "-o", "ControlMaster=no", "-T", *self.ssh_args, RELAY_COMMAND]


class Offline(ValueError):
    """A write while the copy cannot reach the service (or runs other code): the console stays read-only.
    A ValueError, so editors that report a refused save ("Not saved: …") say this one the same way."""


class Remote:
    """The laptop console's state: its link, the in-order write queue, and whether writes are allowed."""

    def __init__(self, link: Link, post):
        self.link, self.post = link, post  # post(command, path, payload, timeout) -> (status, body)
        self.online, self.problem = False, "Connecting to the inbox service…"
        self.app = None
        self.pending = queue.Queue()
        self.client, self.mutations = str(uuid.uuid4()), itertools.count(1)  # this console's writes, in order
        self.busy = 0  # writes applied locally and not yet answered by the service
        self.lock = threading.Lock()
        self.idle = threading.Event()
        self.idle.set()
        self.fresh = threading.Event()  # the queue drained: ask for a snapshot at once
        self.unconfirmed = []  # (name, args, kwargs, mutation) not yet in a sync answer: replayed on each (ADR 0012)
        threading.Thread(target=self._send_loop, daemon=True).start()

    def call(self, name, args, kwargs, timeout=30, mutation=None):
        payload = {"fn": name, "args": encode(list(args)), "kwargs": encode(kwargs)}
        if mutation is not None:  # applied once, however often it is sent (ADR 0012)
            payload |= {"client": self.client, "mutation": mutation}
        status, body = self.post(self.link.command(), "/call", payload, timeout)
        if status == 200:
            return decode(body["result"])
        error = ERRORS.get(body.get("type"))
        if error is not None:
            raise error(body.get("error", ""))
        raise Offline(body.get("error", f"HTTP {status}"))

    def require_online(self):
        if not self.online:
            raise Offline(self.problem or "Offline · read-only")

    def queue(self, name, args, kwargs, mutation):
        with self.lock:
            self.busy += 1
            self.idle.clear()
            self.unconfirmed.append((name, args, kwargs, mutation))
        self.pending.put((name, args, kwargs, mutation))

    def confirm(self, last: int) -> list:
        """Drop the writes the service has applied (up to mutation `last`); the rest, in order, to replay."""
        with self.lock:
            self.unconfirmed = [w for w in self.unconfirmed if w[3] > last]
            return list(self.unconfirmed)

    def drain(self, timeout=30) -> bool:
        return self.idle.wait(timeout)

    def _send_loop(self):
        while True:
            name, args, kwargs, mutation = self.pending.get()
            try:
                self.call(name, args, kwargs, mutation=mutation)
            except Exception as e:  # noqa: BLE001 - every failure reverts the copy to the service's state
                LOG.info("write %s not applied: %s", name, e)
                with self.lock:  # refused or lost: the next sync shows the service's state without it
                    self.unconfirmed = [w for w in self.unconfirmed if w[3] != mutation]
                self.say(f"Not saved on the inbox service · {e}. Showing its state.", error=True)
            finally:
                with self.lock:
                    self.busy -= 1
                    if not self.busy:
                        self.idle.set()
                        self.fresh.set()

    def say(self, message, error=False):
        app = self.app
        if app is not None:
            try:
                app.call_from_thread(app.notify, message, severity="error" if error else "information", timeout=10)
            except RuntimeError:  # the app is not running
                pass


def proxy(remote: Remote, module, prefix: str):
    """A stand-in for `module` in the console: allowlisted writes go through `remote`; reads stay local."""

    class Proxy:
        def __getattr__(self, attr):
            value = getattr(module, attr)
            name = f"{prefix}.{attr}"
            if name not in CALLS:
                return value

            def call(db, *args, **kwargs):
                remote.require_online()
                kind = CALLS[name]
                if "now" not in inspect.signature(value).bind_partial(db, *args, **kwargs).arguments:
                    kwargs = kwargs | {"now": clock.now()}  # one moment for the copy and the service (ADR 0012)
                if kind == "service":
                    return remote.call(name, args, kwargs)
                mutation = next(remote.mutations)
                now = inspect.signature(value).bind_partial(db, *args, **kwargs).arguments["now"]
                with inbox.minting(f"{remote.client}:{mutation}", now):  # the service mints the same ids
                    result = value(db, *args, **kwargs)  # raises exactly as the service would, and paints at once
                remote.queue(name, args, kwargs, mutation)
                return result
            return call

    return Proxy()


def signed_urls(caps: dict):
    """files.file_url for the copy: the service's signed capability, so files-secret never leaves it."""
    import urllib.parse

    def file_url(db, sha, name, host, port):
        cap = caps.get(sha)
        return f"http://{host}:{port}/f/{cap}/{urllib.parse.quote(name, safe='')}" if cap else ""
    return file_url


def install(tui, remote: Remote, caps: dict):
    """Point the console module at the service: proxies for its writes, and no local file store."""
    tui.inbox = proxy(remote, inbox, "inbox")
    tui.sequences = proxy(remote, sequences, "sequences")
    tui.pipeline = proxy(remote, pipeline, "pipeline")
    tui.files = proxy(remote, files, "files")
    files.file_url = signed_urls(caps)  # Attachment bytes and files-secret stay on the service (ADR 0004)


def mirror():
    """An in-memory base: the inbox's SQL functions (column defaults name them), no foreign-key checks, since rows
    arrive in the service's order."""
    db = inbox.connect(":memory:")
    db.execute("PRAGMA foreign_keys = OFF")
    return db


class Syncer:
    """Keeps the laptop copy current (ADR 0012). The base is the service's state as last answered: a whole snapshot
    at first, then Change sets applied to it. After each answer the copy becomes the base again with the console's
    unconfirmed writes replayed on top, so no frame shows the service's state without them."""

    def __init__(self, remote: Remote, db_path: str, caps: dict, build_file: Path | None = None):
        self.remote, self.db_path, self.caps, self.build_file = remote, db_path, caps, build_file
        self.token_file = Path(db_path).parent / "sync-token"
        self.base_path = Path(db_path).with_name("base.db")  # the base, kept for the next start
        self.base, self.cursor, self.changed = None, None, None
        try:
            self.since, caps_json, self.service_build, *rest = json.loads(self.token_file.read_text())
            caps.update(caps_json)
            if rest and rest[0] is not None and self.base_path.exists():
                self.base, self.cursor = mirror(), rest[0]
                sqlite3.connect(self.base_path).backup(self.base)
        except (OSError, ValueError, TypeError, sqlite3.Error):
            self.since, self.service_build, self.base = None, None, None
        self.confirmed = False  # the service answered a /sync in this session
        self.lock = threading.Lock()

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()

    def once(self, timeout=40) -> dict | None:
        """One /sync round: a snapshot or Change set when there is news, else None. Raises Offline when offline."""
        fresh = self.remote.fresh.is_set()  # the queue drained: answer at once with its effects
        self.remote.fresh.clear()
        req = {"since": self.since if self.base is not None else None, "client": self.remote.client,
               "wait": 0 if fresh or not self.confirmed else max(0, timeout - 5)}
        if self.base is not None:
            req |= {"cursor": self.cursor, "schema": inbox.SCHEMA_VERSION}
        status, body = self.remote.post(self.remote.link.command(), "/sync", req, timeout)
        with self.lock:
            return self._received(status, body)

    def _received(self, status, body):
        if status != 200:
            raise Offline(body.get("error", f"HTTP {status}"))
        self.confirmed = True
        if "db" not in body and "changes" not in body:
            self.since = body["since"]
            return None
        self.fetch_raw(body["raw"])
        if "db" in body:
            self.caps.clear()
        self.caps.update(body["caps"])
        self.service_build = body.get("build") or ""
        if self.build_file is not None:
            self.build_file.write_text(self.service_build + "\n")
        return body

    def fetch_raw(self, names):
        raw = Path(self.db_path).parent / "raw"
        raw.mkdir(mode=0o700, exist_ok=True)
        missing = [n for n in names if not (raw / n).exists()]
        while missing:
            status, body = self.remote.post(self.remote.link.command(), "/raw", {"names": missing}, 120)
            got = body.get("raw", {}) if status == 200 else {}
            if not got:
                return
            for name, data in got.items():
                tmp = raw / f".{name}.part"
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.write(base64.b64decode(data))
                os.replace(tmp, raw / name)
            missing = [n for n in missing if n not in got]

    def apply(self, db, body) -> bool:
        """On the console's thread: bring the base up to the answer, make the copy the base, and replay the
        console's unconfirmed writes on it. `changed` keeps the (table, id) pairs a Change set touched (None: all)."""
        if "db" in body:
            base = mirror()
            base.deserialize(gzip.decompress(base64.b64decode(body["db"])))
            for (name,) in base.execute("SELECT name FROM sqlite_schema WHERE type = 'trigger'").fetchall():
                base.execute(f'DROP TRIGGER "{name}"')  # a mirror: rows arrive as the service already wrote them
            self.base, self.changed = base, None
        elif self.base is None:
            return False
        else:
            self._patch(body["changes"])
            self.changed = [(c["tbl"], c["row_id"]) for c in body["changes"]]
        self.since, self.cursor = body["since"], body["cursor"]
        pending = self.remote.confirm(body.get("last_mutation", 0))
        self.base.backup(db)
        for name, args, kwargs, mutation in pending:
            module, fn = name.split(".")
            try:
                with inbox.minting(f"{self.remote.client}:{mutation}", kwargs.get("now") or clock.now()):
                    getattr(MODULES[module], fn)(db, *args, **kwargs)
            except Exception as e:  # noqa: BLE001 - the service will refuse it too; its answer reverts it
                LOG.info("replay %s failed: %s", name, e)
        self.base.backup(saved := sqlite3.connect(self.base_path))
        saved.close()
        tmp = self.token_file.with_name(".sync-token.part")
        tmp.write_text(json.dumps([self.since, self.caps, self.service_build, self.cursor]))
        tmp.replace(self.token_file)
        return True

    def _patch(self, changes):
        """Apply a Change set's rows to the base: each as it now stands on the service, or gone."""
        for c in changes:
            tbl, row = c["tbl"], c["row"]
            keyed = any(col[1] == "id" for col in self.base.execute(f'PRAGMA table_info("{tbl}")'))
            if row is None:
                self.base.execute(f'DELETE FROM "{tbl}" WHERE {"id" if keyed else "rowid"} = ?', (c["row_id"],))
            else:
                cols = ", ".join(f'"{k}"' for k in row)
                self.base.execute(f'INSERT OR REPLACE INTO "{tbl}" ({cols}) VALUES ({", ".join("?" * len(row))})',
                                  list(row.values()))
        self.base.commit()

    def run(self):
        while True:
            app = self.remote.app
            link = self.remote.link
            if not link.ready():
                link.start()
                for _ in range(100):
                    if link.ready():
                        break
                    time.sleep(0.05)
                if not link.ready():
                    self.offline("Offline · cannot reach the inbox service; read-only.")
                    time.sleep(2)
                    continue
            try:
                body = self.once()
            except Exception as e:  # noqa: BLE001 - the loop must outlive any one failed round
                LOG.info("sync failed: %s", e)
                self.offline("Offline · cannot reach the inbox service; read-only.")
                time.sleep(2)
                continue
            if body is None:
                self.online()
                continue
            if app is not None:
                try:
                    app.call_from_thread(app.apply_snapshot, self, body)
                except RuntimeError:  # the app has stopped
                    return

    def online(self):
        app = self.remote.app
        if app is not None and not self.remote.online:
            try:
                app.call_from_thread(app.check_copy)  # back online unless the versions differ
            except RuntimeError:
                pass

    def offline(self, why):
        app = self.remote.app
        if app is None:
            self.remote.online, self.remote.problem = False, why
            return
        try:
            app.call_from_thread(app.set_online, why)
        except RuntimeError:
            pass
