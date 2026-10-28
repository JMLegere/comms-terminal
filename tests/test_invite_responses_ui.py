"""The console's Respond key (FR-IR-1, FR-IR-9, FR-IR-10, NFR-IR-3) through Textual's test driver, with a fake
service call in place of the socket."""

import asyncio
import threading
import time
from types import SimpleNamespace

from steps.calendar_steps import event, meeting_thread_for

from comms_terminal import clock, gcal, inbox
from comms_terminal.tui import Console, StatusLine, socket_respond, thread_head


def meeting(tmp_path, title="Pints"):
    path = str(tmp_path / "inbox.db")
    db = inbox.connect(path)
    inbox.migrate(db)
    with db:
        db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                   " VALUES (1, 'gmail', 'jeremy@agentmaple.com', 'jeremy@agentmaple.com', 'Gmail',"
                   " '2026-10-12T00:00:00+00:00')")
    ctx = SimpleNamespace(db=db)
    meeting_thread_for(ctx, event(ctx, title, "brendan.wood@snaptrade.com"))
    return path, ctx


class Service:
    """Records each Response request and answers `result`, after `delay` seconds."""

    def __init__(self, result=None, delay=0.0):
        self.calls, self.result, self.delay = [], result or {"outcome": "confirmed", "status": "accepted"}, delay
        self.done = threading.Event()

    def __call__(self, payload):
        self.calls.append(payload)
        time.sleep(self.delay)
        self.done.set()
        return self.result


def run(app, thread_id, steps):
    seen = {}

    async def go():
        async with app.run_test(size=(200, 50)) as pilot:
            await pilot.pause()
            app.main.open_thread(thread_id)
            await pilot.pause(0.2)
            seen["head"] = thread_head(inbox.thread_view(app.db, thread_id))
            for step in steps:
                await (step(app, pilot, seen) if callable(step) else pilot.press(step))
                await pilot.pause()
            await pilot.pause(0.2)
            seen["toasts"] = [str(n.message) for n in app._notifications]
            seen["bar"] = app.main.query_one(StatusLine).shown
    asyncio.run(go())
    return seen


async def bar(app, pilot, seen):
    seen.setdefault("bars", []).append(app.main.query_one(StatusLine).shown)


def test_i_then_y_accepts_once_and_shows_the_outcome(tmp_path):
    path, ctx = meeting(tmp_path)
    service = Service()
    seen = run(Console(path, respond=service), ctx.thread.id, ["i", bar, "y"])
    assert "i[/] respond" in seen["head"]  # the Meeting Thread's hint (FR-IR-1)
    assert "RESPOND" in seen["bars"][0] and "y" in seen["bars"][0] and "esc" in seen["bars"][0]
    assert service.calls == [{"thread_id": ctx.thread.id, "answer": "accept"}]
    assert "Accepted · Google told the organizer." in seen["toasts"] and "RESPOND" not in seen["bar"]


def test_escape_after_i_changes_nothing(tmp_path):
    path, ctx = meeting(tmp_path)
    service = Service()
    seen = run(Console(path, respond=service), ctx.thread.id, ["i", "escape"])
    assert service.calls == [] and "Not answered." in seen["toasts"] and "RESPOND" not in seen["bar"]


def test_the_key_never_waits_for_google(tmp_path):
    path, ctx = meeting(tmp_path)
    service = Service(delay=1.0)

    async def timed_y(app, pilot, seen):
        start = time.monotonic()
        await pilot.press("y")
        seen["took"] = time.monotonic() - start
        await asyncio.to_thread(service.done.wait, 3)

    seen = run(Console(path, respond=service), ctx.thread.id, ["i", timed_y])
    assert seen["took"] < 0.5 and len(service.calls) == 1


def test_refused_unknown_and_unavailable_outcomes_are_exact(tmp_path):
    path, ctx = meeting(tmp_path)
    for result, toast in [
            ({"outcome": "refused", "error": "You organized this Event."}, "Not answered · You organized this Event."),
            ({"outcome": "unknown", "error": "x"}, "Outcome unknown · check the Event in Google Calendar before answering again."),
            ({"outcome": "unavailable", "error": "Google Calendar writes are not connected"},
             "Not answered · Google Calendar writes are not connected")]:
        seen = run(Console(path, respond=Service(result)), ctx.thread.id, ["i", "n"])
        assert toast in seen["toasts"]


def test_i_on_a_thread_with_no_invitation_opens_nothing(tmp_path):
    path, ctx = meeting(tmp_path)
    t = inbox.ingest(ctx.db, inbox.Incoming(1, "jeremy@agentmaple.com", "mailbox", "MESSAGE", "<m1@test>",
                                            inbox.Identity("email", "sam@stmec.example"), clock.now(), "Hello",
                                            title="Hello"))
    service = Service()
    seen = run(Console(path, respond=service), t.id, ["i", bar, "y"])
    assert "RESPOND" not in seen["bars"][0] and service.calls == []
    assert "Nothing to respond to: not a Meeting Thread or an invitation." in seen["toasts"]


def test_without_the_service_i_reports_unavailable(tmp_path):
    path, ctx = meeting(tmp_path)
    seen = run(Console(path), ctx.thread.id, ["i", bar])
    assert "RESPOND" not in seen["bars"][0]
    assert any(t.startswith("Not answered · Responses unavailable") for t in seen["toasts"])


def test_broken_writes_show_apart_from_the_feed(tmp_path):
    path, ctx = meeting(tmp_path)
    with ctx.db:
        ctx.db.execute("UPDATE connections SET last_error = ? WHERE platform = 'gcal'",
                       (gcal.WRITES_BROKEN + "Google Calendar sign-in expired or revoked (HTTP 400 invalid_grant)",))
    seen = run(Console(path, respond=Service()), ctx.thread.id, [])
    assert "Google Calendar writes broken" in seen["bar"]


def test_socket_respond_reports_an_unreachable_service_as_unavailable(tmp_path):
    assert socket_respond(str(tmp_path / "missing.sock"))({"thread_id": 1, "answer": "accept"})["outcome"] == "unavailable"
