"""The inbox module: Threads, Status, Activities, Persons, Organizations, Blocks, and Cases.

Interface: ingest, act, reply, wake; people operations block, add_identity, remove_identity.
Design: docs/design/api-and-database.md.
"""

import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

SCHEMA_VERSION = 2
MIGRATIONS = {  # version -> statement that upgrades an existing database to it
    2: "ALTER TABLE cases ADD COLUMN description TEXT NOT NULL DEFAULT ''",
}

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
class MarkTodo:
    detail: str  # CREATED, NEW_REPLY, or IN_PROGRESS (Plain markThreadAsTodo; S key)


@dataclass(frozen=True)
class Ignore: ...


@dataclass(frozen=True)
class Unignore: ...


@dataclass(frozen=True)
class SetCase:
    case_id: int | None = None
    new_title: str | None = None


Action = Done | Snooze | Wait | Investigate | MarkTodo | Ignore | Unignore | SetCase


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
    db.execute("PRAGMA busy_timeout = 5000")  # the service and the console share the file
    if path != ":memory:":
        db.execute("PRAGMA journal_mode = WAL")
    return db


def migrate(db: sqlite3.Connection) -> None:
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version == 0:  # new database: the full schema
        db.executescript((Path(__file__).with_name("schema.sql")).read_text())
    else:
        for v in range(version + 1, SCHEMA_VERSION + 1):
            db.execute(MIGRATIONS[v])
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


ECHO_WINDOW = timedelta(days=1)


def _echo_of_sent(db, thread_id: int, inc: Incoming) -> bool:
    """A copy of Jeremy's own reply coming back in (he is copied, or a recipient forwards to him).

    Resend sends through Amazon SES, which replaces the Message-ID we set, so the copy's native_id never
    matches the stored reply; match on the same text from Jeremy in the same Thread within a day instead.
    """
    text = (inc.content or "").strip()
    if not text:
        return False
    since = _iso(inc.occurred_at - ECHO_WINDOW)
    # The copy is Jeremy's text, possibly followed by the quote his reply included (decision #109).
    norm = lambda x: (x or "").replace("\r\n", "\n").strip()  # noqa: E731
    text = norm(text)

    def same(sent):
        sent = norm(sent)
        return bool(sent) and (text == sent or text.startswith(sent)
                               and re.match(r"\s*On .{0,300}?wrote:", text[len(sent):], re.S) is not None)
    return any(same(r["content"]) for r in db.execute(
        "SELECT content FROM activities WHERE thread_id = ? AND kind = 'MESSAGE' AND identity_id IS NULL"
        " AND occurred_at >= ?", (thread_id, since)))


def raw_dir(db) -> Path | None:
    """Where raw messages are kept: `raw/` beside the database file (decision #106); None in memory."""
    f = db.execute("PRAGMA database_list").fetchone()[2]
    return Path(f).parent / "raw" if f else None


def store_raw(db, raw: bytes) -> str | None:
    """Keep a message's raw bytes, content-addressed and 0600; returns the sha256 name."""
    d = raw_dir(db)
    if d is None:
        return None
    sha = hashlib.sha256(raw).hexdigest()
    d.mkdir(mode=0o700, exist_ok=True)
    path = d / f"{sha}.eml"
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
    return sha


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
            if inc.kind == "MESSAGE" and identity is None and _echo_of_sent(db, thread_id, inc):
                return None
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
            case MarkTodo(detail=detail):
                _set_status(db, thread_id, "TODO", detail, now)
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
    history = [{"native_id": r["native_id"], "data": json.loads(r["data"]), "content": r["content"],
                "at": r["occurred_at"]} for r in db.execute(
        "SELECT native_id, data, content, occurred_at FROM activities WHERE thread_id = ? AND kind = 'MESSAGE'"
        " ORDER BY occurred_at, id", (thread_id,))]
    connection_id = db.execute("SELECT connection_id FROM channels WHERE id = ?",
                               (t.channel_id,)).fetchone()[0]
    try:
        native_id = sender.send(t, text, list(to), list(cc), history)
    except SendFailed as e:
        set_connection_error(db, connection_id, str(e))
        raise
    set_connection_error(db, connection_id, None)
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


# --- connection health inputs (NFR-2) ----------------------------------------

def set_connection_error(db, connection_id: int, error: str | None) -> None:
    with db:
        db.execute("UPDATE connections SET last_error = ? WHERE id = ?", (error, connection_id))


def set_auth(db, connection_id: int, ok: bool) -> None:
    with db:
        db.execute("UPDATE connections SET auth_ok = ? WHERE id = ?", (int(ok), connection_id))


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


def set_case_description(db, case_id: int, description: str) -> None:
    with db:
        db.execute("UPDATE cases SET description = ? WHERE id = ?", (description, case_id))


def case_threads(db, case_id: int) -> list[Thread]:
    return [get_thread(db, r[0]) for r in db.execute(
        "SELECT id FROM threads WHERE case_id = ? ORDER BY id", (case_id,))]


# --- console read models (docs/design/console.md) ---------------------------

VIEWS = {
    "Todo": ("CREATED", "NEW_REPLY", "IN_PROGRESS"),
    "Snoozed": ("WAITING_FOR_CUSTOMER", "WAITING_FOR_DURATION", "WAITING_INDEFINITELY"),
    "Done": ("DONE_MANUALLY_SET",),
    "Ignored": ("IGNORED",),
}
VIEWS["All"] = tuple(d for details in VIEWS.values() for d in details)


def view_counts(db) -> dict[str, int]:
    rows = dict(db.execute("SELECT status_detail, count(*) FROM threads GROUP BY status_detail").fetchall())
    return {name: sum(rows.get(d, 0) for d in details) for name, details in VIEWS.items()}


def _sender(db, identity_id):
    """(label, identity row) for an identity: Person name, else the address."""
    i = db.execute("SELECT i.*, p.display_name FROM identities i LEFT JOIN persons p ON p.id = i.person_id"
                   " WHERE i.id = ?", (identity_id,)).fetchone()
    if i is None:
        return "You", None
    name = i["display_name"]
    return (name if name and name != i["value"] else i["value"]), i


def _where(channel, identity) -> str:
    kind = channel["kind"]
    if kind == "im":
        return "Slack DM"
    if kind == "mpim":
        return "Slack group"
    if kind in ("channel", "private"):
        return "#" + (channel["name"] or channel["native_id"])
    domain = identity["value"].rsplit("@", 1)[-1] if identity and identity["kind"] == "email" else ""
    return domain if domain and domain not in FREE_MAIL else "email"


def queue(db, view: str) -> list[dict]:
    """Rows for a Status view. Todo: longest-waiting first; others: most recent first."""
    details = VIEWS[view]
    rows = db.execute(
        "SELECT t.id, t.title, t.status, t.status_detail, t.case_id, ch.kind, ch.name, ch.native_id,"
        " (SELECT max(occurred_at) FROM activities a WHERE a.thread_id = t.id) AS last_at,"
        " (SELECT max(occurred_at) FROM activities a WHERE a.thread_id = t.id AND a.identity_id IS NOT NULL) AS last_in,"
        " (SELECT identity_id FROM activities a WHERE a.thread_id = t.id AND a.identity_id IS NOT NULL"
        "   ORDER BY occurred_at, id LIMIT 1) AS first_identity"
        f" FROM threads t JOIN channels ch ON ch.id = t.channel_id"
        f" WHERE t.status_detail IN ({','.join('?' * len(details))})", details).fetchall()
    out = []
    for r in rows:
        label, identity = _sender(db, r["first_identity"]) if r["first_identity"] else ("You", None)
        out.append({"id": r["id"], "title": r["title"], "status": r["status"], "detail": r["status_detail"],
                    "who": label, "where": _where(r, identity), "last_at": r["last_at"],
                    "waiting_since": r["last_in"] or r["last_at"]})
    if view == "Todo":
        return sorted(out, key=lambda x: x["waiting_since"])
    return sorted(out, key=lambda x: x["last_at"], reverse=True)


def thread_view(db, thread_id: int) -> dict:
    """Timeline (latest edit wins; deleted messages have no content) and the sender's records."""
    t = get_thread(db, thread_id)
    ch = db.execute("SELECT ch.*, c.platform, c.external_id FROM channels ch"
                    " JOIN connections c ON c.id = ch.connection_id WHERE ch.id = ?", (t.channel_id,)).fetchone()
    acts = db.execute("SELECT * FROM activities WHERE thread_id = ? ORDER BY occurred_at, id", (thread_id,)).fetchall()
    msgs, by_id = [], {}
    for a in acts:
        if a["kind"] == "MESSAGE":
            who, _ = _sender(db, a["identity_id"]) if a["identity_id"] else ("You", None)
            m = {"who": who, "at": a["occurred_at"], "content": a["content"], "edited": False, "deleted": False,
                 "native_id": a["native_id"], "data": json.loads(a["data"])}
            by_id[a["id"]] = m
            msgs.append(m)
        elif a["target_id"] in by_id:
            m = by_id[a["target_id"]]
            if a["kind"] == "EDIT":
                m["edited"], m["content"] = True, a["content"]
            else:
                m["deleted"], m["content"] = True, None
    first = next((a["identity_id"] for a in acts if a["identity_id"]), None)
    person = None
    if first:
        pid = db.execute("SELECT person_id FROM identities WHERE id = ?", (first,)).fetchone()[0]
        person = person_view(db, pid) if pid else None
    case = db.execute("SELECT title FROM cases WHERE id = ?", (t.case_id,)).fetchone() if t.case_id else None
    return {"thread": t, "channel": dict(ch), "messages": msgs, "person": person,
            "case": case["title"] if case else None, "where": _where(ch, None) if ch["kind"] != "mailbox" else "email"}


def person_view(db, person_id: int) -> dict:
    p = db.execute("SELECT * FROM persons WHERE id = ?", (person_id,)).fetchone()
    identities = [dict(r) for r in db.execute(
        "SELECT id, kind, value, blocked FROM identities WHERE person_id = ? ORDER BY kind, value", (person_id,))]
    threads = person_threads(db, person_id)
    cases = sorted({r[0] for r in db.execute(
        "SELECT c.title FROM cases c JOIN threads t ON t.case_id = c.id WHERE t.id IN"
        f" ({','.join(str(t.id) for t in threads) or 'NULL'})")})
    return {"id": person_id, "name": p["display_name"], "identities": identities,
            "organizations": person_organizations(db, person_id), "threads": threads, "cases": cases}


def cases(db) -> list[dict]:
    return [dict(r) for r in db.execute(
        "SELECT c.id, c.title, count(t.id) AS threads FROM cases c LEFT JOIN threads t ON t.case_id = c.id"
        " GROUP BY c.id ORDER BY c.title")]


def connections(db) -> list[dict]:
    return [dict(r) for r in db.execute("SELECT * FROM connections ORDER BY id")]


def channel_rules(db, connection_id: int) -> list[dict]:
    return [dict(r) for r in db.execute(
        "SELECT id, kind, value FROM channel_rules WHERE connection_id = ? ORDER BY kind, value", (connection_id,))]


def add_channel_rule(db, connection_id: int, kind: str, value: str) -> None:
    with db:
        db.execute("INSERT OR IGNORE INTO channel_rules (connection_id, kind, value) VALUES (?, ?, ?)",
                   (connection_id, kind, value))


def delete_channel_rule(db, rule_id: int) -> None:
    with db:
        db.execute("DELETE FROM channel_rules WHERE id = ?", (rule_id,))


def people(db) -> list[dict]:
    """Persons with at least one Identity: name, first address, Organizations, open (Todo) Thread count."""
    out = []
    for p in db.execute("SELECT p.id, p.display_name FROM persons p WHERE EXISTS"
                        " (SELECT 1 FROM identities i WHERE i.person_id = p.id) ORDER BY lower(p.display_name)"):
        ids = db.execute("SELECT value FROM identities WHERE person_id = ? ORDER BY kind, value", (p["id"],)).fetchall()
        threads = person_threads(db, p["id"])
        out.append({"id": p["id"], "name": p["display_name"], "address": ids[0]["value"],
                    "organizations": person_organizations(db, p["id"]),
                    "todo": sum(t.status == "TODO" for t in threads), "threads": len(threads)})
    return out


def organizations(db) -> list[dict]:
    out = []
    for o in db.execute("SELECT id, domain, name FROM organizations ORDER BY domain"):
        view = organization_view(db, o["domain"])
        out.append({"id": o["id"], "domain": o["domain"], "people": len(view["people"]),
                    "todo": sum(t.status == "TODO" for t in view["threads"]), "threads": len(view["threads"])})
    return out


def organization_view(db, domain: str) -> dict:
    """An Organization's Persons (by email domain) and their Threads."""
    pids = [r[0] for r in db.execute(
        "SELECT DISTINCT person_id FROM identities WHERE kind = 'email' AND person_id IS NOT NULL"
        " AND substr(value, instr(value, '@') + 1) = ?", (domain,))]
    persons = [person_view(db, pid) for pid in pids]
    threads = {t.id: t for p in persons for t in p["threads"]}
    return {"domain": domain, "people": persons, "threads": sorted(threads.values(), key=lambda t: t.id)}


# --- undo (console: u) -------------------------------------------------------

def snapshot(db, thread_id: int) -> dict:
    """The Thread's Status, Status detail, snooze, and Case, so an action can be undone."""
    r = db.execute("SELECT id, status, status_detail, status_changed_at, snoozed_until, detail_before_snooze, case_id"
                   " FROM threads WHERE id = ?", (thread_id,)).fetchone()
    return dict(r)


def restore(db, snap: dict) -> Thread:
    with db:
        db.execute("UPDATE threads SET status = ?, status_detail = ?, status_changed_at = ?, snoozed_until = ?,"
                   " detail_before_snooze = ?, case_id = ? WHERE id = ?",
                   (snap["status"], snap["status_detail"], snap["status_changed_at"], snap["snoozed_until"],
                    snap["detail_before_snooze"], snap["case_id"], snap["id"]))
    return get_thread(db, snap["id"])
