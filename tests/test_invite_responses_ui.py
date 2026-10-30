"""The console's Respond key (FR-IR-1, FR-IR-9, FR-IR-10, NFR-IR-3) through Textual's test driver, with a fake
service call in place of the socket."""

import asyncio
import os
import threading
import time
from types import SimpleNamespace
from datetime import date, timedelta

import pytest

from steps.calendar_steps import event, meeting_thread_for, open_event_page

from comms_terminal import calendar as cal, clock, gcal, inbox
from comms_terminal.remote import socket_respond
from comms_terminal.tui import Console, StatusLine, thread_head


def meeting(tmp_path, title="Pints", *, weekly=False):
    path = str(tmp_path / "inbox.db")
    db = inbox.connect(path)
    inbox.migrate(db)
    with db:
        db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                   " VALUES (1, 'gmail', 'jeremy@agentmaple.com', 'jeremy@agentmaple.com', 'Gmail',"
                   " '2026-10-12T00:00:00+00:00')")
    ctx = SimpleNamespace(db=db)
    meeting_thread_for(ctx, event(ctx, title, "brendan.wood@snaptrade.com", weekly=weekly))
    return path, ctx


def sync_feed(ctx):
    cal.sync(ctx.db, "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
             + "".join(f"BEGIN:VEVENT\r\n{e}\r\nEND:VEVENT\r\n" for e in ctx.feed.values())
             + "END:VCALENDAR\r\n", clock.iso(clock.now() + timedelta(minutes=2)))


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


def run(app, thread_id, steps, *, on_event=False, size=(200, 50), event_day=date(2026, 11, 5)):
    seen = {}

    async def go():
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            view = inbox.thread_view(app.db, thread_id)
            if on_event:
                await open_event_page(app, pilot, view["channel"]["native_id"], event_day)
                seen["body"] = str(app.main.page.query_one("#eventbody").render())
            else:
                app.main.open_thread(thread_id)
                await pilot.pause(0.2)
            seen["head"] = thread_head(view)
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


@pytest.mark.parametrize("on_event", [False, True])
def test_refused_unknown_and_unavailable_outcomes_are_exact(tmp_path, on_event):
    path, ctx = meeting(tmp_path)
    for result, toast in [
            ({"outcome": "refused", "error": "You organized this Event."}, "Not answered · You organized this Event."),
            ({"outcome": "unknown", "error": "x"}, "Outcome unknown · check the Event in Google Calendar before answering again."),
            ({"outcome": "unavailable", "error": "Google Calendar writes are not connected"},
             "Not answered · Google Calendar writes are not connected")]:
        seen = run(Console(path, respond=Service(result)), ctx.thread.id, ["i", "n"], on_event=on_event)
        assert toast in seen["toasts"]


def test_i_on_a_thread_with_no_invitation_opens_nothing(tmp_path):
    path, ctx = meeting(tmp_path)
    t = inbox.ingest(ctx.db, inbox.Incoming("1", "jeremy@agentmaple.com", "mailbox", "MESSAGE", "<m1@test>",
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


@pytest.mark.parametrize("key,answer,word", [("y", "accept", "Accepted"), ("m", "maybe", "Maybe"),
                                           ("n", "decline", "Declined")])
def test_event_page_responds_with_uid_and_keeps_feed_state(tmp_path, key, answer, word):
    path, ctx = meeting(tmp_path)
    service = Service()

    async def state(app, pilot, seen):
        seen["state"] = app.main.page.event["state"]
        seen["own"] = next(a["state"] for a in app.main.page.event["attendees"] if a["email"] == "jeremy@agentmaple.com")

    seen = run(Console(path, respond=service), ctx.thread.id, ["i", bar, key, state], on_event=True)
    assert "i respond" in seen["body"] and "[Unanswered]" in seen["body"]
    assert "RESPOND" in seen["bars"][0] and "whole series" not in seen["bars"][0]
    assert service.calls == [{"event_uid": ctx.uids["Pints"], "answer": answer}]
    assert f"{word} · Google told the organizer." in seen["toasts"]
    assert seen["state"] == seen["own"] == "NEEDS-ACTION"


@pytest.mark.parametrize("key", ["escape", "x", "i"])
def test_event_page_cancel_clears_the_picker(tmp_path, key):
    path, ctx = meeting(tmp_path)
    service = Service()
    seen = run(Console(path, respond=service), ctx.thread.id, ["i", key, "y"], on_event=True)
    assert service.calls == [] and "Not answered." in seen["toasts"] and "RESPOND" not in seen["bar"]


@pytest.mark.parametrize("after_open", [False, True])
@pytest.mark.parametrize("unavailable", ["missing", "offline", "outdated"])
def test_event_page_requires_a_current_online_service(tmp_path, unavailable, after_open):
    path, ctx = meeting(tmp_path)
    service = Service()

    async def unavailable_service(app, pilot, seen):
        if unavailable == "missing":
            app.respond = None
        elif unavailable == "offline":
            app.remote = SimpleNamespace(online=False)
        elif unavailable == "outdated":
            app.outdated = True

    steps = ["i", unavailable_service, "y"] if after_open else [unavailable_service, "i", "y"]
    seen = run(Console(path, respond=service), ctx.thread.id, steps, on_event=True)
    assert service.calls == [] and "RESPOND" not in seen["bar"]
    assert "Not answered · Responses unavailable: service offline or update required." in seen["toasts"]


def test_event_page_response_is_background_and_repeated_choices_send_once(tmp_path):
    path, ctx = meeting(tmp_path)
    service = Service(delay=1.0)

    async def repeat_y(app, pilot, seen):
        start = time.monotonic()
        await pilot.press("y")
        seen["took"] = time.monotonic() - start
        await pilot.press("y", "m", "n")
        assert await asyncio.to_thread(service.done.wait, 3)

    seen = run(Console(path, respond=service), ctx.thread.id, ["i", repeat_y], on_event=True)
    assert seen["took"] < 0.5
    assert service.calls == [{"event_uid": ctx.uids["Pints"], "answer": "accept"}]


@pytest.mark.parametrize("destination", ["calendar", "thread"])
def test_navigation_clears_event_response_target(tmp_path, destination):
    path, ctx = meeting(tmp_path)
    service = Service()

    async def navigate(app, pilot, seen):
        app.main.back() if destination == "calendar" else app.main.open_thread(ctx.thread.id)
        await pilot.pause(0.2)

    seen = run(Console(path, respond=service), ctx.thread.id, ["i", navigate, "y"], on_event=True)
    assert service.calls == [] and "RESPOND" not in seen["bar"]


def test_open_event_refreshes_its_same_occurrence_from_the_feed(tmp_path):
    path, ctx = meeting(tmp_path, weekly=True)
    service = Service()

    async def update_feed(app, pilot, seen):
        before = app.main.page.event
        seen["before"] = (before["id"], before["state"], before["start"])
        uid = ctx.uids["Pints"]
        ctx.feed[uid] = ctx.feed[uid].replace("PARTSTAT=NEEDS-ACTION", "PARTSTAT=ACCEPTED")
        cal.sync(ctx.db, "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
                 + "".join(f"BEGIN:VEVENT\r\n{e}\r\nEND:VEVENT\r\n" for e in ctx.feed.values())
                 + "END:VCALENDAR\r\n", clock.iso(clock.now() + timedelta(minutes=2)))
        app.check_changes()
        await pilot.pause()
        after = app.main.page.event
        seen["after"] = (after["id"], after["state"], after["start"])
        seen["own"] = next(a["state"] for a in after["attendees"] if a["email"] == "jeremy@agentmaple.com")

    seen = run(Console(path, respond=service), ctx.thread.id, ["i", "y", update_feed], on_event=True,
               event_day=date(2026, 11, 12))
    assert seen["before"][1] == "NEEDS-ACTION" and seen["after"][1] == seen["own"] == "ACCEPTED"
    assert seen["before"][0] == seen["after"][0] and seen["before"][2] == seen["after"][2]


@pytest.mark.parametrize("recurrence", ["RRULE", "RDATE"])
@pytest.mark.parametrize("size", [(200, 50), (80, 40)])
def test_repeating_event_picker_discloses_whole_series(tmp_path, size, recurrence):
    """INVITE_RESPONSE_SHOTS exports ready/picker SVGs using only synthetic data and a fake service."""
    path, ctx = meeting(tmp_path, weekly=True)
    if recurrence == "RDATE":
        uid = ctx.uids["Pints"]
        ctx.feed[uid] = ctx.feed[uid].replace("RRULE:FREQ=WEEKLY", "RDATE:20261112T190000Z,20261119T190000Z")
        sync_feed(ctx)
    service = Service()

    async def capture(app, pilot, seen):
        if directory := os.environ.get("INVITE_RESPONSE_SHOTS"):
            name = "picker" if "RESPOND" in app.main.query_one(StatusLine).shown else "ready"
            app.save_screenshot(f"event-{size[0]}-{recurrence.lower()}-{name}.svg", directory)

    seen = run(Console(path, respond=service), ctx.thread.id, [capture, "i", bar, capture, "escape"],
               on_event=True, size=size)
    assert "whole series" in seen["bars"][0] and service.calls == []


def test_deleted_event_refuses_response_and_drops_the_hint(tmp_path):
    path, ctx = meeting(tmp_path)
    service = Service()

    async def remove_event(app, pilot, seen):
        cal.sync(ctx.db, "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n")
        app.check_changes()
        await pilot.pause()
        seen["body"] = str(app.main.page.query_one("#eventbody").render())

    seen = run(Console(path, respond=service), ctx.thread.id, [remove_event, "i", "y"], on_event=True)
    assert service.calls == [] and "RESPOND" not in seen["bar"] and "i respond" not in seen["body"]
    assert "Not answered · This Event is deleted." in seen["toasts"]


@pytest.mark.parametrize("page", ["thread", "case"])
def test_selecting_another_thread_clears_response_target_and_footer(tmp_path, page):
    path, ctx = meeting(tmp_path)
    first = ctx.thread.id
    meeting_thread_for(ctx, event(ctx, "Review", "brendan.wood@snaptrade.com"))
    second = ctx.thread.id
    case_id = inbox.create_case(ctx.db, "Meetings")
    with ctx.db:
        ctx.db.execute("UPDATE threads SET case_id = ? WHERE id IN (?, ?)", (case_id, first, second))
    service = Service()

    async def prepare(app, pilot, seen):
        if page == "case":
            from comms_terminal.tui import CasePage
            app.main.open(lambda: CasePage(case_id))
            await pilot.pause(0.2)
            app.main.page.select(next(i for i, v in enumerate(app.main.page.views) if v["thread"].id == first))

    async def navigate(app, pilot, seen):
        if page == "case":
            app.main.page.select(next(i for i, v in enumerate(app.main.page.views) if v["thread"].id == second))
        else:
            app.main.show_thread(second)

    seen = run(Console(path, respond=service), first, [prepare, "i", navigate, bar, "y"])
    assert service.calls == [] and "RESPOND" not in seen["bars"][0]


@pytest.mark.parametrize("weekly", [False, True])
@pytest.mark.parametrize("repaint", [False, True])
def test_moved_event_updates_current_details_and_requires_a_fresh_response_gesture(tmp_path, weekly, repaint):
    path, ctx = meeting(tmp_path, weekly=weekly)
    service = Service()
    uid = ctx.uids["Pints"]

    async def move(app, pilot, seen):
        for timer in app._refresh_timers:
            timer.stop()
        seen["before"] = app.main.page.event["id"]
        current = ctx.feed[uid].replace("SUMMARY:Pints", "SUMMARY:Moved Pints")
        if weekly:
            current = current.replace("\r\nRRULE:FREQ=WEEKLY", "")
            current += "\r\nRECURRENCE-ID:20261112T190000Z"
            ctx.feed[uid + "|override"] = current.replace("20261105T", "20261120T")
        else:
            ctx.feed[uid] = current.replace("20261105T", "20261120T")
        sync_feed(ctx)
        if repaint:
            app.check_changes()
        await pilot.pause()

    async def inspect(app, pilot, seen):
        seen["current"] = app.main.page.event
        seen["current_body"] = str(app.main.page.query_one("#eventbody").render())
        seen["armed"] = app.respond_target
        seen["calls_before_new_gesture"] = list(service.calls)

    seen = run(Console(path, respond=service), ctx.thread.id,
               ["i", move, "y", inspect, "i", "n"], on_event=True,
               event_day=date(2026, 11, 12) if weekly else date(2026, 11, 5))
    assert seen["current"]["uid"] == uid and seen["current"]["start"].day == 20
    assert "Moved Pints" in seen["current_body"]
    assert not weekly or seen["current"]["id"] == seen["before"]
    assert seen["armed"] is None and seen["calls_before_new_gesture"] == []
    assert service.calls == [{"event_uid": uid, "answer": "decline"}]


@pytest.mark.parametrize("mode", ["absent", "deleted", "unresolvable"])
@pytest.mark.parametrize("repaint", [False, True])
def test_unavailable_open_occurrence_cancels_picker_and_refuses_new_answers(tmp_path, mode, repaint):
    path, ctx = meeting(tmp_path, weekly=mode == "unresolvable")
    service = Service()

    async def remove(app, pilot, seen):
        for timer in app._refresh_timers:
            timer.stop()
        if mode == "absent":
            with ctx.db:
                ctx.db.execute("DELETE FROM events")
        elif mode == "deleted":
            ctx.feed.clear()
            sync_feed(ctx)
        else:
            uid = ctx.uids["Pints"]
            ctx.feed[uid] = ctx.feed[uid].replace("T190000Z", "T210000Z").replace("T200000Z", "T220000Z")
            sync_feed(ctx)
        if repaint:
            app.check_changes()
        await pilot.pause()

    async def inspect(app, pilot, seen):
        seen["current_body"] = str(app.main.page.query_one("#eventbody").render())
        seen["armed"] = app.respond_target

    seen = run(Console(path, respond=service), ctx.thread.id,
               ["i", remove, "y", inspect, "i", "y"], on_event=True)
    assert service.calls == [] and seen["armed"] is None and "RESPOND" not in seen["bar"]
    assert "i respond" not in seen["current_body"] and "Unanswered" not in seen["current_body"]
    assert "unavailable" in seen["current_body"].lower()


def test_i_reloads_a_moved_event_before_opening_its_response_picker(tmp_path):
    path, ctx = meeting(tmp_path)
    service = Service()

    async def move(app, pilot, seen):
        for timer in app._refresh_timers:
            timer.stop()
        uid = ctx.uids["Pints"]
        ctx.feed[uid] = ctx.feed[uid].replace("20261105T", "20261120T").replace("SUMMARY:Pints", "SUMMARY:Moved Pints")
        sync_feed(ctx)

    async def inspect(app, pilot, seen):
        seen["body"] = str(app.main.page.query_one("#eventbody").render())
        seen["start"] = app.main.page.event["start"]

    seen = run(Console(path, respond=service), ctx.thread.id, [move, "i", inspect, "n"], on_event=True)
    assert seen["start"].day == 20 and "Moved Pints" in seen["body"]
    assert service.calls == [{"event_uid": ctx.uids["Pints"], "answer": "decline"}]


@pytest.mark.parametrize("reopen", [False, True])
def test_removed_moved_exception_restores_the_original_live_event_page(tmp_path, reopen):
    path, ctx = meeting(tmp_path, weekly=True)
    service = Service()
    uid = ctx.uids["Pints"]
    moved = ctx.feed[uid].replace("\r\nRRULE:FREQ=WEEKLY", "").replace("SUMMARY:Pints", "SUMMARY:Moved Pints")
    ctx.feed[uid + "|override"] = moved.replace("20261105T", "20261120T") + "\r\nRECURRENCE-ID:20261112T190000Z"
    sync_feed(ctx)

    async def restore(app, pilot, seen):
        for timer in app._refresh_timers:
            timer.stop()
        seen["id"] = app.main.page.event["id"]
        del ctx.feed[uid + "|override"]
        sync_feed(ctx)
        app.check_changes()
        if reopen:
            await open_event_page(app, pilot, uid, date(2026, 11, 12))
        await pilot.pause()

    async def inspect(app, pilot, seen):
        current = app.main.page.event
        seen["current"] = (current["id"], current["start"].day, app.main.page.event_available)
        seen["body"] = str(app.main.page.query_one("#eventbody").render())
        seen["calls_before_new_gesture"] = list(service.calls)

    seen = run(Console(path, respond=service), ctx.thread.id,
               ["i", restore, "y", inspect, "i", "m"], on_event=True, event_day=date(2026, 11, 20))
    assert seen["current"] == (seen["id"], 12, True)
    assert "i respond" in seen["body"] and "Event unavailable" not in seen["body"]
    assert seen["calls_before_new_gesture"] == [] and service.calls == [{"event_uid": uid, "answer": "maybe"}]
