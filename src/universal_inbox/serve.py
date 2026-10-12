"""The service: public hooks (/hooks/email, /hooks/slack), the local reply socket (POST /reply), and wake().

Credentials come from systemd's $CREDENTIALS_DIRECTORY (LoadCredentialEncrypted=):
email-hook-secret, slack-signing-secret, slack-user-token, resend-api-key, fastmail-oauth.
"""

import json
import os
import socketserver
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import email, fastmail, inbox, slack


def credential(name: str) -> bytes | None:
    """A systemd credential, or None when this platform is not connected yet (its unit drop-in is absent)."""
    path = Path(os.environ["CREDENTIALS_DIRECTORY"]) / name
    return path.read_bytes().strip() if path.exists() else None


def connection_id(db, platform: str) -> int | None:
    """The hook-fed Connection for a platform (the Gmail forward for email; Fastmail is polled)."""
    r = db.execute("SELECT id FROM connections WHERE platform = ? AND label != ?", (platform, fastmail.LABEL)).fetchone()
    return r and r["id"]


def fastmail_client(db) -> fastmail.Client | None:
    cred = credential("fastmail-oauth")
    state = Path(db.execute("PRAGMA database_list").fetchone()[2] or ".").parent / "fastmail-oauth.json"
    return fastmail.Client.load(cred, state) if cred else None


def senders_from_credentials(db, fm: fastmail.Client | None = None) -> dict:
    """Sender per platform for inbox.reply; the Fastmail Connection's sender is keyed by its id."""
    mailbox = db.execute("SELECT self_id FROM connections WHERE platform = 'email' AND label != ?",
                         (fastmail.LABEL,)).fetchone()
    out = {}
    if token := credential("slack-user-token"):
        out["slack"] = slack.SlackSender(token.decode())
    if mailbox and (key := credential("resend-api-key")):
        out["email"] = email.EmailSender(key.decode(), mailbox["self_id"])
    fm_conn = db.execute("SELECT id FROM connections WHERE label = ?", (fastmail.LABEL,)).fetchone()
    if fm is not None and fm_conn is not None:
        out[fm_conn["id"]] = fastmail.FastmailSender(fm)
    return out


def handle_reply(db, body: bytes, senders: dict) -> tuple[int, dict]:
    """POST /reply: {"thread_id", "text", "to", "cc"} -> (status, JSON body)."""
    try:
        req = json.loads(body)
        thread_id, text = int(req["thread_id"]), str(req["text"])
    except (ValueError, KeyError, TypeError):
        return 400, {"error": "expected thread_id and text"}
    r = db.execute("SELECT c.id, c.platform, c.label FROM threads t JOIN channels ch ON ch.id = t.channel_id"
                   " JOIN connections c ON c.id = ch.connection_id WHERE t.id = ?", (thread_id,)).fetchone()
    if r is None:
        return 404, {"error": "unknown thread"}
    sender = senders.get(r["id"]) or (senders.get(r["platform"]) if r["label"] != fastmail.LABEL else None)
    if sender is None:
        return 503, {"error": f"{r['label'] or r['platform']} is not connected for sending"}
    try:
        t = inbox.reply(db, thread_id, text, sender, req.get("to") or [], req.get("cc") or [])
    except inbox.SendFailed as e:
        return 502, {"error": str(e)}
    return 200, {"status": t.status, "status_detail": t.status_detail}


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


def reply_server(db_path: str, socket_path: str, senders: dict) -> UnixHTTPServer:
    def reply(headers, body):
        db = inbox.connect(db_path)
        try:
            status, out = handle_reply(db, body, senders)
        finally:
            db.close()
        return status, "application/json", json.dumps(out).encode()

    if os.path.exists(socket_path):
        os.unlink(socket_path)
    old = os.umask(0o177)  # socket created 0600 (NFR-4)
    try:
        return UnixHTTPServer(socket_path, _handler({"/reply": reply}))
    finally:
        os.umask(old)


def hooks_server(db_path: str, host: str, port: int, client=None) -> ThreadingHTTPServer:
    """`client` (Slack lookups) is swappable for the 6.3 demo; the service uses the real API."""
    email_secret, slack_secret = credential("email-hook-secret"), credential("slack-signing-secret")
    token = credential("slack-user-token")
    client = client or (slack.SlackClient(token.decode()) if token else None)

    def email_hook(headers, body):
        if email_secret is None:
            return 503, "text/plain", b"email not connected"
        db = inbox.connect(db_path)
        try:
            status = email.handle_hook(db, connection_id(db, "email"), headers, body, email_secret,
                                       datetime.now(timezone.utc))
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

    return ThreadingHTTPServer((host, port), _handler({"/hooks/email": email_hook, "/hooks/slack": slack_hook}))


def wake_loop(db_path: str, stop: threading.Event, every: float = 60) -> None:
    while not stop.wait(every):
        db = inbox.connect(db_path)
        try:
            inbox.wake(db, datetime.now(timezone.utc))
        finally:
            db.close()


def run(db_path: str, socket_path: str, host: str, port: int) -> None:
    import logging
    logging.basicConfig(level=logging.INFO, format="%(message)s")  # journald adds the time
    db = inbox.connect(db_path)
    inbox.migrate(db)
    fm = fastmail_client(db)
    senders = senders_from_credentials(db, fm)
    db.close()
    stop = threading.Event()
    servers = [hooks_server(db_path, host, port), reply_server(db_path, socket_path, senders)]
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in servers]
    threads.append(threading.Thread(target=wake_loop, args=(db_path, stop), daemon=True))
    if fm is not None:
        threads.append(threading.Thread(target=fastmail.poll_loop, args=(db_path, fm, stop), daemon=True))
    for t in threads:
        t.start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        stop.set()
        for s in servers:
            s.shutdown()
