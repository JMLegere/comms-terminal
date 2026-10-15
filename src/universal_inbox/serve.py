"""The service: public hooks (/hooks/email, /hooks/slack, /hooks/quo), the local reply socket (POST /reply), wake(),
and a health check of every Connection each 30 minutes.

Credentials come from systemd's $CREDENTIALS_DIRECTORY (LoadCredentialEncrypted=):
email-hook-secret, slack-signing-secret, slack-user-token, resend-api-key, fastmail-oauth, quo-webhook-secret,
quo-api-key, tana-oauth, attio-token, linear-token.
"""

import importlib
import json
import os
import re
import socketserver
import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import attio, email, fastmail, inbox, linear, mcp, platforms, quo, slack, tana


def credential(name: str) -> bytes | None:
    """A systemd credential, or None when this platform is not connected yet (its unit drop-in is absent)."""
    directory = os.environ.get("CREDENTIALS_DIRECTORY")
    path = Path(directory) / name if directory else None
    return path.read_bytes().strip() if path is not None and path.exists() else None


def connection_id(db, platform: str) -> int | None:
    """The Connection on a Platform (one each)."""
    r = db.execute("SELECT id FROM connections WHERE platform = ?", (platform,)).fetchone()
    return r and r["id"]


def fastmail_client(db) -> fastmail.Client | None:
    cred = credential("fastmail-oauth")
    state = Path(db.execute("PRAGMA database_list").fetchone()[2] or ".").parent / "fastmail-oauth.json"
    return fastmail.Client.load(cred, state) if cred else None


def tana_context(db) -> tana.Context | None:
    cred = credential("tana-oauth")
    state = Path(db.execute("PRAGMA database_list").fetchone()[2] or ".").parent / "tana-oauth.json"
    return tana.Context(tana.Client.load(cred, state)) if cred else None


def attio_context(db) -> tana.Context | None:
    token = credential("attio-token")
    return attio.Context(attio.Client(token.decode())) if token else None


def linear_context(db) -> tana.Context | None:
    token = credential("linear-token")
    return linear.Context(linear.Client(token.decode())) if token else None


def _linear_names(db, emails: list[str], domains: list[str]) -> tuple[list[str], list[str]]:
    """Only unambiguous local full names, selected by this page's exact email/domain keys."""
    emails, domains = set(emails), set(domains)
    with inbox._read_snapshot(db):
        people = [(r["id"], " ".join(r["display_name"].split()))
                  for r in db.execute("SELECT id, display_name FROM persons ORDER BY id")]
        organizations = [(r["domain"].strip().casefold(), " ".join(r["name"].split()))
                         for r in db.execute("SELECT domain, name FROM organizations ORDER BY id")]
        person_ids = {r["person_id"] for r in db.execute("SELECT person_id, value FROM identities WHERE kind = 'email'")
                      if r["value"].strip().casefold() in emails}
    counts = Counter(name.casefold() for _, name in people + organizations)

    def eligible(name):
        return (bool(name) and counts[name.casefold()] == 1 and "@" not in name and "://" not in name
                and any(c.isalpha() for c in name) and not re.fullmatch(r"[\w-]+(?:\.[\w-]+)+\.?", name))

    return ([name for pid, name in people if pid in person_ids and len(name.split()) >= 2 and eligible(name)],
            [name for domain, name in organizations if domain in domains and eligible(name)])


def handle_linear(db, body: bytes, context) -> tuple[int, dict]:
    """POST /linear: email/domain arrays only; unique local names are resolved afresh, without writes."""
    try:
        req = json.loads(body)
        if not isinstance(req, dict):
            raise ValueError
        emails, domains = req.get("emails", []), req.get("domains", [])
        if any(not isinstance(values, list) or any(not isinstance(v, str) for v in values)
               for values in (emails, domains)):
            raise ValueError
    except (ValueError, TypeError):
        return 400, {"error": "expected emails and domains arrays of strings"}
    if context is None:
        return 503, {"error": "Linear is not connected"}
    emails = sorted({value.strip().casefold() for value in emails if value.strip()})
    domains = sorted({value.strip().casefold() for value in domains if value.strip()})
    if not emails and not domains:
        return 200, {"issues": []}
    try:
        person_names, organization_names = _linear_names(db, emails, domains)
        return 200, context.get(emails, domains, person_names, organization_names)
    except (linear.LinearError, OSError, ValueError, KeyError, IndexError, TypeError):
        return 502, {"error": "Linear context could not be loaded"}


def handle_context(body: bytes, context: tana.Context | None, name: str = "Tana") -> tuple[int, dict]:
    """POST /tana and /attio: {"emails", "domains"} -> (status, that Context source's answer or {"error"})
    (FR-13a, FR-13b)."""
    if context is None:
        return 503, {"error": f"{name} is not connected"}
    try:
        req = json.loads(body)
        emails, domains = [str(e) for e in req.get("emails") or []], [str(d) for d in req.get("domains") or []]
    except (ValueError, TypeError, AttributeError):
        return 400, {"error": "expected emails and domains"}
    try:
        return 200, context.get(emails, domains)
    except (mcp.McpError, attio.AttioError, OSError, ValueError, KeyError, IndexError) as e:
        return 502, {"error": str(e) or type(e).__name__}


def handle_attio_people(body: bytes, context: tana.Context | None) -> tuple[int, dict]:
    """POST /attio-people: {"days"} -> Attio People touched in that many days, for `universal-inbox attio-import`."""
    if context is None:
        return 503, {"error": "Attio is not connected"}
    try:
        days = int(json.loads(body)["days"])
    except (ValueError, KeyError, TypeError):
        return 400, {"error": "expected days"}
    try:
        return 200, {"people": attio.recent_people(context.client, datetime.now(timezone.utc) - timedelta(days=days))}
    except (attio.AttioError, OSError, ValueError, KeyError, IndexError) as e:
        return 502, {"error": str(e) or type(e).__name__}


class Senders:
    """Reply senders per Connection, made by its Platform's adapter on first use: a Connection added while the
    service runs can send without a restart. `shared` holds process-wide clients (Fastmail's)."""

    def __init__(self, shared: dict | None = None, made: dict | None = None):
        self.shared, self.made = shared or {}, dict(made or {})

    def get(self, connection):
        if connection["id"] not in self.made:
            adapter = importlib.import_module(f".{platforms.PLATFORMS[connection['platform']].adapter}", __package__)
            sender = adapter.make_sender(connection, credential, self.shared)
            if sender is None:
                return None
            self.made[connection["id"]] = sender
        return self.made[connection["id"]]


def handle_reply(db, body: bytes, senders: Senders) -> tuple[int, dict]:
    """POST /reply: {"thread_id", "text", "to", "cc"} -> (status, JSON body)."""
    try:
        req = json.loads(body)
        thread_id, text = int(req["thread_id"]), str(req["text"])
    except (ValueError, KeyError, TypeError):
        return 400, {"error": "expected thread_id and text"}
    r = db.execute("SELECT c.* FROM threads t JOIN channels ch ON ch.id = t.channel_id"
                   " JOIN connections c ON c.id = ch.connection_id WHERE t.id = ?", (thread_id,)).fetchone()
    if r is None:
        return 404, {"error": "unknown thread"}
    sender = senders.get(r)
    if sender is None:
        return 503, {"error": f"{r['label'] or r['platform']} is not connected for sending"}
    try:
        t = inbox.reply(db, thread_id, text, sender, req.get("to") or [], req.get("cc") or [])
    except inbox.SendFailed as e:
        return 502, {"error": str(e)}
    return 200, {"status": t.status, "status_detail": t.status_detail}


def handle_send_draft(db, body: bytes, senders: Senders) -> tuple[int, dict]:
    """POST /send-draft: {"draft_id", "revision", "again"} -> 200 {thread_id}; 502 refused (Draft editable);
    504 outcome unknown (Draft locked)."""
    try:
        req = json.loads(body)
        draft_id, revision, again = int(req["draft_id"]), int(req["revision"]), bool(req.get("again"))
    except (ValueError, KeyError, TypeError):
        return 400, {"error": "expected draft_id and revision"}
    try:
        d = inbox.get_draft(db, draft_id)
    except (KeyError, ValueError):
        return 404, {"error": "unknown draft"}
    r = db.execute("SELECT * FROM connections WHERE id = ?", (d["connection_id"],)).fetchone()
    if r is None:
        return 400, {"error": "Choose a Connection"}
    sender = senders.get(r)
    if sender is None:
        return 503, {"error": f"{r['label'] or r['platform']} is not connected for sending"}
    try:
        t = inbox.send_draft(db, draft_id, revision, sender, again=again)
    except inbox.DraftConflict as e:
        return 409, {"error": str(e)}
    except inbox.SendUnknown as e:
        return 504, {"error": str(e)}
    except inbox.SendFailed as e:
        return 502, {"error": str(e)}
    except ValueError as e:
        return 400, {"error": str(e)}
    return 200, {"thread_id": t.id}


def handle_slack_conversations(db, body: bytes, senders: Senders) -> tuple[int, dict]:
    """POST /slack-conversations: {"connection_id"} -> {"conversations": [{id, kind, label}]} for the Slack Draft picker."""
    try:
        connection_id = int(json.loads(body)["connection_id"])
    except (ValueError, KeyError, TypeError):
        return 400, {"error": "expected connection_id"}
    r = db.execute("SELECT * FROM connections WHERE id = ? AND platform = 'slack'", (connection_id,)).fetchone()
    sender = senders.get(r) if r else None
    if sender is None:
        return 503, {"error": "Slack is not connected"}
    try:
        return 200, {"conversations": sender.conversations()}
    except (RuntimeError, OSError, ValueError) as e:
        return 502, {"error": str(e)}


class _Handler(BaseHTTPRequestHandler):
    routes: dict = {}  # path -> callable(headers, body) -> (status, content_type, bytes)

    def do_POST(self):
        route = self.routes.get(self.path)
        if route is None:
            self.send_error(404)
            return
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        status, ctype, out = route(self.headers, body)
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def address_string(self):  # Unix sockets have no peer address
        return "local"

    def log_message(self, fmt, *args):  # no request bodies or tokens in logs (NFR-5)
        pass


def _handler(routes):
    return type("Handler", (_Handler,), {"routes": routes})


class UnixHTTPServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def reply_server(db_path: str, socket_path: str, senders: Senders, contexts: dict | None = None) -> UnixHTTPServer:
    """The local socket: POST /reply, POST /send-draft, POST /slack-conversations, and POST /<source> for each Context source in `contexts` ({"tana": …})."""
    contexts = contexts or {}
    def reply(headers, body):
        db = inbox.connect(db_path)
        try:
            status, out = handle_reply(db, body, senders)
        finally:
            db.close()
        return status, "application/json", json.dumps(out).encode()

    def send_draft(headers, body):
        db = inbox.connect(db_path)
        try:
            status, out = handle_send_draft(db, body, senders)
        finally:
            db.close()
        return status, "application/json", json.dumps(out).encode()

    def slack_conversations(headers, body):
        db = inbox.connect(db_path)
        try:
            status, out = handle_slack_conversations(db, body, senders)
        finally:
            db.close()
        return status, "application/json", json.dumps(out).encode()

    def attio_people(headers, body):
        status, out = handle_attio_people(body, contexts.get("attio"))
        return status, "application/json", json.dumps(out).encode()

    def linear_route(headers, body):
        db = inbox.connect(db_path)
        try:
            status, out = handle_linear(db, body, contexts.get("linear"))
        finally:
            db.close()
        return status, "application/json", json.dumps(out).encode()

    def context_route(name):
        def route(headers, body):
            status, out = handle_context(body, contexts.get(name), name.title())
            return status, "application/json", json.dumps(out).encode()
        return route

    if os.path.exists(socket_path):
        os.unlink(socket_path)
    old = os.umask(0o177)  # socket created 0600 (NFR-4)
    try:
        return UnixHTTPServer(socket_path, _handler({"/reply": reply, "/send-draft": send_draft,
                                                         "/slack-conversations": slack_conversations, "/attio-people": attio_people, "/tana": context_route("tana"), "/attio": context_route("attio"), "/linear": linear_route}))
    finally:
        os.umask(old)


def hooks_server(db_path: str, host: str, port: int, client=None) -> ThreadingHTTPServer:
    """`client` (Slack lookups) is swappable for the 6.3 demo; the service uses the real API."""
    email_secret, slack_secret = credential("email-hook-secret"), credential("slack-signing-secret")
    quo_secret = credential("quo-webhook-secret")
    token = credential("slack-user-token")
    client = client or (slack.SlackClient(token.decode()) if token else None)

    def email_hook(headers, body):
        if email_secret is None:
            return 503, "text/plain", b"email not connected"
        db = inbox.connect(db_path)
        try:
            # The envelope recipient picks the Platform: Fastmail's rule copies to its own address (#113).
            to = (headers.get("X-Inbox-To") or "").lower()
            conn_id = connection_id(db, "fastmail" if to == fastmail.INBOUND else "gmail")
            status = email.handle_hook(db, conn_id, headers, body, email_secret, datetime.now(timezone.utc))
        finally:
            db.close()
        return status, "text/plain", b""

    def slack_hook(headers, body):
        if slack_secret is None or client is None:
            return 503, "text/plain", b"slack not connected"
        db = inbox.connect(db_path)
        try:
            status, text = slack.handle_hook(db, connection_id(db, "slack"), headers, body, slack_secret,
                                             datetime.now(timezone.utc), client)
        finally:
            db.close()
        return status, "text/plain", text.encode()

    def quo_hook(headers, body):
        if quo_secret is None:
            return 503, "text/plain", b"quo not connected"
        db = inbox.connect(db_path)
        try:
            status = quo.handle_hook(db, connection_id(db, "quo"), headers, body, quo_secret, datetime.now(timezone.utc))
        finally:
            db.close()
        return status, "text/plain", b""

    return ThreadingHTTPServer((host, port), _handler({"/hooks/email": email_hook, "/hooks/slack": slack_hook,
                                                       "/hooks/quo": quo_hook}))


def wake_loop(db_path: str, stop: threading.Event, every: float = 60) -> None:
    while not stop.wait(every):
        db = inbox.connect(db_path)
        try:
            inbox.wake(db, datetime.now(timezone.utc))
        finally:
            db.close()


def probe_all(db, shared: dict, cred=credential) -> None:
    """One health check per Connection through its adapter's `probe` (decision #119). Fastmail has none:
    its 10-minute Sent poll records the same result."""
    import logging
    for c in inbox.connections(db):
        adapter = importlib.import_module(f".{platforms.PLATFORMS[c['platform']].adapter}", __package__)
        if (probe := getattr(adapter, "probe", None)) is None:
            continue
        try:
            error = probe(c, cred, shared)
        except Exception as e:  # noqa: BLE001 - one bad check must not stop the others or the loop
            error = f"health check crashed: {e}"
        inbox.set_probe(db, c["id"], error)
        logging.getLogger("universal_inbox.health").info("health check %s: %s", c["platform"], error or "ok")


def probe_loop(db_path: str, shared: dict, stop: threading.Event, every: float = 1800) -> None:
    # ponytail: one failed check shows BROKEN for 30 minutes; retry once inside probe_all if blips annoy
    while True:
        db = inbox.connect(db_path)
        try:
            probe_all(db, shared)
        finally:
            db.close()
        if stop.wait(every):
            return


def run(db_path: str, socket_path: str, host: str, port: int) -> None:
    import logging
    logging.basicConfig(level=logging.INFO, format="%(message)s")  # journald adds the time
    db = inbox.connect(db_path)
    inbox.migrate(db)
    fm = fastmail_client(db)
    senders = Senders({"fastmail": fm} if fm is not None else {})
    contexts = {"tana": tana_context(db), "attio": attio_context(db), "linear": linear_context(db)}
    db.close()
    stop = threading.Event()
    servers = [hooks_server(db_path, host, port), reply_server(db_path, socket_path, senders, contexts)]
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in servers]
    threads.append(threading.Thread(target=wake_loop, args=(db_path, stop), daemon=True))
    threads.append(threading.Thread(target=probe_loop, args=(db_path, senders.shared, stop), daemon=True))
    if fm is not None:
        threads.append(threading.Thread(target=fastmail.poll_loop, args=(db_path, fm, stop), daemon=True))
    if token := credential("slack-user-token"):
        def names():
            db = inbox.connect(db_path)
            try:
                slack.refresh_names(db, slack.SlackClient(token.decode()))
            except (OSError, ValueError, KeyError) as e:
                import logging
                logging.getLogger("universal_inbox.slack").info("slack channel names not refreshed: %s", e)
            finally:
                db.close()
        threads.append(threading.Thread(target=names, daemon=True))
    for t in threads:
        t.start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        stop.set()
        for s in servers:
            s.shutdown()
