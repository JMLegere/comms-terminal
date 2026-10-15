"""universal-inbox: the console (default), `serve` (the systemd service), `init` (create Connections), and
`threads`/`thread`/`drafts`/`draft` for a terminal agent (agent.py)."""

import argparse
import os
import time
from datetime import datetime, timezone
from pathlib import Path


TRUECOLOR_TERMS = ("ghostty", "kitty", "wezterm", "alacritty", "foot", "truecolor", "direct")
LOCAL_TZ = "America/Halifax"  # Jeremy's time; Workbox runs in UTC. TZ in the environment overrides it.


def use_local_time() -> None:
    """Shown times (headers, reply quotes, forwards, invites) read in Jeremy's zone, not the host's UTC."""
    os.environ.setdefault("TZ", LOCAL_TZ)
    time.tzset()


def default_db() -> str:
    return os.environ.get("UNIVERSAL_INBOX_DB",
                          str(Path.home() / ".local/share/universal-inbox/inbox.db"))


SERVICE_SOCKET = "/run/universal-inbox/inbox.sock"  # deploy/universal-inbox.service


def default_socket() -> str:
    if "UNIVERSAL_INBOX_SOCKET" in os.environ:
        return os.environ["UNIVERSAL_INBOX_SOCKET"]
    if Path(SERVICE_SOCKET).parent.is_dir():  # the installed service is running
        return SERVICE_SOCKET
    return str(Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "universal-inbox.sock")


def main(argv=None) -> None:
    use_local_time()
    p = argparse.ArgumentParser(prog="universal-inbox")
    p.add_argument("--db", default=default_db())
    p.add_argument("--socket", default=default_socket())
    p.add_argument("--files-host", help="console: the file server's LAN address in Attachment links (192.168.0.107)")
    p.add_argument("--files-port", type=int, help="console: the file server's port (8789)")
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("serve", help="run the hooks server, reply socket, and wake timer")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8787)
    s.add_argument("--files-host", help="LAN address for the file server (Attachments); off when omitted")
    s.add_argument("--files-port", type=int, default=8789)
    i = sub.add_parser("init", help="create Connections, one per Platform (platforms.py)")
    i.add_argument("--gmail", "--email", dest="gmail", help="Jeremy's forwarded Gmail address, e.g. jeremy@agentmaple.com")
    i.add_argument("--slack-team", help="Slack team ID")
    i.add_argument("--slack-user", help="Jeremy's Slack user ID")
    i.add_argument("--quo", "--sms", dest="quo", help="the Quo Work number, E.164 (+1…)")
    i.add_argument("--fastmail", help="Jeremy's Fastmail address")
    si = sub.add_parser("slack-import", help="one-time import of recent Slack, through the live filter")
    si.add_argument("--days", type=int, default=7)
    f = sub.add_parser("fastmail-import", help="one-time import of recent Fastmail Inbox and Sent, left in Todo")
    f.add_argument("--cred", default=str(Path.home() / ".config/universal-inbox/fastmail-oauth.json"))
    f.add_argument("--days", type=int, default=30)
    ai = sub.add_parser("attio-import", help="add Attio People touched recently; merge and rename to match Attio")
    ai.add_argument("--days", type=int, default=89)
    sub.add_parser("attachments-backfill", help="one-time copy of stored Gmail messages' Attachments into files/")
    from . import agent
    agent.add_parsers(sub)
    a = p.parse_args(argv)
    Path(a.db).parent.mkdir(parents=True, exist_ok=True)

    if a.cmd in ("threads", "thread", "drafts", "draft"):
        from . import inbox
        db = inbox.connect(a.db)
        inbox.migrate(db)
        agent.run(a, db)
    elif a.cmd == "serve":
        from . import serve
        serve.run(a.db, a.socket, a.host, a.port, a.files_host, a.files_port)
    elif a.cmd == "init":
        from . import inbox
        from .platforms import PLATFORMS
        db = inbox.connect(a.db)
        inbox.migrate(db)
        now = datetime.now(timezone.utc).isoformat()
        with db:
            new = [("gmail", a.gmail and a.gmail.lower(), None), ("fastmail", a.fastmail and a.fastmail.lower(), None),
                   ("slack", a.slack_team if a.slack_user else None, a.slack_user), ("quo", a.quo, None)]
            for platform, external_id, self_id in new:
                if external_id:
                    db.execute("INSERT OR IGNORE INTO connections (platform, external_id, self_id, label, connected_at)"
                               " VALUES (?, ?, ?, ?, ?)", (platform, external_id, self_id or external_id,
                                                           PLATFORMS[platform].title, now))
        print(f"Connections in {a.db}: " + ", ".join(r[0] for r in db.execute("SELECT platform FROM connections")))
    elif a.cmd == "slack-import":
        # Run with the service's credential, e.g. sudo systemd-run --uid=jerry --pipe --wait
        #   -p LoadCredentialEncrypted=slack-user-token:/etc/credstore.encrypted/universal-inbox.slack-user-token …
        from . import inbox, serve, slack
        token = serve.credential("slack-user-token")
        if token is None:
            raise SystemExit("no slack-user-token credential (run through systemd-run; runbook §3)")
        db = inbox.connect(a.db)
        conn = db.execute("SELECT * FROM connections WHERE platform = 'slack'").fetchone()
        if conn is None:
            raise SystemExit("no Slack Connection: run `universal-inbox init --slack-team … --slack-user …` first")
        n = slack.import_recent(db, conn, slack.SlackClient(token.decode()), a.days)
        print(f"Slack: stored {n} messages from the last {a.days} days")
    elif a.cmd == "attio-import":
        # The service holds the Attio token; it reads Attio, this writes the database.
        import json
        from . import inbox
        from .tui import _post
        status, body = _post(a.socket, "/attio-people", {"days": a.days}, timeout=600)
        if status != 200:
            raise SystemExit(f"Attio: {body.get('error', status)}")
        db = inbox.connect(a.db)
        inbox.migrate(db)
        report = inbox.import_people(db, body["people"])
        report["same name"] = inbox.merge_duplicates(db)
        print(json.dumps({"attio people": len(body["people"]), **report}, indent=1, ensure_ascii=False))
    elif a.cmd == "attachments-backfill":
        from . import email, inbox
        db = inbox.connect(a.db)
        inbox.migrate(db)
        read, stored = email.backfill_parts(db)
        print(f"Gmail: read {read} messages without Attachments, stored {stored} Attachments")
    elif a.cmd == "fastmail-import":
        from . import fastmail, inbox
        db = inbox.connect(a.db)
        inbox.migrate(db)
        conn = db.execute("SELECT * FROM connections WHERE platform = 'fastmail'").fetchone()
        if conn is None:
            raise SystemExit("no Fastmail Connection: run `universal-inbox init --fastmail <address>` first")
        client = fastmail.Client.load(Path(a.cred).expanduser().read_bytes(), Path(a.db).parent / "fastmail-oauth.json")
        n = fastmail.import_recent(db, client, conn, a.days)
        print(f"Fastmail: imported into {n} Threads (last {a.days} days of Inbox and Sent), all in Todo")
    else:
        # SSH does not forward COLORTERM; without it Textual drops to a 256/16-colour palette.
        if not os.environ.get("COLORTERM") and any(t in os.environ.get("TERM", "") for t in TRUECOLOR_TERMS):
            os.environ["COLORTERM"] = "truecolor"
        import textual_image.widget  # noqa: F401 - asks the terminal about Kitty graphics before Textual owns it
        from .tui import (CONTEXT_SOURCES, FILE_SERVER, Console, socket_context, socket_reply, socket_send_draft,
                          socket_slack_conversations)
        Console(a.db, socket_reply(a.socket), {k: socket_context(a.socket, k) for k in CONTEXT_SOURCES},
                socket_send_draft(a.socket), socket_slack_conversations(a.socket),
                (a.files_host or FILE_SERVER[0], a.files_port or FILE_SERVER[1])).run()


if __name__ == "__main__":
    main()
