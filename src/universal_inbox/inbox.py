"""The inbox module: Threads, Status, Activities, Persons, Organizations, Blocks, and Cases.

Interface: ingest, act, reply, wake; people operations block, add_identity, remove_identity.
Design: docs/design/api-and-database.md.
"""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

SCHEMA_VERSION = 1

# ponytail: fixed list; extend when a free-mail sender forms an Organization by mistake.
FREE_MAIL = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com",
    "yahoo.com", "ymail.com", "icloud.com", "me.com", "mac.com", "aol.com", "proton.me",
    "protonmail.com", "gmx.com", "gmx.net", "mail.com", "yandex.com", "zoho.com",
    "fastmail.com", "hey.com",
})

SAME_SENDER_WINDOW = timedelta(hours=2)
OTHER_SENDER_WINDOW = timedelta(hours=1)


class SendFailed(Exception):
    """Raised by a Sender when a reply could not be sent."""


class IdentityTaken(Exception):
    """Raised when an Identity already belongs to another Person (I-6)."""


@dataclass(frozen=True)
class Identity:
    kind: str  # "email" or "slack_user"
    value: str
    display_name: str | None = None
    profile_email: str | None = None


@dataclass(frozen=True)
class Incoming:
    connection_id: int
    channel: str
    channel_kind: str  # mailbox, channel, private, im, mpim
    kind: str  # MESSAGE, EDIT, DELETE
    native_id: str
    sender: Identity | None  # None = Jeremy
    occurred_at: datetime
    content: str | None
    thread_ref: list[str] = field(default_factory=list)
    title: str | None = None
    data: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Thread:
    id: int
    channel_id: int
    title: str
    status: str
    status_detail: str
    snoozed_until: str | None
    case_id: int | None


@dataclass(frozen=True)
class Done: ...


@dataclass(frozen=True)
class Snooze:
    until: datetime | None = None


@dataclass(frozen=True)
class Wait: ...


@dataclass(frozen=True)
class Investigate: ...


@dataclass(frozen=True)
class Ignore: ...


@dataclass(frozen=True)
class Unignore: ...


@dataclass(frozen=True)
class SetCase:
    case_id: int | None = None
    new_title: str | None = None


Action = Done | Snooze | Wait | Investigate | Ignore | Unignore | SetCase


class Sender(Protocol):
    def send(self, thread: Thread, text: str, to: list[str], cc: list[str],
             history: list[dict]) -> str:
        """Send a reply; return its native_id. history: the Thread's messages, oldest
        first, as {"native_id", "data"}. Raise SendFailed on failure."""


# --- storage ---------------------------------------------------------------

def connect(path: str = ":memory:") -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    if path != ":memory:":
        db.execute("PRAGMA journal_mode = WAL")
    return db


def migrate(db: sqlite3.Connection) -> None:
    if db.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
        db.executescript((Path(__file__).with_name("schema.sql")).read_text())
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_thread(db, thread_id: int) -> Thread:
    r = db.execute(
        "SELECT id, channel_id, title, status, status_detail, snoozed_until, case_id"
        " FROM threads WHERE id = ?", (thread_id,)).fetchone()
    if r is None:
        raise KeyError(f"thread {thread_id}")
    return Thread(*r)


def _set_status(db, thread_id, status, detail, at, snoozed_until=None, before=None):
    db.execute(
        "UPDATE threads SET status = ?, status_detail = ?, status_changed_at = ?,"
        " snoozed_until = ?, detail_before_snooze = ? WHERE id = ?",
        (status, detail, _iso(at), snoozed_until, before, thread_id))


def _has_replied(db, thread_id) -> bool:
    return db.execute(
        "SELECT 1 FROM activities WHERE thread_id = ? AND kind = 'MESSAGE'"
        " AND identity_id IS NULL", (thread_id,)).fetchone() is not None


# --- status rules (design §4) -------------------------------------------------

def _after_activity(status, detail, from_jeremy, kind):
    """Status after an Activity on an existing Thread, or None for unchanged."""
    if detail == "IGNORED":
        return None
    if from_jeremy:
        return ("SNOOZED", "WAITING_FOR_CUSTOMER") if kind == "MESSAGE" else None
    if status == "TODO":
        return None if detail == "CREATED" else ("TODO", "NEW_REPLY")
    return ("TODO", "NEW_REPLY")


# --- people ----------------------------------------------------------------

def _ensure_org(db, kind, value):
    if kind != "email" or "@" not in value:
        return
    domain = value.rsplit("@", 1)[1]
    if domain not in FREE_MAIL:
        db.execute("INSERT OR IGNORE INTO organizations (domain, name, created_at)"
                   " VALUES (?, ?, ?)", (domain, domain, _iso(_now())))


def _new_person(db, name) -> int:
    return db.execute("INSERT INTO persons (display_name, created_at) VALUES (?, ?)",
                      (name, _iso(_now()))).lastrowid


def _identity_for(db, sender: Identity) -> sqlite3.Row:
    """Find or create the sender's Identity and attach it to a Person (FR-10)."""
    value = sender.value.lower() if sender.kind == "email" else sender.value
    row = db.execute("SELECT * FROM identities WHERE kind = ? AND value = ?",
                     (sender.kind, value)).fetchone()
    if row is not None and row["person_id"] is not None:
        return row
    person_id = None
    if sender.kind == "slack_user" and sender.profile_email:
        owner = db.execute(
            "SELECT person_id FROM identities WHERE kind = 'email' AND value = ?"
            " AND person_id IS NOT NULL", (sender.profile_email.lower(),)).fetchone()
        person_id = owner and owner["person_id"]
    if person_id is None:
        person_id = _new_person(db, sender.display_name or value)
    if row is None:
        db.execute("INSERT INTO identities (person_id, kind, value) VALUES (?, ?, ?)",
                   (person_id, sender.kind, value))
    else:
        db.execute("UPDATE identities SET person_id = ? WHERE id = ?", (person_id, row["id"]))
    _ensure_org(db, sender.kind, value)
    return db.execute("SELECT * FROM identities WHERE kind = ? AND value = ?",
                      (sender.kind, value)).fetchone()


def block(db, kind: str, value: str, blocked: bool = True) -> None:
    value = value.lower() if kind == "email" else value
    with db:
        if db.execute("SELECT 1 FROM identities WHERE kind = ? AND value = ?",
                      (kind, value)).fetchone() is None:
            _identity_for(db, Identity(kind, value))
        db.execute("UPDATE identities SET blocked = ? WHERE kind = ? AND value = ?",
                   (int(blocked), kind, value))


def add_identity(db, person_id: int, kind: str, value: str) -> None:
    value = value.lower() if kind == "email" else value
    with db:
        row = db.execute("SELECT person_id FROM identities WHERE kind = ? AND value = ?",
                         (kind, value)).fetchone()
        if row is None:
            db.execute("INSERT INTO identities (person_id, kind, value) VALUES (?, ?, ?)",
                       (person_id, kind, value))
        elif row["person_id"] is None:
            db.execute("UPDATE identities SET person_id = ? WHERE kind = ? AND value = ?",
                       (person_id, kind, value))
        elif row["person_id"] != person_id:
            raise IdentityTaken(value)
        _ensure_org(db, kind, value)


def remove_identity(db, identity_id: int) -> None:
    with db:
        db.execute("UPDATE identities SET person_id = NULL WHERE id = ?", (identity_id,))


# --- ingest ----------------------------------------------------------------

def _channel_id(db, inc: Incoming) -> int:
    db.execute("INSERT OR IGNORE INTO channels (connection_id, native_id, kind) VALUES (?, ?, ?)",
               (inc.connection_id, inc.channel, inc.channel_kind))
    return db.execute("SELECT id FROM channels WHERE connection_id = ? AND native_id = ?",
                      (inc.connection_id, inc.channel)).fetchone()["id"]


def _thread_for_message(db, channel_id, inc: Incoming, identity_id):
    """Existing Thread this message joins, or None (design §4 Threading)."""
    for ref in inc.thread_ref:
        r = db.execute("SELECT thread_id FROM activities WHERE kind = 'MESSAGE' AND native_id = ?",
                       (ref,)).fetchone()
        if r:
            return r["thread_id"]
    if inc.channel_kind == "mailbox":
        return None
    last = db.execute(
        "SELECT a.thread_id, a.identity_id, a.occurred_at FROM activities a"
        " JOIN threads t ON t.id = a.thread_id"
        " WHERE t.channel_id = ? AND a.kind = 'MESSAGE'"
        " ORDER BY a.occurred_at DESC, a.id DESC LIMIT 1", (channel_id,)).fetchone()
    if last is None:
        return None
    gap = inc.occurred_at - datetime.fromisoformat(last["occurred_at"])
    window = SAME_SENDER_WINDOW if last["identity_id"] == identity_id else OTHER_SENDER_WINDOW
    return last["thread_id"] if timedelta(0) <= gap <= window else None


def ingest(db, inc: Incoming) -> Thread | None:
    """Store one message, edit, or deletion; returns its Thread, or None for a
    duplicate or an edit/deletion of a message that was never stored."""
    with db:
        if db.execute("SELECT 1 FROM activities WHERE kind = ? AND native_id = ?",
                      (inc.kind, inc.native_id)).fetchone():
            return None
        channel_id = _channel_id(db, inc)
        identity = _identity_for(db, inc.sender) if inc.sender else None
        identity_id = identity["id"] if identity else None
        at = inc.occurred_at

        target_id = None
        if inc.kind == "MESSAGE":
            thread_id = _thread_for_message(db, channel_id, inc, identity_id)
        else:
            target_native = inc.native_id if inc.kind == "DELETE" else inc.native_id.rsplit(":", 1)[0]
            target = db.execute("SELECT id, thread_id FROM activities WHERE kind = 'MESSAGE'"
                                " AND native_id = ?", (target_native,)).fetchone()
            if target is None:
                return None
            target_id, thread_id = target["id"], target["thread_id"]

        if thread_id is None:
            if identity is None:
                status = ("SNOOZED", "WAITING_FOR_CUSTOMER")  # decision #61
            elif identity["blocked"]:
                status = ("DONE", "IGNORED")  # FR-7a
            else:
                status = ("TODO", "CREATED")  # FR-6a
            title = inc.title or (inc.content or "").strip()[:80] or "(no subject)"
            thread_id = db.execute(
                "INSERT INTO threads (channel_id, native_thread_id, title, status, status_detail,"
                " status_changed_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (channel_id, inc.native_id, title, *status, _iso(at), _iso(at))).lastrowid
        else:
            t = get_thread(db, thread_id)
            nxt = _after_activity(t.status, t.status_detail, identity is None, inc.kind)
            if nxt:
                _set_status(db, thread_id, *nxt, at)

        db.execute(
            "INSERT INTO activities (thread_id, kind, occurred_at, identity_id, native_id,"
            " target_id, content, data) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (thread_id, inc.kind, _iso(at), identity_id, inc.native_id, target_id,
             None if inc.kind == "DELETE" else inc.content, json.dumps(inc.data)))
        if inc.kind == "DELETE":  # FR-2a
            db.execute("UPDATE activities SET content = NULL WHERE id = ?"
                       " OR (kind = 'EDIT' AND target_id = ?)", (target_id, target_id))
        return get_thread(db, thread_id)


# --- act, reply, wake ------------------------------------------------------

def act(db, thread_id: int, action: Action) -> Thread:
    with db:
        t = get_thread(db, thread_id)
        now = _now()
        match action:
            case Done():
                _set_status(db, thread_id, "DONE", "DONE_MANUALLY_SET", now)
            case Snooze(until=until):
                before = t.status_detail if t.status == "TODO" else None
                if until is None:
                    _set_status(db, thread_id, "SNOOZED", "WAITING_INDEFINITELY", now, before=before)
                else:
                    _set_status(db, thread_id, "SNOOZED", "WAITING_FOR_DURATION", now,
                                snoozed_until=_iso(until), before=before)
            case Wait():
                _set_status(db, thread_id, "SNOOZED", "WAITING_FOR_CUSTOMER", now)
            case Investigate():
                _set_status(db, thread_id, "TODO", "IN_PROGRESS", now)
            case Ignore():
                _set_status(db, thread_id, "DONE", "IGNORED", now)
            case Unignore():
                if t.status_detail == "IGNORED":
                    detail = "NEW_REPLY" if _has_replied(db, thread_id) else "CREATED"
                    _set_status(db, thread_id, "TODO", detail, now)
            case SetCase(case_id=case_id, new_title=new_title):
                if new_title is not None:
                    case_id = db.execute("INSERT INTO cases (title, created_at) VALUES (?, ?)",
                                         (new_title, _iso(now))).lastrowid
                db.execute("UPDATE threads SET case_id = ? WHERE id = ?", (case_id, thread_id))
        return get_thread(db, thread_id)


def reply(db, thread_id: int, text: str, sender: Sender,
          to: list[str] = (), cc: list[str] = ()) -> Thread:
    """Send first; store the outbound Activity only on success (decision #59).
    SendFailed propagates and nothing is stored."""
    t = get_thread(db, thread_id)
    history = [{"native_id": r["native_id"], "data": json.loads(r["data"])} for r in db.execute(
        "SELECT native_id, data FROM activities WHERE thread_id = ? AND kind = 'MESSAGE'"
        " ORDER BY occurred_at, id", (thread_id,))]
    native_id = sender.send(t, text, list(to), list(cc), history)
    now = _now()
    with db:
        db.execute(
            "INSERT OR IGNORE INTO activities (thread_id, kind, occurred_at, native_id, content, data)"
            " VALUES (?, 'MESSAGE', ?, ?, ?, ?)",
            (thread_id, _iso(now), native_id, text, json.dumps({"to": list(to), "cc": list(cc)})))
        nxt = _after_activity(t.status, t.status_detail, True, "MESSAGE")
        if nxt:
            _set_status(db, thread_id, *nxt, now)
    return get_thread(db, thread_id)


def wake(db, now: datetime) -> int:
    """End timed snoozes that are due (FR-6d); returns how many woke."""
    with db:
        due = db.execute("SELECT id, detail_before_snooze FROM threads"
                         " WHERE status_detail = 'WAITING_FOR_DURATION' AND snoozed_until <= ?",
                         (_iso(now),)).fetchall()
        for r in due:
            detail = r["detail_before_snooze"] or (
                "NEW_REPLY" if _has_replied(db, r["id"]) else "CREATED")
            _set_status(db, r["id"], "TODO", detail, now)
    return len(due)


# --- read side (records) ---------------------------------------------------

def person_threads(db, person_id: int) -> list[Thread]:
    ids = [r[0] for r in db.execute(
        "SELECT DISTINCT a.thread_id FROM activities a JOIN identities i ON i.id = a.identity_id"
        " WHERE i.person_id = ? ORDER BY a.thread_id", (person_id,))]
    return [get_thread(db, i) for i in ids]


def person_organizations(db, person_id: int) -> list[str]:
    return [r[0] for r in db.execute(
        "SELECT o.domain FROM organizations o JOIN identities i"
        " ON i.kind = 'email' AND substr(i.value, instr(i.value, '@') + 1) = o.domain"
        " WHERE i.person_id = ? ORDER BY o.domain", (person_id,))]


def case_threads(db, case_id: int) -> list[Thread]:
    return [get_thread(db, r[0]) for r in db.execute(
        "SELECT id FROM threads WHERE case_id = ? ORDER BY id", (case_id,))]
