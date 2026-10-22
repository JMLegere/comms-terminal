"""Exact-reference reconciliation through public inbox calls; no provider or live DB access."""

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from comms_terminal import email, files, inbox

AT = datetime(2026, 10, 12, 10, tzinfo=timezone.utc)
SELF = "me@example.test"
OTHER = "other@example.test"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(inbox, "_now", lambda: AT + timedelta(days=1))
    db = inbox.connect(str(tmp_path / "inbox.db"))
    inbox.migrate(db)
    with db:
        db.executemany(
            "INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [(1, "gmail", SELF, SELF, "Mail", AT.isoformat()),
             (2, "gmail", OTHER, OTHER, "Other mail", AT.isoformat()),
             (3, "slack", "TTEST", "USELF", "Slack", AT.isoformat())],
        )
    yield db
    db.close()


def incoming(mid, refs=(), **changes):
    return replace(inbox.Incoming(
        1, SELF, "mailbox", "MESSAGE", mid, inbox.Identity("email", "ana@acme.test"),
        AT, "Same body", list(refs), "Same subject",
        {"from": "ana@acme.test", "to": [SELF], "cc": [], "fm_thread": "same-provider-thread"},
    ), **changes)


def historical(db, mid, refs="", **changes):
    """Pre-index import: raw RFC headers survived, declared Thread Ref did not."""
    raw = (f"From: ana@acme.test\r\nTo: {SELF}\r\nMessage-ID: {mid}\r\n"
           f"Date: Mon, 12 Oct 2026 10:00:00 +0000\r\nSubject: Same subject\r\n"
           f"References: {refs}\r\nContent-Type: text/plain\r\n\r\nSame body").encode()
    connection = db.execute("SELECT * FROM connections WHERE id = 1").fetchone()
    parsed = email.parse(raw, connection)
    return replace(parsed, thread_ref=[], data={**parsed.data, "raw": files.store_raw(db, raw)}, **changes)


def activities(db):
    return [tuple(row) for row in db.execute("SELECT * FROM activities ORDER BY id")]


def persisted(db):
    """The dump without audit log rows: undo restores state and appends its own entry."""
    return "\n".join(line for line in db.iterdump() if not line.startswith('INSERT INTO "audit_log"'))


def raw_bytes(db):
    directory = files.raw_dir(db)
    return {p.name: p.read_bytes() for p in directory.iterdir()} if directory.exists() else {}


def message_ids(db, thread_id):
    return [m["native_id"] for m in inbox.thread_view(db, thread_id)["messages"]]


def triage(db, thread_id):
    return tuple(db.execute(
        "SELECT status, status_detail, status_changed_at, snoozed_until, detail_before_snooze, follow_up_at"
        " FROM threads WHERE id = ?", (thread_id,),
    ).fetchone())


def test_late_parent_keeps_original_thread_identity_and_combines_history(db):
    child = inbox.ingest(db, incoming("<child@test>", ["<parent@test>"]))
    original = tuple(db.execute(
        "SELECT title, created_at, native_thread_id FROM threads WHERE id = ?", (child.id,),
    ).fetchone())
    parent = inbox.ingest(db, incoming("<parent@test>", occurred_at=AT - timedelta(hours=1), title="Parent"))
    assert parent.id == child.id
    assert message_ids(db, child.id) == ["<parent@test>", "<child@test>"]
    assert tuple(db.execute(
        "SELECT title, created_at, native_thread_id FROM threads WHERE id = ?", (child.id,),
    ).fetchone()) == original


def test_bridge_uses_lowest_id_and_flattens_existing_aliases(db):
    one = inbox.ingest(db, incoming("<one@test>"))
    two = inbox.ingest(db, incoming("<two@test>", occurred_at=AT + timedelta(minutes=1)))
    three = inbox.ingest(db, incoming("<three@test>", occurred_at=AT + timedelta(minutes=2)))
    inbox.act(db, three.id, inbox.Investigate())
    inbox.act(db, three.id, inbox.Snooze(AT + timedelta(days=5)))
    selected = triage(db, three.id)
    audit = [tuple(r) for r in db.execute("SELECT * FROM audit_log ORDER BY id")]
    early = activities(db)
    bridge = incoming("<bridge@test>", ["<three@test>", "<two@test>"], occurred_at=AT + timedelta(minutes=3))
    inbox.block(db, bridge.sender.kind, bridge.sender.value)
    assert inbox.ingest(db, bridge).id == two.id
    assert triage(db, two.id) == selected
    assert [tuple(r) for r in db.execute("SELECT * FROM audit_log ORDER BY id")] == audit
    assert inbox.ingest(db, replace(bridge, thread_ref=["<one@test>", "<three@test>"])) is None
    assert [inbox.get_thread(db, t.id).id for t in (one, two, three)] == [one.id] * 3
    assert [tuple(r) for r in db.execute("SELECT id, merged_into FROM threads ORDER BY id")] == [
        (one.id, None), (two.id, one.id), (three.id, one.id),
    ]
    assert activities(db)[:3] == early
    assert message_ids(db, three.id) == ["<one@test>", "<two@test>", "<three@test>", "<bridge@test>"]
    assert triage(db, one.id) == selected
    assert [tuple(r) for r in db.execute("SELECT * FROM audit_log ORDER BY id")] == audit


def test_shared_absent_ancestor_merges_but_subject_sender_body_and_provider_thread_do_not(db):
    one = inbox.ingest(db, incoming("<one@test>", ["<absent@test>"]))
    same = inbox.ingest(db, incoming("<same@test>"))
    two = inbox.ingest(db, incoming("<two@test>", ["<absent@test>"]))
    assert two.id == one.id != same.id
    assert message_ids(db, one.id) == ["<one@test>", "<two@test>"]
    assert message_ids(db, same.id) == ["<same@test>"]
    assert inbox.view_counts(db)["All"] == 2


@pytest.mark.parametrize("mid", ["bare@test", "<bad id@test>", "<no-at>"])
def test_unsafe_or_unbracketed_identifiers_are_not_reference_evidence(db, mid):
    first = inbox.ingest(db, incoming(mid))
    second = inbox.ingest(db, incoming("<child@test>", [mid]))
    assert first.id != second.id
    assert inbox.reconcile_email_threads(db, apply=True)["merged"] == 0


@pytest.mark.parametrize("header", ["References", "In-Reply-To"])
def test_email_parse_does_not_turn_comment_contents_into_references(db, header):
    root = inbox.ingest(db, incoming("<root@test>"))
    raw = (f"From: ana@acme.test\r\nTo: {SELF}\r\nMessage-ID: <child@test>\r\n"
           f"{header}: (comment <root@test> ) <bad id@test> garbage\r\n\r\nHello").encode()
    parsed = email.parse(raw, db.execute("SELECT * FROM connections WHERE id = 1").fetchone())
    child = inbox.ingest(db, replace(parsed, occurred_at=AT))
    assert child.id != root.id
    assert inbox.reconcile_email_threads(db, apply=True)["merged"] == 0


@pytest.mark.parametrize("header", ["References", "In-Reply-To"])
@pytest.mark.parametrize("value", ['"mentions <root@test>"', "mentions <root@test>"])
def test_email_header_prose_cannot_supply_exact_reference_evidence(db, header, value):
    root = inbox.ingest(db, incoming("<root@test>"))
    raw = (f"From: ana@acme.test\r\nTo: {SELF}\r\nMessage-ID: <child@test>\r\n"
           f"{header}: {value}\r\n\r\nHello").encode()
    parsed = email.parse(raw, db.execute("SELECT * FROM connections WHERE id = 1").fetchone())
    child = inbox.ingest(db, replace(parsed, occurred_at=AT,
                                    data={**parsed.data, "raw": files.store_raw(db, raw)}))
    assert child.id != root.id
    physical, stored_raw = activities(db), raw_bytes(db)
    result = inbox.reconcile_email_threads(db, apply=True)
    assert (result["groups"], result["merged"], result["references_added"]) == ([], 0, 0)
    assert message_ids(db, root.id) == ["<root@test>"]
    assert message_ids(db, child.id) == ["<child@test>"]
    assert activities(db) == physical and raw_bytes(db) == stored_raw


@pytest.mark.parametrize("quoted", [
    '"mentions <root@test>"',
    '"mentions \\"<root@test>\\""',
    '"mentions (<root@test>)"',
])
def test_email_parse_keeps_only_references_outside_quoted_prose(db, quoted):
    root = inbox.ingest(db, incoming("<root@test>"))
    real = inbox.ingest(db, incoming("<real@test>"))
    raw = (f"From: ana@acme.test\r\nTo: {SELF}\r\nMessage-ID: <child@test>\r\n"
           f"References: {quoted} <real@test>\r\n\r\nHello").encode()
    parsed = email.parse(raw, db.execute("SELECT * FROM connections WHERE id = 1").fetchone())
    child = inbox.ingest(db, replace(parsed, occurred_at=AT + timedelta(minutes=1)))
    assert child.id == real.id != root.id
    assert message_ids(db, root.id) == ["<root@test>"]
    assert message_ids(db, real.id) == ["<real@test>", "<child@test>"]


def test_identifier_case_is_preserved_when_header_syntax_is_normalized(db):
    root = inbox.ingest(db, incoming("<Root@test>"))
    lower = inbox.ingest(db, incoming("<lower@test>", ["<root@test>"]))
    exact = inbox.ingest(db, incoming("<exact@test>", ["(ignored) <Root@test>"]))
    assert exact.id == root.id != lower.id
    assert message_ids(db, root.id) == ["<Root@test>", "<exact@test>"]


@pytest.mark.parametrize("connection_id,channel", [(2, OTHER), (1, "alias@example.test")])
def test_known_foreign_mailbox_id_is_not_a_shared_virtual_ancestor(db, connection_id, channel):
    root = inbox.ingest(db, incoming("<root@test>"))
    child = inbox.ingest(db, incoming("<child@test>", ["<root@test>"], connection_id=connection_id, channel=channel))
    sibling = inbox.ingest(db, incoming("<sibling@test>", ["<root@test>"], connection_id=connection_id, channel=channel))
    assert len({root.id, child.id, sibling.id}) == 3
    assert inbox.reconcile_email_threads(db, apply=True)["groups"] == []
    for thread, mid in ((root, "<root@test>"), (child, "<child@test>"), (sibling, "<sibling@test>")):
        assert message_ids(db, thread.id) == [mid]


def test_cross_mailbox_duplicate_cannot_add_evidence_to_original_activity(db):
    one = inbox.ingest(db, incoming("<one@test>"))
    original = incoming("<copy@test>")
    two = inbox.ingest(db, original)
    foreign = inbox.ingest(db, incoming("<foreign@test>", connection_id=2, channel=OTHER))
    before = activities(db)
    assert inbox.ingest(db, replace(original, connection_id=2, channel=OTHER,
                                    thread_ref=["<one@test>", "<foreign@test>"])) is None
    result = inbox.reconcile_email_threads(db, apply=True)
    assert (result["groups"], result["merged"], result["references_added"]) == ([], 0, 0)
    assert [inbox.get_thread(db, t.id).id for t in (one, two, foreign)] == [one.id, two.id, foreign.id]
    assert activities(db) == before


def test_duplicate_adds_exact_evidence_without_rewriting_or_replaying_status(db):
    root = inbox.ingest(db, incoming("<root@test>"))
    original = incoming("<child@test>", occurred_at=AT + timedelta(minutes=1))
    child = inbox.ingest(db, original)
    inbox.act(db, child.id, inbox.Ignore())
    state, before = triage(db, child.id), activities(db)
    duplicate = replace(original, thread_ref=["<root@test>"], content="Replacement forbidden",
                        sender=None, occurred_at=AT + timedelta(days=30), data={"replacement": True})
    assert inbox.ingest(db, duplicate) is None
    assert inbox.get_thread(db, child.id).id == root.id
    assert triage(db, root.id) == state
    assert activities(db) == before
    assert inbox.ingest(db, duplicate) is None
    assert triage(db, root.id) == state
    assert inbox.reconcile_email_threads(db, apply=True)["merged"] == 0


def test_raw_preview_is_immutable_apply_is_atomic_and_idempotent(db):
    root = inbox.ingest(db, historical(db, "<root@test>"))
    child = inbox.ingest(db, historical(db, "<child@test>", "<root@test>", occurred_at=AT + timedelta(minutes=1)))
    before, physical, raw = persisted(db), activities(db), raw_bytes(db)
    preview = inbox.reconcile_email_threads(db)
    assert preview["groups"] == [{"keep": root.id, "merge": [child.id], "status_source": child.id, "case_id": None}]
    assert (preview["conflicts"], preview["merged"], preview["references_added"]) == ([], 0, 0)
    assert persisted(db) == before and raw_bytes(db) == raw
    applied = inbox.reconcile_email_threads(db, apply=True, channel_id=root.channel_id)
    assert applied["groups"] == preview["groups"]
    assert (applied["merged"], applied["references_added"]) == (1, 1)
    assert activities(db) == physical and raw_bytes(db) == raw
    assert message_ids(db, child.id) == ["<root@test>", "<child@test>"]
    after = persisted(db)
    again = inbox.reconcile_email_threads(db, apply=True)
    assert (again["groups"], again["conflicts"], again["merged"], again["references_added"]) == ([], [], 0, 0)
    assert persisted(db) == after and activities(db) == physical and raw_bytes(db) == raw
    for sql in ("UPDATE activities SET thread_id = 999", "UPDATE activities SET content = 'changed'", "DELETE FROM activities"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            db.execute(sql)


def test_preview_keeps_one_wal_snapshot_and_preserves_callers_pending_transaction(db, tmp_path):
    root = inbox.ingest(db, historical(db, "<root@test>"))
    child = inbox.ingest(db, historical(db, "<child@test>", "<root@test>"))
    expected = inbox.reconcile_email_threads(db)
    physical, raw = activities(db), raw_bytes(db)
    writer = inbox.connect(str(tmp_path / "inbox.db"))
    merged = []

    def merge_between_reads(sql):
        if "SELECT activity_id, native_id FROM email_refs" in sql:
            db.set_trace_callback(None)
            try:
                merged.append(inbox.reconcile_email_threads(writer, apply=True)["merged"])
            except Exception as exc:
                merged.append(exc)

    db.set_trace_callback(merge_between_reads)
    try:
        preview = inbox.reconcile_email_threads(db)
    finally:
        db.set_trace_callback(None)
        writer.close()
    assert merged == [1] and preview == expected
    assert not db.in_transaction
    assert inbox.get_thread(db, child.id).id == root.id
    after_writer = persisted(db)
    db.execute("UPDATE connections SET label = 'Caller pending' WHERE id = 1")
    pending = persisted(db)
    assert inbox.reconcile_email_threads(db)["groups"] == []
    assert db.in_transaction and persisted(db) == pending
    db.rollback()
    assert persisted(db) == after_writer and activities(db) == physical and raw_bytes(db) == raw


def test_thread_view_keeps_history_and_membership_coherent_across_wal_alias_change(db, tmp_path):
    root = inbox.ingest(db, historical(db, "<root@test>"))
    child = inbox.ingest(db, historical(db, "<child@test>", "<root@test>", occurred_at=AT + timedelta(minutes=1)))
    expected = inbox.thread_view(db, child.id)
    assert expected["members"] == [child.id]
    physical, raw = activities(db), raw_bytes(db)
    writer = inbox.connect(str(tmp_path / "inbox.db"))
    merged = []

    def merge_before_history_read(sql):
        if sql.startswith("SELECT * FROM thread_activities WHERE thread_id"):
            db.set_trace_callback(None)
            try:
                merged.append(inbox.reconcile_email_threads(writer, apply=True)["merged"])
            except Exception as exc:
                merged.append(exc)

    db.set_trace_callback(merge_before_history_read)
    try:
        view = inbox.thread_view(db, child.id)
    finally:
        db.set_trace_callback(None)
        writer.close()
    assert merged == [1] and view == expected
    assert view["thread"].id == child.id and view["members"] == [child.id]
    assert [m["native_id"] for m in view["messages"]] == ["<child@test>"]
    assert not db.in_transaction
    current = inbox.thread_view(db, child.id)
    assert current["thread"].id == root.id and current["members"] == [root.id, child.id]
    assert [m["native_id"] for m in current["messages"]] == ["<root@test>", "<child@test>"]
    assert activities(db) == physical and raw_bytes(db) == raw


def test_failed_apply_rolls_back_recovered_references_and_aliases_together(db):
    root = inbox.ingest(db, historical(db, "<root@test>"))
    child = inbox.ingest(db, historical(db, "<child@test>", "<root@test>"))
    with db:
        db.execute("CREATE TRIGGER reject_alias BEFORE UPDATE OF merged_into ON threads"
                   " WHEN NEW.merged_into IS NOT NULL BEGIN SELECT RAISE(ABORT, 'fixture rejects alias'); END")
    before, raw = persisted(db), raw_bytes(db)
    with pytest.raises(sqlite3.DatabaseError, match="fixture rejects alias"):
        inbox.reconcile_email_threads(db, apply=True)
    assert persisted(db) == before and raw_bytes(db) == raw
    assert [inbox.get_thread(db, t.id).id for t in (root, child)] == [root.id, child.id]
    with db:
        db.execute("DROP TRIGGER reject_alias")
    applied = inbox.reconcile_email_threads(db, apply=True)
    assert (applied["merged"], applied["references_added"]) == (1, 1)


@pytest.mark.parametrize("pointer", [None, "0" * 64, "../outside.eml", "corrupt"])
def test_missing_unsafe_or_hash_mismatched_raw_is_held_without_guessing(db, tmp_path, pointer):
    root = inbox.ingest(db, incoming("<root@test>"))
    raw = b"Message-ID: <child@test>\r\nReferences: <root@test>\r\n\r\nHello"
    (tmp_path / "outside.eml").write_bytes(raw)
    if pointer == "corrupt":
        pointer = files.store_raw(db, raw)
        (files.raw_dir(db) / f"{pointer}.eml").write_bytes(raw + b"tampered")
    child = inbox.ingest(db, incoming("<child@test>", data={"raw": pointer}))
    before, raw_before = activities(db), raw_bytes(db)
    result = inbox.reconcile_email_threads(db, apply=True)
    assert (result["groups"], result["merged"], result["references_added"]) == ([], 0, 0)
    assert inbox.get_thread(db, child.id).id != root.id
    assert activities(db) == before and raw_bytes(db) == raw_before


def test_raw_recovery_reads_headers_not_body(db):
    root = inbox.ingest(db, incoming("<root@test>"))
    raw = b"Message-ID: <child@test>\r\n\r\nReferences: <root@test>\r\nquoted mail"
    child = inbox.ingest(db, incoming("<child@test>", data={"raw": files.store_raw(db, raw)}))
    assert inbox.reconcile_email_threads(db, apply=True)["merged"] == 0
    assert inbox.get_thread(db, child.id).id != root.id


@pytest.mark.parametrize("status,detail,until,before,follow_up", [
    ("SNOOZED", "WAITING_FOR_DURATION", "2026-10-20T10:00:00.000000+00:00", "IN_PROGRESS", None),
    ("SNOOZED", "WAITING_FOR_CUSTOMER", None, None, "2026-10-20T10:00:00.000000+00:00"),
    ("TODO", "IN_PROGRESS", None, None, None),
    ("DONE", "DONE_MANUALLY_SET", None, None, None),
])
def test_latest_complete_triage_state_wins_and_retired_threads_do_not_wake(db, status, detail, until, before, follow_up):
    root = inbox.ingest(db, historical(db, "<root@test>"))
    child = inbox.ingest(db, historical(db, "<child@test>", "<root@test>"))
    stable = tuple(db.execute("SELECT title, created_at, native_thread_id FROM threads WHERE id = ?", (root.id,)).fetchone())
    changed = "2026-10-13T10:00:00.000000+00:00"
    with db:
        db.execute("UPDATE threads SET status = 'SNOOZED', status_detail = 'WAITING_FOR_DURATION',"
                   " snoozed_until = '2026-10-19T10:00:00.000000+00:00', detail_before_snooze = 'CREATED' WHERE id = ?", (root.id,))
        db.execute("UPDATE threads SET status = ?, status_detail = ?, status_changed_at = ?, snoozed_until = ?,"
                   " detail_before_snooze = ?, follow_up_at = ? WHERE id = ?",
                   (status, detail, changed, until, before, follow_up, child.id))
    result = inbox.reconcile_email_threads(db, apply=True)
    assert result["groups"][0]["status_source"] == child.id
    assert triage(db, root.id) == (status, detail, changed, until, before, follow_up)
    assert tuple(db.execute("SELECT title, created_at, native_thread_id FROM threads WHERE id = ?", (root.id,)).fetchone()) == stable
    assert inbox.get_thread(db, child.id).follow_up_at == follow_up
    if until or follow_up:
        due = datetime.fromisoformat(until or follow_up)
        assert inbox.wake(db, due) == 1
        assert inbox.get_thread(db, child.id).status_detail == (before if until else "NEW_REPLY")
        assert inbox.wake(db, due) == 0


def test_equal_triage_timestamps_choose_lower_thread_id(db):
    root = inbox.ingest(db, historical(db, "<root@test>"))
    child = inbox.ingest(db, historical(db, "<child@test>", "<root@test>"))
    with db:
        db.execute("UPDATE threads SET status = 'DONE', status_detail = 'DONE_MANUALLY_SET' WHERE id = ?", (root.id,))
    result = inbox.reconcile_email_threads(db, apply=True)
    assert result["groups"][0]["status_source"] == root.id
    assert (inbox.get_thread(db, child.id).status, inbox.get_thread(db, child.id).status_detail) == ("DONE", "DONE_MANUALLY_SET")


def test_multiple_cases_skip_entire_component_until_conflict_is_resolved(db):
    one = inbox.ingest(db, historical(db, "<one@test>"))
    two = inbox.ingest(db, historical(db, "<two@test>"))
    bridge = inbox.ingest(db, historical(db, "<bridge@test>", "<two@test> <one@test>"))
    case_one = inbox.act(db, one.id, inbox.SetCase(new_title="One")).case_id
    case_two = inbox.act(db, two.id, inbox.SetCase(new_title="Two")).case_id
    ids = [one.id, two.id, bridge.id]
    states, physical = [triage(db, tid) for tid in ids], activities(db)
    preview = inbox.reconcile_email_threads(db)
    assert preview["groups"] == [] and preview["conflicts"] == [ids]
    result = inbox.reconcile_email_threads(db, apply=True)
    assert (result["groups"], result["conflicts"], result["merged"]) == ([], [ids], 0)
    assert [inbox.get_thread(db, tid).id for tid in ids] == ids
    assert [triage(db, tid) for tid in ids] == states and activities(db) == physical
    assert inbox.get_thread(db, one.id).case_id == case_one
    assert inbox.get_thread(db, two.id).case_id == case_two
    inbox.act(db, two.id, inbox.SetCase(case_id=case_one))
    resolved = inbox.reconcile_email_threads(db, apply=True)
    assert (resolved["merged"], resolved["conflicts"]) == (2, [])
    assert [t.id for t in inbox.case_threads(db, case_one)] == [one.id]
    assert inbox.case_threads(db, case_two) == []
    assert activities(db) == physical


def test_case_conflicting_bridge_attaches_to_first_explicit_parent_without_merging(db):
    one = inbox.ingest(db, historical(db, "<one@test>"))
    two = inbox.ingest(db, historical(db, "<two@test>"))
    case_one = inbox.act(db, one.id, inbox.SetCase(new_title="One")).case_id
    case_two = inbox.act(db, two.id, inbox.SetCase(new_title="Two")).case_id
    untouched, physical = inbox.snapshot(db, one.id), activities(db)
    bridge = replace(historical(db, "<bridge@test>", "<two@test> <one@test>",
                                occurred_at=AT + timedelta(minutes=1)),
                     thread_ref=["<two@test>", "<one@test>"])
    raw = raw_bytes(db)
    attached = inbox.ingest(db, bridge)
    assert attached.id == two.id and attached.case_id == case_two
    assert inbox.get_thread(db, one.id).case_id == case_one
    assert inbox.snapshot(db, one.id) == untouched
    assert [inbox.get_thread(db, t.id).id for t in (one, two)] == [one.id, two.id]
    assert message_ids(db, one.id) == ["<one@test>"]
    assert message_ids(db, two.id) == ["<two@test>", "<bridge@test>"]
    assert db.execute("SELECT thread_id FROM activities WHERE native_id = '<bridge@test>'").fetchone()[0] == two.id
    assert activities(db)[:len(physical)] == physical and raw_bytes(db) == raw
    before = persisted(db)
    result = inbox.reconcile_email_threads(db, apply=True)
    assert (result["groups"], result["conflicts"], result["merged"]) == ([], [[one.id, two.id]], 0)
    assert persisted(db) == before and raw_bytes(db) == raw


class RecordingSender:
    def send(self, thread, text, to, cc, history, files=()):
        self.thread, self.history = thread, history
        self.arguments = text, to, cc
        return "<sent@test>"


def test_old_ids_keep_get_act_reply_snapshot_restore_and_logical_records_working(db):
    one = inbox.ingest(db, incoming("<one@test>"))
    two = inbox.ingest(db, incoming("<two@test>", occurred_at=AT + timedelta(minutes=1),
                                   sender=inbox.Identity("email", "bob@acme.test"),
                                   data={"from": "bob@acme.test", "to": [SELF], "cc": []}))
    case_id = inbox.act(db, two.id, inbox.SetCase(new_title="Shared")).case_id
    inbox.act(db, two.id, inbox.Wait())
    stale_snapshot = inbox.snapshot(db, two.id)
    bridge = inbox.ingest(db, incoming("<bridge@test>", ["<two@test>", "<one@test>"], occurred_at=AT + timedelta(minutes=2)))
    assert bridge.id == one.id
    assert inbox.get_thread(db, two.id).case_id == case_id
    assert inbox.snapshot(db, two.id)["id"] == one.id
    before_restore = persisted(db)
    with pytest.raises(inbox.StaleThreadSnapshot):
        inbox.restore(db, stale_snapshot)
    assert persisted(db) == before_restore
    inbox.act(db, two.id, inbox.Wait())
    current_snapshot = inbox.snapshot(db, two.id)
    assert current_snapshot["id"] == one.id
    inbox.act(db, two.id, inbox.MarkTodo("IN_PROGRESS"))
    restored = inbox.restore(db, current_snapshot)
    assert restored.id == one.id and restored.status_detail == "WAITING_FOR_CUSTOMER"
    assert restored.follow_up_at == current_snapshot["follow_up_at"]
    assert inbox.act(db, two.id, inbox.MarkTodo("NEW_REPLY")).id == one.id
    assert [r["id"] for r in inbox.queue(db, "Todo")] == [one.id]
    assert (inbox.view_counts(db)["Todo"], inbox.view_counts(db)["All"]) == (1, 1)
    assert [t.id for t in inbox.case_threads(db, case_id)] == [one.id]
    assert inbox.cases(db) == [{"id": case_id, "title": "Shared", "threads": 1}]
    persons = inbox.people(db)
    assert len(persons) == 2 and all((p["threads"], p["todo"]) == (1, 1) for p in persons)
    for person in persons:
        assert [t.id for t in inbox.person_threads(db, person["id"])] == [one.id]
        assert [t.id for t in inbox.person_view(db, person["id"])["threads"]] == [one.id]
    sender = RecordingSender()
    replied = inbox.reply(db, two.id, "Reply via old link", sender, to=["bob@acme.test"])
    assert replied.id == sender.thread.id == one.id
    assert sender.thread.channel_id == one.channel_id
    assert [m["native_id"] for m in sender.history] == ["<one@test>", "<two@test>", "<bridge@test>"]
    assert sender.arguments == ("Reply via old link", ["bob@acme.test"], [])
    assert message_ids(db, two.id) == ["<one@test>", "<two@test>", "<bridge@test>", "<sent@test>"]
    assert db.execute("SELECT thread_id FROM activities WHERE native_id = '<sent@test>'").fetchone()[0] == one.id
    assert inbox.act(db, two.id, inbox.Ignore()).id == one.id
    assert inbox.act(db, two.id, inbox.Unignore()).status_detail == "NEW_REPLY"


@pytest.mark.parametrize("snapshot_id", ["survivor", "retired"])
@pytest.mark.parametrize("legacy", [False, True], ids=["members", "legacy"])
def test_premerge_snapshot_rejects_without_writes_and_postmerge_snapshot_restores(db, monkeypatch, snapshot_id, legacy):
    root = inbox.ingest(db, historical(db, "<root@test>"))
    child = inbox.ingest(db, historical(db, "<child@test>", "<root@test>"))
    inbox.act(db, root.id, inbox.Wait())
    monkeypatch.setattr(inbox, "_now", lambda: AT + timedelta(days=2))
    inbox.act(db, child.id, inbox.SetCase(new_title="Shared"))
    inbox.act(db, child.id, inbox.Snooze(AT + timedelta(days=7)))
    thread_id = root.id if snapshot_id == "survivor" else child.id
    stale = inbox.snapshot(db, thread_id)
    assert stale["_members"] == [thread_id]
    if legacy:
        stale.pop("_members")
    physical, raw = activities(db), raw_bytes(db)
    assert inbox.reconcile_email_threads(db, apply=True)["merged"] == 1
    after_merge = persisted(db)
    with pytest.raises(inbox.StaleThreadSnapshot):
        inbox.restore(db, stale)
    assert persisted(db) == after_merge and activities(db) == physical and raw_bytes(db) == raw

    current = inbox.snapshot(db, thread_id)
    assert current == inbox.snapshot(db, root.id)
    assert current["id"] == root.id and current["_members"] == [root.id, child.id]
    inbox.act(db, child.id, inbox.MarkTodo("IN_PROGRESS"))
    inbox.act(db, child.id, inbox.SetCase())
    restored = inbox.restore(db, current)
    assert restored == inbox.get_thread(db, root.id)
    assert inbox.snapshot(db, child.id) == current
    assert persisted(db) == after_merge and activities(db) == physical and raw_bytes(db) == raw


def test_legacy_snapshot_restores_unmerged_singleton(db):
    thread = inbox.ingest(db, historical(db, "<singleton@test>"))
    inbox.act(db, thread.id, inbox.SetCase(new_title="Singleton"))
    inbox.act(db, thread.id, inbox.Wait())
    legacy = inbox.snapshot(db, thread.id)
    assert legacy.pop("_members") == [thread.id]
    before, physical, raw = persisted(db), activities(db), raw_bytes(db)
    inbox.act(db, thread.id, inbox.MarkTodo("IN_PROGRESS"))
    inbox.act(db, thread.id, inbox.SetCase())
    restored = inbox.restore(db, legacy)
    assert restored.id == thread.id and restored.follow_up_at == legacy["follow_up_at"]
    assert {key: inbox.snapshot(db, thread.id)[key] for key in legacy} == legacy
    assert persisted(db) == before and activities(db) == physical and raw_bytes(db) == raw


@pytest.mark.parametrize("ignore_during_send", [False, True])
def test_reply_resolves_alias_again_after_sender_returns(db, ignore_during_send):
    root = inbox.ingest(db, historical(db, "<root@test>"))
    child = inbox.ingest(db, historical(db, "<child@test>", "<root@test>", occurred_at=AT + timedelta(minutes=1)))
    child_state = triage(db, child.id)

    class MergingSender(RecordingSender):
        def send(self, thread, text, to, cc, history, files=()):
            assert thread.id == child.id
            assert inbox.reconcile_email_threads(db, apply=True)["merged"] == 1
            if ignore_during_send:
                inbox.act(db, root.id, inbox.Ignore())
            return super().send(thread, text, to, cc, history)

    replied = inbox.reply(db, child.id, "Reply while history reconciles", MergingSender())
    assert replied.id == root.id
    assert replied.status_detail == ("IGNORED" if ignore_during_send else "WAITING_FOR_CUSTOMER")
    assert db.execute("SELECT thread_id FROM activities WHERE native_id = '<sent@test>'").fetchone()[0] == root.id
    assert triage(db, child.id) == child_state
    assert message_ids(db, child.id) == ["<root@test>", "<child@test>", "<sent@test>"]


def test_reply_prepares_coherent_history_before_wal_merge_and_releases_snapshot_before_send(db, tmp_path):
    root = inbox.ingest(db, historical(db, "<root@test>"))
    child = inbox.ingest(db, historical(db, "<child@test>", "<root@test>", occurred_at=AT + timedelta(minutes=1)))
    with db:
        db.executemany("UPDATE connections SET last_error = ? WHERE id = ?",
                       [("Mailbox error", 1), ("Other account error", 2)])
    physical, raw, child_state = activities(db), raw_bytes(db), triage(db, child.id)
    writer = inbox.connect(str(tmp_path / "inbox.db"))
    merged = []

    def merge_before_history_read(sql):
        if sql.startswith("SELECT native_id, data, content, occurred_at"):
            db.set_trace_callback(None)
            try:
                merged.append(inbox.reconcile_email_threads(writer, apply=True)["merged"])
            except Exception as exc:
                merged.append(exc)

    class CoherentSender(RecordingSender):
        def send(self, thread, text, to, cc, history, files=()):
            assert merged == [1] and not db.in_transaction
            assert thread.id == child.id and thread.channel_id == child.channel_id
            assert [m["native_id"] for m in history] == ["<child@test>"]
            assert history[0]["data"]["to"] == [SELF]
            return super().send(thread, text, to, cc, history)

    sender = CoherentSender()
    db.set_trace_callback(merge_before_history_read)
    try:
        replied = inbox.reply(db, child.id, "Prepared reply", sender, to=["ana@acme.test"])
    finally:
        db.set_trace_callback(None)
        writer.close()
    assert replied.id == root.id and replied.status_detail == "WAITING_FOR_CUSTOMER"
    assert sender.arguments == ("Prepared reply", ["ana@acme.test"], [])
    assert db.execute("SELECT thread_id FROM activities WHERE native_id = '<sent@test>'").fetchone()[0] == root.id
    assert [tuple(r) for r in db.execute("SELECT id, last_error FROM connections WHERE platform = 'gmail' ORDER BY id")] == [
        (1, None), (2, "Other account error"),
    ]
    assert triage(db, child.id) == child_state
    assert message_ids(db, child.id) == ["<root@test>", "<child@test>", "<sent@test>"]
    assert activities(db)[:len(physical)] == physical and raw_bytes(db) == raw


# Frozen schema-3 shapes: migration fixture does not read or transform current implementation DDL.
SCHEMA3 = """
CREATE TABLE connections (id INTEGER PRIMARY KEY, platform TEXT NOT NULL CHECK(platform IN ('email','slack')),
 external_id TEXT NOT NULL UNIQUE, self_id TEXT NOT NULL, label TEXT NOT NULL, connected_at TEXT NOT NULL,
 auth_ok INTEGER NOT NULL DEFAULT 1 CHECK(auth_ok IN (0,1)), last_error TEXT) STRICT;
CREATE TABLE channels (id INTEGER PRIMARY KEY, connection_id INTEGER NOT NULL REFERENCES connections(id),
 native_id TEXT NOT NULL, name TEXT, kind TEXT NOT NULL CHECK(kind IN ('mailbox','channel','private','im','mpim')),
 UNIQUE(connection_id,native_id)) STRICT;
CREATE TABLE channel_rules (id INTEGER PRIMARY KEY, connection_id INTEGER NOT NULL REFERENCES connections(id),
 kind TEXT NOT NULL CHECK(kind IN ('channel','prefix','suffix')), value TEXT NOT NULL, UNIQUE(connection_id,kind,value)) STRICT;
CREATE TABLE cases (id INTEGER PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL) STRICT;
CREATE TABLE threads (id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL REFERENCES channels(id),
 native_thread_id TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL, status_detail TEXT NOT NULL,
 status_changed_at TEXT NOT NULL, snoozed_until TEXT, detail_before_snooze TEXT CHECK(detail_before_snooze IN ('CREATED','NEW_REPLY','IN_PROGRESS')),
 case_id INTEGER REFERENCES cases(id), created_at TEXT NOT NULL, follow_up_at TEXT, UNIQUE(channel_id,native_thread_id),
 CHECK((status='TODO' AND status_detail IN ('CREATED','NEW_REPLY','IN_PROGRESS'))
 OR (status='SNOOZED' AND status_detail IN ('WAITING_FOR_CUSTOMER','WAITING_FOR_DURATION','WAITING_INDEFINITELY'))
 OR (status='DONE' AND status_detail IN ('DONE_MANUALLY_SET','IGNORED'))),
 CHECK((status_detail='WAITING_FOR_DURATION')=(snoozed_until IS NOT NULL))) STRICT;
CREATE INDEX threads_queue ON threads(status,status_changed_at);
CREATE INDEX threads_case ON threads(case_id);
CREATE TABLE persons (id INTEGER PRIMARY KEY, display_name TEXT NOT NULL, created_at TEXT NOT NULL) STRICT;
CREATE TABLE identities (id INTEGER PRIMARY KEY, person_id INTEGER REFERENCES persons(id),
 kind TEXT NOT NULL CHECK(kind IN ('email','slack_user')), value TEXT NOT NULL,
 blocked INTEGER NOT NULL DEFAULT 0 CHECK(blocked IN (0,1)), UNIQUE(kind,value)) STRICT;
CREATE INDEX identities_person ON identities(person_id);
CREATE TABLE organizations (id INTEGER PRIMARY KEY, domain TEXT NOT NULL UNIQUE, name TEXT NOT NULL, created_at TEXT NOT NULL) STRICT;
CREATE TABLE activities (id INTEGER PRIMARY KEY, thread_id INTEGER NOT NULL REFERENCES threads(id),
 kind TEXT NOT NULL CHECK(kind IN ('MESSAGE','EDIT','DELETE')), occurred_at TEXT NOT NULL,
 identity_id INTEGER REFERENCES identities(id), native_id TEXT NOT NULL, target_id INTEGER REFERENCES activities(id),
 content TEXT, data TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(data)), UNIQUE(kind,native_id), CHECK((kind='MESSAGE')=(target_id IS NULL))) STRICT;
CREATE INDEX activities_thread ON activities(thread_id,occurred_at);
CREATE INDEX activities_identity ON activities(identity_id);
CREATE TRIGGER activities_no_delete BEFORE DELETE ON activities BEGIN SELECT RAISE(ABORT, 'activities are append-only'); END;
CREATE TRIGGER activities_no_update BEFORE UPDATE ON activities
WHEN NOT (NEW.content IS NULL AND NEW.id IS OLD.id AND NEW.thread_id IS OLD.thread_id
 AND NEW.kind IS OLD.kind AND NEW.occurred_at IS OLD.occurred_at AND NEW.identity_id IS OLD.identity_id
 AND NEW.native_id IS OLD.native_id AND NEW.target_id IS OLD.target_id AND NEW.data IS OLD.data
 AND EXISTS(SELECT 1 FROM activities d WHERE d.kind='DELETE' AND d.target_id=COALESCE(OLD.target_id,OLD.id)))
BEGIN SELECT RAISE(ABORT, 'activities are append-only'); END;
PRAGMA user_version=3;
"""


def test_schema3_migration_preserves_records_raw_and_append_only_guards(tmp_path):
    db = inbox.connect(str(tmp_path / "schema3.db"))
    try:
        db.executescript(SCHEMA3)
        with db:
            db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                       " VALUES (1, 'email', ?, ?, 'Mail', ?)", (SELF, SELF, AT.isoformat()))
            db.execute("INSERT INTO channels (id, connection_id, native_id, kind) VALUES (1, 1, ?, 'mailbox')", (SELF,))
            for tid, mid in ((1, "<root@test>"), (2, "<child@test>")):
                raw = (f"Message-ID: {mid}\r\nReferences: " + ("<root@test>" if tid == 2 else "") + "\r\n\r\nHello").encode()
                pointer = files.store_raw(db, raw)
                db.execute("INSERT INTO threads (id, channel_id, native_thread_id, title, status, status_detail, status_changed_at, created_at)"
                           " VALUES (?, 1, ?, 'Original', 'TODO', 'CREATED', ?, ?)", (tid, mid, AT.isoformat(), AT.isoformat()))
                db.execute("INSERT INTO activities (id, thread_id, kind, occurred_at, native_id, content, data)"
                           " VALUES (?, ?, 'MESSAGE', ?, ?, 'Original body', ?)", (tid, tid, AT.isoformat(), mid, json.dumps({"raw": pointer})))
        physical, raw = activities(db), raw_bytes(db)
        inbox.migrate(db)
        assert db.execute("PRAGMA user_version").fetchone()[0] == inbox.SCHEMA_VERSION
        assert activities(db) == physical and raw_bytes(db) == raw
        assert [inbox.get_thread(db, tid).id for tid in (1, 2)] == [1, 2]
        assert inbox.reconcile_email_threads(db, apply=True)["merged"] == 1
        assert inbox.get_thread(db, 2).id == 1 and message_ids(db, 2) == ["<root@test>", "<child@test>"]
        assert activities(db) == physical and raw_bytes(db) == raw
        for sql in ("UPDATE activities SET thread_id = 2 WHERE id = 1", "DELETE FROM activities"):
            with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                db.execute(sql)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        before = persisted(db)
        inbox.migrate(db)
        assert persisted(db) == before
    finally:
        db.close()


def test_migrate_rejects_newer_schema_without_downgrading_or_mutating(db):
    inbox.ingest(db, incoming("<root@test>"))
    with db:
        db.execute(f"PRAGMA user_version = {inbox.SCHEMA_VERSION + 1}")
    before = persisted(db)
    with pytest.raises((ValueError, RuntimeError)):
        inbox.migrate(db)
    assert db.execute("PRAGMA user_version").fetchone()[0] == inbox.SCHEMA_VERSION + 1
    assert persisted(db) == before


def test_reconcile_rejects_unknown_and_non_mailbox_scope_without_mutation(db):
    root = inbox.ingest(db, incoming("<root@test>"))
    slack = inbox.ingest(db, replace(incoming("C1:1"), connection_id=3, channel="C1", channel_kind="channel",
                                   sender=inbox.Identity("slack_user", "UANA")))
    before = persisted(db)
    for channel_id in (999, slack.channel_id):
        with pytest.raises(ValueError):
            inbox.reconcile_email_threads(db, apply=True, channel_id=channel_id)
    assert persisted(db) == before
    assert inbox.get_thread(db, root.id).id == root.id


def test_takeout_late_parent_shared_ancestor_and_bridge_count_logical_threads(tmp_path):
    from test_takeout import SINCE, UNTIL, _archive, _database, _mail, import_takeout

    path, db = _database(tmp_path)
    before = persisted(db)
    db.close()
    bridge = _mail("bridge", "Wed, 07 Oct 2026 00:00:00 +0000")
    bridge["References"] = "<split-two@fixture.invalid> <split-one@fixture.invalid>"
    archive, _ = _archive(tmp_path, {"Inbox": [
        _mail("late-child", "Thu, 01 Oct 2026 00:00:00 +0000", reply="late-parent"),
        _mail("late-parent", "Fri, 02 Oct 2026 00:00:00 +0000"),
        _mail("shared-one", "Sat, 03 Oct 2026 00:00:00 +0000", reply="absent"),
        _mail("shared-two", "Sun, 04 Oct 2026 00:00:00 +0000", reply="absent"),
        _mail("split-one", "Mon, 05 Oct 2026 00:00:00 +0000"),
        _mail("split-two", "Tue, 06 Oct 2026 00:00:00 +0000"),
        bridge,
    ], "Sent": []})
    preview = import_takeout.run(archive, path, 1, SINCE, UNTIL)
    db = inbox.connect(str(path))
    try:
        assert persisted(db) == before and raw_bytes(db) == {}
        applied = import_takeout.run(archive, path, 1, SINCE, UNTIL, apply=True)
        assert applied == preview
        assert (applied["stored"], applied["duplicates"], applied["verified_todo"]) == (7, 0, 3)
        assert len(applied["threads"]) == 3
        assert {frozenset(message_ids(db, tid)) for tid in applied["threads"]} == {
            frozenset(("<late-child@fixture.invalid>", "<late-parent@fixture.invalid>")),
            frozenset(("<shared-one@fixture.invalid>", "<shared-two@fixture.invalid>")),
            frozenset(("<split-one@fixture.invalid>", "<split-two@fixture.invalid>", "<bridge@fixture.invalid>")),
        }
        assert (inbox.view_counts(db)["Todo"], inbox.view_counts(db)["All"]) == (3, 3)
        physical, raw = activities(db), raw_bytes(db)
        repeated = import_takeout.run(archive, path, 1, SINCE, UNTIL, apply=True)
        assert (repeated["stored"], repeated["duplicates"], repeated["threads"], repeated["verified_todo"]) == (0, 7, [], 0)
        assert activities(db) == physical and raw_bytes(db) == raw
    finally:
        db.close()
