"""Audit log: every Status or Case change of a Thread is recorded with its cause and shown in the timeline."""

import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from comms_terminal import clock as comms_clock, inbox, sequences
from comms_terminal.tui import audit_markup

NOW = datetime(2026, 10, 15, 15, 0, tzinfo=timezone.utc)
ANA = inbox.Identity("email", "ana@acme.com", "Ana")


def mail(native_id, at, sender=ANA, refs=()):
    return inbox.Incoming("1", "me@x.io", "mailbox", "MESSAGE", native_id, sender, at, "hi", thread_ref=list(refs))


def entries(db, thread_id):
    return [(e["kind"], e["cause"], e["old_detail"], e["new_detail"], e["case_title"])
            for e in inbox.thread_view(db, thread_id)["audit"]]


def test_status_and_case_changes_are_logged_with_their_cause(monkeypatch):
    db = inbox.connect()
    inbox.migrate(db)
    with db:
        db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                   " VALUES ((SELECT printf('%d', count(*) + 1) FROM connections), 'gmail', 'me@x.io', 'me@x.io', 'Email', ?)", (NOW.isoformat(),))
    minutes = iter(range(1, 1000))
    clock = lambda: NOW + timedelta(minutes=next(minutes))  # noqa: E731
    monkeypatch.setattr(comms_clock, "now", clock)
    t = inbox.ingest(db, mail("<a@acme.com>", NOW))
    assert entries(db, t.id) == []  # a new Thread's Status is not a change
    inbox.act(db, t.id, inbox.Wait())
    inbox.ingest(db, mail("<b@acme.com>", clock(), refs=["<a@acme.com>"]))
    inbox.ingest(db, mail("<c@acme.com>", clock(), refs=["<a@acme.com>"]))  # still Todo: no entry
    snap = inbox.snapshot(db, t.id)
    inbox.act(db, t.id, inbox.SetCase(new_title="Renewal"))
    inbox.act(db, t.id, inbox.Done())
    inbox.restore(db, snap)
    inbox.act(db, t.id, inbox.Snooze(NOW + timedelta(days=1)))
    inbox.wake(db, NOW + timedelta(days=2))
    assert entries(db, t.id) == [
        ("STATUS", "action", "CREATED", "WAITING_FOR_CUSTOMER", None),
        ("STATUS", "message", "WAITING_FOR_CUSTOMER", "NEW_REPLY", None),
        ("CASE", "action", None, None, "Renewal"),
        ("STATUS", "action", "NEW_REPLY", "DONE_MANUALLY_SET", None),
        ("STATUS", "undo", "DONE_MANUALLY_SET", "NEW_REPLY", None),
        ("CASE", "undo", None, None, None),
        ("STATUS", "action", "NEW_REPLY", "WAITING_FOR_DURATION", None),
        ("STATUS", "woke", "WAITING_FOR_DURATION", "NEW_REPLY", None),
    ]
    log = inbox.thread_view(db, t.id)["audit"]
    assert "Paused for later until" in audit_markup(log[6]) and "· you" in audit_markup(log[6])
    assert "snooze ended" in audit_markup(log[7]) and "Removed from its Case" in audit_markup(log[5])

    inbox.block(db, "email", "spam@acme.com")
    spam = inbox.ingest(db, mail("<s@acme.com>", NOW, inbox.Identity("email", "spam@acme.com")))
    assert entries(db, spam.id) == [("STATUS", "blocked", None, "IGNORED", None)]

    for sql in ("UPDATE audit_log SET cause = 'action'", "DELETE FROM audit_log"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            db.execute(sql)


def test_case_feed_shows_threads_joining_and_leaving_and_description_edits(monkeypatch):
    db = inbox.connect()
    inbox.migrate(db)
    with db:
        db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                   " VALUES ((SELECT printf('%d', count(*) + 1) FROM connections), 'gmail', 'me@x.io', 'me@x.io', 'Email', ?)", (NOW.isoformat(),))
    minutes = iter(range(1, 1000))
    monkeypatch.setattr(comms_clock, "now", lambda: NOW + timedelta(minutes=next(minutes)))
    a = inbox.ingest(db, mail("<a@acme.com>", NOW))
    b = inbox.ingest(db, mail("<b@acme.com>", NOW, inbox.Identity("email", "bo@acme.com")))
    renewal, other = inbox.create_case(db, "Renewal"), inbox.create_case(db, "Other")
    inbox.act(db, a.id, inbox.SetCase(case_id=renewal))
    inbox.act(db, b.id, inbox.SetCase(case_id=renewal))
    inbox.set_case_description(db, renewal, "Close by Friday")
    inbox.set_case_description(db, renewal, "Close by Friday")  # unchanged: no entry
    snap = inbox.snapshot(db, b.id)
    inbox.act(db, b.id, inbox.SetCase(case_id=other))  # moved: leaves Renewal, joins Other
    inbox.restore(db, snap)
    inbox.act(db, a.id, inbox.SetCase())
    feed = lambda case_id: [(e["kind"], e.get("thread_id"), e.get("cause")) for e in inbox.case_feed(db, case_id)]  # noqa: E731
    assert feed(renewal) == [("created", None, None), ("added", a.id, "action"), ("added", b.id, "action"),
                             ("description", None, None), ("removed", b.id, "action"), ("added", b.id, "undo"),
                             ("removed", a.id, "action")]
    assert feed(other) == [("created", None, None), ("added", b.id, "action"), ("removed", b.id, "undo")]
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        db.execute("DELETE FROM case_log")


@pytest.fixture
def slack_thread(monkeypatch):
    monkeypatch.setattr(comms_clock, "now", lambda: NOW)
    db = inbox.connect()
    inbox.migrate(db)
    with db:
        db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                   " VALUES ((SELECT printf('%d', count(*) + 1) FROM connections), 'slack', 'T0TEST', 'U0SELF', 'Slack', ?)", (NOW.isoformat(),))
    original = inbox.Incoming("1", "D0TEST", "im", "MESSAGE", "D0TEST:1",
                              inbox.Identity("slack_user", "U0BOT"), NOW - timedelta(minutes=2), "original")
    thread = inbox.ingest(db, original)
    yield db, original, thread
    db.close()


@pytest.mark.parametrize("action", [
    inbox.Wait(), inbox.Snooze(NOW + timedelta(days=1)), inbox.Done(), inbox.Investigate(),
], ids=["waiting", "timed-snooze", "done", "investigating"])
@pytest.mark.parametrize("kind", ["MESSAGE", "EDIT", "DELETE"])
def test_blocked_activity_preserves_triage_audit_and_ingestion_effects(slack_thread, monkeypatch, action, kind):
    db, original, thread = slack_thread
    edited = replace(original, kind="EDIT", native_id="D0TEST:1:2", content="edited",
                     occurred_at=NOW - timedelta(minutes=1))
    inbox.ingest(db, edited)
    inbox.act(db, thread.id, inbox.Investigate())  # a timed snooze must retain its prior Todo detail
    inbox.act(db, thread.id, action)
    inbox.block(db, "slack_user", "U0BOT")
    before, audit = inbox.snapshot(db, thread.id), entries(db, thread.id)
    paused = []
    monkeypatch.setattr(sequences, "pause_person", lambda *args: paused.append(args))
    native_id = {"MESSAGE": "D0TEST:3", "EDIT": "D0TEST:1:3", "DELETE": original.native_id}[kind]
    at = NOW + timedelta(minutes=1)
    result = inbox.ingest(db, replace(original, kind=kind, native_id=native_id, occurred_at=at,
                                      content="blocked update", thread_ref=[original.native_id]))
    assert result.id == thread.id
    assert inbox.snapshot(db, thread.id) == before  # all six triage fields, not only Status
    assert entries(db, thread.id) == audit
    assert [tuple(r) for r in db.execute(
        "SELECT kind, native_id, content FROM activities WHERE thread_id = ? ORDER BY id", (thread.id,),
    )] == [
        ("MESSAGE", original.native_id, None if kind == "DELETE" else "original"),
        ("EDIT", edited.native_id, None if kind == "DELETE" else "edited"),
        (kind, native_id, None if kind == "DELETE" else "blocked update"),
    ]
    person = db.execute("SELECT person_id FROM identities WHERE kind = 'slack_user' AND value = 'U0BOT'").fetchone()[0]
    assert paused == ([(db, person, at, "replied")] if kind == "MESSAGE" else [])


@pytest.mark.parametrize("unblock", [False, True], ids=["other-participant", "unblocked-sender"])
def test_unblocked_identity_can_reopen_the_same_thread(slack_thread, unblock):
    db, original, thread = slack_thread
    inbox.block(db, "slack_user", "U0BOT")
    inbox.act(db, thread.id, inbox.Wait())
    if unblock:
        inbox.block(db, "slack_user", "U0BOT", False)
    sender = original.sender if unblock else inbox.Identity("slack_user", "U0OTHER")
    result = inbox.ingest(db, replace(original, native_id="D0TEST:2", sender=sender,
                                      occurred_at=NOW + timedelta(minutes=1), thread_ref=[original.native_id]))
    assert (result.id, result.status, result.status_detail) == (thread.id, "TODO", "NEW_REPLY")
    assert entries(db, thread.id)[-1] == ("STATUS", "message", "WAITING_FOR_CUSTOMER", "NEW_REPLY", None)


def test_ignored_thread_stays_sticky_after_block_unblock_and_other_arrivals(slack_thread):
    db, original, thread = slack_thread
    inbox.act(db, thread.id, inbox.Ignore())
    before, audit = inbox.snapshot(db, thread.id), entries(db, thread.id)
    arrivals = [(original.sender, True), (original.sender, False),
                (inbox.Identity("slack_user", "U0OTHER"), False), (None, False)]
    for n, (sender, blocked) in enumerate(arrivals, 2):
        inbox.block(db, "slack_user", "U0BOT", blocked)
        result = inbox.ingest(db, replace(original, native_id=f"D0TEST:{n}", sender=sender,
                                          occurred_at=NOW + timedelta(minutes=n), thread_ref=[original.native_id]))
        assert result.id == thread.id
        assert inbox.snapshot(db, thread.id) == before
        assert entries(db, thread.id) == audit


@pytest.mark.parametrize("action,detail", [
    (inbox.Wait(), "NEW_REPLY"), (inbox.Snooze(NOW + timedelta(days=1)), "IN_PROGRESS"),
], ids=["follow-up", "timed-snooze"])
def test_blocked_reply_does_not_prevent_independent_due_wake(slack_thread, action, detail):
    db, original, thread = slack_thread
    inbox.act(db, thread.id, inbox.Investigate())
    waiting = inbox.act(db, thread.id, action)
    inbox.block(db, "slack_user", "U0BOT")
    before, audit = inbox.snapshot(db, thread.id), entries(db, thread.id)
    inbox.ingest(db, replace(original, native_id="D0TEST:2", occurred_at=NOW + timedelta(minutes=1),
                             thread_ref=[original.native_id]))
    assert inbox.snapshot(db, thread.id) == before
    due = datetime.fromisoformat(waiting.follow_up_at or waiting.snoozed_until)
    assert inbox.wake(db, due - timedelta(microseconds=1)) == 0
    assert inbox.wake(db, due) == 1
    assert inbox.wake(db, due) == 0
    assert inbox.get_thread(db, thread.id).status_detail == detail
    assert entries(db, thread.id) == audit + [("STATUS", "woke", waiting.status_detail, detail, None)]


@pytest.mark.parametrize("action,expected", [
    (inbox.Wait(), ("TODO", "NEW_REPLY")), (inbox.Ignore(), ("DONE", "IGNORED")),
], ids=["waiting", "ignored"])
def test_blocked_phone_calls_keep_existing_creation_and_reopen_rules(slack_thread, action, expected):
    db, _, _ = slack_thread
    with db:
        db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                   " VALUES (2, 'quo', '+15555550000', '+15555550000', 'Phone', ?)", (NOW.isoformat(),))
    inbox.block(db, "phone", "+15555550101")
    call = inbox.Incoming("2", "+15555550101", "im", "MESSAGE", "call-1",
                          inbox.Identity("phone", "+15555550101"), NOW - timedelta(minutes=2), "Missed call",
                          data={"peer": "+15555550101", "call": {"direction": "incoming", "outcome": "missed", "length": 0}}, synchronous="call")
    thread = inbox.ingest(db, call)
    assert (thread.status, thread.status_detail) == ("DONE", "IGNORED")
    assert entries(db, thread.id) == [("STATUS", "blocked", None, "IGNORED", None)]
    inbox.act(db, thread.id, action)
    before, audit = inbox.snapshot(db, thread.id), entries(db, thread.id)
    result = inbox.ingest(db, replace(call, native_id="call-2", occurred_at=NOW + timedelta(minutes=1)))
    assert (result.id, result.status, result.status_detail) == (thread.id, *expected)
    if expected[1] == "IGNORED":
        assert inbox.snapshot(db, thread.id) == before
        assert entries(db, thread.id) == audit
    else:
        assert entries(db, thread.id) == audit + [("STATUS", "call", "WAITING_FOR_CUSTOMER", "NEW_REPLY", None)]
