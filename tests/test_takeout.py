"""Takeout import contract using synthetic mail only; no provider access."""

import hashlib
import json
import mailbox
import sqlite3
import stat
import sys
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from zipfile import ZipFile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import import_takeout  # noqa: E402

from universal_inbox import email, inbox  # noqa: E402

SINCE = datetime(2026, 9, 12, tzinfo=timezone.utc)
UNTIL = datetime(2026, 10, 12, 16, tzinfo=timezone.utc)
SELF = "me@fixture.invalid"


def _id(key):
    return f"<{key}@fixture.invalid>"


def _mail(key, date="Thu, 01 Oct 2026 00:00:00 +0000", *, sender="sender@fixture.invalid",
          reply=None, body=None):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, SELF, f"Fixture {key}"
    msg["Message-ID"] = _id(key)
    if date is not None:
        msg["Date"] = date
    if reply:
        msg["In-Reply-To"] = msg["References"] = _id(reply)
    msg.set_content(body or f"Synthetic body {key}")
    return msg


def _archive(tmp_path, labels):
    path, raw = tmp_path / "takeout.zip", {}
    with ZipFile(path, "w") as archive:
        for label, messages in labels.items():
            mbox_path = tmp_path / f"{label}.mbox"
            box = mailbox.mbox(mbox_path)
            try:
                keys = [(box.add(msg), str(msg["Message-ID"])) for msg in messages]
                box.flush()
                for key, native_id in keys:
                    raw[native_id] = box.get_bytes(key)
                archive.write(mbox_path, f"Takeout/Mail/{label}.mbox")
            finally:
                box.close()
    return path, raw


def _database(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    path = target / "inbox.db"
    db = inbox.connect(str(path))
    inbox.migrate(db)
    with db:
        db.executemany(
            "INSERT INTO connections (id, platform, external_id, self_id, label, connected_at, last_error)"
            " VALUES (?, 'gmail', ?, ?, 'Fixture', ?, 'fixture health error')",
            [(1, SELF, SELF, SINCE.isoformat()),
             (2, "other@fixture.invalid", "other@fixture.invalid", SINCE.isoformat())])
    return path, db


def _seed(db, key, action, connection_id=1):
    connection = db.execute("SELECT * FROM connections WHERE id = ?", (connection_id,)).fetchone()
    raw = _mail(key, "Thu, 10 Sep 2026 00:00:00 +0000").as_bytes()
    incoming = email.parse(raw, connection)
    incoming.data["raw"] = inbox.store_raw(db, raw)
    thread = inbox.ingest(db, incoming)
    inbox.act(db, thread.id, action)
    return thread.id


def _files(directory):
    return {str(p.relative_to(directory)): (stat.S_IMODE(p.stat().st_mode), p.read_bytes())
            for p in directory.rglob("*") if p.is_file()}


def _rows(db, table):
    return [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY id")]


def test_takeout_preview_apply_threading_scope_and_repeat(tmp_path):
    path, db = _database(tmp_path)
    done = _seed(db, "done-root", inbox.Done())
    snoozed = _seed(db, "snoozed-root", inbox.Snooze(UNTIL + timedelta(days=1)))
    duplicate = _seed(db, "existing", inbox.Ignore())
    untouched_done = _seed(db, "untouched-done", inbox.Done())
    untouched_snooze = _seed(db, "untouched-snooze", inbox.Snooze())
    foreign = _seed(db, "foreign-root", inbox.Wait(), connection_id=2)
    untouched = {thread_id: inbox.snapshot(db, thread_id)
                 for thread_id in (duplicate, untouched_done, untouched_snooze, foreign)}
    connections = _rows(db, "connections")
    old_activity_ids = {row[0] for row in db.execute("SELECT id FROM activities")}
    db.close()

    root = _mail("root", "Sat, 12 Sep 2026 03:00:00 +0300")  # Inclusive SINCE in UTC.
    root.add_attachment(b"tiny attachment", maintype="application", subtype="octet-stream", filename="tiny.bin")
    automated = _mail("automated", "Tue, 06 Oct 2026 00:00:00 +0000")
    automated["Auto-Submitted"] = "auto-replied"
    archive, raw = _archive(tmp_path, {
        "Inbox": [
            _mail("reply", "Sat, 10 Oct 2026 00:00:00 +0000", reply="root"),
            _mail("existing", "Mon, 05 Oct 2026 00:00:00 +0000"),
            _mail("foreign-reference", "Sun, 04 Oct 2026 00:00:00 +0000", reply="foreign-root"),
            _mail("before", "Fri, 11 Sep 2026 23:59:59 +0000"),
            _mail("end", "Mon, 12 Oct 2026 12:00:00 -0400"),  # Exclusive UNTIL in UTC.
            _mail("future", "Tue, 13 Oct 2026 00:00:00 +0000"),
            _mail("invalid", "not a date"),
            _mail("missing", None),
            _mail("naive", "Thu, 01 Oct 2026 00:00:00 -0000"),
            automated,
            root,
        ],
        "Sent": [
            _mail("echo", "Fri, 02 Oct 2026 00:00:01 +0000", sender=SELF, reply="done-root", body="Same reply"),
            _mail("snoozed-reply", "Sat, 03 Oct 2026 00:00:00 +0000", sender=SELF, reply="snoozed-root"),
            _mail("done-reply", "Fri, 02 Oct 2026 00:00:00 +0000", sender=SELF, reply="done-root", body="Same reply"),
            _mail("sent-only", sender=SELF),
            root,
        ],
        "Archived": [_mail("archived")],
    })

    before = _files(path.parent)
    preview = import_takeout.run(archive, path, 1, SINCE, UNTIL)
    assert _files(path.parent) == before
    expected = {"scanned": 16, "outside_range": 3, "invalid_date": 3,
                "filtered": 2, "duplicates": 2, "stored": 6,
                "existing_threads_changed": 2, "verified_todo": 5}
    assert {key: preview[key] for key in expected} == expected
    assert len(preview["threads"]) == 5

    offset = timezone(timedelta(hours=-4))
    applied = import_takeout.run(archive, path, 1, SINCE.astimezone(offset), UNTIL.astimezone(offset), apply=True)
    assert {key: applied[key] for key in expected} == expected
    assert set(applied["threads"]) == set(preview["threads"])
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    affected = set(applied["threads"])
    assert {done, snoozed} <= affected
    assert affected.isdisjoint(untouched)
    for thread_id in affected:
        state = inbox.snapshot(db, thread_id)
        assert state["status"] == "TODO"
        assert state["snoozed_until"] is None and state["detail_before_snooze"] is None
    assert {thread_id: inbox.snapshot(db, thread_id) for thread_id in untouched} == untouched
    assert _rows(db, "connections") == connections

    new = {row["native_id"]: row for row in db.execute("SELECT * FROM activities ORDER BY id")
           if row["id"] not in old_activity_ids}
    assert set(new) == {_id(key) for key in ("root", "reply", "sent-only", "done-reply", "snoozed-reply", "foreign-reference")}
    assert new[_id("root")]["thread_id"] == new[_id("reply")]["thread_id"]
    assert new[_id("done-reply")]["thread_id"] == done
    assert new[_id("snoozed-reply")]["thread_id"] == snoozed
    assert new[_id("sent-only")]["identity_id"] is None
    channel = db.execute("SELECT c.connection_id FROM threads t JOIN channels c ON c.id = t.channel_id"
                         " WHERE t.id = ?", (new[_id("foreign-reference")]["thread_id"],)).fetchone()
    assert channel[0] == 1
    occurred = [datetime.fromisoformat(row["occurred_at"]) for row in new.values()]
    assert occurred == sorted(occurred)
    assert datetime.fromisoformat(new[_id("root")]["occurred_at"]) == SINCE
    for native_id, row in new.items():
        sha = hashlib.sha256(raw[native_id]).hexdigest()
        assert json.loads(row["data"])["raw"] == sha
        saved = path.parent / "raw" / f"{sha}.eml"
        assert saved.read_bytes() == raw[native_id]
        assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    expected_raw = {Path(name).name for name in before if name.startswith("raw/")}
    expected_raw |= {f"{hashlib.sha256(raw[native_id]).hexdigest()}.eml" for native_id in new}
    assert {p.name for p in (path.parent / "raw").iterdir()} == expected_raw

    inbox.act(db, new[_id("sent-only")]["thread_id"], inbox.Done())
    inbox.act(db, new[_id("root")]["thread_id"], inbox.Snooze())
    states = _rows(db, "threads")
    activities = _rows(db, "activities")
    db.close()
    before_repeat = _files(path.parent)
    repeated = import_takeout.run(archive, path, 1, SINCE, UNTIL, apply=True)
    assert repeated["stored"] == 0 and repeated["duplicates"] == 8
    assert repeated["threads"] == [] and repeated["existing_threads_changed"] == 0
    assert repeated["verified_todo"] == 0
    assert _files(path.parent) == before_repeat
    db = sqlite3.connect(path)
    assert _rows(db, "threads") == states and _rows(db, "activities") == activities
    db.close()


@pytest.mark.parametrize("since,until", [(SINCE.replace(tzinfo=None), UNTIL),
                                         (SINCE, UNTIL.replace(tzinfo=None)), (UNTIL, SINCE)])
def test_takeout_rejects_invalid_bounds_without_mutation(tmp_path, since, until):
    path, db = _database(tmp_path)
    db.close()
    archive, _ = _archive(tmp_path, {"Inbox": [_mail("fixture")], "Sent": []})
    before = _files(path.parent)
    with pytest.raises(ValueError):
        import_takeout.run(archive, path, 1, since, until, apply=True)
    assert _files(path.parent) == before


def test_takeout_requires_existing_database_and_connection(tmp_path):
    archive, _ = _archive(tmp_path, {"Inbox": [_mail("fixture")], "Sent": []})
    missing = tmp_path / "missing.db"
    with pytest.raises((ValueError, FileNotFoundError, sqlite3.OperationalError)):
        import_takeout.run(archive, missing, 1, SINCE, UNTIL, apply=True)
    assert not missing.exists() and not (tmp_path / "raw").exists()
    path, db = _database(tmp_path)
    db.close()
    before = _files(path.parent)
    with pytest.raises(ValueError):
        import_takeout.run(archive, path, 99, SINCE, UNTIL, apply=True)
    assert _files(path.parent) == before
