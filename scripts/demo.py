"""Phase 6.3 demo: the real service on synthetic data (docs/prd.md §5, Phase 6.3).

Runs `comms-terminal serve`'s pieces in-process: the hooks server over HTTP, the reply socket, and
encrypted-credential loading (from a temporary directory of demo secrets). Inbound mail and Slack
events are signed fixture requests sent over HTTP; outbound sends go to fake Resend and Slack
endpoints that record what would have been sent. Nothing leaves this machine.

Walks AS-1, AS-3, AS-11, AS-4, AS-6, AS-13, and AS-17 and checks each outcome.

    uv run python scripts/demo.py                    # run all scenarios, print PASS/FAIL
    uv run python scripts/demo.py --step --keep      # pause between scenarios; keep serving after
    comms-terminal --db <db> --socket <socket>      # in another pane, to watch and reply
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from comms_terminal import inbox, serve, slack
from comms_terminal.email import EmailSender
from comms_terminal.slack import SlackSender
from comms_terminal.tui import socket_reply

MAILBOX, TEAM, ME = "jeremy@agentmaple.com", "T0AGENTMAPLE", "U0JEREMY"
SLACK_USERS = {"U0LOGAN": ("Logan Carr", "logan@agentmaple.com")}


class FakeEndpoints:
    """Stands in for Resend and Slack's Web API; records each call."""

    def __init__(self):
        self.sent, self.resend_id = [], None

    def resend(self, url, headers, payload):
        self.sent.append(("resend", payload))
        self.resend_id = f"00000000-0000-4000-8000-{len(self.sent):012x}"
        return 200, {"id": self.resend_id}

    def slack_post(self, url, token, payload):
        self.sent.append(("slack", payload))
        return {"ok": True, "ts": f"{time.time():.6f}"}

    def slack_get(self, url, token, params):
        if url.endswith("users.info"):
            name, mail = SLACK_USERS.get(params["user"], (params["user"], None))
            return {"ok": True, "user": {"real_name": name, "profile": {"email": mail, "real_name": name}}}
        return {"ok": True, "channel": {"name": params["channel"]}}


class Demo:
    def __init__(self, db_path, socket_path, port, secrets_):
        self.db_path, self.socket_path, self.port, self.secrets = db_path, socket_path, port, secrets_
        self.db = inbox.connect(db_path)
        self.reply = socket_reply(socket_path)
        self.results = []

    # --- signed fixture requests over HTTP -----------------------------------

    def post(self, path, body: bytes, headers: dict) -> int:
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", body, headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def mail(self, frm, name, subject, text, message_id, in_reply_to=None, extra=()):
        lines = [f"From: {name} <{frm}>", f"To: {MAILBOX}", f"Subject: {subject}", f"Message-ID: {message_id}",
                 f"Date: {datetime.now(timezone.utc):%a, %d %b %Y %H:%M:%S +0000}",
                 *([f"In-Reply-To: {in_reply_to}", f"References: {in_reply_to}"] if in_reply_to else []),
                 *extra, "Content-Type: text/plain; charset=utf-8", "", text]
        body = "\r\n".join(lines).encode()
        ts = str(int(time.time()))
        sig = "sha256=" + hmac.new(self.secrets["email-hook-secret"], ts.encode() + b"." + body, hashlib.sha256).hexdigest()
        return self.post("/hooks/email", body, {"X-Inbox-Timestamp": ts, "X-Inbox-Signature": sig})

    def sent_receipt(self, email_id, message_id):
        body = json.dumps({"type": "email.sent", "data": {"email_id": email_id, "message_id": message_id}}).encode()
        wid, ts = "msg_demo_receipt", str(int(time.time()))
        key = base64.b64decode(self.secrets["resend-webhook-secret"].removeprefix(b"whsec_"))
        sig = base64.b64encode(hmac.new(key, f"{wid}.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
        return self.post("/hooks/resend", body, {"svix-id": wid, "svix-timestamp": ts, "svix-signature": f"v1,{sig}"})

    def slack_message(self, channel, user, text, at: datetime, channel_type="im"):
        ts = f"{at.timestamp():.6f}"
        event = {"type": "message", "channel": channel, "channel_type": channel_type, "user": user,
                 "text": text, "ts": ts, "event_ts": ts}
        body = json.dumps({"type": "event_callback", "team_id": TEAM, "event": event}).encode()
        now = str(int(time.time()))
        sig = "v0=" + hmac.new(self.secrets["slack-signing-secret"], f"v0:{now}:".encode() + body,
                               hashlib.sha256).hexdigest()
        return self.post("/hooks/slack", body, {"X-Slack-Request-Timestamp": now, "X-Slack-Signature": sig,
                                                "Content-Type": "application/json"})

    # --- checks ----------------------------------------------------------------

    def thread_by_title(self, title):
        r = self.db.execute("SELECT id FROM threads WHERE title = ? ORDER BY id DESC", (title,)).fetchone()
        return inbox.get_thread(self.db, r["id"]) if r else None

    def check(self, scenario, what, ok):
        self.results.append((scenario, ok))
        print(f"  {'PASS' if ok else 'FAIL'}  {scenario}: {what}")


def run(demo: Demo, fakes: FakeEndpoints, step):
    now = datetime.now(timezone.utc)

    def pause(title):
        print(f"\n{title}")
        if step:
            input("  [enter] to run ")

    pause("AS-1  Ana emails; Jeremy reads it in Gmail and forgets it.")
    demo.mail("ana@acme.com", "Ana Ruiz", "Pricing for 40 seats?",
              "Hi Jeremy, can we talk pricing for 40 seats?", "<as1@acme.com>")
    t = demo.thread_by_title("Pricing for 40 seats?")
    demo.check("AS-1", "Thread is Todo / Needs first response",
               t is not None and (t.status, t.status_detail) == ("TODO", "CREATED"))

    pause("AS-3  Jeremy replies from the console (through the reply socket).")
    demo.reply(t.id, "Happy to. Does Tuesday 2pm work?", [], [])
    resend = [p for kind, p in fakes.sent if kind == "resend"]
    t = inbox.get_thread(demo.db, t.id)
    demo.check("AS-3", "sent with In-Reply-To <as1@acme.com>; Thread is Snoozed / Waiting for them",
               bool(resend) and resend[-1]["headers"].get("In-Reply-To") == "<as1@acme.com>"
               and (t.status, t.status_detail) == ("SNOOZED", "WAITING_FOR_CUSTOMER"))
    our_id = "<demo-smtp-1@email.amazonses.test>"
    demo.check("AS-3", "signed sent receipt records the delivered SMTP identity",
               demo.sent_receipt(fakes.resend_id, our_id) == 204)

    pause("AS-11 Ana's out-of-office reply arrives (Auto-Submitted: auto-replied).")
    before = demo.db.execute("SELECT count(*) FROM activities").fetchone()[0]
    demo.mail("ana@acme.com", "Ana Ruiz", "Re: Pricing for 40 seats?", "I am out of office until Monday.",
              "<as11@acme.com>", in_reply_to=our_id, extra=("Auto-Submitted: auto-replied",))
    t = inbox.get_thread(demo.db, t.id)
    after = demo.db.execute("SELECT count(*) FROM activities").fetchone()[0]
    demo.check("AS-11", "filtered, not stored; Thread stays Snoozed / Waiting for them",
               after == before and t.status_detail == "WAITING_FOR_CUSTOMER")

    pause("AS-4  Ana replies for real.")
    demo.mail("ana@acme.com", "Ana Ruiz", "Re: Pricing for 40 seats?", "Tuesday works.", "<as4@acme.com>",
              in_reply_to=our_id)
    t = inbox.get_thread(demo.db, t.id)
    demo.check("AS-4", "Thread is Todo / Needs next response", (t.status, t.status_detail) == ("TODO", "NEW_REPLY"))

    pause("AS-6  Jeremy blocks alerts@signoz.io; a new alert arrives.")
    inbox.block(demo.db, "email", "alerts@signoz.io")
    demo.mail("alerts@signoz.io", "SigNoz", "[Alert] p99 latency high", "p99 > 2s for 5m", "<as6@signoz.io>")
    t6 = demo.thread_by_title("[Alert] p99 latency high")
    demo.check("AS-6", "new Thread is Done / Ignored (visible under Ignored)",
               t6 is not None and (t6.status, t6.status_detail) == ("DONE", "IGNORED")
               and t6.id in [r["id"] for r in inbox.queue(demo.db, "Ignored")])

    pause("AS-13 Logan DMs, again 30 minutes later, then again 25 hours after that.")
    demo.slack_message("D0LOGAN", "U0LOGAN", "staging looks good", now - timedelta(hours=26))
    demo.slack_message("D0LOGAN", "U0LOGAN", "any blockers on your side?", now - timedelta(hours=25, minutes=30))
    first = demo.db.execute("SELECT count(*) FROM threads t JOIN channels c ON c.id = t.channel_id"
                            " WHERE c.native_id = 'D0LOGAN'").fetchone()[0]
    demo.slack_message("D0LOGAN", "U0LOGAN", "deploying now", now - timedelta(minutes=30))
    second = demo.db.execute("SELECT count(*) FROM threads t JOIN channels c ON c.id = t.channel_id"
                             " WHERE c.native_id = 'D0LOGAN'").fetchone()[0]
    demo.check("AS-13", "30 minutes apart: one Thread; 25 hours later: a new Thread", (first, second) == (1, 2))

    pause("AS-17 Jeremy replies to Logan's latest Slack Thread from the console.")
    latest = demo.db.execute("SELECT t.id FROM threads t JOIN channels c ON c.id = t.channel_id"
                             " WHERE c.native_id = 'D0LOGAN' ORDER BY t.id DESC").fetchone()["id"]
    demo.reply(latest, "nice, ship it", [], [])
    posted = [p for kind, p in fakes.sent if kind == "slack"]
    t = inbox.get_thread(demo.db, latest)
    demo.check("AS-17", "posted to D0LOGAN under Jeremy's token; Thread is Snoozed / Waiting for them",
               bool(posted) and posted[-1]["channel"] == "D0LOGAN"
               and (t.status, t.status_detail) == ("SNOOZED", "WAITING_FOR_CUSTOMER"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=str(Path(tempfile.gettempdir()) / "comms-terminal-demo.db"))
    ap.add_argument("--socket", default=str(Path(os.environ.get("XDG_RUNTIME_DIR", tempfile.gettempdir()))
                                            / "comms-terminal-demo.sock"))
    ap.add_argument("--port", type=int, default=8788)
    ap.add_argument("--step", action="store_true", help="pause before each scenario")
    ap.add_argument("--keep", action="store_true", help="keep serving after the scenarios (for the console)")
    a = ap.parse_args(argv)

    for f in (a.db, a.db + "-wal", a.db + "-shm"):
        if os.path.exists(f):
            os.unlink(f)
    db = inbox.connect(a.db)
    inbox.migrate(db)
    stamp = datetime.now(timezone.utc).isoformat()
    with db:
        gmail = db.execute("INSERT INTO connections (platform, external_id, self_id, label, connected_at)"
                           " VALUES ('gmail', ?, ?, 'Gmail', ?)", (MAILBOX, MAILBOX, stamp)).lastrowid
        slack_id = db.execute("INSERT INTO connections (platform, external_id, self_id, label, connected_at)"
                              " VALUES ('slack', ?, ?, 'Slack', ?)", (TEAM, ME, stamp)).lastrowid
    db.close()

    # Demo secrets in a private directory, loaded the way systemd's LoadCredentialEncrypted= exposes them.
    creds = tempfile.mkdtemp(prefix="comms-terminal-demo-creds-")
    demo_secrets = {n: secrets.token_hex(32).encode() for n in ("email-hook-secret", "slack-signing-secret")}
    demo_secrets["resend-webhook-secret"] = b"whsec_" + base64.b64encode(secrets.token_bytes(32))
    for name, value in {**demo_secrets, "slack-user-token": b"xoxp-demo", "resend-api-key": b"re_demo"}.items():
        Path(creds, name).write_bytes(value)
        os.chmod(Path(creds, name), 0o600)
    os.environ["CREDENTIALS_DIRECTORY"] = creds

    fakes = FakeEndpoints()
    senders = serve.Senders(made={gmail: EmailSender("re_demo", MAILBOX, post=fakes.resend),
                                  slack_id: SlackSender("xoxp-demo", post=fakes.slack_post)})
    servers = [serve.hooks_server(a.db, "127.0.0.1", a.port, client=slack.SlackClient("xoxp-demo", get=fakes.slack_get)),
               serve.reply_server(a.db, a.socket, senders)]
    for s in servers:
        threading.Thread(target=s.serve_forever, daemon=True).start()

    print(f"Demo service: hooks on http://127.0.0.1:{a.port}, reply socket {a.socket}, database {a.db}")
    print(f"Watch it:     comms-terminal --db {a.db} --socket {a.socket}")
    demo = Demo(a.db, a.socket, a.port, demo_secrets)
    run(demo, fakes, a.step)
    failed = [s for s, ok in demo.results if not ok]
    print(f"\n{len(demo.results) - len(failed)}/{len(demo.results)} scenarios passed"
          + (f"; failed: {', '.join(failed)}" if failed else "."))
    if a.keep:
        print("Serving until ctrl+c. Replies from the console go to the fake endpoints.")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    for s in servers:
        s.shutdown()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
