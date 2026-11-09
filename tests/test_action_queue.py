"""The Action Queue timeline (ADR 0022, PRD .agents/prd/2026-11-08-action-queue.md FR-AQT-1–8): every Action that
waits for a time, read from the records that already hold its time and outcome."""

from datetime import date, datetime, timedelta, timezone

import pytest

from test_console import seed_demo  # puts scripts/ on the path
from comms_terminal import actions, inbox
from comms_terminal import sequences as sq

NOW = datetime(2026, 11, 9, 14, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path):
    db = inbox.connect(str(tmp_path / "inbox.db"))
    return db, seed_demo.seed(db)


def new_draft(db, subject, to="ana@acme.com"):
    conn = db.execute("SELECT id FROM connections WHERE kind = 'email' OR platform IN ('gmail', 'fastmail')"
                      " ORDER BY id LIMIT 1").fetchone()[0]
    return inbox.save_draft(db, {"connection_id": conn, "kind": "email", "sender": "jeremy@agentmaple.com",
                                 "to": to, "cc": "", "subject": subject, "body": "Numbers attached."})["id"]


def person(db):
    return db.execute("SELECT id FROM persons ORDER BY id LIMIT 1").fetchone()[0]


def by_key(rows):
    return {r["key"]: r for r in rows}


def seeded(db):
    """The demo seed's own Snoozes and waits, which these tests leave aside."""
    return {r["key"] for r in actions.queue(db, NOW)} | {
        f"wake:{t}" for (t,) in db.execute("SELECT id FROM threads WHERE status_detail LIKE 'WAITING_FOR_%'")}


def mine(db, before, now=NOW):
    return [r for r in actions.queue(db, now) if r["key"] not in before and not
            (r["kind"] == "wake" and r["section"] == "sent" and f"wake:{r['target_id']}" in before)]


def test_scheduled_sends_wakes_and_follow_ups_list_furthest_ahead_first(db):
    db, ids = db
    before = seeded(db) - {f"wake:{ids['ana']}", f"wake:{ids['logan']}"}
    d = new_draft(db, "Q4 pricing")
    inbox.schedule_draft(db, d, NOW + timedelta(days=1), now=NOW)
    inbox.act(db, ids["ana"], inbox.Snooze(NOW + timedelta(hours=1)), now=NOW)
    inbox.act(db, ids["logan"], inbox.Wait(), now=NOW)  # back to Todo in 4 days with no reply
    rows = [r for r in mine(db, before) if r["section"] == "upcoming"]
    assert [(r["kind"], r["what"]) for r in rows] == [("wake", "Follow-up"), ("email", "Send"),
                                                      ("wake", "Snooze ends")]
    send = next(r for r in rows if r["kind"] == "email")
    assert send["target"] == "Q4 pricing · ana@acme.com" and (send["target_kind"], send["target_id"]) == ("draft", d)
    assert all(r["target"] for r in rows) and [r["at"] for r in rows] == sorted((r["at"] for r in rows), reverse=True)


def test_a_due_wake_is_in_flight_then_sent_from_its_audit_entry(db):
    db, ids = db
    inbox.act(db, ids["ana"], inbox.Snooze(NOW + timedelta(hours=1)), now=NOW)
    later = NOW + timedelta(hours=2)
    assert by_key(actions.queue(db, later))[f"wake:{ids['ana']}"]["section"] == "in_flight"
    inbox.wake(db, later)
    sent = [r for r in actions.queue(db, later) if r["kind"] == "wake" and r["target_id"] == ids["ana"]]
    assert [(r["section"], r["outcome"]) for r in sent] == [("sent", "confirmed")]
    gone = [r for r in actions.queue(db, later + timedelta(days=8)) if r["target_id"] == ids["ana"]]
    assert gone == []  # Sent reaches back 7 days


def test_send_outcomes_show_in_sent_for_seven_days(db):
    db, _ = db
    before = seeded(db)
    conn = db.execute("SELECT id FROM connections WHERE platform = 'gmail' LIMIT 1").fetchone()[0]
    ok = actions.start(db, conn, "send", "gone-1", {"label": "Intro · ana@acme.com"}, False, NOW - timedelta(days=1))
    actions.settle(db, ok, "confirmed", None, NOW - timedelta(days=1))
    old = actions.start(db, conn, "send", "gone-2", {"label": "Old · lee@x.com"}, False, NOW - timedelta(days=8))
    actions.settle(db, old, "refused", "Gmail refused", NOW - timedelta(days=8))
    lost = actions.start(db, conn, "send", "gone-3", {"label": "Lost · raj@x.com"}, False, NOW - timedelta(hours=1))
    actions.settle(db, lost, "unknown", "timed out", NOW - timedelta(hours=1))
    rows = mine(db, before)
    assert [(r["section"], r["kind"], r["target"], r["outcome"]) for r in rows] == [
        ("sent", "email", "Lost · raj@x.com", "unknown"), ("sent", "email", "Intro · ana@acme.com", "confirmed")]


def test_issue_changes_responds_and_tasks_jeremy_does_stay_off(db):
    db, _ = db
    before = seeded(db)
    conn = db.execute("SELECT id FROM connections WHERE platform = 'gmail' LIMIT 1").fetchone()[0]
    r = actions.start(db, conn, "respond", "ev1", {"label": "Accept · Pints"}, False, NOW)
    actions.settle(db, r, "refused", "no", NOW)
    actions.enqueue(db, conn, "issue_update", "i1", {"priority": 1}, {"priority": 0}, True, NOW)
    s = sq.create_sequence(db, "Intro", now=NOW)
    sq.add_step(db, s, "email", now=NOW)
    sq.add_step(db, s, "call", now=NOW)
    sq.enroll(db, person(db), s, now=NOW)  # starts today: no start time to wait for
    sq.materialize(db, NOW)
    assert db.execute("SELECT count(*) FROM tasks WHERE state = 'DUE'").fetchone()[0] >= 1
    assert mine(db, before) == []


def test_a_later_enrollment_start_is_upcoming_then_sent(db):
    db, _ = db
    before = seeded(db)
    s = sq.create_sequence(db, "Fundraising", now=NOW)
    sq.add_step(db, s, "email", now=NOW)
    p = person(db)
    sq.enroll(db, p, s, now=NOW, start=date(2026, 11, 12))
    [row] = mine(db, before)
    assert (row["section"], row["kind"], row["what"], row["target_kind"], row["target_id"]) == (
        "upcoming", "enrol", "Enrollment starts", "person", p)
    later = datetime(2026, 11, 12, 15, tzinfo=timezone.utc)
    sq.materialize(db, later)
    assert [(r["section"], r["outcome"]) for r in mine(db, before, later) if r["kind"] == "enrol"] == [
        ("sent", "confirmed")]


def test_an_action_step_that_ran_is_sent_and_one_that_waits_for_jeremy_is_off(db):
    from comms_terminal import pipeline
    db, _ = db
    before = seeded(db)
    s = sq.create_sequence(db, "Mark it", now=NOW)
    step = sq.add_step(db, s, "action", action_kind="set_stage", now=NOW)
    sq.save_step(db, step, pipeline=pipeline.FUNDRAISING, stage=pipeline.STAGES[pipeline.FUNDRAISING][1], now=NOW)
    shelved = sq.create_sequence(db, "Shelved", now=NOW)
    sq.add_step(db, shelved, "email", now=NOW)
    sq.archive_sequence(db, shelved, False, now=NOW)
    held = sq.add_step(db, s, "action", now=NOW)  # Enroll into an archived Sequence waits for Jeremy
    sq.save_step(db, held, enroll_sequence_id=shelved, now=NOW)
    sq.enroll(db, person(db), s, now=NOW)
    sq.materialize(db, NOW)
    assert db.execute("SELECT flag FROM tasks WHERE step_id = ?", (held,)).fetchone()[0]  # waits for Jeremy
    rows = [r for r in mine(db, before) if r["kind"] == "step"]
    assert [(r["section"], r["what"], r["outcome"]) for r in rows] == [("sent", "Set Stage", "confirmed")]


def test_with_sequences_off_no_enrollment_or_step_rows(db):
    db, _ = db
    before = seeded(db)
    s = sq.create_sequence(db, "Later", now=NOW)
    sq.add_step(db, s, "email", now=NOW)
    sq.enroll(db, person(db), s, now=NOW, start=date(2026, 11, 20))
    assert [r["kind"] for r in mine(db, before)] == ["enrol"]
    with db:
        db.execute("UPDATE modules SET enabled = 0 WHERE id = 'sequences'")
    assert mine(db, before) == []


def test_day_counts_and_badge(db):
    db, ids = db
    before = seeded(db) - {f"wake:{ids['ana']}"}
    for subject in ("One", "Two"):
        inbox.schedule_draft(db, new_draft(db, subject), NOW + timedelta(hours=3), now=NOW)
    inbox.act(db, ids["ana"], inbox.Snooze(NOW + timedelta(hours=4)), now=NOW)
    rows = mine(db, before)
    day = rows[0]["day"]
    assert actions.counts(rows)[day] == {"email": 2, "wake": 1}
    assert actions.badge(rows) == (3, 0)


def test_a_step_of_a_paused_or_finished_enrollment_is_off(db):  # the timed pass carries only Active or Finishing
    db, _ = db
    before = seeded(db)
    s = sq.create_sequence(db, "Later step", now=NOW)
    sq.add_step(db, s, "email", now=NOW)
    sq.add_step(db, s, "action", action_kind="set_stage", now=NOW)
    e = sq.enroll(db, person(db), s, now=NOW)
    with db:  # a due Action Step Task, as a finished Enrollment can leave behind
        step = db.execute("SELECT id FROM sequence_steps WHERE sequence_id = ? AND kind = 'action'", (s,)).fetchone()[0]
        db.execute("INSERT INTO tasks (enrollment_id, step_id, due_at, state) VALUES (?, ?, ?, 'DUE')",
                   (e, step, (NOW - timedelta(days=4)).isoformat()))
    assert [r["kind"] for r in mine(db, before)] == ["step"]
    for state in ("PAUSED", "FINISHED"):
        with db:
            db.execute("UPDATE enrollments SET state = ?, reason = 'CANCELED' WHERE id = ?", (state, e))
        assert [r for r in mine(db, before) if r["kind"] == "step"] == []
