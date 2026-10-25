"""Meetings on Threads (ADR 0007; .agents/prd/2026-10-24-organization-actions.md FR-OA-11..15): a Google Calendar
Event with someone outside Agent Maple, first seen after go-live, is one Thread in Todo; each ended occurrence,
cancellation, and outside decline is a Meeting Activity that puts it back in Todo; a reschedule changes nothing."""

from datetime import datetime, timedelta, timezone

import pytest

from comms_terminal import calendar as cal, clock as comms_clock, inbox, meetings, pipeline as pl, platforms

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
        d.execute("INSERT INTO connections (platform, external_id, self_id, label, connected_at, inbound) VALUES"
                  " ('gmail', 'jeremy@agentmaple.com', 'jeremy@agentmaple.com', 'Gmail', '2026-10-01T00:00:00+00:00',"
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


def test_meetings_book_hold_cancel_decline_and_reschedule(db, clock):
    cal.sync(db, feed(OLD), "2026-10-20T12:00:00+00:00")
    c = meetings.connect(db)  # go-live 13:00
    assert meetings.connect(db) == c and platforms.PLATFORMS["gcal"].format is None

    clock[0] = datetime(2026, 10, 20, 14, tzinfo=UTC)
    events = [OLD, DEMO, WEEKLY.format(bob="ACCEPTED"), DECLINED, INTERNAL]
    cal.sync(db, feed(*events), "2026-10-20T14:00:00+00:00")
    assert meetings.synced(db) == 3 and meetings.synced(db) == 0  # once each
    assert thread(db, "old") is None and thread(db, "internal") is None  # before go-live; no one outside
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
    assert meetings.held(db, datetime(2026, 10, 22, 15, 31, tzinfo=UTC)) == 2  # the demo and Acme's 10-21
    assert meetings.held(db, datetime(2026, 10, 22, 15, 32, tzinfo=UTC)) == 0
    t = inbox.get_thread(db, demo.id)
    assert (t.status, t.status_detail) == ("TODO", "NEW_REPLY")
    assert inbox.thread_view(db, demo.id)["audit"][-1]["cause"] == "meeting"
    inbox.act(db, weekly.id, inbox.Done())
    assert meetings.held(db, datetime(2026, 10, 28, 15, 31, tzinfo=UTC)) == 1  # the next week's
    assert meeting_activities(db, "weekly") == [":booked", "20261021T150000Z:held", "20261028T150000Z:held"]
    assert inbox.get_thread(db, weekly.id).status == "TODO"
    assert meeting_activities(db, "declined") == [":booked"]  # Jeremy declined: never held

    # Declined by someone outside, and cancelled
    inbox.act(db, weekly.id, inbox.Done())
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
    status, body = serve.handle_reply(db, f'{{"thread_id": {thread(db, "demo").id}, "text": "hi"}}'.encode(),
                                      serve.Senders())
    assert (status, body["error"]) == (400, inbox.NO_REPLY)
