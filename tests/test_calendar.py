"""The Calendar's store (ADR 0005): Events kept as Google gives them, Deleted Events kept, repeats expanded on read."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from universal_inbox import calendar as cal, inbox

HFX = ZoneInfo("America/Halifax")


@pytest.fixture(autouse=True)
def local_time(monkeypatch):
    monkeypatch.setenv("TZ", "America/Halifax")
    import time
    time.tzset()


@pytest.fixture
def db():
    d = inbox.connect()
    inbox.migrate(d)
    return d


def feed(*events: str) -> str:
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nX-WR-CALNAME:jeremy@agentmaple.com\r\n"
            + "".join(f"BEGIN:VEVENT\r\nDTSTAMP:20261020T150000Z\r\n{e.strip()}\r\nEND:VEVENT\r\n" for e in events)
            + "END:VCALENDAR\r\n").replace("\n", "\r\n").replace("\r\r", "\r")


STANDUP = """UID:standup
SUMMARY:Agent Maple Standup
DTSTART;TZID=America/Halifax:20261019T100000
DTEND;TZID=America/Halifax:20261019T103000
RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR
ORGANIZER;CN=Logan:mailto:logan@agentmaple.com
ATTENDEE;PARTSTAT=ACCEPTED:mailto:jeremy@agentmaple.com
ATTENDEE;PARTSTAT=ACCEPTED:mailto:logan@agentmaple.com"""
DINNER = """UID:dinner
SUMMARY:Dinner with mom
DTSTART:20261020T210000Z
DTEND:20261020T220000Z
ORGANIZER:mailto:jeremy@agentmaple.com"""
INVITE = """UID:invite
SUMMARY:Canadian Tire Appointment
DTSTART:20261021T170000Z
DTEND:20261021T180000Z
ORGANIZER:mailto:alyssa@example.com
ATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:jeremy@agentmaple.com
X-GOOGLE-CONFERENCE:https://meet.google.com/abc-defg-hij"""
OFF = """UID:off
SUMMARY:Thanksgiving
DTSTART;VALUE=DATE:20261012
DTEND;VALUE=DATE:20261013"""


def titles(db, start, end, deleted=True):
    return [(e["start"].isoformat(), e["title"], bool(e["deleted_at"])) for e in cal.expand(db, start, end, deleted)]


def test_sync_stores_events_and_writes_nothing_when_unchanged(db):
    assert cal.sync(db, feed(STANDUP, DINNER), "2026-10-20T15:00:00+00:00")
    before = db.execute("PRAGMA data_version").fetchone()[0], db.total_changes
    assert not cal.sync(db, feed(STANDUP, DINNER).replace("150000Z", "150030Z"))  # only DTSTAMP moved
    assert db.total_changes == before[1]
    assert cal.provider(db)["owner"] == "jeremy@agentmaple.com"


def test_kinds_states_and_join_links(db):
    cal.sync(db, feed(STANDUP, DINNER, INVITE, OFF))
    by = {e["title"]: e for e in cal.expand(db, date(2026, 10, 12), date(2026, 10, 22))}
    assert by["Agent Maple Standup"]["kind"] == "meeting" and by["Agent Maple Standup"]["repeats"]
    assert by["Dinner with mom"]["kind"] == "block" and by["Dinner with mom"]["state"] == "ACCEPTED"
    assert by["Canadian Tire Appointment"]["state"] == "NEEDS-ACTION"
    assert by["Canadian Tire Appointment"]["join_url"] == "https://meet.google.com/abc-defg-hij"
    assert by["Thanksgiving"]["kind"] == "all-day" and by["Thanksgiving"]["all_day"]


def test_repeating_event_expands_with_no_horizon_and_across_daylight_saving(db):
    cal.sync(db, feed(STANDUP))
    far = cal.expand(db, date(2031, 3, 3), date(2031, 3, 4))
    assert [e["start"] for e in far] == [datetime(2031, 3, 3, 10, tzinfo=HFX)]
    after_dst = cal.expand(db, date(2026, 11, 2), date(2026, 11, 3))  # clocks change 2026-11-01
    assert after_dst[0]["start"].astimezone(HFX).hour == 10


def test_event_gone_from_feed_is_kept_as_deleted_and_comes_back(db):
    cal.sync(db, feed(STANDUP, DINNER), "2026-10-20T15:00:00+00:00")
    cal.sync(db, feed(STANDUP), "2026-10-20T15:01:00+00:00")
    gone = [e for e in cal.expand(db, date(2026, 10, 20), date(2026, 10, 21)) if e["title"] == "Dinner with mom"]
    assert gone[0]["deleted_at"] == "2026-10-20T15:01:00+00:00"
    assert "Dinner with mom" not in [t for _, t, _ in titles(db, date(2026, 10, 20), date(2026, 10, 21), False)]
    cal.sync(db, feed(STANDUP, DINNER))
    assert [d for _, t, d in titles(db, date(2026, 10, 20), date(2026, 10, 21)) if t == "Dinner with mom"] == [False]


def test_one_removed_occurrence_of_a_series_is_a_deleted_event(db):
    cal.sync(db, feed(STANDUP))
    cal.sync(db, feed(STANDUP + "\nEXDATE;TZID=America/Halifax:20261021T100000"), "2026-10-20T16:00:00+00:00")
    wed = titles(db, date(2026, 10, 21), date(2026, 10, 22))
    assert wed == [("2026-10-21T10:00:00-03:00", "Agent Maple Standup", True)]
    assert titles(db, date(2026, 10, 22), date(2026, 10, 23)) == [("2026-10-22T10:00:00-03:00", "Agent Maple Standup", False)]
    cal.sync(db, feed(STANDUP))  # put back in Google: one live occurrence, no struck copy
    assert titles(db, date(2026, 10, 21), date(2026, 10, 22)) == [("2026-10-21T10:00:00-03:00", "Agent Maple Standup", False)]


def test_now_and_next_skip_declined_all_day_and_deleted(db):
    declined = INVITE.replace("UID:invite", "UID:no").replace("NEEDS-ACTION", "DECLINED").replace("20261021T17", "20261020T14")
    cal.sync(db, feed(STANDUP, DINNER, declined, OFF))
    now = datetime(2026, 10, 20, 10, 10, tzinfo=HFX)
    current, nxt = cal.now_and_next(db, now)
    assert current["title"] == "Agent Maple Standup" and nxt["title"] == "Dinner with mom"
    current, nxt = cal.now_and_next(db, datetime(2026, 10, 20, 19, tzinfo=HFX))
    assert current is None and nxt["start"] == datetime(2026, 10, 21, 10, tzinfo=HFX)  # next day counts


def test_health_needs_three_failures_and_a_good_fetch_clears_it(db):
    cal.sync(db, feed(DINNER))
    for _ in range(2):
        cal.record_failure(db, "HTTP Error 404: Not Found", "2026-10-20T15:00:00+00:00")
    assert cal.broken(db) is None
    cal.record_failure(db, "HTTP Error 404: Not Found", None)
    assert cal.broken(db) == "HTTP Error 404: Not Found"
    assert cal.provider(db)["ok_at"] == "2026-10-20T15:00:00+00:00"
    cal.record_ok(db)
    assert cal.broken(db) is None and cal.provider(db)["failures"] == 0


def test_items_place_scheduled_drafts_beside_events(db):
    cal.sync(db, feed(DINNER))
    db.execute("""INSERT INTO composition_drafts (revision, kind, sender, "to", cc, subject, body, created_at, updated_at,
                  send_at) VALUES (1, 'email', 'jeremy@agentmaple.com', 'a@b.c', '', 'Proposal', '', 'x', 'x',
                  '2026-10-20T13:00:00+00:00')""")
    got = [(i["kind"], i["title"], i["at"].astimezone(HFX).hour) for i in cal.items(db, date(2026, 10, 20), date(2026, 10, 21))]
    assert got == [("draft", "Proposal", 10), ("event", "Dinner with mom", 18)]
