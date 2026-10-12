"""The service: public hooks (/hooks/email, /hooks/slack), the local reply socket (POST /reply), and wake().

Credentials come from systemd's $CREDENTIALS_DIRECTORY (LoadCredentialEncrypted=):
email-hook-secret, slack-signing-secret, slack-user-token, resend-api-key.
"""

import json
import os
import socketserver
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import email, inbox, slack


def credential(name: str) -> bytes:
    return (Path(os.environ["CREDENTIALS_DIRECTORY"]) / name).read_bytes().strip()


def connection_id(db, platform: str) -> int | None:
    r = db.execute("SELECT id FROM connections WHERE platform = ?", (platform,)).fetchone()
    return r and r["id"]


def senders_from_credentials(db) -> dict:
    """Sender per platform for inbox.reply."""
    mailbox = db.execute("SELECT self_id FROM connections WHERE platform = 'email'").fetchone()
    out = {"slack": slack.SlackSender(credential("slack-user-token").decode())}
    if mailbox:
        out["email"] = email.EmailSender(credential("resend-api-key").decode(), mailbox["self_id"])
    return out


def handle_reply(db, body: bytes, senders: dict) -> tuple[int, dict]:
    """POST /reply: {"thread_id", "text", "to", "cc"} -> (status, JSON body)."""
    try:
        req = json.loads(body)
        thread_id, text = int(req["thread_id"]), str(req["text"])
    except (ValueError, KeyError, TypeError):
        return 400, {"error": "expected thread_id and text"}
    r = db.execute("SELECT c.platform FROM threads t JOIN channels ch ON ch.id = t.channel_id"
                   " JOIN connections c ON c.id = ch.connection_id WHERE t.id = ?", (thread_id,)).fetchone()
    if r is None:
        return 404, {"error": "unknown thread"}
    try:
        t = inbox.reply(db, thread_id, text, senders[r["platform"]], req.get("to") or [], req.get("cc") or [])
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


def hooks_server(db_path: str, host: str, port: int) -> ThreadingHTTPServer:
    email_secret, slack_secret = credential("email-hook-secret"), credential("slack-signing-secret")
    client = slack.SlackClient(credential("slack-user-token").decode())

    def email_hook(headers, body):
        db = inbox.connect(db_path)
        try:
            status = email.handle_hook(db, connection_id(db, "email"), headers, body, email_secret,
                                       datetime.now(timezone.utc))
        finally:
            db.close()
        return status, "text/plain", b""

    def slack_hook(headers, body):
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
    db = inbox.connect(db_path)
    inbox.migrate(db)
    senders = senders_from_credentials(db)
    db.close()
    stop = threading.Event()
    servers = [hooks_server(db_path, host, port), reply_server(db_path, socket_path, senders)]
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in servers]
    threads.append(threading.Thread(target=wake_loop, args=(db_path, stop), daemon=True))
    for t in threads:
        t.start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        stop.set()
        for s in servers:
            s.shutdown()
