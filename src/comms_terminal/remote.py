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
import json
import logging
import os
import queue
import socket
import sqlite3
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from . import files, inbox, pipeline, sequences, writers

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


def creates(name: str, db, args, kwargs) -> bool:
    """Whether this call makes a row whose id later actions use: those wait for the service's answer."""
    if CALLS[name] != "write":
        return True
    if name == "inbox.act":
        action = args[1] if len(args) > 1 else kwargs.get("action")
        return isinstance(action, inbox.SetCase) and bool(action.new_title)
    if name == "inbox.save_draft":
        return kwargs.get("draft_id") is None
    if name in ("inbox.save_reply_draft", "inbox.schedule_reply"):
        thread_id, body = args[0], args[1] if len(args) > 1 else kwargs.get("body", "")
        return bool(body.strip()) and inbox.reply_draft(db, thread_id) is None
    return False


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


def ident(result):
    """The part of a create's result that later actions rely on: the new row's id."""
    if isinstance(result, (int, type(None))):
        return result
    if isinstance(result, dict):
        return result.get("id")
    if isinstance(result, tuple):
        return tuple(ident(x) for x in result)
    if isinstance(result, inbox.Thread):
        return result.id
    return result


# --- service side ----------------------------------------------------------------

def handle_call(db_path: str, body: bytes) -> tuple[int, dict]:
    """POST /call {fn, args, kwargs}: one allowlisted function on the service's database."""
    try:
        req = json.loads(body)
        name = req["fn"]
        if name not in CALLS:
            return 403, {"error": f"{name} is not callable"}
        module, fn = name.split(".")
        args, kwargs = decode(req.get("args", [])), decode(req.get("kwargs", {}))
    except (ValueError, KeyError, TypeError):
        return 400, {"error": "bad call"}
    db = inbox.connect(db_path)
    try:
        result = getattr(MODULES[module], fn)(db, *args, **kwargs)
        return 200, {"result": encode(result)}
    except tuple(ERRORS.values()) as e:
        kind = next(n for n, c in ERRORS.items() if isinstance(e, c))  # most specific first
        return 409, {"error": str(e.args[0]) if e.args else kind, "type": kind}
    except Exception as e:  # noqa: BLE001 - answer, so the console reverts instead of waiting
        LOG.info("call %s failed: %s", name, e)
        return 500, {"error": f"{type(e).__name__}: {e}"}
    finally:
        db.close()


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


def handle_sync(db_path: str, changes: Changes, body: bytes, wait: float = 25) -> tuple[int, dict]:
    """POST /sync {since}: once the database differs from `since` (at once for none), its snapshot, the raw
    message names, signed Attachment link capabilities, and the service's build."""
    try:
        req = json.loads(body or b"{}")
        since, wait = req.get("since"), min(wait, max(0.0, float(req.get("wait", wait))))
    except (ValueError, AttributeError, TypeError):
        return 400, {"error": "bad sync"}
    token = changes.wait(since, wait)
    if token == since:
        return 200, {"since": token}  # nothing new
    data = snapshot_bytes(db_path)
    db = inbox.connect(db_path)
    try:
        raw = files.raw_dir(db)
        names = sorted(p.name for p in raw.iterdir()) if raw is not None and raw.is_dir() else []
        secret = files.files_secret(db)
        caps = {r[0]: files.file_cap(secret, r[0])
                for r in db.execute("SELECT DISTINCT sha FROM attachments WHERE sha IS NOT NULL")}
    finally:
        db.close()
    return 200, {"since": token, "db": base64.b64encode(data).decode(), "raw": names, "caps": caps,
                 "build": build()}


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
        self.busy = 0  # writes applied locally and not yet answered by the service
        self.lock = threading.Lock()
        self.idle = threading.Event()
        self.idle.set()
        self.fresh = threading.Event()  # the queue drained: ask for a snapshot at once
        self.resync = None  # Syncer.now: the service's snapshot, applied before a create returns
        threading.Thread(target=self._send_loop, daemon=True).start()

    def call(self, name, args, kwargs, timeout=30):
        status, body = self.post(self.link.command(), "/call",
                                 {"fn": name, "args": encode(list(args)), "kwargs": encode(kwargs)}, timeout)
        if status == 200:
            return decode(body["result"])
        error = ERRORS.get(body.get("type"))
        if error is not None:
            raise error(body.get("error", ""))
        raise Offline(body.get("error", f"HTTP {status}"))

    def require_online(self):
        if not self.online:
            raise Offline(self.problem or "Offline · read-only")

    def queue(self, name, args, kwargs):
        with self.lock:
            self.busy += 1
            self.idle.clear()
        self.pending.put((name, args, kwargs))

    def drain(self, timeout=30) -> bool:
        return self.idle.wait(timeout)

    def _send_loop(self):
        while True:
            name, args, kwargs = self.pending.get()
            try:
                self.call(name, args, kwargs)
            except Exception as e:  # noqa: BLE001 - every failure reverts the copy to the service's state
                LOG.info("write %s not applied: %s", name, e)
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
                if kind == "service":
                    return remote.call(name, args, kwargs)
                if creates(name, db, args, kwargs):
                    if not remote.drain():
                        raise Offline("The inbox service has not answered earlier changes yet.")
                    result = remote.call(name, args, kwargs)
                    try:  # the copy follows; ids agree while it matches the service
                        same = ident(value(db, *args, **kwargs)) == ident(result)
                    except Exception:  # noqa: BLE001
                        same = False
                    if not same:  # the copy made another row: take the service's state before going on
                        remote.fresh.set()
                        if remote.resync is not None:
                            remote.resync(db)
                    return result
                result = value(db, *args, **kwargs)  # raises exactly as the service would, and paints at once
                remote.queue(name, args, kwargs)
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


class Syncer:
    """Keeps the laptop copy current: waits on /sync, fetches new raw messages, and swaps the snapshot in on the
    console's thread once no local write is still on its way."""

    def __init__(self, remote: Remote, db_path: str, caps: dict, build_file: Path | None = None):
        self.remote, self.db_path, self.caps, self.build_file = remote, db_path, caps, build_file
        self.token_file = Path(db_path).parent / "sync-token"  # the copy's snapshot: unchanged, no new download
        try:
            self.since, caps_json, self.service_build = json.loads(self.token_file.read_text())
            caps.update(caps_json)
        except (OSError, ValueError, TypeError):
            self.since, self.service_build = None, None
        self.confirmed = False  # the service answered a /sync in this session
        self.lock = threading.Lock()  # the loop and a create's resync both store what a /sync returned
        remote.resync = self.now

    def now(self, db):
        """On the console's thread: fetch and apply the service's current snapshot (a create whose new id the
        copy did not reproduce)."""
        try:
            self.remote.fresh.set()
            body = self.once(timeout=30)
            if body is not None:
                self.apply(db, body)
        except Exception as e:  # noqa: BLE001 - the loop's next round catches up
            LOG.info("resync failed: %s", e)

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()

    def once(self, timeout=40) -> dict | None:
        """One /sync round: the snapshot body when there is news, else None. Raises ReplyError when offline."""
        fresh = self.remote.fresh.is_set()
        self.remote.fresh.clear()
        status, body = self.remote.post(self.remote.link.command(), "/sync",
                                        {"since": None if fresh else self.since,
                                         "wait": 0 if fresh or not self.confirmed else max(0, timeout - 5)},
                                        timeout)  # the session's first answer at once: writes wait on it
        with self.lock:  # not around the request: the loop's long poll must not hold up a create's resync
            return self._received(status, body)

    def _received(self, status, body):
        if status != 200:
            raise Offline(body.get("error", f"HTTP {status}"))
        self.confirmed = True
        if "db" not in body:
            self.since = body["since"]
            return None
        self.fetch_raw(body["raw"])
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
        """Swap the snapshot into the console's connection; False (try again) while a local write is pending."""
        if not self.remote.idle.is_set():
            self.remote.fresh.set()
            return False
        mem = sqlite3.connect(":memory:")
        try:
            mem.deserialize(gzip.decompress(base64.b64decode(body["db"])))
            mem.backup(db)
        finally:
            mem.close()
        self.since = body["since"]
        tmp = self.token_file.with_name(".sync-token.part")
        tmp.write_text(json.dumps([self.since, self.caps, self.service_build]))
        tmp.replace(self.token_file)
        return True

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
            self.remote.drain(None)  # a snapshot taken mid-write would only be refused
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
