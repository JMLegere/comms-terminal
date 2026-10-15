"""universal-inbox: the console (default), `serve` (the systemd service), and `init` (create Connections)."""

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path


TRUECOLOR_TERMS = ("ghostty", "kitty", "wezterm", "alacritty", "foot", "truecolor", "direct")


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
    p = argparse.ArgumentParser(prog="universal-inbox")
    p.add_argument("--db", default=default_db())
    p.add_argument("--socket", default=default_socket())
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("serve", help="run the hooks server, reply socket, and wake timer")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8787)
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
    a = p.parse_args(argv)
    Path(a.db).parent.mkdir(parents=True, exist_ok=True)

    if a.cmd == "serve":
        from . import serve
        serve.run(a.db, a.socket, a.host, a.port)
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
        from .tui import (CONTEXT_SOURCES, Console, socket_context, socket_reply, socket_send_draft,
                          socket_slack_conversations)
        Console(a.db, socket_reply(a.socket), {k: socket_context(a.socket, k) for k in CONTEXT_SOURCES},
                socket_send_draft(a.socket), socket_slack_conversations(a.socket)).run()


if __name__ == "__main__":
    main()
