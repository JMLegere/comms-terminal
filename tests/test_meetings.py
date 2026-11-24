"""Meetings on Threads: a new Event starts in Todo. Held preserves triage; cancellation and outside decline
can reopen it (ADR 0007; FR-OA-11..15, FR-ICA-1..2). A reschedule changes no Status."""

from datetime import datetime, timedelta, timezone

import pytest

from comms_terminal import calendar as cal, clock as comms_clock, inbox, meetings, pipeline as pl, platforms
from comms_terminal import sequences as sq

UTC = timezone.utc


@pytest.fixture(autouse=True)
def local_time(monkeypatch):
    monkeypatch.setenv("TZ", "America/Halifax")
    import time
    time.tzset()


@pytest.fixture
def clock(monkeypatch):
    at = [datetime(2026, 10, 20, 13, tzinfo=UTC)]
    monkeypatch.setattr(comms_clock, "now", lambda: at[0])
    return at


@pytest.fixture
def db():
    d = inbox.connect()
    inbox.migrate(d)
    with d:
        d.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at, inbound) VALUES"
                  " ((SELECT printf('%d', count(*) + 1) FROM connections), 'gmail', 'jeremy@agentmaple.com', 'jeremy@agentmaple.com', 'Gmail', '2026-10-01T00:00:00+00:00',"
                  " 'jeremy@inbox.agentmaple.com')")
    return d


def feed(*events: str) -> str:
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nX-WR-CALNAME:jeremy@agentmaple.com\r\n"
            + "".join(f"BEGIN:VEVENT\r\nDTSTAMP:20261020T150000Z\r\n{e.strip()}\r\nEND:VEVENT\r\n" for e in events)
            + "END:VCALENDAR\r\n").replace("\n", "\r\n").replace("\r\r", "\r")


DEMO = """UID:demo
SUMMARY:STMEC demo
DTSTART:20261022T140000Z
DTEND:20261022T143000Z
ORGANIZER:mailto:jeremy@agentmaple.com
ATTENDEE;PARTSTAT=ACCEPTED:mailto:jeremy@agentmaple.com
ATTENDEE;CN=Sam Lee;PARTSTAT=ACCEPTED:mailto:sam@stmec.com
X-GOOGLE-CONFERENCE:https://meet.google.com/abc-defg-hij"""
WEEKLY = """UID:weekly
SUMMARY:Acme weekly
DTSTART:20261021T150000Z
DTEND:20261021T153000Z
RRULE:FREQ=WEEKLY
LOCATION:https://acme.zoom.us/j/123
ORGANIZER;CN=Ana Ruiz:mailto:ana@acme.com
ATTENDEE;PARTSTAT=ACCEPTED:mailto:jeremy@agentmaple.com
ATTENDEE;CN=Bob;PARTSTAT={bob}:mailto:bob@acme.com
ATTENDEE;PARTSTAT=ACCEPTED:mailto:room-1@resource.calendar.google.com"""
DECLINED = """UID:declined
SUMMARY:Globex pitch
DTSTART:20261023T150000Z
DTEND:20261023T160000Z
LOCATION:+1 902 555 0100
ORGANIZER:mailto:pat@globex.com
ATTENDEE;PARTSTAT=DECLINED:mailto:jeremy@agentmaple.com"""
INTERNAL = """UID:internal
SUMMARY:Standup
DTSTART:20261021T130000Z
DTEND:20261021T133000Z
ORGANIZER:mailto:logan@agentmaple.com
ATTENDEE;PARTSTAT=ACCEPTED:mailto:jeremy@agentmaple.com"""
OLD = """UID:old
SUMMARY:Booked before go-live
DTSTART:20261024T150000Z
DTEND:20261024T160000Z
ORGANIZER:mailto:x@old.example"""


def thread(db, uid):
    r = db.execute("SELECT t.id FROM threads t JOIN channels ch ON ch.id = t.channel_id WHERE ch.native_id = ?"
                   " AND ch.kind = 'meeting'", (uid,)).fetchone()
    return r and inbox.get_thread(db, r[0])


def meeting_activities(db, uid):
    return [a["native_id"].split(":", 2)[2] for a in db.execute(
        "SELECT a.native_id FROM activities a JOIN threads t ON t.id = a.thread_id JOIN channels ch"
        " ON ch.id = t.channel_id WHERE ch.native_id = ? ORDER BY a.id", (uid,))]


def triage(db, tid):
    return tuple(db.execute(
        "SELECT status, status_detail, status_changed_at, snoozed_until, detail_before_snooze, follow_up_at"
        " FROM threads WHERE id = ?", (tid,)).fetchone())


def book(db, clock, *events):
    meetings.connect(db)
    clock[0] = datetime(2026, 10, 20, 14, tzinfo=UTC)
    cal.sync(db, feed(*events), comms_clock.iso(clock[0]))
    assert meetings.synced(db) == len(events)


def test_meetings_book_hold_cancel_decline_and_reschedule(db, clock):
    cal.sync(db, feed(OLD), "2026-10-20T12:00:00+00:00")
    c = meetings.connect(db)  # go-live 13:00
    assert meetings.connect(db) == c and platforms.PLATFORMS["gcal"].format is None

    clock[0] = datetime(2026, 10, 20, 14, tzinfo=UTC)
    events = [OLD, DEMO, WEEKLY.format(bob="ACCEPTED"), DECLINED, INTERNAL]
    cal.sync(db, feed(*events), "2026-10-20T14:00:00+00:00")
    assert meetings.synced(db) == 4 and meetings.synced(db) == 0  # once each
    assert thread(db, "old") is None  # before go-live
    assert thread(db, "internal").status_detail == "CREATED"  # an internal Event is a Meeting Thread too (#142)
    demo, weekly, declined = thread(db, "demo"), thread(db, "weekly"), thread(db, "declined")
    assert [t.status_detail for t in (demo, weekly, declined)] == ["CREATED"] * 3  # new Threads are Todo
    assert demo.title == "STMEC demo"

    v = inbox.thread_view(db, demo.id)
    assert v["where"] == "Meeting" and v["messages"][0]["link"] == "https://meet.google.com/abc-defg-hij"
    assert "Google Meet" in v["messages"][0]["content"] and "Sam Lee" in v["messages"][0]["content"]
    sam = [p for p in v["people"] if p["name"] == "Sam Lee"]
    assert sam and sam[0]["organizations"] == ["stmec.com"]  # FR-OA-15: attendees are Participants
    w = inbox.thread_view(db, weekly.id)
    assert {p["name"] for p in w["people"]} == {"Ana Ruiz", "Bob"}  # the room is no one
    assert inbox.get_thread(db, weekly.id).status == "TODO"
    with pytest.raises(ValueError, match="no replies"):
        inbox.save_reply_draft(db, demo.id, "see you then")

    org = db.execute("SELECT id FROM organizations WHERE domain = 'stmec.com'").fetchone()[0]
    pl.set_stage(db, org, pl.REVENUE, "Awareness")
    inbox.act(db, demo.id, inbox.Snooze(datetime(2026, 10, 22, 12, tzinfo=UTC)))  # prep the day before
    assert pl.attention(db)[org]["rollup"] == "Snoozed"

    # Rescheduled: the Calendar moves, the snooze stays
    moved = DEMO.replace("20261022T140000Z", "20261022T150000Z").replace("20261022T143000Z", "20261022T153000Z")
    events[1] = moved
    cal.sync(db, feed(*events), "2026-10-20T15:00:00+00:00")
    assert meetings.synced(db) == 0 and inbox.get_thread(db, demo.id).status_detail == "WAITING_FOR_DURATION"

    # Held: each ended occurrence once
    assert meetings.held(db, datetime(2026, 10, 22, 15, 31, tzinfo=UTC)) == 3  # the demo, Acme's 10-21, the standup
    assert meetings.held(db, datetime(2026, 10, 22, 15, 32, tzinfo=UTC)) == 0
    t = inbox.get_thread(db, demo.id)
    assert (t.status, t.status_detail) == ("SNOOZED", "WAITING_FOR_DURATION")
    assert inbox.thread_view(db, demo.id)["audit"][-1]["cause"] == "action"

    # Both the pre-go-live Event and the Event Jeremy declined have now ended.
    assert meetings.held(db, datetime(2026, 10, 24, 16, 1, tzinfo=UTC)) == 0
    assert meeting_activities(db, "declined") == [":booked"]
    assert thread(db, "old") is None

    inbox.act(db, weekly.id, inbox.Done())
    assert meetings.held(db, datetime(2026, 10, 28, 15, 31, tzinfo=UTC)) == 1  # the next week's
    assert meeting_activities(db, "weekly") == [":booked", "20261021T150000Z:held", "20261028T150000Z:held"]
    assert inbox.get_thread(db, weekly.id).status == "DONE"

    # Declined by someone outside, and cancelled
    inbox.act(db, declined.id, inbox.Done())
    events[2] = WEEKLY.format(bob="DECLINED")
    del events[3]
    cal.sync(db, feed(*events), "2026-10-29T12:00:00+00:00")
    assert meetings.synced(db) == 2
    assert meeting_activities(db, "weekly")[-1] == ":declined:bob@acme.com"
    assert meeting_activities(db, "declined")[-1] == ":cancelled"
    assert inbox.get_thread(db, weekly.id).status == inbox.get_thread(db, declined.id).status == "TODO"
    assert "phone" in inbox.thread_view(db, declined.id)["messages"][-1]["content"]


def test_a_meeting_thread_in_the_console(tmp_path, clock):
    """The Thread page shows the Meeting card and its people; r says it takes no replies; the Connection lists."""
    import asyncio
    import os
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import seed_demo
    from comms_terminal.tui import Console

    path = str(tmp_path / "inbox.db")
    seed_demo.seed(inbox.connect(path))
    db = inbox.connect(path)
    meetings.connect(db)
    clock[0] = datetime(2026, 10, 20, 14, tzinfo=UTC)
    cal.sync(db, feed(DEMO), "2026-10-20T14:00:00+00:00")
    assert meetings.synced(db) == 1
    meetings.held(db, datetime(2026, 10, 22, 14, 31, tzinfo=UTC))
    tid, gcal = thread(db, "demo").id, db.execute("SELECT id FROM connections WHERE platform = 'gcal'").fetchone()[0]
    seen = {}

    async def go():
        async with Console(path).run_test(size=(160, 45)) as pilot:
            app = pilot.app
            await pilot.pause()
            app.main.open_thread(tid)
            await pilot.pause(0.3)
            tv = app.main.thread_view
            seen["msgs"] = " ".join(str(w.render()) for w in tv.query(".msgs Static"))
            seen["details"] = str(tv.query_one(".detailtext").render())
            if os.environ.get("MEETING_SHOTS"):
                app.save_screenshot("13-meeting-thread.svg", os.environ["MEETING_SHOTS"])
            tv.reply()
            await pilot.pause()
            seen["notes"] = [str(n.message) for n in app._notifications]
            seen["box"] = tv.query_one("ReplyBox").has_class("open")
            app.main.go(f"conn:{gcal}")
            await pilot.pause(0.3)
            if os.environ.get("MEETING_SHOTS"):
                app.save_screenshot("14-connections.svg", os.environ["MEETING_SHOTS"])
            seen["rail"] = [str(app.main.rail.get_option_at_index(i).prompt) for i in range(app.main.rail.option_count)]
    asyncio.run(go())
    assert "Held · STMEC demo" in seen["msgs"] and "Booked · STMEC demo" in seen["msgs"]
    assert "Google Meet" in seen["msgs"] and "Sam Lee" in seen["details"]
    assert any("no replies" in n for n in seen["notes"]) and not seen["box"]
    assert any("Google Calendar" in r for r in seen["rail"])


def test_the_service_refuses_a_reply_on_a_meeting_thread(db, clock):
    """POST /reply on a Meeting Thread says it takes no replies; its Platform has no Sender to make."""
    from comms_terminal import serve
    meetings.connect(db)
    clock[0] = datetime(2026, 10, 20, 14, tzinfo=UTC)
    cal.sync(db, feed(DEMO), "2026-10-20T14:00:00+00:00")
    meetings.synced(db)
    status, body = serve.handle_reply(db, f'{{"thread_id": "{thread(db, "demo").id}", "text": "hi"}}'.encode(),
                                      serve.Senders())
    assert (status, body["error"]) == (400, inbox.NO_REPLY)


SOLO = """UID:solo
SUMMARY:Focus block
DTSTART:20261021T170000Z
DTEND:20261021T180000Z
ORGANIZER:mailto:jeremy@agentmaple.com"""


def test_block_booking_stays_out_of_todo_but_eligible_cancellation_reopens(db, clock):
    meetings.connect(db)
    clock[0] = datetime(2026, 10, 20, 14, tzinfo=UTC)
    cal.sync(db, feed(SOLO), "2026-10-20T14:00:00+00:00")
    assert meetings.synced(db) == 1
    t = thread(db, "solo")
    assert (t.title, t.status, t.status_detail) == ("Focus block", "DONE", "DONE_MANUALLY_SET")
    first = inbox.thread_view(db, t.id)["messages"][0]
    assert first["content"].startswith("Booked · Focus block") and "With:" not in first["content"]
    before = triage(db, t.id)
    audit = inbox.thread_view(db, t.id)["audit"]
    assert not audit
    assert meetings.held(db, datetime(2026, 10, 21, 18, 1, tzinfo=UTC)) == 1
    assert meeting_activities(db, "solo") == [":booked", "20261021T170000Z:held"]
    assert triage(db, t.id) == before
    assert inbox.thread_view(db, t.id)["audit"] == audit
    cal.sync(db, feed(), "2026-10-21T19:00:00+00:00")
    assert meetings.synced(db) == 1 and meeting_activities(db, "solo")[-1] == ":cancelled"
    assert inbox.get_thread(db, t.id).status == "TODO"


TIMER_DUE = datetime(2026, 10, 24, 15, 31, tzinfo=UTC)


@pytest.mark.parametrize(("action", "status", "detail", "wake_detail"), [
    (None, "TODO", "CREATED", None),
    (inbox.Investigate(), "TODO", "IN_PROGRESS", None),
    (inbox.Done(), "DONE", "DONE_MANUALLY_SET", None),
    (inbox.Ignore(), "DONE", "IGNORED", None),
    (inbox.Snooze(TIMER_DUE), "SNOOZED", "WAITING_FOR_DURATION", "IN_PROGRESS"),
    (inbox.Wait(), "SNOOZED", "WAITING_FOR_CUSTOMER", "NEW_REPLY"),
])
def test_held_activity_preserves_exact_triage_and_audit(db, clock, action, status, detail, wake_detail):
    # Two occurrences three days apart: Held arrives before the timer and again in its due Wake pass.
    series = WEEKLY.format(bob="ACCEPTED").replace("FREQ=WEEKLY", "FREQ=DAILY;INTERVAL=3")
    book(db, clock, series)
    t = thread(db, "weekly")
    if action is not None:
        clock[0] = datetime(2026, 10, 20, 15, 31, tzinfo=UTC)
        if isinstance(action, inbox.Snooze):
            inbox.act(db, t.id, inbox.Investigate())
        inbox.act(db, t.id, action)
    before = triage(db, t.id)
    snooze = isinstance(action, inbox.Snooze)
    assert before == (
        status, detail, comms_clock.iso(clock[0]), comms_clock.iso(TIMER_DUE) if snooze else None,
        "IN_PROGRESS" if snooze else None, comms_clock.iso(TIMER_DUE) if isinstance(action, inbox.Wait) else None,
    )
    before_view = inbox.thread_view(db, t.id)

    clock[0] = datetime(2026, 10, 21, 15, 31, tzinfo=UTC)
    assert inbox.wake(db, clock[0]) == 0
    assert meetings.held(db, clock[0]) == 1
    assert meetings.held(db, clock[0]) == 0
    assert meeting_activities(db, "weekly") == [":booked", "20261021T150000Z:held"]
    assert [r["id"] for r in db.execute("SELECT id FROM threads")] == [t.id]
    after_view = inbox.thread_view(db, t.id)
    assert [p["id"] for p in after_view["people"]] == [p["id"] for p in before_view["people"]]
    _, held = after_view["messages"]
    assert held["who"] == "Ana Ruiz" and held["to"] == ["bob@acme.com"]
    assert {a["email"]: a["state"] for a in held["data"]["meeting"]["attendees"]} == {
        "jeremy@agentmaple.com": "ACCEPTED",
        "bob@acme.com": "ACCEPTED",
        "room-1@resource.calendar.google.com": "ACCEPTED",
    }
    assert triage(db, t.id) == before
    assert after_view["audit"] == before_view["audit"]
    assert (t.id in {r["id"] for r in inbox.queue(db, "Todo")}) == (status == "TODO")

    if wake_detail is not None:
        clock[0] = TIMER_DUE
        assert inbox.wake(db, TIMER_DUE - timedelta(seconds=1)) == 0
        assert inbox.wake(db, TIMER_DUE) == 1
        assert meetings.held(db, TIMER_DUE) == 1
        assert inbox.wake(db, TIMER_DUE) == meetings.held(db, TIMER_DUE) == 0
        assert triage(db, t.id) == ("TODO", wake_detail, comms_clock.iso(TIMER_DUE), None, None, None)
        audit = inbox.thread_view(db, t.id)["audit"]
        assert audit[:-1] == before_view["audit"]
        assert (audit[-1]["cause"], audit[-1]["occurred_at"], audit[-1]["new_detail"]) == (
            "woke", comms_clock.iso(TIMER_DUE), wake_detail)
        assert meeting_activities(db, "weekly")[-1] == "20261024T150000Z:held"


def test_held_activity_skips_deleted_and_all_day_events(db, clock):
    all_day = """UID:all-day
SUMMARY:Holiday
DTSTART;VALUE=DATE:20261022
DTEND;VALUE=DATE:20261023
ORGANIZER:mailto:jeremy@agentmaple.com"""
    book(db, clock, DEMO, all_day)
    cal.sync(db, feed(all_day), "2026-10-21T13:00:00+00:00")
    assert meetings.synced(db, datetime(2026, 10, 21, 13, tzinfo=UTC)) == 1
    assert meetings.held(db, datetime(2026, 10, 23, 14, tzinfo=UTC)) == 0
    assert meeting_activities(db, "demo") == [":booked", ":cancelled"]
    assert meeting_activities(db, "all-day") == [":booked"]


def test_held_activity_updates_pipeline_history_without_todo(db, clock):
    book(db, clock, DEMO)
    inbox.act(db, thread(db, "demo").id, inbox.Done())
    org = db.execute("SELECT id FROM organizations WHERE domain = 'stmec.com'").fetchone()[0]
    pl.set_stage(db, org, pl.REVENUE, "Awareness")
    before = pl.attention(db)[org]
    assert (before["rollup"], before["unattended"], before["last"]) == ("Done", False, clock[0])

    clock[0] = datetime(2026, 10, 22, 14, 31, tzinfo=UTC)
    assert meetings.held(db, clock[0]) == 1
    after = pl.attention(db)[org]
    assert (after["rollup"], after["unattended"], after["last"]) == (
        "Done", True, datetime(2026, 10, 22, 14, 30, tzinfo=UTC))


def test_held_activity_still_pauses_matching_enrollment(db, clock):
    book(db, clock, DEMO)
    t = thread(db, "demo")
    inbox.act(db, t.id, inbox.Done())
    (sam,) = inbox.thread_view(db, t.id)["people"]
    s = sq.create_sequence(db, "Follow up")
    sq.add_step(db, s, "call")
    sq.enroll(db, sam["id"], s)
    assert sq.enrollments(db, sam["id"])[0]["state"] == "ACTIVE"

    assert meetings.held(db, datetime(2026, 10, 22, 14, 31, tzinfo=UTC)) == 1
    (e,) = sq.enrollments(db, sam["id"])
    ended_at = "2026-10-22T14:30:00.000000+00:00"
    assert (e["state"], e["pause_reason"], e["paused_at"], e["responded_at"]) == (
        "PAUSED", "replied", ended_at, ended_at)
    assert inbox.get_thread(db, t.id).status == "DONE"


def test_calendar_attendee_acceptance_changes_event_but_not_activity_or_triage(db, clock):
    unanswered = DEMO.replace("CN=Sam Lee;PARTSTAT=ACCEPTED", "CN=Sam Lee;PARTSTAT=NEEDS-ACTION")
    book(db, clock, unanswered)
    t = thread(db, "demo")
    inbox.act(db, t.id, inbox.Done())
    before = triage(db, t.id)
    audit = inbox.thread_view(db, t.id)["audit"]
    day = datetime(2026, 10, 22, tzinfo=UTC).date()

    def attendee_state():
        (event,) = cal.expand(db, day, day + timedelta(days=1))
        return next(a["state"] for a in event["attendees"] if a["email"] == "sam@stmec.com")

    assert attendee_state() == "NEEDS-ACTION"
    clock[0] += timedelta(hours=1)
    assert cal.sync(db, feed(DEMO), comms_clock.iso(clock[0]))
    assert meetings.synced(db) == 0
    assert attendee_state() == "ACCEPTED"
    assert meeting_activities(db, "demo") == [":booked"]
    assert triage(db, t.id) == before
    assert inbox.thread_view(db, t.id)["audit"] == audit


@pytest.mark.parametrize("data", [{}, {"meeting": None}, {"meeting": {}}], ids=["missing", "null", "no-what"])
def test_meeting_arrival_without_held_type_still_reopens(db, clock, data):
    book(db, clock, DEMO)
    t = thread(db, "demo")
    inbox.act(db, t.id, inbox.Done())
    channel = inbox.thread_view(db, t.id)["channel"]

    clock[0] += timedelta(hours=1)
    arrived = inbox.ingest(db, inbox.Incoming(
        channel["connection_id"], channel["native_id"], "meeting", "MESSAGE", "gcal:demo::untyped",
        inbox.Identity("email", "sam@stmec.com"), clock[0], "Meeting update", data=data, synchronous="meeting",
    ))
    assert (arrived.id, arrived.status, arrived.status_detail) == (t.id, "TODO", "NEW_REPLY")
    assert meeting_activities(db, "demo") == [":booked", ":untyped"]
