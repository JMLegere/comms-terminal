"""The change log and Change sets behind the laptop's sync (ADR 0012, Phase 1 of the 2026-10-29 sync PRD): every
row change is logged, /sync sends only the rows changed since a cursor, and anything it cannot
answer that way still gets the whole snapshot."""

import json
from datetime import datetime, timedelta, timezone

from schema_history import downgrade
from test_console import seed_demo  # noqa: F401 - test_console puts scripts/ on the path

from comms_terminal import clock, inbox, remote


def demo(tmp_path):
    path = str(tmp_path / "inbox.db")
    db = inbox.connect(path)
    ids = seed_demo.seed(db)
    return path, db, ids


def sync(path, **req):
    return remote.handle_sync(path, remote.Changes(path), json.dumps(req).encode(), wait=0)


def triggers(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type = 'trigger' AND name LIKE 'changes_%'")}


def tables(db):
    return [r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type = 'table'"
                                     " AND name NOT LIKE 'sqlite_%' AND name NOT IN ('changes', 'applied_mutations')")]


def test_every_table_logs_its_changes_fresh_and_after_an_upgrade(tmp_path):
    fresh = inbox.connect()
    inbox.migrate(fresh)
    want = {f"changes_{t}_{e}" for t in tables(fresh) for e in ("insert", "update", "delete")}
    assert len(tables(fresh)) >= 25 and triggers(fresh) == want

    old = inbox.connect(str(tmp_path / "old.db"))
    inbox.migrate(old)
    downgrade(old, 31)
    assert not triggers(old) and "changes" not in tables(old) + [r[0] for r in old.execute(
        "SELECT name FROM sqlite_schema WHERE name = 'changes'")]
    inbox.migrate(old)
    assert triggers(old) == want


def test_a_change_set_carries_only_the_rows_changed_since_the_cursor(tmp_path):
    path, db, ids = demo(tmp_path)
    at = remote.cursor(db)
    tid = inbox.queue(db, "Todo")[0]["id"]
    inbox.act(db, tid, inbox.Done())

    status, body = sync(path, cursor=at, schema=inbox.SCHEMA_VERSION)

    assert status == 200 and "db" not in body
    assert body["cursor"] == remote.cursor(db) > at
    thread = [c for c in body["changes"] if c["tbl"] == "threads"]
    assert [c["row"]["id"] for c in thread] == [tid] and thread[0]["row"]["status"] == "DONE"
    assert len(json.dumps(body)) < 10_000  # NFR-2: one change costs kilobytes, not the 1.5 MB snapshot


def test_a_deleted_row_comes_as_none_and_each_row_once(tmp_path):
    path, db, _ = demo(tmp_path)
    values = {"connection_id": "1", "kind": "email", "sender": "me@x.io", "to": "ana@acme.com", "cc": "",
              "subject": "Hi", "body": "one"}
    draft = inbox.save_draft(db, values)
    at = remote.cursor(db)
    inbox.save_draft(db, dict(values, body="two"), draft_id=draft["id"], expected_revision=draft["revision"])
    with db:
        db.execute("DELETE FROM composition_drafts WHERE id = ?", (draft["id"],))

    rows = remote.change_set(db, at)

    assert [(c["tbl"], c["row"]) for c in rows if c["tbl"] == "composition_drafts"] == [("composition_drafts", None)]


def test_a_new_message_brings_its_raw_name(tmp_path):
    path, db, _ = demo(tmp_path)
    at = remote.cursor(db)
    with db:
        tid = db.execute("SELECT id FROM threads LIMIT 1").fetchone()[0]
        db.execute("INSERT INTO activities (thread_id, kind, occurred_at, native_id, content, data)"
                   " VALUES (?, 'MESSAGE', ?, 'probe-1', 'hi', ?)",
                   (tid, clock.iso(datetime.now(timezone.utc)), json.dumps({"raw": "abc123"})))

    _, body = sync(path, cursor=at, schema=inbox.SCHEMA_VERSION)

    assert body["raw"] == ["abc123.eml"]


def test_old_clients_old_cursors_and_other_schemas_get_the_snapshot(tmp_path):
    path, db, _ = demo(tmp_path)
    assert "db" in sync(path)[1] and sync(path)[1]["cursor"] == remote.cursor(db)  # today's console: unchanged
    assert "db" in sync(path, cursor=remote.cursor(db), schema=inbox.SCHEMA_VERSION - 1)[1]
    assert "db" in sync(path, cursor=remote.cursor(db) + 5, schema=inbox.SCHEMA_VERSION)[1]  # ahead of the log

    old = remote.cursor(db)
    with db:
        db.execute("UPDATE changes SET at = '2000-01-01T00:00:00.000000+00:00'")
    inbox.act(db, inbox.queue(db, "Todo")[0]["id"], inbox.Done())
    assert inbox.prune_changes(db, datetime.now(timezone.utc)) > 0
    assert "db" in sync(path, cursor=0, schema=inbox.SCHEMA_VERSION)[1]  # older than the kept log
    assert "changes" in sync(path, cursor=old, schema=inbox.SCHEMA_VERSION)[1]  # still inside it


def test_prune_keeps_seven_days(tmp_path):
    _, db, _ = demo(tmp_path)
    now = datetime(2026, 10, 30, tzinfo=timezone.utc)
    with db:
        db.execute("DELETE FROM changes")
        for days in (1, 6, 8, 30):
            db.execute("INSERT INTO changes (tbl, row_id, op, at) VALUES ('threads', 1, 'U', ?)",
                       (clock.iso(now - timedelta(days=days)),))
    assert inbox.prune_changes(db, now) == 2
    assert db.execute("SELECT count(*) FROM changes").fetchone()[0] == 2
