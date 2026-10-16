"""Audit log: every Status or Case change of a Thread is recorded with its cause and shown in the timeline."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from universal_inbox import inbox
from universal_inbox.tui import audit_markup

NOW = datetime(2026, 10, 15, 15, 0, tzinfo=timezone.utc)
ANA = inbox.Identity("email", "ana@acme.com", "Ana")


def mail(native_id, at, sender=ANA, refs=()):
    return inbox.Incoming(1, "me@x.io", "mailbox", "MESSAGE", native_id, sender, at, "hi", thread_ref=list(refs))


def entries(db, thread_id):
    return [(e["kind"], e["cause"], e["old_detail"], e["new_detail"], e["case_title"])
            for e in inbox.thread_view(db, thread_id)["audit"]]


def test_status_and_case_changes_are_logged_with_their_cause(monkeypatch):
    db = inbox.connect()
    inbox.migrate(db)
    with db:
        db.execute("INSERT INTO connections (platform, external_id, self_id, label, connected_at)"
                   " VALUES ('gmail', 'me@x.io', 'me@x.io', 'Email', ?)", (NOW.isoformat(),))
    minutes = iter(range(1, 1000))
    clock = lambda: NOW + timedelta(minutes=next(minutes))  # noqa: E731
    monkeypatch.setattr(inbox, "_now", clock)
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
        db.execute("INSERT INTO connections (platform, external_id, self_id, label, connected_at)"
                   " VALUES ('gmail', 'me@x.io', 'me@x.io', 'Email', ?)", (NOW.isoformat(),))
    minutes = iter(range(1, 1000))
    monkeypatch.setattr(inbox, "_now", lambda: NOW + timedelta(minutes=next(minutes)))
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
