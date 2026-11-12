"""Planning (PRD FR-IS-5–9, FR-IS-13; FR-PL-1–18): Planned day, Day capacity, urgency, warnings, Plan Issues,
Pinned, Today, and their CLI."""

import io
import sys
import time as clock_time
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from comms_terminal import agent, calendar, commands, inbox, issues, linear

LOCAL = ZoneInfo("America/Halifax")
MONDAY = datetime(2026, 11, 9, 14, 0, tzinfo=timezone.utc)  # 10:00 Halifax
ME = "jeremy@agentmaple.example"


@pytest.fixture
def db():
    db = inbox.connect()
    inbox.migrate(db)
    cid = inbox.ensure_connection(db, "linear", "issue", MONDAY)
    with db:
        db.execute("INSERT INTO connections (platform, external_id, self_id, label, connected_at)"
                   " VALUES ('gmail', ?, ?, 'Gmail', ?)", (ME, ME, MONDAY.isoformat()))
    issues.replace_statuses(db, cid, [{"id": s, "team_id": "team", "name": n, "type": t} for s, n, t in (
        ("todo", "Todo", "unstarted"), ("doing", "Making It Happen", "started"), ("blocked", "Blocked", "unstarted"),
        ("done", "Done", "completed"))])
    yield db
    db.close()


def add(db, n, **fields):
    cid = issues.issue_connection(db, "linear")["id"]
    issues.upsert(db, cid, {"id": f"id-{n}", "identifier": n, "title": n, "team_id": "team", "state_id": "todo",
                               "priority": 0, "created_at": "2026-11-01T00:00:00Z", "updated_at": "2026-11-01"}
                  | fields, MONDAY)


def event(db, uid, day, start, end, extra=""):
    at = lambda t: datetime.combine(day, time.fromisoformat(t), LOCAL).astimezone(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ")
    ics = f"BEGIN:VEVENT\r\nUID:{uid}\r\nSUMMARY:{uid}\r\nDTSTART:{at(start)}\r\nDTEND:{at(end)}\r\n{extra}END:VEVENT\r\n"
    with db:
        db.execute("INSERT INTO events (uid, ics, first_seen_at, changed_at) VALUES (?, ?, ?, ?)",
                   (uid, ics, MONDAY.isoformat(), MONDAY.isoformat()))


TUESDAY = date(2026, 11, 10)


def test_capacity_merges_overlaps_and_skips_free_and_declined(db):
    event(db, "a", TUESDAY, "09:00", "11:00")
    event(db, "b", TUESDAY, "10:00", "12:00")  # overlaps a: 3 hours in all
    event(db, "free", TUESDAY, "13:00", "15:00", "TRANSP:TRANSPARENT\r\n")
    event(db, "no", TUESDAY, "15:00", "16:00", f"ORGANIZER:mailto:x@y.example\r\nATTENDEE;PARTSTAT=DECLINED:mailto:{ME}\r\n")
    add(db, "BIZ-1", estimate=4)
    issues.plan(db, "BIZ-1", TUESDAY.isoformat(), now=MONDAY)
    assert issues.capacity(db, TUESDAY, MONDAY) == {"capacity": 6.0, "used": 2.0, "left": 4.0}


def test_today_counts_only_what_is_left_before_18(db):
    event(db, "past", MONDAY.astimezone(LOCAL).date(), "08:00", "09:30")
    event(db, "later", MONDAY.astimezone(LOCAL).date(), "15:00", "16:00")
    event(db, "evening", MONDAY.astimezone(LOCAL).date(), "19:00", "20:00")  # after the working day: not counted
    at_17 = datetime(2026, 11, 9, 21, 0, tzinfo=timezone.utc)  # Monday 17:00 Halifax
    at_22 = datetime(2026, 11, 10, 2, 0, tzinfo=timezone.utc)  # Monday 22:00 Halifax
    assert issues.capacity(db, date(2026, 11, 9), MONDAY)["capacity"] == 7.0  # 10:00 to 18:00, less 15:00-16:00
    assert issues.capacity(db, date(2026, 11, 9), at_17)["capacity"] == 1.0
    assert issues.capacity(db, date(2026, 11, 9), at_22)["capacity"] == 0


def test_weekends_and_busy_all_day_events_hold_none(db):
    saturday = date(2026, 11, 14)
    assert issues.capacity(db, saturday, MONDAY)["capacity"] == 0
    add(db, "BIZ-1")
    issues.plan(db, "BIZ-1", saturday.isoformat(), now=MONDAY)
    assert issues.capacity(db, saturday, MONDAY)["capacity"] == 9  # planned there: the day counts
    with db:
        db.execute("INSERT INTO events (uid, ics, first_seen_at, changed_at) VALUES ('off', ?, ?, ?)",
                   ("BEGIN:VEVENT\r\nUID:off\r\nSUMMARY:Off\r\nDTSTART;VALUE=DATE:20261110\r\n"
                    "DTEND;VALUE=DATE:20261111\r\nEND:VEVENT\r\n", MONDAY.isoformat(), MONDAY.isoformat()))
    assert issues.capacity(db, TUESDAY, MONDAY)["capacity"] == 0


def test_urgency_follows_the_taskwarrior_factors(db):
    c = issues.coefficients(db)
    day = date(2026, 11, 9)
    base = {"priority": 0, "due_date": None, "state_type": "unstarted", "data": {}, "created_at": "2026-11-09"}
    assert issues.urgency(base, c, day) == 1.95
    assert issues.urgency(base | {"priority": 1}, c, day) == 9
    assert issues.urgency(base | {"due_date": "2026-11-02"}, c, day) == 1.95 + 12  # 7 days late: the full ramp
    assert issues.urgency(base | {"due_date": "2026-11-30"}, c, day) == round(1.95 + 12 * 0.2, 4)
    assert issues.urgency(base | {"state_type": "started"}, c, day) == 5.95
    assert issues.urgency(base | {"data": {"blocking": 2, "blocked": 1}}, c, day) == round(1.95 + 8 - 5, 4)
    assert issues.urgency(base | {"created_at": "2025-11-09"}, c, day) == 3.95  # a year old: the full age


def test_warnings_far_out_oversized_and_due(db):
    add(db, "BIZ-1", priority=1, due_date="2026-11-10")  # most urgent
    add(db, "BIZ-2")
    add(db, "BIZ-3", estimate=20)  # ten hours
    issues.plan(db, "BIZ-1", "2026-11-11", now=MONDAY)  # top percentile two days out, after its due date
    issues.plan(db, "BIZ-2", "2026-11-10", now=MONDAY)
    issues.plan(db, "BIZ-3", "2026-11-12", now=MONDAY)
    w = issues.warnings(db, MONDAY)
    assert w["id-BIZ-1"] == ["Planned after its due date", "Planned too far out for its urgency"]
    assert w["id-BIZ-3"] == ["Larger than a day"] and "id-BIZ-2" not in w


def local(day, at):
    return datetime.combine(day, time.fromisoformat(at), LOCAL).astimezone(timezone.utc)


def days(db, *names):
    return [issues.find(db, n)["planned_day"] for n in names]


def all_day(db, uid, day):
    with db:
        db.execute("INSERT INTO events (uid, ics, first_seen_at, changed_at) VALUES (?, ?, ?, ?)",
                   (uid, f"BEGIN:VEVENT\r\nUID:{uid}\r\nSUMMARY:Off\r\nDTSTART;VALUE=DATE:{day:%Y%m%d}\r\n"
                         f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}\r\nEND:VEVENT\r\n",
                    MONDAY.isoformat(), MONDAY.isoformat()))


def test_feasibility_boost_follows_superplan(db):
    c = issues.coefficients(db)
    assert c["feasibility"] == 5
    room = {date(2026, 11, 9): 8, date(2026, 11, 10): 0}.get
    due = lambda d: {"due_date": d, "estimate": None}
    assert issues._feasibility(due("2026-11-10"), lambda d: room(d, 9), date(2026, 11, 9), c) == 3.75  # 1 day
    assert issues._feasibility(due("2026-11-06"), lambda d: 9, date(2026, 11, 9), c) == 5  # already passed: 0 days
    assert issues._feasibility(due("2026-11-11"), lambda d: 9, date(2026, 11, 9), c) == 2  # 3 days
    assert issues._feasibility(due("2026-11-30"), lambda d: 9, date(2026, 11, 9), c) == 0
    assert issues._feasibility({"due_date": None}, lambda d: 9, date(2026, 11, 9), c) == 0


def test_an_issue_with_no_room_before_its_due_date_goes_first(db):
    issues.set_urgency(db, "due", 0, now=MONDAY)  # isolate the boost from the due ramp
    all_day(db, "off", TUESDAY)
    event(db, "busy", date(2026, 11, 11), "09:00", "17:00")  # Wednesday holds 1 hour
    add(db, "BIZ-1", priority=2)  # High: 6
    add(db, "BIZ-2", due_date="2026-11-10")  # No priority 1.95, no workday left that holds it: +5
    issues.plan_all(db, now=local(date(2026, 11, 9), "19:00"))
    assert days(db, "BIZ-2", "BIZ-1") == ["2026-11-11", "2026-11-12"]


def test_a_pinned_overdue_issue_uses_todays_capacity(db):
    add(db, "BIZ-1", estimate=14)  # 7 hours, pinned to last Friday
    issues.plan(db, "BIZ-1", "2026-11-06", now=MONDAY)
    issues.pin(db, "BIZ-1", True, now=MONDAY)
    add(db, "BIZ-2", estimate=4)  # 2 hours: today holds 8 - 7 = 1
    issues.plan_all(db, now=MONDAY)
    assert days(db, "BIZ-1", "BIZ-2") == ["2026-11-06", "2026-11-10"]


def test_plan_issues_skips_weekends(db):
    friday = local(date(2026, 11, 13), "10:00")  # 8 hours left
    for n in range(10):
        add(db, f"BIZ-{n:02}")
    issues.plan_all(db, now=friday)
    planned = [i["planned_day"] for i in issues.issues(db)]
    assert planned.count("2026-11-13") == 8 and planned.count("2026-11-16") == 2


def test_a_run_that_changes_nothing_records_no_command(db):
    add(db, "BIZ-1")
    with commands.source("console"):
        assert issues.plan_all(db, now=MONDAY) == 1
        before = db.execute("SELECT count(*) FROM commands").fetchone()[0]
        assert issues.plan_all(db, now=MONDAY) == 0
    assert db.execute("SELECT count(*) FROM commands").fetchone()[0] == before


def test_undo_of_a_run_is_refused_once_linear_rewrites_an_issue(db):
    add(db, "BIZ-1")
    with commands.source("console"):
        issues.plan_all(db, now=MONDAY)
        add(db, "BIZ-1", updated_at="2026-11-09")  # a full read takes Linear's newer version
        with pytest.raises(commands.Refused, match="changed since"):
            commands.undo(db, commands.newest(db)["id"], now=MONDAY)


def test_a_pin_survives_linear_and_is_never_sent(db):
    add(db, "BIZ-1")
    issues.pin(db, "BIZ-1", True, now=MONDAY)
    add(db, "BIZ-1", updated_at="2026-11-09", title="Renamed")
    assert issues.find(db, "BIZ-1")["pinned"] == 1
    assert db.execute("SELECT count(*) FROM actions").fetchone()[0] == 0


def test_an_overdue_issue_is_not_larger_than_its_passed_day(db):
    add(db, "BIZ-1")
    issues.plan(db, "BIZ-1", "2026-11-06", now=MONDAY)
    assert issues.warnings(db, MONDAY) == {}
    groups = issues.view(db, {"grouping": "planned_day"}, MONDAY)["groups"]
    assert [("capacity" in g) for g in groups] == [False]  # a passed day's group shows no "N of 0 h"


def test_plan_issues_is_fast(db):
    for n in range(100):
        add(db, f"BIZ-{n:03}", estimate=n % 7 or None, priority=n % 5, due_date=f"2026-11-{10 + n % 20}")
    started = clock_time.perf_counter()
    issues.plan_all(db, now=MONDAY)
    assert clock_time.perf_counter() - started < 1.0  # NFR-1
    assert all(i["planned_day"] for i in issues.issues(db))


def test_a_webhook_keeps_the_last_reads_relations(db):
    add(db, "BIZ-1", data={"blocking": 1, "blocked": 0})
    add(db, "BIZ-1", updated_at="2026-11-02", data={"team_key": "BIZ"})
    assert issues.issues(db)[0]["data"] == {"blocking": 1, "blocked": 0, "team_key": "BIZ"}
    node = {"id": "x", "identifier": "BIZ-1", "createdAt": "c", "updatedAt": "u",
            "relations": {"nodes": [{"type": "blocks", "relatedIssue": {"completedAt": None}},
                                    {"type": "blocks", "relatedIssue": {"completedAt": "done"}},
                                    {"type": "related", "relatedIssue": {}}]},
            "inverseRelations": {"nodes": [{"type": "blocks", "issue": {"canceledAt": None}}]}}
    assert linear.normal(node)["data"] == {"blocking": 1, "blocked": 1}


def test_planned_issues_are_calendar_items(db):
    add(db, "BIZ-1")
    issues.plan(db, "BIZ-1", "2026-11-10", now=MONDAY)
    items = [i for i in calendar.items(db, TUESDAY, date(2026, 11, 11)) if i["kind"] == "issue"]
    assert [(i["title"], i["all_day"], i["ref"]) for i in items] == [("BIZ-1 · BIZ-1", True, "id-BIZ-1")]


def cli(db, *argv):
    import argparse
    p = argparse.ArgumentParser()
    agent.add_parsers(p.add_subparsers(dest="cmd"))
    a = p.parse_args(argv)
    for name in ("delete", "attach", "detach"):
        setattr(a, name, getattr(a, name, None))
    old, sys.stdin = sys.stdin, io.StringIO("")
    try:
        agent.run(a, db)
    finally:
        sys.stdin = old


def test_cli_plans_and_sets_urgency(db, capsys, monkeypatch):
    monkeypatch.setattr(agent.clock, "now", lambda: MONDAY)
    monkeypatch.setattr(issues.clock, "now", lambda: MONDAY)
    add(db, "BIZ-1")
    cli(db, "issue", "BIZ-1", "--plan", "tomorrow")
    assert "Planned day: 2026-11-10" in capsys.readouterr().out
    cli(db, "issue", "BIZ-1", "--plan", "none")
    assert issues.find(db, "BIZ-1")["planned_day"] is None
    assert db.execute("SELECT count(*) FROM actions").fetchone()[0] == 0  # local only
    cli(db, "urgency", "set", "due", "10")
    assert "due\t10" in capsys.readouterr().out.splitlines()
    with pytest.raises(SystemExit):
        cli(db, "urgency", "set", "nope", "1")


def test_cli_plans_issues_and_pins(db, capsys, monkeypatch):
    monkeypatch.setattr(agent.clock, "now", lambda: MONDAY)
    add(db, "BIZ-1", priority=1)
    add(db, "BIZ-2")
    cli(db, "issue", "BIZ-2", "--pin")
    assert "· Pinned" in capsys.readouterr().out
    cli(db, "issues", "plan")
    out = capsys.readouterr().out
    assert out.startswith("Planned 1 Issue\n") and "BIZ-1\tTodo\turgent\t2026-11-09" in out
    assert days(db, "BIZ-2") == [None]
    cli(db, "issue", "BIZ-2", "--unpin")
    assert issues.find(db, "BIZ-2")["pinned"] == 0
    assert db.execute("SELECT count(*) FROM actions").fetchone()[0] == 0  # local only (AS-PL-11)


def test_due_now_is_yesterday_to_tomorrow_by_due_date_or_planned_day(db):
    day = date(2026, 11, 9)
    i = lambda due=None, planned=None: {"due_date": due, "planned_day": planned, "completed_at": None,
                                         "canceled_at": None}
    assert issues.due_now(i(due="2026-11-08"), day) and issues.due_now(i(planned="2026-11-10"), day)
    assert not issues.due_now(i(due="2026-11-07"), day) and issues.overdue(i(due="2026-11-07"), day)
    assert not issues.overdue(i(due="2026-09-22", planned="2026-11-09"), day)  # planned today: due now
    assert not issues.on_today(i(planned="2026-11-11"), day) and not issues.on_today(i(), day)


def test_day_lookup_finds_what_a_one_day_expansion_finds(db):
    """Capacity reads days from one cached year (calendar.on_day): each day still holds every occurrence a one-day
    expansion finds, and All-day Events exactly their own days."""
    event(db, "late", TUESDAY, "22:00", "23:59", "RRULE:FREQ=WEEKLY;COUNT=3\r\n")
    event(db, "overnight", TUESDAY, "23:00", "23:30")
    with db:
        db.execute("INSERT INTO events (uid, ics, first_seen_at, changed_at) VALUES ('trip', ?, ?, ?)",
                   ("BEGIN:VEVENT\r\nUID:trip\r\nSUMMARY:Trip\r\nDTSTART;VALUE=DATE:20261112\r\n"
                    "DTEND;VALUE=DATE:20261114\r\nEND:VEVENT\r\n", MONDAY.isoformat(), MONDAY.isoformat()))
    today = MONDAY.astimezone(LOCAL).date()
    for n in range(-3, 30):
        day = today + timedelta(days=n)
        found = {e["id"] for e in calendar.on_day(db, day, today)}
        assert {e["id"] for e in calendar.expand(db, day, day + timedelta(days=1), deleted=False)} <= found, day
        assert any(i.startswith("trip|") for i in found) == (date(2026, 11, 12) <= day < date(2026, 11, 14)), day
