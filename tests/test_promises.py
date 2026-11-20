"""Promises (ADR 0024, 0027; FR-PR-1–10): Slack and At risk boundaries, Origins, and the agent CLI."""

import argparse
from datetime import datetime, timedelta, timezone

import pytest

from comms_terminal import agent, clock, commands, inbox, promises

ME = "jeremy@agentmaple.example"
NOW = datetime(2026, 11, 16, 14, 0, tzinfo=timezone.utc)  # Monday 10:00 Halifax


@pytest.fixture
def db(monkeypatch):
    d = inbox.connect()
    inbox.migrate(d)
    with d:
        d.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                  " VALUES ('1', 'fastmail', ?, ?, 'Fastmail', ?)", (ME, ME, NOW.isoformat()))
    monkeypatch.setattr(clock, "now", lambda: NOW)
    inbox.ingest(d, inbox.Incoming("1", ME, "mailbox", "MESSAGE", "<a@x>", inbox.Identity("email", "anna@example.com",
                                   "Anna Ciprick"), NOW - timedelta(hours=2), "Any update?", title="Update"))
    inbox.ingest(d, inbox.Incoming("1", ME, "mailbox", "MESSAGE", "<b@x>", None, NOW - timedelta(hours=1),
                                   "I'll send it Friday", title="Update", thread_ref=["<a@x>"]))
    with commands.source("cli"):
        yield d
    d.close()


def anna(db):
    return db.execute("SELECT person_id FROM identities WHERE value = 'anna@example.com'").fetchone()[0]


def mine(db):
    return {"kind": "activity", "ref": db.execute("SELECT id FROM activities WHERE identity_id IS NULL").fetchone()[0]}


def row(due, work=0, travel=0, state="OPEN"):
    return {"due_at": due.isoformat(), "work_minutes": work, "travel_minutes": travel, "state": state}


@pytest.mark.parametrize("hours, work, travel, expected", [
    (24, 120, 0, None),         # 24 hours left = 12 x 2 hours: not less, so not At risk
    (23, 120, 0, "at_risk"),    # 23 hours left < 24
    (4, 120, 0, "at_risk"),     # 4 hours left = 2 x 2 hours: not less, so not Urgent
    (3, 120, 0, "urgent"),      # 3 hours left < 4
    (3, 60, 61, "urgent"),      # travel counts like work
    (1, 120, 0, "urgent"),      # cannot fit at all: Slack-time is negative
    (0, 0, 0, "overdue"),       # at its time and not kept
    (-24, 0, 0, "overdue"),
    (72, 0, 0, None),
])
def test_urgent_under_2x_and_at_risk_under_12x_the_work_still_needed(hours, work, travel, expected):
    assert promises.risk(row(NOW + timedelta(hours=hours), work, travel), NOW) == expected


def test_closed_promises_have_no_risk():
    for state in ("FULFILLED", "CANCELLED"):
        assert promises.risk(row(NOW - timedelta(days=1), state=state), NOW) is None


def test_slack_time_is_minutes_left_less_work_and_travel():
    assert promises.slack_time(row(NOW + timedelta(hours=3), 30, 15), NOW) == 135


def test_a_day_alone_is_due_at_17_jeremys_time():
    assert promises.due_from("2026-11-20") == datetime(2026, 11, 20, 21, 0, tzinfo=timezone.utc)
    assert promises.due_from("2026-11-20 09:30") == datetime(2026, 11, 20, 13, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize("text", ["", "activity", "email:x", "words:", "activity:  "])
def test_a_malformed_origin_is_refused(text):
    with pytest.raises(promises.Refused):
        promises.origin_from(text)


def test_an_activity_origin_must_exist(db):
    with pytest.raises(promises.Refused, match="no Activity"):
        promises.record(db, "x", [anna(db)], NOW, {"kind": "activity", "ref": "missing"}, now=NOW)


def test_the_app_cannot_change_a_promise(db):
    pid = promises.record(db, "Send the deck", [anna(db)], NOW + timedelta(days=1), mine(db), now=NOW)
    with commands.source("app"), pytest.raises(promises.Refused, match="only an agent"):
        promises.cancel(db, pid, {"kind": "words", "words": "drop it"}, now=NOW)


def test_changes_cannot_be_rewritten(db):
    promises.record(db, "Send the deck", [anna(db)], NOW + timedelta(days=1), mine(db), now=NOW)
    with pytest.raises(Exception, match="append-only"):
        with db:
            db.execute("UPDATE promise_changes SET words = 'x'")


def test_a_closed_promise_cannot_change(db):
    pid = promises.record(db, "Send the deck", [anna(db)], NOW + timedelta(days=1), mine(db), now=NOW)
    promises.fulfil(db, pid, mine(db), now=NOW)
    with pytest.raises(promises.Refused, match="already Fulfilled"):
        promises.cancel(db, pid, mine(db), now=NOW)


def cli(db, *argv):
    p = argparse.ArgumentParser()
    agent.add_parsers(p.add_subparsers(dest="cmd", required=True))
    a = p.parse_args(argv)
    a.socket = None
    agent.run(a, db)


def test_the_cli_records_lists_and_fulfils(db, capsys):
    origin = f"activity:{mine(db)['ref']}"
    cli(db, "promise", "new", "--what", "Send the traction update", "--to", "anna@example.com",
        "--due", "2026-11-20", "--work", "30", "--origin", origin, "--link", "url:https://example.com/deck")
    shown = capsys.readouterr().out
    assert "Send the traction update" in shown and "Owed to: Anna Ciprick" in shown and "url https://example.com/deck" in shown
    cli(db, "promises")
    listed = capsys.readouterr().out
    assert "## This week" in listed and "Captured up to nothing yet" in listed
    pid = db.execute("SELECT id FROM promises").fetchone()[0]
    cli(db, "promise", pid, "--fulfil", "--actual", "45", "--origin", origin)
    shown = capsys.readouterr().out
    assert "Fulfilled" in shown and "actual 45m" in shown
    cli(db, "promises", "--kept", "6")
    assert "Send the traction update" in (out := capsys.readouterr().out) and "work 30m" in out and "actual 45m" in out


def test_the_cli_refuses_a_change_without_an_origin(db):
    with pytest.raises(SystemExit, match="needs --origin"):
        cli(db, "promise", "new", "--what", "x", "--to", "anna@example.com", "--due", "2026-11-20")


def test_capture_pending_lists_who_wrote_each(db, capsys):
    cli(db, "capture", "pending")
    out = capsys.readouterr().out.splitlines()
    assert [line.split("\t")[3] for line in out] == ["Anna Ciprick", "Jeremy"]
    cli(db, "capture", "advance", mine(db)["ref"])
    assert "Captured up to Activity" in capsys.readouterr().out
    # where capture is: the Activity's own time, not when the mark moved (read live as "caught up")
    assert datetime.fromisoformat(promises.capture_mark(db)["occurred_at"]) == NOW - timedelta(hours=1)
    cli(db, "capture", "pending")
    assert capsys.readouterr().out == "Nothing after the mark.\n"


@pytest.mark.parametrize("row, expected", [
    ({"state": "TENTATIVE", "risk": "overdue"}, "tentative"),  # not owed yet, so never late
    ({"state": "OPEN", "risk": "overdue"}, "overdue"),
    ({"state": "OPEN", "risk": "urgent"}, "urgent"),
    ({"state": "OPEN", "risk": "at_risk"}, "at_risk"),
    ({"state": "OPEN", "risk": None}, "on_track"),
    ({"state": "OPEN", "risk": None, "kind": "prep", "prepared": True}, "on_track"),  # Prepped is On track
    ({"state": "OPEN", "risk": None, "kind": "prep", "prepared": False}, "on_track"),
])
def test_each_row_has_one_status_its_bullet(row, expected):
    assert promises.status(row) == expected
