"""Run by test_installed_console.py with an installed comms-terminal's own Python, so it imports the built package
and none of the source tree: start the console the way `comms-terminal --remote` does, against a local service,
and print how it ended as JSON. argv: work directory, then "same" (the service runs this console's build) or
"other" (it runs another, so the console quits with UPDATE_EXIT to update)."""

import asyncio
import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from comms_terminal import inbox, remote, serve, tui


class LocalLink:
    """remote.Link without SSH: each request runs the relay script against the local service's socket."""

    def __init__(self, sock):
        self.relay = remote.RELAY.replace(repr(remote.SERVICE_SOCKET), repr(sock))

    def start(self):
        pass

    def ready(self):
        return True

    def stop(self):
        pass

    def command(self):
        return [sys.executable, "-I", "-c", self.relay]


work, mode = Path(sys.argv[1]), sys.argv[2]
(work / "service").mkdir(parents=True)
(work / "laptop").mkdir()
path = str(work / "service" / "inbox.db")
db = inbox.connect(path)
inbox.migrate(db)
with db:
    db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
               " VALUES (2, 'slack', 'T0', 'U0JEREMY', 'Slack', '2026-10-12T00:00:00+00:00')")
inbox.ingest(db, inbox.Incoming(2, "D0ANA", "im", "MESSAGE", "D0ANA:1", inbox.Identity("slack_user", "U0ANA"),
                                datetime.now(timezone.utc), "hi", data={"channel": "D0ANA"}))
server = serve.reply_server(path, str(work / "s.sock"), serve.Senders(made={}))
threading.Thread(target=server.serve_forever, daemon=True).start()

rem, caps = remote.Remote(LocalLink(str(work / "s.sock")), remote.post), {}
remote.install(tui, rem, caps)
copy = str(work / "laptop" / "inbox.db")
syncer = remote.Syncer(rem, copy, caps)
c = inbox.connect(copy)
syncer.apply(c, syncer.once())
c.close()
rem.online = True
mine = tui.build_version().rsplit("-", 1)[-1]
syncer.service_build = mine if mode == "same" else "0000000"
app, seen = tui.Console(copy, remote=rem, syncer=syncer), {}


async def go():
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.pause(0.5)
        seen["main"] = app.is_screen_installed("main") and bool(app.main.query("#main"))
asyncio.run(go())
print(json.dumps({"build": mine, "main": seen.get("main"), "return_code": app.return_code,
                  "exception": repr(app._exception) if app._exception else None}))
