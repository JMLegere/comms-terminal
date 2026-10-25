"""Signed receipt metadata and exact correlation; fake senders only, no network or live data."""

import itertools
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from urllib.parse import quote

import pytest

from comms_terminal import email, files, inbox, platforms
from test_email_reconcile import (  # noqa: F401
    AT, OTHER, SELF, RecordingSender, activities, db, historical, incoming,
    message_ids, persisted, raw_bytes, triage,
)

EMAIL_ID = "a1111111-b111-4111-8111-c11111111111"
NEXT_ID = "22222222-2222-4222-8222-222222222222"
MARKER = f"<{EMAIL_ID}@resend>"
WIRE = "<Delivered.Case-Sensitive@amazonses.test>"
ATTEMPT = AT + timedelta(days=1)


def draft(db):
    return inbox.save_draft(db, {"connection_id": 1, "kind": "email", "sender": SELF,
                                  "to": "ana@acme.test", "cc": "", "subject": "Checklist", "body": "Hi"})


class StartSender:
    def start(self, *args, **kwargs):
        return MARKER


def outbound(db, **changes):
    return inbox.ingest(db, incoming(MARKER, sender=None, occurred_at=ATTEMPT, **changes))


def receipt(db):
    return inbox.record_resend_receipt(db, 1, EMAIL_ID, WIRE)


def staged_file(db, **owner):
    sha = files.store_file(db, b"private attachment")
    inbox.stage(db, sha=sha, name="private.txt", type="text/plain", size=18, **owner)
    return files.attachment_path(db, sha)


def test_receipt_is_minimal_durable_idempotent_and_does_not_create_work(db, tmp_path):
    assert inbox.resend_native_id(EMAIL_ID.upper()) == MARKER
    assert inbox.record_resend_receipt(db, 1, EMAIL_ID.upper(), WIRE) is True
    assert not db.in_transaction
    reader = inbox.connect(str(tmp_path / "inbox.db"))
    try:
        assert [tuple(r) for r in reader.execute("SELECT * FROM resend_receipts")] == [(EMAIL_ID, 1, WIRE)]
        assert reader.execute("SELECT count(*) FROM activities").fetchone()[0] == 0
        assert reader.execute("SELECT count(*) FROM threads").fetchone()[0] == 0
    finally:
        reader.close()
    before = persisted(db)
    assert receipt(db) is False
    for connection_id, message_id in ((2, WIRE), (1, "<Different@amazonses.test>"), (1, WIRE.lower())):
        with pytest.raises(inbox.ResendReceiptConflict):
            inbox.record_resend_receipt(db, connection_id, EMAIL_ID, message_id)
        assert not db.in_transaction and persisted(db) == before


@pytest.mark.parametrize("value", [None, {}, 7, "", "not-a-uuid", EMAIL_ID.replace("-", ""), " " + EMAIL_ID])
def test_invalid_provider_ids_cannot_become_local_or_receipt_identity(db, value):
    before = persisted(db)
    with pytest.raises(ValueError):
        inbox.resend_native_id(value)
    with pytest.raises(ValueError):
        inbox.record_resend_receipt(db, 1, value, WIRE)
    assert persisted(db) == before


@pytest.mark.parametrize("value", [None, {}, "", "bare@test", "<bad id@test>", "<<a@test>>",
                                   "<a@test> <b@test>", "(comment)<a@test>", "<a@test>\r\n", MARKER])
def test_receipt_requires_one_strict_smtp_id(db, value):
    before = persisted(db)
    with pytest.raises(ValueError):
        inbox.record_resend_receipt(db, 1, EMAIL_ID, value)
    assert persisted(db) == before


@pytest.mark.parametrize("connection_id", [True, 0, -1, 3, 999])
def test_receipt_requires_an_explicit_eligible_gmail_connection(db, connection_id):
    before = persisted(db)
    with pytest.raises(ValueError):
        inbox.record_resend_receipt(db, connection_id, EMAIL_ID, WIRE)
    assert persisted(db) == before


@pytest.mark.parametrize("field", ["self_id", "external_id"])
def test_receipt_rejects_malformed_gmail_ownership(db, field):
    with db:
        db.execute(f"UPDATE connections SET {field} = ' ' WHERE id = 1")
    before = persisted(db)
    with pytest.raises(ValueError, match="Gmail Connection"):
        receipt(db)
    assert persisted(db) == before


def test_receipt_rejects_caller_transaction_without_committing_or_rolling_it_back(db):
    db.execute("UPDATE connections SET label = 'Caller pending' WHERE id = 1")
    with pytest.raises(ValueError, match="idle connection"):
        receipt(db)
    assert db.in_transaction
    assert db.execute("SELECT label FROM connections WHERE id = 1").fetchone()[0] == "Caller pending"
    assert db.execute("SELECT count(*) FROM resend_receipts").fetchone()[0] == 0
    db.rollback()
    assert db.execute("SELECT label FROM connections WHERE id = 1").fetchone()[0] == "Mail"


@pytest.mark.parametrize("conflict", [False, True])
def test_receipt_writers_serialize_duplicate_and_conflicting_uuid(db, tmp_path, conflict):
    barrier = threading.Barrier(2)
    values = [WIRE, "<Other@amazonses.test>" if conflict else WIRE]

    def store(value):
        writer = inbox.connect(str(tmp_path / "inbox.db"))
        try:
            barrier.wait(timeout=5)
            try:
                return inbox.record_resend_receipt(writer, 1, EMAIL_ID, value)
            except inbox.ResendReceiptConflict:
                return "conflict"
        finally:
            writer.close()

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(store, values))
    assert sum(value is True for value in results) == 1
    assert ("conflict" in results) is conflict
    assert (False in results) is not conflict
    stored = db.execute("SELECT message_id FROM resend_receipts").fetchone()[0]
    assert stored in values
    with pytest.raises(inbox.ResendReceiptConflict):
        inbox.record_resend_receipt(db, 1, EMAIL_ID, "<Never-replace@amazonses.test>")
    assert db.execute("SELECT message_id FROM resend_receipts").fetchone()[0] == stored


@pytest.mark.parametrize("order", list(itertools.permutations("ROI")), ids=lambda order: "".join(order))
def test_every_receipt_outgoing_incoming_order_survives_restart(db, tmp_path, order):
    d = draft(db)
    path = str(tmp_path / "inbox.db")
    try:
        for event in order:
            if event == "R":
                assert receipt(db) is True
            elif event == "O":
                inbox.send_draft(db, d["id"], d["revision"], StartSender())
            else:
                inc = historical(db, "<child@test>", WIRE, occurred_at=ATTEMPT + timedelta(minutes=1))
                inbox.ingest(db, replace(inc, thread_ref=[WIRE]))
            db.close()
            db = inbox.connect(path)
            inbox.reconcile_resend_receipts(db)
        physical, raw = activities(db), raw_bytes(db)
        original_ids = [r[0] for r in db.execute("SELECT id FROM threads ORDER BY id")]
        canonical = inbox.get_thread(db, original_ids[0])
        assert all(inbox.get_thread(db, tid).id == canonical.id for tid in original_ids)
        assert message_ids(db, canonical.id) == [MARKER, "<child@test>"]
        assert canonical.status == "TODO"
        assert inbox.list_drafts(db) == []
        assert receipt(db) is False
        inbox.reconcile_resend_receipts(db)
        assert activities(db) == physical and raw_bytes(db) == raw
        assert inbox.get_thread(db, canonical.id).status == "TODO"
    finally:
        db.close()


def test_receipt_reconciliation_and_public_apply_preserve_outer_rollback(db):
    parent = outbound(db)
    child = inbox.ingest(db, incoming("<child@test>", [WIRE]))
    receipt(db)
    before = persisted(db)
    db.execute("UPDATE connections SET label = 'Caller pending' WHERE id = 1")
    inbox.reconcile_resend_receipts(db)
    assert db.in_transaction and inbox.get_thread(db, child.id).id == parent.id
    db.rollback()
    assert persisted(db) == before
    db.execute("UPDATE connections SET label = 'Caller pending' WHERE id = 1")
    assert inbox.reconcile_email_threads(db, apply=True)["merged"] == 1
    assert db.in_transaction
    db.rollback()
    assert persisted(db) == before


def test_receipt_survives_failed_reconcile_and_retries_without_raw_recovery(db, monkeypatch):
    parent = outbound(db)
    child = inbox.ingest(db, incoming("<child@test>", [WIRE]))
    receipt(db)
    with db:
        db.execute("CREATE TRIGGER reject_receipt_alias BEFORE UPDATE OF merged_into ON threads"
                   " WHEN NEW.merged_into IS NOT NULL BEGIN SELECT RAISE(ABORT, 'alias blocked'); END")
    before = persisted(db)
    monkeypatch.setattr(files, "raw_dir", lambda db: pytest.fail("receipt wake must not read raw files"))
    with pytest.raises(sqlite3.DatabaseError, match="alias blocked"):
        inbox.reconcile_resend_receipts(db)
    assert persisted(db) == before and inbox.get_thread(db, child.id).id == child.id
    with db:
        db.execute("DROP TRIGGER reject_receipt_alias")
    inbox.reconcile_resend_receipts(db)
    assert inbox.get_thread(db, child.id).id == parent.id
    assert db.execute("SELECT message_id FROM resend_receipts").fetchone()[0] == WIRE


@pytest.mark.parametrize("raw_id", [MARKER, WIRE], ids=["original-singleton", "alias-is-not-raw-identity"])
def test_raw_recovery_compares_original_native_singleton_not_expanded_aliases(db, raw_id):
    parent = inbox.ingest(db, incoming("<ancestor@test>"))
    inc = historical(db, raw_id, "<ancestor@test>", sender=None, occurred_at=ATTEMPT)
    child = inbox.ingest(db, replace(inc, native_id=MARKER))
    receipt(db)
    physical, raw = activities(db), raw_bytes(db)
    result = inbox.reconcile_email_threads(db, apply=True)
    assert result["raw_invalid"] == (raw_id != MARKER)
    assert result["merged"] == (raw_id == MARKER)
    assert inbox.get_thread(db, child.id).id == (parent.id if raw_id == MARKER else child.id)
    assert activities(db) == physical and raw_bytes(db) == raw


@pytest.mark.parametrize("connection_id,channel,sender", [
    (1, SELF, inbox.Identity("email", "ana@acme.test")),
    (2, OTHER, None),
])
def test_only_bound_outbound_self_activity_consumes_receipt(db, connection_id, channel, sender):
    parent = inbox.ingest(db, incoming(MARKER, connection_id=connection_id, channel=channel, sender=sender))
    receipt(db)
    child = inbox.ingest(db, incoming("<child@test>", [WIRE], connection_id=connection_id, channel=channel))
    inbox.reconcile_resend_receipts(db)
    assert child.id != parent.id
    assert inbox.thread_view(db, parent.id)["messages"][0]["wire_id"] == (MARKER if sender is not None else None)


def test_alias_is_known_in_foreign_mailbox_and_cannot_be_a_shared_absent_ancestor(db):
    parent = outbound(db)
    receipt(db)
    children = [inbox.ingest(db, incoming(f"<{name}@test>", [WIRE], connection_id=2, channel=OTHER))
                for name in ("one", "two")]
    result = inbox.reconcile_email_threads(db, apply=True)
    assert result["held_references"] == 2 and result["groups"] == []
    assert len({parent.id, *(thread.id for thread in children)}) == 3


def test_receipt_merge_preserves_cases_latest_triage_old_ids_and_immutable_activity(db, monkeypatch):
    parent = outbound(db)
    child = inbox.ingest(db, incoming("<child@test>", [WIRE], occurred_at=ATTEMPT + timedelta(minutes=1)))
    case = inbox.act(db, parent.id, inbox.SetCase(new_title="Shared")).case_id
    other = inbox.act(db, child.id, inbox.SetCase(new_title="Conflicting")).case_id
    monkeypatch.setattr(inbox, "_now", lambda: ATTEMPT + timedelta(hours=1))
    inbox.act(db, child.id, inbox.Snooze(ATTEMPT + timedelta(days=7)))
    state, physical, raw = triage(db, child.id), activities(db), raw_bytes(db)
    receipt(db)
    inbox.reconcile_resend_receipts(db)
    assert inbox.get_thread(db, child.id).id == child.id
    assert inbox.reconcile_email_threads(db)["conflicts"] == [[parent.id, child.id]]
    inbox.act(db, child.id, inbox.SetCase(case_id=case))
    inbox.reconcile_resend_receipts(db)
    assert inbox.get_thread(db, child.id).id == parent.id
    assert inbox.get_thread(db, child.id).case_id == case
    assert inbox.case_threads(db, other) == []
    assert triage(db, parent.id) == state
    assert activities(db) == physical and raw_bytes(db) == raw
    after = persisted(db)
    assert receipt(db) is False
    inbox.reconcile_resend_receipts(db)
    assert persisted(db) == after


def test_pending_parent_refuses_before_sender_and_keeps_files_and_stored_identity(db):
    parent = outbound(db)
    path = staged_file(db, thread_id=parent.id)
    sender = RecordingSender()
    before = persisted(db)
    with pytest.raises(ValueError, match="confirmation pending"):
        inbox.reply(db, parent.id, "Keep this text", sender)
    assert not hasattr(sender, "history") and persisted(db) == before
    message = inbox.thread_view(db, parent.id)["messages"][0]
    assert message["native_id"] == MARKER and message["wire_id"] is None and message["link"] is None
    assert inbox.staged(db, thread_id=parent.id) and path.read_bytes() == b"private attachment"


def test_resolved_gmail_history_and_link_use_wire_id_without_public_native_rewrite(db):
    parent = outbound(db)
    receipt(db)
    physical = activities(db)
    message = inbox.thread_view(db, parent.id)["messages"][0]
    assert message["native_id"] == MARKER and message["wire_id"] == WIRE
    assert message["link"].endswith(quote(WIRE.strip("<>")))
    assert platforms.PLATFORMS["gmail"].link({"native_id": "<legacy@test>"}, {}) is not None
    posts = []
    sender = email.EmailSender("fake", SELF, post=lambda *args: posts.append(args) or (200, {"id": NEXT_ID}))
    inbox.reply(db, parent.id, "Reply", sender, to=["ana@acme.test"])
    headers = posts[0][2]["headers"]
    assert headers["In-Reply-To"] == WIRE and headers["References"] == WIRE
    assert "@resend>" not in str(headers)
    assert message_ids(db, parent.id) == [MARKER, inbox.resend_native_id(NEXT_ID)]
    assert activities(db)[:len(physical)] == physical


def test_earlier_pending_id_is_omitted_but_quotable_history_is_retained(db):
    root = inbox.ingest(db, incoming("<root@test>"))

    class PendingSender(RecordingSender):
        def send(self, *args, **kwargs):
            return MARKER

    inbox.reply(db, root.id, "Earlier outgoing", PendingSender())
    inbox.ingest(db, incoming("<new-inbound@test>", ["<root@test>"], content="Quote this reply",
                              occurred_at=ATTEMPT + timedelta(minutes=1)))
    posts = []
    sender = email.EmailSender("fake", SELF, post=lambda *args: posts.append(args) or (200, {"id": NEXT_ID}))
    inbox.reply(db, root.id, "Answer", sender, to=["ana@acme.test"])
    payload = posts[0][2]
    assert payload["headers"]["In-Reply-To"] == "<new-inbound@test>"
    assert payload["headers"]["References"] == "<root@test> <new-inbound@test>"
    assert "Quote this reply" in payload["text"] and "@resend>" not in str(payload["headers"])


def test_fastmail_and_generic_direct_ids_keep_existing_sender_contract(db):
    with db:
        db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                   " VALUES (4, 'fastmail', 'fast@test', 'fast@test', 'Fastmail', ?)", (AT.isoformat(),))
    with pytest.raises(ValueError, match="Gmail Connection"):
        inbox.record_resend_receipt(db, 4, EMAIL_ID, WIRE)
    receipt(db)
    parent = inbox.ingest(db, incoming(MARKER, connection_id=4, channel="fast@test", sender=None))
    child = inbox.ingest(db, incoming("<fast-child@test>", [WIRE], connection_id=4, channel="fast@test"))
    inbox.reconcile_resend_receipts(db)
    assert inbox.get_thread(db, parent.id).id != inbox.get_thread(db, child.id).id
    sender = RecordingSender()
    inbox.reply(db, parent.id, "No Resend projection on Fastmail", sender)
    assert sender.history[0]["native_id"] == MARKER
    assert "wire_id" not in inbox.thread_view(db, parent.id)["messages"][0]
    direct = inbox.ingest(db, incoming("<direct@test>"))
    inbox.reply(db, direct.id, "Direct sender ID", sender)
    assert sender.history[0]["native_id"] == "<direct@test>"


@pytest.mark.parametrize("error", [inbox.SendFailed, inbox.SendUnknown])
@pytest.mark.parametrize("kind", ["draft", "reply"])
def test_refused_and_unknown_outcomes_keep_staged_files(db, error, kind):
    class FailedSender:
        def start(self, *args, **kwargs):
            raise error("fake outcome")

        send = start

    if kind == "draft":
        d = draft(db)
        owner = {"draft_id": d["id"]}
        send = lambda: inbox.send_draft(db, d["id"], d["revision"], FailedSender())
    else:
        parent = inbox.ingest(db, incoming("<root@test>"))
        owner = {"thread_id": parent.id}
        send = lambda: inbox.reply(db, parent.id, "Keep text", FailedSender())
    path = staged_file(db, **owner)
    physical = activities(db)
    with pytest.raises(error):
        send()
    assert inbox.staged(db, **owner) and path.read_bytes() == b"private attachment"
    assert activities(db) == physical
    if kind == "draft":
        assert bool(inbox.get_draft(db, d["id"])["sending_at"]) is (error is inbox.SendUnknown)


@pytest.mark.parametrize("receipt_first", [False, True])
@pytest.mark.parametrize("action", [None, inbox.Investigate(), inbox.Done(), inbox.Snooze(ATTEMPT + timedelta(days=7))])
def test_initial_send_preserves_incoming_and_user_triage_before_local_record(db, monkeypatch, receipt_first, action):
    d = draft(db)
    seen = {}

    class RacingSender(StartSender):
        def start(self, *args, **kwargs):
            if receipt_first:
                receipt(db)
            # Provider clock may lag: arrival/high-water, not timestamps alone, protects new work.
            child = inbox.ingest(db, incoming("<early@test>", [WIRE], occurred_at=ATTEMPT - timedelta(seconds=1)))
            monkeypatch.setattr(inbox, "_now", lambda: ATTEMPT + timedelta(hours=1))
            inbox.act(db, child.id, inbox.SetCase(new_title="Arrived during send"))
            if action is not None:
                inbox.act(db, child.id, action)
            seen["id"], seen["state"] = child.id, triage(db, child.id)
            if not receipt_first:
                receipt(db)
            return MARKER

    result = inbox.send_draft(db, d["id"], d["revision"], RacingSender())
    assert result.id == inbox.get_thread(db, seen["id"]).id
    assert triage(db, result.id) == seen["state"]
    assert result.case_id is not None
    outgoing = db.execute("SELECT occurred_at FROM activities WHERE native_id = ?", (MARKER,)).fetchone()
    assert outgoing[0] == inbox._iso(ATTEMPT)


@pytest.mark.parametrize("reference", ["<root@test>", WIRE], ids=["unchanged-todo", "new-receipt-parent"])
@pytest.mark.parametrize("receipt_first", [False, True])
def test_reply_preserves_inbound_arrival_even_when_todo_detail_does_not_change(db, monkeypatch, reference, receipt_first):
    root = inbox.ingest(db, incoming("<root@test>"))
    seen = {}

    class RacingSender(RecordingSender):
        def send(self, *args, **kwargs):
            if receipt_first:
                receipt(db)
            child = inbox.ingest(db, incoming("<early@test>", [reference], occurred_at=ATTEMPT + timedelta(minutes=1)))
            seen["id"], seen["state"] = child.id, triage(db, child.id)
            if not receipt_first:
                receipt(db)
            monkeypatch.setattr(inbox, "_now", lambda: ATTEMPT + timedelta(hours=1))
            return MARKER

    result = inbox.reply(db, root.id, "Accepted", RacingSender())
    assert inbox.get_thread(db, seen["id"]).id == result.id == root.id
    assert triage(db, result.id) == seen["state"]
    assert db.execute("SELECT occurred_at FROM activities WHERE native_id = ?", (MARKER,)).fetchone()[0] == inbox._iso(ATTEMPT)


@pytest.mark.parametrize("action", [inbox.Investigate(), inbox.Done(), inbox.Snooze(ATTEMPT + timedelta(days=7))])
def test_reply_preserves_user_triage_during_provider_call(db, monkeypatch, action):
    root = inbox.ingest(db, incoming("<root@test>"))
    seen = {}

    class RacingSender(RecordingSender):
        def send(self, *args, **kwargs):
            monkeypatch.setattr(inbox, "_now", lambda: ATTEMPT + timedelta(hours=1))
            inbox.act(db, root.id, action)
            seen["state"] = triage(db, root.id)
            return MARKER

    result = inbox.reply(db, root.id, "Accepted", RacingSender())
    assert triage(db, result.id) == seen["state"]
    assert db.execute("SELECT occurred_at FROM activities WHERE native_id = ?", (MARKER,)).fetchone()[0] == inbox._iso(ATTEMPT)


@pytest.mark.parametrize("platform", ["quo", "slack"])
def test_non_gmail_start_uses_completion_time_and_joins_inbound_during_provider_call(db, monkeypatch, platform):
    peer, number = "+15550001002", "+15550001001"
    if platform == "quo":
        with db:
            db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                       " VALUES (4, 'quo', ?, ?, 'Quo', ?)", (number, number, AT.isoformat()))
        connection_id, kind, sender_id, destination = 4, "sms", number, peer
        inc = inbox.Incoming(4, peer, "im", "MESSAGE", "quo-in", inbox.Identity("phone", peer),
                             ATTEMPT + timedelta(seconds=1), "During send", data={"peer": peer})
        native_id = "quo-out"
    else:
        connection_id, kind, sender_id, destination = 3, "slack", "USELF", "Sam <D1>"
        inc = inbox.Incoming(3, "D1", "im", "MESSAGE", "D1:1.0", inbox.Identity("slack_user", "USAM"),
                             ATTEMPT + timedelta(seconds=1), "During send",
                             data={"channel": "D1", "ts": "1.0", "kind": "im"})
        native_id = "D1:2.0"
    d = inbox.save_draft(db, {"connection_id": connection_id, "kind": kind, "sender": sender_id,
                             "to": destination, "cc": "", "subject": None, "body": "Outgoing"})
    seen = {}
    completed = ATTEMPT + timedelta(seconds=2)

    class RacingSender:
        def start(self, *args, **kwargs):
            seen["incoming"] = inbox.ingest(db, inc).id
            monkeypatch.setattr(inbox, "_now", lambda: completed)
            return native_id

        def conversation(self, channel):
            return "im", "Sam"

    result = inbox.send_draft(db, d["id"], d["revision"], RacingSender())
    assert result.id == seen["incoming"]
    assert inbox.view_counts(db)["All"] == 1
    assert db.execute("SELECT occurred_at FROM activities WHERE native_id = ?", (native_id,)).fetchone()[0] == inbox._iso(completed)
    data = json.loads(db.execute("SELECT data FROM activities WHERE native_id = ?", (native_id,)).fetchone()[0])
    assert "seen_through_activity_id" not in data


@pytest.mark.parametrize("platform", ["fastmail", "slack", "quo"])
def test_non_gmail_reply_retains_completion_time(db, monkeypatch, platform):
    if platform == "slack":
        inc = inbox.Incoming(3, "D1", "im", "MESSAGE", "D1:1.0", inbox.Identity("slack_user", "USAM"),
                             AT, "Earlier", data={"channel": "D1", "ts": "1.0", "kind": "im"})
    else:
        own = "fast@example.test" if platform == "fastmail" else "+15550001001"
        peer = "ana@acme.test" if platform == "fastmail" else "+15550001002"
        with db:
            db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                       " VALUES (4, ?, ?, ?, ?, ?)", (platform, own, own, platform, AT.isoformat()))
        inc = inbox.Incoming(4, own if platform == "fastmail" else peer,
                             "mailbox" if platform == "fastmail" else "im", "MESSAGE", "<earlier@test>",
                             inbox.Identity("email" if platform == "fastmail" else "phone", peer), AT, "Earlier")
    root = inbox.ingest(db, inc)
    completed = ATTEMPT + timedelta(seconds=2)

    class SlowSender(RecordingSender):
        def send(self, *args, **kwargs):
            monkeypatch.setattr(inbox, "_now", lambda: completed)
            return "<completed@test>"

    result = inbox.reply(db, root.id, "Reply", SlowSender())
    assert result.status_detail == "WAITING_FOR_CUSTOMER"
    assert db.execute("SELECT occurred_at FROM activities WHERE native_id = '<completed@test>'").fetchone()[0] == inbox._iso(completed)
    assert triage(db, result.id)[2] == inbox._iso(completed)
    data = json.loads(db.execute("SELECT data FROM activities WHERE native_id = '<completed@test>'").fetchone()[0])
    assert "seen_through_activity_id" not in data


def test_incoming_uuid_shaped_resend_id_is_authoritative_wire_identity(db):
    parent = inbox.ingest(db, incoming(MARKER))
    receipt(db)  # An incoming message must not consume another outbound send's association.
    posts = []
    sender = email.EmailSender("fake", SELF, post=lambda *args: posts.append(args) or (200, {"id": NEXT_ID}))
    inbox.reply(db, parent.id, "Answer incoming mail", sender, to=["ana@acme.test"])
    headers = posts[0][2]["headers"]
    assert headers["In-Reply-To"] == MARKER and headers["References"] == MARKER
    message = inbox.thread_view(db, parent.id)["messages"][0]
    assert message["native_id"] == message["wire_id"] == MARKER
    assert message["link"].endswith(quote(MARKER.strip("<>")))


def skewed_send_before_receipt(db, kind):
    """A real referenced incoming reply arrives during POST, with an earlier Date, before its receipt."""
    seen = {}

    class RacingSender(StartSender):
        def start(self, *args, **kwargs):
            inc = historical(db, "<skewed@test>", WIRE, occurred_at=ATTEMPT - timedelta(seconds=1))
            child = inbox.ingest(db, replace(inc, thread_ref=[WIRE]))
            inbox.act(db, child.id, inbox.SetCase(new_title="Skewed reply"))
            seen["incoming"], seen["state"] = child.id, triage(db, child.id)
            return MARKER

        send = start

    if kind == "draft":
        d = draft(db)
        sent = inbox.send_draft(db, d["id"], d["revision"], RacingSender())
    else:
        root = inbox.ingest(db, incoming("<original@test>"))
        sent = inbox.reply(db, root.id, "Outgoing reply", RacingSender())
    assert sent.status_detail == "WAITING_FOR_CUSTOMER"
    assert sent.id != seen["incoming"]
    assert db.execute("SELECT count(*) FROM resend_receipts").fetchone()[0] == 0
    outgoing = db.execute("SELECT id, data FROM activities WHERE native_id = ?", (MARKER,)).fetchone()
    seen_through = json.loads(outgoing["data"])["seen_through_activity_id"]
    incoming_id = db.execute("SELECT id FROM activities WHERE native_id = '<skewed@test>'").fetchone()[0]
    assert type(seen_through) is int and 0 <= seen_through < outgoing["id"]
    assert incoming_id > seen_through
    return sent.id, seen["incoming"], seen["state"]


@pytest.mark.parametrize("kind", ["draft", "reply"])
def test_late_receipt_after_local_record_and_restart_uses_exact_reply_causality_despite_date_skew(db, tmp_path, kind):
    sent_id, incoming_id, state = skewed_send_before_receipt(db, kind)
    db.close()
    db = inbox.connect(str(tmp_path / "inbox.db"))
    try:
        assert receipt(db) is True
        db.close()
        db = inbox.connect(str(tmp_path / "inbox.db"))
        physical, raw = activities(db), raw_bytes(db)
        inbox.reconcile_resend_receipts(db)
        canonical = inbox.get_thread(db, sent_id)
        assert canonical.id == inbox.get_thread(db, incoming_id).id
        assert triage(db, canonical.id) == state
        assert canonical.case_id is not None and canonical.status == "TODO"
        assert activities(db) == physical and raw_bytes(db) == raw
        after = persisted(db)
        assert receipt(db) is False
        inbox.reconcile_resend_receipts(db)
        assert persisted(db) == after
    finally:
        db.close()


@pytest.mark.parametrize("kind", ["draft", "reply"])
def test_later_own_reply_stays_waiting_when_earlier_skewed_reply_receipt_arrives(db, tmp_path, monkeypatch, kind):
    sent_id, incoming_id, _ = skewed_send_before_receipt(db, kind)
    monkeypatch.setattr(inbox, "_now", lambda: ATTEMPT + timedelta(hours=1))

    class LaterSender(RecordingSender):
        def send(self, *args, **kwargs):
            return inbox.resend_native_id(NEXT_ID)

    later = inbox.reply(db, incoming_id, "Already answered this reply", LaterSender())
    state = triage(db, later.id)
    db.close()
    db = inbox.connect(str(tmp_path / "inbox.db"))
    try:
        receipt(db)
        inbox.reconcile_resend_receipts(db)
        assert inbox.get_thread(db, sent_id).id == inbox.get_thread(db, incoming_id).id
        assert triage(db, inbox.get_thread(db, sent_id).id) == state
        assert inbox.get_thread(db, sent_id).status_detail == "WAITING_FOR_CUSTOMER"
    finally:
        db.close()


@pytest.mark.parametrize("kind", ["draft", "reply"])
@pytest.mark.parametrize("action", [inbox.Wait(), inbox.Done(), inbox.Snooze(ATTEMPT + timedelta(days=7)),
                                   inbox.Ignore()])
def test_later_user_triage_wins_over_receipt_causality(db, monkeypatch, kind, action):
    sent_id, incoming_id, _ = skewed_send_before_receipt(db, kind)
    monkeypatch.setattr(inbox, "_now", lambda: ATTEMPT + timedelta(hours=1))
    inbox.act(db, sent_id, action)
    state = triage(db, sent_id)
    receipt(db)
    inbox.reconcile_resend_receipts(db)
    assert inbox.get_thread(db, sent_id).id == inbox.get_thread(db, incoming_id).id
    assert triage(db, inbox.get_thread(db, sent_id).id) == state


def test_ordinary_nonreceipt_merge_keeps_existing_date_based_triage_reducer(db):
    parent = inbox.ingest(db, incoming("<direct@test>", sender=None, occurred_at=ATTEMPT))
    child = inbox.ingest(db, historical(db, "<old-date@test>", "<direct@test>",
                                        occurred_at=ATTEMPT - timedelta(seconds=1)))
    state = triage(db, parent.id)
    assert inbox.reconcile_email_threads(db, apply=True)["merged"] == 1
    assert inbox.get_thread(db, child.id).id == parent.id
    assert triage(db, parent.id) == state


@pytest.mark.parametrize("kind", ["draft", "reply"])
def test_overlapping_two_sends_keep_second_unseen_reply_after_late_first_receipt(db, tmp_path, monkeypatch, kind):
    first_id, incoming_id, state = skewed_send_before_receipt(db, kind)
    monkeypatch.setattr(inbox, "_now", lambda: ATTEMPT + timedelta(hours=1))
    high_water = db.execute("SELECT max(id) FROM activities").fetchone()[0]

    class OverlappingSender(RecordingSender):
        def send(self, *args, **kwargs):
            inc = historical(db, "<second-inbound@test>", "<skewed@test>",
                             occurred_at=ATTEMPT - timedelta(milliseconds=500))
            assert inbox.ingest(db, replace(inc, thread_ref=["<skewed@test>"])).id == incoming_id
            assert triage(db, incoming_id) == state  # Todo/Created's detail and timestamp stay unchanged.
            return inbox.resend_native_id(NEXT_ID)

    second = inbox.reply(db, incoming_id, "Second outgoing", OverlappingSender())
    assert second.status == "TODO" and triage(db, second.id) == state
    second_data = json.loads(db.execute("SELECT data FROM activities WHERE native_id = ?",
                                       (inbox.resend_native_id(NEXT_ID),)).fetchone()[0])
    assert second_data["seen_through_activity_id"] == high_water
    assert db.execute("SELECT id FROM activities WHERE native_id = '<second-inbound@test>'").fetchone()[0] > high_water
    db.close()
    db = inbox.connect(str(tmp_path / "inbox.db"))
    try:
        receipt(db)  # O2's receipt deliberately remains absent.
        physical, raw = activities(db), raw_bytes(db)
        inbox.reconcile_resend_receipts(db)
        result = inbox.get_thread(db, first_id)
        assert result.id == inbox.get_thread(db, incoming_id).id
        assert result.status == "TODO" and triage(db, result.id) == state
        assert result.case_id == second.case_id
        assert activities(db) == physical and raw_bytes(db) == raw
        after = persisted(db)
        assert receipt(db) is False
        inbox.reconcile_resend_receipts(db)
        assert persisted(db) == after
    finally:
        db.close()


@pytest.mark.parametrize("seen", ["missing", None, True, -1, "0", 999])
def test_legacy_or_invalid_send_checkpoint_retains_ordinary_reducer(db, seen):
    data = {"from": SELF, "to": ["ana@acme.test"]}
    if seen != "missing":
        data["seen_through_activity_id"] = seen
    parent = outbound(db, data=data)
    child = inbox.ingest(db, incoming("<legacy-skewed@test>", [WIRE],
                                      occurred_at=ATTEMPT - timedelta(seconds=1)))
    state, physical = triage(db, parent.id), activities(db)
    receipt(db)
    inbox.reconcile_resend_receipts(db)
    assert inbox.get_thread(db, child.id).id == parent.id
    assert triage(db, parent.id) == state
    assert activities(db) == physical


def test_checkpoint_override_keeps_receipt_case_conflict_hold(db):
    sent_id, incoming_id, state = skewed_send_before_receipt(db, "draft")
    inbox.act(db, sent_id, inbox.SetCase(new_title="Another Case"))
    receipt(db)
    inbox.reconcile_resend_receipts(db)
    assert inbox.get_thread(db, sent_id).id != inbox.get_thread(db, incoming_id).id
    assert triage(db, incoming_id) == state
    assert inbox.reconcile_email_threads(db)["conflicts"] == [[min(sent_id, incoming_id), max(sent_id, incoming_id)]]


def test_checkpoint_override_keeps_foreign_mailbox_reference_hold(db):
    d = draft(db)
    sent = inbox.send_draft(db, d["id"], d["revision"], StartSender())
    foreign = inbox.ingest(db, incoming("<foreign-skewed@test>", [WIRE], connection_id=2, channel=OTHER,
                                        occurred_at=ATTEMPT - timedelta(seconds=1)))
    receipt(db)
    inbox.reconcile_resend_receipts(db)
    assert inbox.get_thread(db, sent.id).status_detail == "WAITING_FOR_CUSTOMER"
    assert inbox.get_thread(db, foreign.id).id != sent.id
    assert inbox.get_thread(db, foreign.id).status == "TODO"
    assert inbox.reconcile_email_threads(db)["held_references"] == 1
