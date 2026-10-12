"""universal-inbox: the console (default), `serve` (the systemd service), and `init` (create Connections)."""

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path


TRUECOLOR_TERMS = ("ghostty", "kitty", "wezterm", "alacritty", "foot", "truecolor", "direct")


def default_db() -> str:
    return os.environ.get("UNIVERSAL_INBOX_DB",
                          str(Path.home() / ".local/share/universal-inbox/inbox.db"))


def default_socket() -> str:
    return os.environ.get("UNIVERSAL_INBOX_SOCKET",
                          str(Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "universal-inbox.sock"))


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="universal-inbox")
    p.add_argument("--db", default=default_db())
    p.add_argument("--socket", default=default_socket())
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("serve", help="run the hooks server, reply socket, and wake timer")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8787)
    i = sub.add_parser("init", help="create the email and Slack Connections")
    i.add_argument("--email", help="Jeremy's forwarded address, e.g. jeremy@agentmaple.com")
    i.add_argument("--slack-team", help="Slack team ID")
    i.add_argument("--slack-user", help="Jeremy's Slack user ID")
    a = p.parse_args(argv)
    Path(a.db).parent.mkdir(parents=True, exist_ok=True)

    if a.cmd == "serve":
        from . import serve
        serve.run(a.db, a.socket, a.host, a.port)
    elif a.cmd == "init":
        from . import inbox
        db = inbox.connect(a.db)
        inbox.migrate(db)
        now = datetime.now(timezone.utc).isoformat()
        with db:
            if a.email:
                db.execute("INSERT OR IGNORE INTO connections (platform, external_id, self_id, label, connected_at)"
                           " VALUES ('email', ?, ?, 'Email', ?)", (a.email.lower(), a.email.lower(), now))
            if a.slack_team and a.slack_user:
                db.execute("INSERT OR IGNORE INTO connections (platform, external_id, self_id, label, connected_at)"
                           " VALUES ('slack', ?, ?, 'Slack', ?)", (a.slack_team, a.slack_user, now))
        print(f"Connections in {a.db}: " + ", ".join(r[0] for r in db.execute("SELECT platform FROM connections")))
    else:
        # SSH does not forward COLORTERM; without it Textual drops to a 256/16-colour palette.
        if not os.environ.get("COLORTERM") and any(t in os.environ.get("TERM", "") for t in TRUECOLOR_TERMS):
            os.environ["COLORTERM"] = "truecolor"
        from .tui import Console, socket_reply
        Console(a.db, socket_reply(a.socket)).run()


if __name__ == "__main__":
    main()
