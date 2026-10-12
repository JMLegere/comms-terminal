"""The service's local reply socket (POST /reply) end to end, with fake senders."""

import json
import threading
from datetime import datetime, timezone

from universal_inbox import inbox, serve
from universal_inbox.tui import ReplyError, socket_reply


class Sender:
    def __init__(self, fail=False):
        self.fail = fail

    def send(self, thread, text, to, cc, history):
        if self.fail:
            raise inbox.SendFailed("slack send failed: invalid_auth")
        return "D0ANA:1728700999.000100"


def setup(tmp_path):
    path = str(tmp_path / "inbox.db")
    db = inbox.connect(path)
    inbox.migrate(db)
    with db:
        db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                   " VALUES (2, 'slack', 'T0', 'U0JEREMY', 'Slack', '2026-10-12T00:00:00+00:00')")
    t = inbox.ingest(db, inbox.Incoming(2, "D0ANA", "im", "MESSAGE", "D0ANA:1", inbox.Identity("slack_user", "U0ANA"),
                                        datetime.now(timezone.utc), "hi", data={"channel": "D0ANA"}))
    return path, t.id


def test_handle_reply_statuses(tmp_path):
    path, tid = setup(tmp_path)
    db = inbox.connect(path)
    assert serve.handle_reply(db, b"{}", {})[0] == 400
    assert serve.handle_reply(db, json.dumps({"thread_id": 999, "text": "x"}).encode(), {})[0] == 404
    status, body = serve.handle_reply(db, json.dumps({"thread_id": tid, "text": "x"}).encode(), {"slack": Sender(True)})
    assert status == 502 and "invalid_auth" in body["error"]


def test_reply_over_unix_socket(tmp_path):
    path, tid = setup(tmp_path)
    sock = str(tmp_path / "s.sock")
    server = serve.reply_server(path, sock, {"slack": Sender()})
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        import os
        import stat
        assert stat.S_IMODE(os.stat(sock).st_mode) == 0o600
        out = socket_reply(sock)(tid, "on it", [], [])
        assert out == {"status": "SNOOZED", "status_detail": "WAITING_FOR_CUSTOMER"}
    finally:
        server.shutdown()
        server.server_close()
    try:
        socket_reply(sock + ".missing")(tid, "x", [], [])
    except ReplyError as e:
        assert "not reachable" in str(e)
    else:
        raise AssertionError("expected ReplyError")


def test_service_runs_with_email_only(tmp_path, monkeypatch):
    """Slack not connected yet: /hooks/slack answers 503 and email still works (runbook §6)."""
    import urllib.error
    import urllib.request
    import threading as _threading
    from universal_inbox import serve as _serve
    creds = tmp_path / "creds"
    creds.mkdir()
    (creds / "email-hook-secret").write_text("e" * 64)
    (creds / "resend-api-key").write_text("re_x")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(creds))
    db_path = str(tmp_path / "inbox.db")
    db = inbox.connect(db_path)
    inbox.migrate(db)
    assert set(_serve.senders_from_credentials(db)) == set()  # no email Connection yet, no Slack token
    server = _serve.hooks_server(db_path, "127.0.0.1", 0)
    _threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}/hooks/slack", b"{}", method="POST")
        try:
            urllib.request.urlopen(req, timeout=5)
            status = 200
        except urllib.error.HTTPError as e:
            status = e.code
        assert status == 503
    finally:
        server.shutdown()


def test_email_post_sends_a_user_agent_and_returns_provider_errors():
    """Resend sits behind Cloudflare, which 403s Python's default User-Agent; errors keep the provider's message."""
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from universal_inbox import email as _email
    seen = {}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            seen["ua"] = self.headers.get("User-Agent")
            self.rfile.read(int(self.headers["Content-Length"]))
            body = b'{"message":"The agentmaple.com domain is not verified"}'
            self.send_response(403)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    status, body = _email._post(f"http://127.0.0.1:{srv.server_address[1]}/emails", {}, {"x": 1})
    srv.server_close()
    assert status == 403 and body["message"] == "The agentmaple.com domain is not verified"
    assert seen["ua"].startswith("universal-inbox/")
