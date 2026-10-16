"""Import only Google Takeout Inbox and Sent; preview unless --apply is supplied."""

import argparse
import fcntl
import json
import mailbox
import shutil
import sqlite3
import struct
import tempfile
import time
from contextlib import ExitStack, closing
from dataclasses import replace
from datetime import datetime, timezone
from email import policy
from email.parser import BytesHeaderParser
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from zipfile import ZipFile

from universal_inbox import email, files, inbox, platforms

MEMBERS = ("Takeout/Mail/Inbox.mbox", "Takeout/Mail/Sent.mbox")
STATUS_COLUMNS = "status, status_detail, snoozed_until, detail_before_snooze"


def _utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Import dates must be timezone-aware")
    return value.astimezone(timezone.utc)


def _preview(db, path, private):
    snapshot = private / "snapshot.db"
    # ponytail: preview needs idle SQLite connections; use a read-only SQLite VFS for online previews.
    # Linux OFD locks conflict with SQLite's POSIX locks, including same-process readers.
    # Lock SQLite's pending, reserved, and shared bytes; never write the source descriptor.
    lock = struct.pack("hhqqi4x", fcntl.F_WRLCK, 0, 0x40000000, 512, 0)
    # Linux UAPI value; uv's standalone Python may omit the exported constant.
    lock_command = getattr(fcntl, "F_OFD_SETLK", 37)
    with path.open("rb+") as source:
        deadline = time.monotonic() + 5
        while True:
            try:
                fcntl.fcntl(source, lock_command, lock)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise sqlite3.OperationalError("Preview snapshot requires idle database connections") from None
                time.sleep(0.05)
        with snapshot.open("xb") as dest:
            shutil.copyfileobj(source, dest)
        for suffix in ("-wal", "-journal"):
            sidecar = path.with_name(path.name + suffix)
            if sidecar.exists():
                shutil.copyfile(sidecar, str(snapshot) + suffix)
    # SQLite may create WAL/SHM even for mode=ro; only the private copy is opened.
    with closing(sqlite3.connect(snapshot.as_uri() + "?mode=ro", uri=True)) as source:
        source.backup(db)


def run(archive, db_path, connection_id, since, until, apply=False) -> dict:
    """Simulate or apply [since, until) to an existing email Connection; return counts only."""
    if not isinstance(apply, bool):
        raise ValueError("Apply must be a boolean")
    since, until = _utc(since), _utc(until)
    if since >= until:
        raise ValueError("Import range must have since before until")
    path = Path(db_path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError("Import requires an existing database file")
    counts = dict(scanned=0, outside_range=0, invalid_date=0, filtered=0, malformed=0, duplicates=0,
                  stored=0, threads=[], existing_threads_changed=0, verified_todo=0)
    with ExitStack() as stack:
        private = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="universal-inbox-takeout-")))
        if apply:
            db = stack.enter_context(closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True)))
        else:
            db = stack.enter_context(closing(sqlite3.connect(":memory:")))
            _preview(db, path, private)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA busy_timeout = 5000")
        if db.execute("PRAGMA user_version").fetchone()[0] != inbox.SCHEMA_VERSION:
            raise ValueError("Import requires the current database schema")
        connection = db.execute("SELECT * FROM connections WHERE id = ?", (connection_id,)).fetchone()
        if connection is None or connection["platform"] not in platforms.EMAIL:
            raise ValueError("Import requires an existing email Connection")
        self_id = connection["self_id"].lower()
        if (connection["external_id"].lower() != self_id or self_id.count("@") != 1
                or parseaddr(self_id)[1] != self_id):
            raise ValueError("Email Connection mailbox identities must match")
        initial = {r["id"]: tuple(r)[1:] for r in db.execute(f"SELECT id, {STATUS_COLUMNS} FROM threads")}
        boxes, found = [], []
        with ZipFile(archive) as source:
            names = source.namelist()
            if any(names.count(member) != 1 for member in MEMBERS):
                raise ValueError("Archive must contain exactly one Inbox and one Sent mailbox")
            for n, member in enumerate(MEMBERS):
                extracted = private / f"{n}.mbox"
                with source.open(member) as zipped, extracted.open("xb") as dest:
                    shutil.copyfileobj(zipped, dest)
                box = mailbox.mbox(extracted, create=False)
                stack.callback(box.close)
                boxes.append(box)
                for key in box.iterkeys():
                    counts["scanned"] += 1
                    headers = BytesHeaderParser(policy=policy.compat32).parsebytes(box.get_bytes(key))
                    try:
                        dates = headers.get_all("Date", [])
                        if len(dates) != 1:
                            raise ValueError("Message requires one Date header")
                        at = _utc(parsedate_to_datetime(dates[0]))
                    except (TypeError, ValueError, OverflowError):
                        counts["invalid_date"] += 1
                        continue
                    if not since <= at < until:
                        counts["outside_range"] += 1
                        continue
                    found.append((at, n, key))
        affected = set()
        for at, n, key in sorted(found):
            raw = boxes[n].get_bytes(key)
            try:
                incoming = email.parse(raw, connection)
            except (TypeError, ValueError, LookupError):
                counts["malformed"] += 1
                continue
            if incoming is None:
                counts["filtered"] += 1
                continue
            if db.execute("SELECT 1 FROM activities WHERE kind = 'MESSAGE' AND native_id = ?",
                          (incoming.native_id,)).fetchone():
                counts["duplicates"] += 1
                continue
            refs, thread_id = [], None
            for ref in incoming.thread_ref:
                known = db.execute(
                    "SELECT a.thread_id, c.connection_id, c.native_id, c.kind FROM thread_activities a"
                    " JOIN threads t ON t.id = a.thread_id JOIN channels c ON c.id = t.channel_id"
                    " WHERE a.kind = 'MESSAGE' AND a.native_id = ?", (ref,)).fetchone()
                # Preserve raw references while importing only links within this mailbox.
                if known is None:
                    refs.append(ref)
                elif (known["connection_id"] == incoming.connection_id and known["native_id"] == incoming.channel
                      and known["kind"] == "mailbox"):
                    refs.append(ref)
                    thread_id = thread_id or known["thread_id"]
            incoming = replace(incoming, occurred_at=at, thread_ref=refs)
            if incoming.sender is None and thread_id is not None and inbox._echo_of_sent(db, thread_id, incoming):
                counts["filtered"] += 1
                continue
            incoming.data["raw"] = files.store_raw(db, raw)
            thread = inbox.ingest(db, incoming)
            if thread is None:
                counts["filtered"] += 1
                continue
            counts["stored"] += 1
            affected.add(thread.id)
            detail = thread.status_detail
            if thread.status != "TODO":
                replied = db.execute("SELECT 1 FROM thread_activities WHERE thread_id = ? AND kind = 'MESSAGE'"
                                     " AND identity_id IS NULL", (thread.id,)).fetchone()
                detail = "NEW_REPLY" if replied else "CREATED"
            inbox.act(db, thread.id, inbox.MarkTodo(detail))
        counts["threads"] = sorted({inbox.get_thread(db, tid).id for tid in affected})
        for thread_id in counts["threads"]:
            state = tuple(db.execute(f"SELECT {STATUS_COLUMNS} FROM threads WHERE id = ?",
                                     (thread_id,)).fetchone())
            if state[0] != "TODO" or state[2] is not None or state[3] is not None:
                raise RuntimeError("Imported Thread failed Todo verification")
            counts["verified_todo"] += 1
            counts["existing_threads_changed"] += thread_id in initial and initial[thread_id] != state
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive")
    parser.add_argument("--db", required=True)
    parser.add_argument("--connection-id", required=True, type=int)
    parser.add_argument("--since", required=True)
    parser.add_argument("--until", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        counts = run(args.archive, args.db, args.connection_id, args.since, args.until, args.apply)
    except Exception as error:
        # Never expose MIME/parser errors: they can include private message data.
        print(json.dumps({"error": type(error).__name__}))
        return 1
    print(json.dumps(counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
