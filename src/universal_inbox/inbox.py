"""The inbox module: Threads, Status, Activities, Persons, Organizations, Blocks, Cases, and Drafts.

Interface: ingest, act, reply, wake, reopen_imported, reconcile_email_threads; people operations block, add_identity, remove_identity; save_draft, get_draft, delete_draft, restore_draft, list_drafts, recipient_names, recipient_threads.
Design: docs/design/api-and-database.md.
"""

import hashlib
import json
import os
import re
from collections import Counter
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from email import policy
from email.parser import BytesHeaderParser
from email.utils import getaddresses
from typing import Protocol

from . import platforms

SCHEMA_VERSION = 12
WAIT = timedelta(days=4)  # Waiting for them ends after this with no reply: back to Todo to follow up (#116)
MIGRATIONS = {  # version -> statement that upgrades an existing database to it
    2: "ALTER TABLE cases ADD COLUMN description TEXT NOT NULL DEFAULT ''",
    3: "ALTER TABLE threads ADD COLUMN follow_up_at TEXT;"
       " UPDATE threads SET follow_up_at = strftime('%Y-%m-%dT%H:%M:%f', status_changed_at, '+5 days') || '000+00:00'"
       " WHERE status_detail = 'WAITING_FOR_CUSTOMER';",
    4: """ALTER TABLE threads ADD COLUMN merged_into INTEGER REFERENCES threads (id) CHECK (merged_into < id);
CREATE TRIGGER threads_alias_insert BEFORE INSERT ON threads
WHEN NEW.merged_into IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM threads t JOIN channels ch ON ch.id = t.channel_id
  WHERE t.id = NEW.merged_into AND t.merged_into IS NULL
    AND t.channel_id = NEW.channel_id AND ch.kind = 'mailbox'
)
BEGIN
  SELECT RAISE(ABORT, 'thread aliases require an active older thread in the same mailbox');
END;
CREATE TRIGGER threads_alias_update BEFORE UPDATE OF merged_into, channel_id ON threads
WHEN (NEW.merged_into IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM threads t JOIN channels ch ON ch.id = t.channel_id
  WHERE t.id = NEW.merged_into AND t.merged_into IS NULL
    AND t.channel_id = NEW.channel_id AND ch.kind = 'mailbox'
)) OR (EXISTS (SELECT 1 FROM threads t WHERE t.merged_into = OLD.id)
       AND (NEW.merged_into IS NOT NULL OR NEW.channel_id IS NOT OLD.channel_id))
BEGIN
  SELECT RAISE(ABORT, 'thread aliases require an active older thread in the same mailbox');
END;
CREATE TABLE email_refs (
  activity_id INTEGER NOT NULL REFERENCES activities (id),
  native_id TEXT NOT NULL,
  PRIMARY KEY (activity_id, native_id)
) STRICT;
CREATE INDEX email_refs_native ON email_refs (native_id);
CREATE VIEW thread_activities AS
SELECT a.id, COALESCE(t.merged_into, a.thread_id) AS thread_id,
       a.kind, a.occurred_at, a.identity_id, a.native_id, a.target_id, a.content, a.data
FROM activities a JOIN threads t ON t.id = a.thread_id;""",
    # SMS Connection (Quo): widen two CHECKs by SQLite's table rebuild; migrate() turns foreign keys off around it.
    # Nothing references the *_new names, so the renames need no schema-wide rewrite (legacy_alter_table).
    5: """PRAGMA legacy_alter_table = ON;
CREATE TABLE connections_new (
  id INTEGER PRIMARY KEY,
  platform TEXT NOT NULL CHECK (platform IN ('email', 'slack', 'sms')),
  external_id TEXT NOT NULL UNIQUE, self_id TEXT NOT NULL, label TEXT NOT NULL, connected_at TEXT NOT NULL,
  auth_ok INTEGER NOT NULL DEFAULT 1 CHECK (auth_ok IN (0, 1)), last_error TEXT
) STRICT;
INSERT INTO connections_new SELECT id, platform, external_id, self_id, label, connected_at, auth_ok, last_error
FROM connections;
DROP TABLE connections;
ALTER TABLE connections_new RENAME TO connections;
CREATE TABLE identities_new (
  id INTEGER PRIMARY KEY,
  person_id INTEGER REFERENCES persons (id),
  kind TEXT NOT NULL CHECK (kind IN ('email', 'slack_user', 'phone')),
  value TEXT NOT NULL,
  blocked INTEGER NOT NULL DEFAULT 0 CHECK (blocked IN (0, 1)),
  UNIQUE (kind, value)
) STRICT;
INSERT INTO identities_new SELECT id, person_id, kind, value, blocked FROM identities;
DROP TABLE identities;
ALTER TABLE identities_new RENAME TO identities;
CREATE INDEX identities_person ON identities (person_id);
PRAGMA legacy_alter_table = OFF;""",
    # Platforms are services (GLOSSARY): the email Platform splits into gmail and fastmail; sms is quo.
    6: """PRAGMA legacy_alter_table = ON;
CREATE TABLE connections_new (
  id INTEGER PRIMARY KEY,
  platform TEXT NOT NULL CHECK (platform IN ('gmail', 'fastmail', 'slack', 'quo')),
  external_id TEXT NOT NULL UNIQUE, self_id TEXT NOT NULL, label TEXT NOT NULL, connected_at TEXT NOT NULL,
  auth_ok INTEGER NOT NULL DEFAULT 1 CHECK (auth_ok IN (0, 1)), last_error TEXT
) STRICT;
INSERT INTO connections_new SELECT id,
  CASE WHEN platform = 'email' AND label = 'Fastmail' THEN 'fastmail' WHEN platform = 'email' THEN 'gmail'
       WHEN platform = 'sms' THEN 'quo' ELSE platform END,
  external_id, self_id, label, connected_at, auth_ok, last_error FROM connections;
DROP TABLE connections;
ALTER TABLE connections_new RENAME TO connections;
PRAGMA legacy_alter_table = OFF;""",
    # One Organization key (_org): subdomain rows fold into their registrable domain, keeping a name Jeremy set.
    7: """INSERT OR IGNORE INTO organizations (domain, name, created_at)
SELECT org_key(domain), CASE WHEN max(name != domain) THEN max(CASE WHEN name != domain THEN name END)
                             ELSE org_key(domain) END, min(created_at)
FROM organizations WHERE org_key(domain) != domain GROUP BY org_key(domain);
DELETE FROM organizations WHERE org_key(domain) != domain;""",
    # Health checks every 30 minutes (decision #119): their result apart from send and inbound errors.
    8: "ALTER TABLE connections ADD COLUMN probe_error TEXT; ALTER TABLE connections ADD COLUMN probed_at TEXT;",
    9: """CREATE TABLE composition_drafts (
  id INTEGER PRIMARY KEY,
  revision INTEGER NOT NULL CHECK (revision > 0),
  connection_id INTEGER REFERENCES connections (id),
  kind TEXT NOT NULL CHECK (kind IN ('email', 'sms')),
  sender TEXT NOT NULL,
  "to" TEXT NOT NULL,
  cc TEXT NOT NULL,
  subject TEXT,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK ((kind = 'email' AND subject IS NOT NULL)
         OR (kind = 'sms' AND subject IS NULL AND cc = ''))
) STRICT;""",
    10: "ALTER TABLE composition_drafts ADD COLUMN sending_at TEXT;",
    # Slack Drafts: a third kind, posted into one existing Slack conversation (no subject, no Cc).
    11: """PRAGMA legacy_alter_table = ON;
CREATE TABLE composition_drafts_new (
  id INTEGER PRIMARY KEY,
  revision INTEGER NOT NULL CHECK (revision > 0),
  connection_id INTEGER REFERENCES connections (id),
  kind TEXT NOT NULL CHECK (kind IN ('email', 'sms', 'slack')),
  sender TEXT NOT NULL,
  "to" TEXT NOT NULL,
  cc TEXT NOT NULL,
  subject TEXT,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  sending_at TEXT,
  CHECK ((kind = 'email' AND subject IS NOT NULL)
         OR (kind IN ('sms', 'slack') AND subject IS NULL AND cc = ''))
) STRICT;
INSERT INTO composition_drafts_new SELECT id, revision, connection_id, kind, sender, "to", cc, subject, body,
  created_at, updated_at, sending_at FROM composition_drafts;
DROP TABLE composition_drafts;
ALTER TABLE composition_drafts_new RENAME TO composition_drafts;
PRAGMA legacy_alter_table = OFF;""",
    # Attachments: received files copied on arrival, and files staged on a Draft or reply.
    12: """CREATE TABLE attachments (
  id INTEGER PRIMARY KEY,
  activity_id INTEGER NOT NULL REFERENCES activities (id),
  position INTEGER NOT NULL CHECK (position >= 0),
  name TEXT NOT NULL,
  type TEXT NOT NULL,
  size INTEGER NOT NULL CHECK (size >= 0),
  inline INTEGER NOT NULL DEFAULT 0 CHECK (inline IN (0, 1)),
  content_id TEXT,
  source TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(source)),
  sha TEXT,
  state TEXT NOT NULL CHECK (state IN ('pending', 'stored', 'failed', 'expired')),
  tries INTEGER NOT NULL DEFAULT 0,
  UNIQUE (activity_id, position),
  CHECK ((state = 'stored') = (sha IS NOT NULL))
) STRICT;
CREATE INDEX attachments_sha ON attachments (sha);
CREATE INDEX attachments_pending ON attachments (state) WHERE state = 'pending';

CREATE TABLE staged_attachments (
  id INTEGER PRIMARY KEY,
  draft_id INTEGER REFERENCES composition_drafts (id) ON DELETE CASCADE,
  thread_id INTEGER REFERENCES threads (id),
  sha TEXT NOT NULL,
  name TEXT NOT NULL,
  type TEXT NOT NULL,
  size INTEGER NOT NULL CHECK (size >= 0),
  added_at TEXT NOT NULL,
  CHECK ((draft_id IS NULL) <> (thread_id IS NULL))
) STRICT;""",
}

# ponytail: fixed list; extend when a free-mail sender forms an Organization by mistake.
FREE_MAIL = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com",
    "yahoo.com", "ymail.com", "icloud.com", "me.com", "mac.com", "aol.com", "proton.me",
    "protonmail.com", "gmx.com", "gmx.net", "mail.com", "yandex.com", "zoho.com",
    "fastmail.com", "hey.com",
})

# A conversation's messages group into one Thread until it has been quiet this long, whoever sent them (FR-38).
SLACK_QUIET = timedelta(hours=24)
SMS_QUIET = timedelta(days=7)


class SendFailed(Exception):
    """Raised by a Sender when a reply could not be sent."""


class SendUnknown(SendFailed):
    """The provider may or may not have sent the message (timeout, lost response, crash before recording)."""


class IdentityTaken(Exception):
    """Raised when an Identity already belongs to another Person (I-6)."""


class StaleThreadSnapshot(ValueError):
    """The snapshot predates a merge of its Thread; restoring it would overwrite another triage state."""


class DraftConflict(ValueError):
    """A newer saved Draft revision exists; stale edits must not overwrite it."""


@dataclass(frozen=True)
class Identity:
    kind: str  # "email", "slack_user", or "phone"
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
    channel_name: str | None = None  # a friendly name for the Channel (Slack #name, DM partner); kept current


@dataclass(frozen=True)
class Thread:
    id: int
    channel_id: int
    title: str
    status: str
    status_detail: str
    snoozed_until: str | None
    case_id: int | None
    follow_up_at: str | None = None  # Waiting for them: back to Todo then with no reply (#116)


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

    def start(self, to: list[str], cc: list[str], subject: str | None, body: str, key: str) -> str:
        """Send a new message (no Thread); return its native_id. `key` is unique per send attempt.
        Raise SendFailed when the provider refused it, SendUnknown when it may have gone out."""


# --- storage ---------------------------------------------------------------

def connect(path: str = ":memory:") -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA busy_timeout = 5000")  # the service and the console share the file
    db.create_function("org_key", 1, _org, deterministic=True)
    if path != ":memory:":
        db.execute("PRAGMA journal_mode = WAL")
    return db


@contextmanager
def _read_snapshot(db):
    """One coherent read snapshot, nested without committing caller-owned work."""
    db.execute("SAVEPOINT inbox_read_snapshot")
    try:
        yield
    finally:
        db.execute("RELEASE inbox_read_snapshot")


def migrate(db: sqlite3.Connection) -> None:
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise ValueError(f"database schema {version} is newer than supported schema {SCHEMA_VERSION}")
    if version == SCHEMA_VERSION:
        return
    sql = (Path(__file__).with_name("schema.sql")).read_text() if version == 0 else ";\n".join(
        MIGRATIONS[v] for v in range(version + 1, SCHEMA_VERSION + 1))
    db.create_function("org_key", 1, _org, deterministic=True)  # migration 7 (connect() also registers it)
    fk = db.execute("PRAGMA foreign_keys").fetchone()[0]
    db.execute("PRAGMA foreign_keys = OFF")  # table rebuilds (5) drop and rename parents; checked before commit
    try:
        db.executescript(f"BEGIN IMMEDIATE;\n{sql}\nPRAGMA user_version = {SCHEMA_VERSION};")
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("migration would leave a broken foreign key")
        db.execute("COMMIT")
    except Exception:
        db.rollback()
        raise
    finally:
        db.execute("PRAGMA legacy_alter_table = OFF")
        db.execute(f"PRAGMA foreign_keys = {fk}")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_thread(db, thread_id: int) -> Thread:
    r = db.execute(
        "SELECT id, channel_id, title, status, status_detail, snoozed_until, case_id, follow_up_at"
        " FROM threads WHERE id = (SELECT COALESCE(merged_into, id) FROM threads WHERE id = ?)",
        (thread_id,)).fetchone()
    if r is None:
        raise KeyError(f"thread {thread_id}")
    return Thread(*r)



# --- unsent composition ------------------------------------------------------

def save_draft(db, values: dict, *, draft_id: int | None = None,
               expected_revision: int | None = None) -> dict:
    """Persist the complete edited envelope, including incomplete text; never send or create work."""
    if not isinstance(values, dict) or set(values) != {
            "connection_id", "kind", "sender", "to", "cc", "subject", "body"}:
        raise ValueError("draft values must contain exactly connection_id, kind, sender, to, cc, subject, body")
    if type(values["kind"]) is not str or values["kind"] not in ("email", "sms", "slack"):
        raise ValueError("draft kind must be email, sms, or slack")
    if any(type(values[k]) is not str for k in ("sender", "to", "cc", "body")):
        raise ValueError("draft sender, to, cc, and body must be strings")
    if values["kind"] == "email":
        if type(values["subject"]) is not str:
            raise ValueError("email draft subject must be a string")
    elif values["subject"] is not None or values["cc"] != "":
        raise ValueError("SMS and Slack draft subject must be None and cc must be empty")
    connection_id = values["connection_id"]
    if connection_id is not None and (type(connection_id) is not int or connection_id <= 0):
        raise ValueError("draft connection_id must be a positive integer or None")
    if (draft_id, expected_revision) != (None, None) and (
            type(draft_id) is not int or draft_id <= 0
            or type(expected_revision) is not int or expected_revision <= 0):
        raise ValueError("existing draft requires positive integer draft_id and expected_revision")
    with db:
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        if connection_id is not None:
            c = db.execute("SELECT platform FROM connections WHERE id = ?", (connection_id,)).fetchone()
            platform = platforms.PLATFORMS.get(c["platform"]) if c else None
            if platform is None or platform.format != values["kind"]:
                raise ValueError("draft Connection must support its message format")
        at = _iso(_now())
        envelope = tuple(values[k] for k in ("connection_id", "kind", "sender", "to", "cc", "subject", "body"))
        if draft_id is None:
            draft_id = db.execute(
                'INSERT INTO composition_drafts (connection_id, kind, sender, "to", cc, subject, body,'
                " revision, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (*envelope, at, at)).lastrowid
        else:
            saved = db.execute(
                'UPDATE composition_drafts SET connection_id = ?, kind = ?, sender = ?, "to" = ?,'
                " cc = ?, subject = ?, body = ?, revision = revision + 1, updated_at = ?"
                " WHERE id = ? AND revision = ?", (*envelope, at, draft_id, expected_revision)).rowcount
            if not saved:
                get_draft(db, draft_id)  # a missing row is distinct from a stale writer
                raise DraftConflict("Draft changed since this revision; reload before saving.")
        return get_draft(db, draft_id)


def get_draft(db, draft_id: int) -> dict:
    if type(draft_id) is not int or draft_id <= 0:
        raise ValueError("draft_id must be a positive integer")
    r = db.execute("SELECT * FROM composition_drafts WHERE id = ?", (draft_id,)).fetchone()
    if r is None:
        raise KeyError(f"draft {draft_id}")
    return dict(r)


def delete_draft(db, draft_id: int) -> dict:
    """Remove one unsent Draft; return its row so `restore_draft` can put it back."""
    with db:
        draft = get_draft(db, draft_id)
        db.execute("DELETE FROM composition_drafts WHERE id = ?", (draft_id,))
    return draft


def restore_draft(db, draft: dict) -> None:
    columns = ", ".join(f'"{c}"' for c in draft)
    with db:
        db.execute(f"INSERT INTO composition_drafts ({columns}) VALUES ({', '.join('?' * len(draft))})",
                   tuple(draft.values()))


def list_drafts(db) -> list[dict]:
    return [dict(r) for r in db.execute("SELECT * FROM composition_drafts ORDER BY updated_at DESC, id DESC")]


def recipient_names(db, to: str, kind: str) -> str:
    """Draft recipients as People names; addresses no Person owns stay as typed. Slack: the conversation's name."""
    if kind == "slack":
        return re.sub(r"\s*<[A-Z0-9]+>", "", to).strip()
    names = []
    for typed, value in ([(n, n) for n in _destinations(to, "sms")] if kind == "sms"
                         else [(person_name(n) or a, a.lower()) for n, a in getaddresses([to]) if a]):
        row = db.execute("SELECT p.display_name FROM identities i JOIN persons p ON p.id = i.person_id"
                         " WHERE i.kind = ? AND i.value = ?", ("phone" if kind == "sms" else "email", value)).fetchone()
        names.append(row[0] if row else typed)
    return ", ".join(names)


def draft_problem(values: dict) -> str | None:
    """What a Draft still needs before it can send; None when complete. Providers judge the addresses."""
    if values["connection_id"] is None:
        return "Choose a Connection"
    if values["kind"] == "slack" and len(_destinations(values["to"], "slack")) != 1:
        return "Choose one conversation"
    if not values["to"].strip():
        return "Add a recipient"
    if values["kind"] == "sms" and len(_destinations(values["to"], "sms")) != 1:
        return "SMS needs one number"
    if values["kind"] == "email" and not values["subject"].strip():
        return "Add a subject"
    if not values["body"].strip():
        return "Write a message"
    return None


def _destinations(text: str, kind: str) -> list[str]:
    if kind == "slack":  # "#eng <C0123>": the label is for people, the ID is where it posts
        return re.findall(r"<([A-Z0-9]+)>", text)
    if kind == "sms":
        return [_e164(n) for n in re.split(r"[,;]+", text) if n.strip()]
    return [a for _, a in getaddresses([text]) if a]


def _e164(number: str) -> str:
    """The +E.164 form Quo files messages under, so replies find the Thread.
    ponytail: bare numbers are taken as North American; type + for other countries."""
    digits = re.sub(r"\D", "", number)
    if number.strip().startswith("+"):
        return "+" + digits
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return number.strip()


def send_draft(db, draft_id: int, revision: int, sender: Sender, *, again: bool = False) -> Thread:
    """Send one saved Draft revision, record it, then remove the Draft. An email starts a new Thread; an SMS or
    Slack message is recorded like any message Jeremy sends there, so it joins the conversation's Thread unless
    the conversation has been quiet long enough (FR-33).

    `sending_at` is set before the provider call and cleared only on a refusal, so a send whose outcome is
    unknown leaves the Draft locked; `again` is Jeremy's explicit choice to send it once more anyway.
    """
    with db:
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        d = get_draft(db, draft_id)
        if d["revision"] != revision:
            raise DraftConflict("Draft changed since this revision; reload before sending.")
        if d["sending_at"] and not again:
            raise SendUnknown("Send outcome unknown; check before sending again.")
        problem = draft_problem(d)
        if problem:
            raise ValueError(problem)
        connection = db.execute("SELECT * FROM connections WHERE id = ?", (d["connection_id"],)).fetchone()
        at = _iso(_now())
        db.execute("UPDATE composition_drafts SET sending_at = ? WHERE id = ?", (at, draft_id))
    sms = d["kind"] == "sms"
    to, cc = _destinations(d["to"], d["kind"]), _destinations(d["cc"], d["kind"])
    try:
        native_id = sender.start(to, cc, d["subject"], d["body"], f"draft-{draft_id}-{revision}-{at}")
    except SendUnknown:
        raise
    except SendFailed:
        with db:
            db.execute("UPDATE composition_drafts SET sending_at = NULL WHERE id = ?", (draft_id,))
        raise
    except Exception as e:  # anything unexpected after the claim may have reached the provider
        raise SendUnknown(f"Send outcome unknown: {e}") from e
    now = _now()
    with db:
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        echo = db.execute("SELECT thread_id FROM activities WHERE kind = 'MESSAGE' AND native_id = ?",
                          (native_id,)).fetchone()
        if echo:  # the provider's own copy arrived first (Fastmail Sent poll, Quo webhook, Slack event)
            thread_id = get_thread(db, echo["thread_id"]).id
        elif d["kind"] in ("sms", "slack"):
            title = next((ln.strip() for ln in d["body"].splitlines() if ln.strip()), "Text")[:80]
            if sms:
                inc = Incoming(connection["id"], to[0], "im", "MESSAGE", native_id, None, now, d["body"],
                               title=title, data={"peer": to[0]}, channel_name=to[0])
            else:
                kind, label = sender.conversation(to[0])
                inc = Incoming(connection["id"], to[0], kind, "MESSAGE", native_id, None, now, d["body"],
                               title=title, data={"channel": to[0], "ts": native_id.split(":", 1)[1], "kind": kind},
                               channel_name=label)
            thread_id = _ingest(db, inc).id
        else:
            mailbox = db.execute("SELECT native_id FROM channels WHERE connection_id = ? AND kind = 'mailbox'",
                                 (connection["id"],)).fetchone()
            channel = mailbox["native_id"] if mailbox else connection["self_id"]
            channel_id = _channel_id(db, Incoming(connection["id"], channel, "mailbox", "MESSAGE",
                                                  native_id, None, now, d["body"]))
            title = d["subject"].strip()[:80]
            status = _initial_status(None)
            thread_id = db.execute(
                "INSERT INTO threads (channel_id, native_thread_id, title, status, status_detail,"
                " status_changed_at, created_at, follow_up_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (channel_id, native_id, title, *status, _iso(now), _iso(now), _follow_up(status[1], now))).lastrowid
            data = {"from": connection["self_id"], "to": to, "cc": cc, "subject": d["subject"]}
            db.execute("INSERT INTO activities (thread_id, kind, occurred_at, native_id, content, data)"
                       " VALUES (?, 'MESSAGE', ?, ?, ?, ?)", (thread_id, _iso(now), native_id, d["body"], json.dumps(data)))
        db.execute("DELETE FROM composition_drafts WHERE id = ?", (draft_id,))
    return get_thread(db, thread_id)


def _set_status(db, thread_id, status, detail, at, snoozed_until=None, before=None):
    follow_up = _follow_up(detail, at)
    db.execute(
        "UPDATE threads SET status = ?, status_detail = ?, status_changed_at = ?,"
        " snoozed_until = ?, detail_before_snooze = ?, follow_up_at = ? WHERE id = ?",
        (status, detail, _iso(at), snoozed_until, before, follow_up, thread_id))


def _has_replied(db, thread_id) -> bool:
    return db.execute(
        "SELECT 1 FROM thread_activities WHERE thread_id = ? AND kind = 'MESSAGE'"
        " AND identity_id IS NULL", (thread_id,)).fetchone() is not None


# --- status rules (design §4) -------------------------------------------------

def _initial_status(identity) -> tuple[str, str]:
    """A new Thread's Status: Jeremy started it (#61), a Blocked sender (FR-7a), or anyone else (FR-6a)."""
    if identity is None:
        return ("SNOOZED", "WAITING_FOR_CUSTOMER")
    return ("DONE", "IGNORED") if identity["blocked"] else ("TODO", "CREATED")


def _follow_up(detail, at) -> str | None:
    """Waiting for them ends after WAIT with no reply (#116)."""
    return _iso(at + WAIT) if detail == "WAITING_FOR_CUSTOMER" else None


def reopen_imported(db, thread_id: int) -> Thread:
    """A one-time import leaves every touched Thread in Todo: Needs next response once Jeremy has replied,
    else Needs first response."""
    t = get_thread(db, thread_id)
    if t.status == "TODO":
        return t
    return act(db, t.id, MarkTodo("NEW_REPLY" if _has_replied(db, t.id) else "CREATED"))


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
                   " VALUES (?, ?, ?)", (_org(domain), _org(domain), _iso(_now())))


AUTOMATED = re.compile(r"^(no-?reply|do-?not-?reply|donotreply|noreply|service|notifications?|notify|alerts?|"
                       r"mailer(-daemon)?|bounces?|support|team|info|hello|news(letter)?|updates?|billing|"
                       r"invoices?|receipts?|accounts?|calendar|esignature[-\w]*|[a-z]-[0-9a-f]{20,})@", re.I)


def person_name(name: str) -> str:
    """How a Person is named: "First Last". Outlook's "Last, First" is turned around (Jeremy, 2026-10-12)."""
    name = re.sub(r"\s+", " ", (name or "").strip().strip('"'))
    m = re.fullmatch(r"([^,]+),\s*([^,]+)", name)
    return f"{m.group(2)} {m.group(1)}" if m and "@" not in name else name


GENERIC = re.compile(AUTOMATED.pattern.replace(r"|[a-z]-[0-9a-f]{20,}", ""), re.I)  # shared role addresses


def sender_name(name: str | None, address: str) -> str:
    """The Person's name for a sender (#117). A shared service address (service@, noreply@, …) is named after
    the service, not the human it writes for: "Jeremy Legere (via Google Workspace eSignatures)" is "Google
    Workspace eSignatures", and a TidyCal booking "from" Tony Sheehan is "Tidycal". Everyone else keeps their
    own name, in First Last order."""
    name = person_name(name or "") or address
    if "@" not in address or not GENERIC.match(address):
        return name
    via = re.search(r"\(via ([^)]+)\)", name)
    if via:
        return via.group(1).strip()
    org = _org(address.rsplit("@", 1)[1]).split(".")[0]
    return name if org in _name_key(name).replace(" ", "") else org.capitalize()


def _name_key(name: str) -> str:
    """Comparable form of a full name: First Last order, no accents, no "(via …)", lower case."""
    import unicodedata
    name = re.sub(r"\(.*?\)", "", person_name(name))
    name = "".join(c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c))
    return " ".join(name.lower().split())


# Public suffixes of two labels that Jeremy's correspondents use (Canadian provinces, UK, AU, NZ, …).
# ponytail: hand list, not the full Public Suffix List; add tldextract if an Organization splits or merges wrongly
TWO_LABEL_SUFFIXES = frozenset(
    f"{p}.ca" for p in ("ab", "bc", "mb", "nb", "nf", "nl", "ns", "nt", "nu", "on", "pe", "qc", "sk", "yk", "gc")
) | frozenset({"co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au", "gov.au", "co.nz",
               "org.nz", "co.jp", "com.br", "co.in", "com.mx", "co.za"})


def _org(domain: str) -> str:
    """The Organization key of a domain: the domain its company registers. accounts.google.com -> google.com,
    ws-ts.nb.ca stays whole (nb.ca is a public suffix). Used to store, match, and join Organizations."""
    labels = domain.lower().split(".")
    return ".".join(labels[-3:] if ".".join(labels[-2:]) in TWO_LABEL_SUFFIXES else labels[-2:])


def _company_domains(db, person_id, automated: bool = False) -> set[str]:
    """The organizations a Person writes from: humans' company domains, or (automated=True) the domains of
    automated senders, which only ever match other automated senders."""
    return {_org(r[0].rsplit("@", 1)[1]) for r in db.execute(
        "SELECT value FROM identities WHERE person_id = ? AND kind = 'email'", (person_id,))
        if bool(AUTOMATED.match(r[0])) == automated and r[0].rsplit("@", 1)[1] not in FREE_MAIL}


def _matches(key: str, mine: set[str], theirs: set[str], automated: bool) -> bool:
    """The same-person rule (#117) once a name key matches: people need a full name (two words or more) and a
    shared company organization; automated senders a shared sender organization."""
    return bool(key) and (automated or len(key.split()) >= 2) and bool(mine & theirs)


def _same_person(db, name: str, value: str, kind: str) -> int | None:
    """The existing Person this sender is (#117): the same name and the same organization. For people, a full
    name (two words or more) and a company domain; for automated senders (noreply, notifications, …) the same
    sender name from the same organization, so Google's no-reply addresses are one "Google Takeout". Never on
    free-mail domains, and never between a person and an automated relay (a TidyCal booking "from" Tony)."""
    key = _name_key(name or "")
    if kind != "email" or not key:
        return None
    automated = bool(AUTOMATED.match(value))
    domain = value.rsplit("@", 1)[1]
    if domain in FREE_MAIL:
        return None
    for p in db.execute("SELECT id, display_name FROM persons").fetchall():
        if _name_key(p["display_name"]) == key and _matches(key, {_org(domain)}, _company_domains(db, p["id"], automated),
                                                            automated):
            return p["id"]
    return None


def rename_person(db, person_id: int, name: str) -> None:
    """Jeremy's own name for a Person; senders' display names only replace a name that is still an address."""
    with db:
        db.execute("UPDATE persons SET display_name = ? WHERE id = ?", (name.strip(), person_id))


def merge_persons(db, keep: int, drop: int) -> None:
    """One Person from two: `drop`'s Identities (and so its Threads) move to `keep`, which keeps the
    fuller name."""
    if keep == drop:
        return
    with db:
        name = _merged_name(db, keep, drop)
        db.execute("UPDATE identities SET person_id = ? WHERE person_id = ?", (keep, drop))
        db.execute("DELETE FROM persons WHERE id = ?", (drop,))
        db.execute("UPDATE persons SET display_name = ? WHERE id = ?", (name, keep))


def _merged_name(db, keep: int, drop: int) -> str:
    names = [r[0] for r in db.execute("SELECT display_name FROM persons WHERE id IN (?, ?) ORDER BY id = ?",
                                      (keep, drop, drop))]
    return person_name(max(names, key=lambda n: ("@" not in n, len(_name_key(n).split()), len(n))))


def merge_preview(db, keep: int, drop: int) -> dict:
    """What merge_persons(keep, drop) would make, without changing anything: name, Identities,
    Organizations, and Thread counts."""
    with _read_snapshot(db):
        identities = [dict(r) for r in db.execute(
            "SELECT kind, value FROM identities WHERE person_id IN (?, ?) ORDER BY kind, value", (keep, drop))]
        threads = {t.id: t for pid in (keep, drop) for t in person_threads(db, pid)}
        orgs = sorted({o for pid in (keep, drop) for o in person_organizations(db, pid)})
        return {"name": _merged_name(db, keep, drop), "identities": identities, "organizations": orgs,
                "threads": len(threads), "todo": sum(t.status == "TODO" for t in threads.values())}


def merge_duplicates(db) -> list[tuple[str, str]]:
    """Apply the same-person rule to everyone stored, and turn names to First Last; returns (kept, merged)."""
    merged = []
    with db:
        for p in db.execute("SELECT id, display_name FROM persons").fetchall():
            addrs = [r[0] for r in db.execute("SELECT value FROM identities WHERE person_id = ?", (p["id"],))]
            # a Person whose only address is a shared service address is named after the service
            name = sender_name(p["display_name"], addrs[0]) if len(addrs) == 1 else person_name(p["display_name"])
            if name != p["display_name"]:
                db.execute("UPDATE persons SET display_name = ? WHERE id = ?", (name, p["id"]))
    people = db.execute("SELECT id, display_name FROM persons ORDER BY id").fetchall()
    gone = set()
    for i, a in enumerate(people):
        for b in people[i + 1:]:
            key = _name_key(a["display_name"])
            if a["id"] in gone or b["id"] in gone or key != _name_key(b["display_name"]):
                continue
            if any(_matches(key, _company_domains(db, a["id"], auto), _company_domains(db, b["id"], auto), auto)
                   for auto in (False, True)):
                merge_persons(db, a["id"], b["id"])
                gone.add(b["id"])
                merged.append((a["display_name"], b["display_name"]))
    return merged


def import_people(db, people: list[dict]) -> dict:
    """Attio People into local Persons (Jeremy, 2026-10-14). One Attio person is one Person: local Persons that
    own any of their addresses or numbers are merged, and the rest are added. Attio's name wins unless the
    local one is fuller ("Kevin McFarlane" over "Kevin"). Their Company names the Organizations of its domains."""
    report = {"created": [], "merged": [], "renamed": [], "organizations": []}
    mine = {r[0].lower() for r in db.execute("SELECT self_id FROM connections") if r[0]}
    own_domains = {m.rsplit("@", 1)[1] for m in mine if "@" in m}

    def usable(kind, value):  # an address that is one human's own
        if kind != "email":
            return True
        local, _, domain = value.partition("@")
        return not (AUTOMATED.match(value) or local.startswith(("prvs=", "postmaster", "unsubscribe"))
                    or domain.startswith(("noreply.", "reply.")) or ("+" in local and domain in own_domains))

    def vendor(p):  # a brand writing as itself: "Cloudflare" from cloudflare.com, "The Quo Team", "NoReply"
        name = p["name"].strip()
        if re.search(r"\b(team|support|no-?reply)\b", name, re.I):
            return True
        return " " not in name and any(name.lower() in _org(e.rsplit("@", 1)[-1]) for e in p["emails"] if name)

    every = [[("email", e.lower()) for e in p["emails"]] + [("phone", n) for n in p["phones"]] for p in people]
    claims = Counter(i for ids in every for i in set(ids))
    for p, ids in zip(people, every):
        if any(value in mine for _, value in ids) or vendor(p):  # Jeremy himself is never a Person
            continue
        # a company's main line or a shared inbox listed on several Attio People belongs to none of them
        ids = [i for i in ids if claims[i] == 1 and usable(*i)]
        if not ids:
            continue
        owners = sorted({r[0] for kind, value in ids for r in db.execute(
            "SELECT person_id FROM identities WHERE kind = ? AND value = ? AND person_id IS NOT NULL", (kind, value))})
        name = clean_name(p["name"])
        if not owners:
            name = name or address_name(ids[0][1]) or ids[0][1]
            with db:
                keep = _new_person(db, name)
            report["created"].append(name)
        else:
            keep = owners[0]
            for drop in owners[1:]:
                names = [display_name(db, keep), display_name(db, drop)]
                merge_persons(db, keep, drop)
                report["merged"].append(names)
        current = display_name(db, keep)
        new, old = _name_key(name).split(), _name_key(current).split()
        fuller = len(old) > len(new) and old[:len(new)] == new and "@" not in current
        if name and new != old and not fuller:
            rename_person(db, keep, name)
            report["renamed"].append([current, name])
        for kind, value in ids:
            add_identity(db, keep, kind, value)
        company = p["company"]["name"].strip()
        for domain in p["company"]["domains"] if company else []:
            with db:
                if db.execute("UPDATE organizations SET name = ? WHERE domain = ? AND name != ?",
                              (company, _org(domain), company)).rowcount:
                    report["organizations"].append([_org(domain), company])
    return report


def address_name(value: str) -> str:
    """A name read from an address like leslie.magee@… ("Leslie Magee"); "" when it holds no first and last name."""
    parts = re.split(r"[._]", re.sub(r"\d+", "", value.partition("@")[0])) if "@" in value else []
    return " ".join(x.capitalize() for x in parts) if len(parts) >= 2 and all(
        len(x) >= 2 and x.isalpha() for x in parts) else ""


def clean_name(name: str) -> str:
    """A CRM name as a Person's name: First Last, without notes in brackets or a P.Eng credential."""
    name = re.sub(r"\([^)]*\)", " ", name or "")
    name = re.sub(r"(?:^|[\s,-]+)P\.\s?Eng\.?(?=\s|,|$)", " ", name, flags=re.I)
    return person_name(re.sub(r"\s+", " ", name).strip(" ,-"))


def display_name(db, person_id: int) -> str:
    return db.execute("SELECT display_name FROM persons WHERE id = ?", (person_id,)).fetchone()[0]


def _new_person(db, name) -> int:
    return db.execute("INSERT INTO persons (display_name, created_at) VALUES (?, ?)",
                      (name, _iso(_now()))).lastrowid


def _identity_for(db, sender: Identity) -> sqlite3.Row:
    """Find or create the sender's Identity and attach it to a Person (FR-10)."""
    value = sender.value.lower() if sender.kind == "email" else sender.value
    row = db.execute("SELECT * FROM identities WHERE kind = ? AND value = ?",
                     (sender.kind, value)).fetchone()
    if row is not None and row["person_id"] is not None:
        if sender.display_name:  # a Person still named by its address or user ID gets the real name
            db.execute("UPDATE persons SET display_name = ? WHERE id = ? AND display_name = ?",
                       (sender_name(sender.display_name, value), row["person_id"], value))
        return row
    person_id = None
    if sender.kind == "slack_user" and sender.profile_email:
        owner = db.execute(
            "SELECT person_id FROM identities WHERE kind = 'email' AND value = ?"
            " AND person_id IS NOT NULL", (sender.profile_email.lower(),)).fetchone()
        person_id = owner and owner["person_id"]
    if person_id is None and sender.display_name:
        person_id = _same_person(db, sender.display_name, value, sender.kind)
    if person_id is None:
        person_id = _new_person(db, sender_name(sender.display_name, value))
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
    if inc.channel_name:
        db.execute("UPDATE channels SET name = ? WHERE connection_id = ? AND native_id = ? AND name IS NOT ?",
                   (inc.channel_name, inc.connection_id, inc.channel, inc.channel_name))
    return db.execute("SELECT id FROM channels WHERE connection_id = ? AND native_id = ?",
                      (inc.connection_id, inc.channel)).fetchone()["id"]


def _thread_for_message(db, channel_id, inc: Incoming, identity_id):
    """Existing Thread this message joins, or None (design §4 Threading)."""
    if inc.channel_kind == "mailbox":
        rows, own, refs, known = _email_evidence(db)
        declared = _email_ids(inc.thread_ref, ordered=True)
        ids = set(declared) | _email_ids([inc.native_id], single=True)
        ids = {n for n in ids if not (known.get(n, set()) - {channel_id})}
        for native_id in declared:
            if native_id not in ids:
                continue
            for a in rows:
                if a["channel_id"] == channel_id and native_id in own[a["id"]]:
                    return a["thread_id"]
        return min((a["thread_id"] for a in rows if a["channel_id"] == channel_id
                    and ids & (own[a["id"]] | refs[a["id"]])), default=None)
    for ref in inc.thread_ref:
        r = db.execute("SELECT a.thread_id FROM thread_activities a JOIN threads t ON t.id = a.thread_id"
                       " WHERE t.channel_id = ? AND a.kind = 'MESSAGE' AND a.native_id = ?",
                       (channel_id, ref)).fetchone()
        if r:
            return r["thread_id"]
    if inc.data.get("thread_ts") not in (None, inc.data.get("ts")):
        return None  # the first reply to a top-level Slack message starts its own Thread (FR-4b)
    if inc.channel_kind in ("channel", "private"):
        # A top-level message joins the channel's latest top-level conversation unless the channel has been
        # quiet for a day (FR-4b, FR-38).
        last = db.execute("SELECT max(a.occurred_at) FROM thread_activities a JOIN threads t ON t.id = a.thread_id"
                          " WHERE t.channel_id = ? AND a.kind = 'MESSAGE'", (channel_id,)).fetchone()[0]
        if last is None or inc.occurred_at - datetime.fromisoformat(last) > SLACK_QUIET:
            return None
        r = db.execute(
            "SELECT a.thread_id FROM thread_activities a JOIN threads t ON t.id = a.thread_id"
            " WHERE t.channel_id = ? AND a.kind = 'MESSAGE' AND json_extract(a.data, '$.ts') IS NOT NULL"
            " AND coalesce(json_extract(a.data, '$.thread_ts'), json_extract(a.data, '$.ts')) = json_extract(a.data, '$.ts')"
            " ORDER BY a.occurred_at DESC, a.id DESC LIMIT 1", (channel_id,)).fetchone()
        return r["thread_id"] if r else None
    last = db.execute(
        "SELECT a.thread_id, a.identity_id, a.occurred_at FROM thread_activities a"
        " JOIN threads t ON t.id = a.thread_id"
        " WHERE t.channel_id = ? AND a.kind = 'MESSAGE'"
        " ORDER BY a.occurred_at DESC, a.id DESC LIMIT 1", (channel_id,)).fetchone()
    if last is None:
        return None
    gap = inc.occurred_at - datetime.fromisoformat(last["occurred_at"])
    platform = db.execute("SELECT platform FROM connections WHERE id = ?", (inc.connection_id,)).fetchone()
    window = SMS_QUIET if platform and platforms.PLATFORMS[platform[0]].format == "sms" else SLACK_QUIET
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
        "SELECT content FROM thread_activities WHERE thread_id = ? AND kind = 'MESSAGE' AND identity_id IS NULL"
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


# --- attachments (.agents/prd/2026-10-15-attachments.md) -------------------

ATTACHMENT_TRIES = 5  # downloads given up after this many failures


def files_dir(db) -> Path | None:
    """Where Attachment bytes are kept: `files/` beside the database; None in memory."""
    f = db.execute("PRAGMA database_list").fetchone()[2]
    return Path(f).parent / "files" if f else None


def store_file(db, data: bytes) -> str | None:
    """Keep one file's bytes, content-addressed and 0600; returns the sha256 name (unchanged once written)."""
    d = files_dir(db)
    if d is None:
        return None
    sha = hashlib.sha256(data).hexdigest()
    d.mkdir(mode=0o700, exist_ok=True)
    path = d / sha
    if not path.exists():
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:  # another process wrote the same bytes first
            return sha
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    return sha


def attachment_path(db, sha: str) -> Path | None:
    d = files_dir(db)
    return d / sha if d is not None and re.fullmatch(r"[0-9a-f]{64}", sha or "") else None


def add_attachments(db, activity_id: int, items: list[dict]) -> list[int]:
    """Record a message's Attachments in order. Each item: name, type, size, and optional inline, content_id,
    source (dict), sha. An item with a sha is stored; one without waits for a download."""
    with db:
        return [db.execute(
            "INSERT INTO attachments (activity_id, position, name, type, size, inline, content_id, source, sha, state)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (activity_id, k, it["name"], it["type"], it["size"], int(bool(it.get("inline"))), it.get("content_id"),
             json.dumps(it.get("source") or {}), it.get("sha"), "stored" if it.get("sha") else "pending")).lastrowid
            for k, it in enumerate(items)]


def attachments(db, activity_id: int) -> list[dict]:
    return [dict(r) for r in db.execute(
        "SELECT * FROM attachments WHERE activity_id = ? ORDER BY position", (activity_id,))]


def pending_attachments(db, limit: int = 50) -> list[dict]:
    return [dict(r) for r in db.execute(
        "SELECT * FROM attachments WHERE state = 'pending' ORDER BY id LIMIT ?", (limit,))]


def mark_stored(db, attachment_id: int, sha: str) -> None:
    with db:
        db.execute("UPDATE attachments SET sha = ?, state = 'stored' WHERE id = ?", (sha, attachment_id))


def mark_failed(db, attachment_id: int) -> None:
    """Count one failed download; after ATTACHMENT_TRIES the Attachment stays failed (its chip keeps the Platform link)."""
    with db:
        db.execute("UPDATE attachments SET tries = tries + 1,"
                   " state = CASE WHEN tries + 1 >= ? THEN 'failed' ELSE 'pending' END WHERE id = ?",
                   (ATTACHMENT_TRIES, attachment_id))


def _owner(draft_id, thread_id) -> tuple[str, int]:
    if (draft_id is None) == (thread_id is None):
        raise ValueError("stage on exactly one of draft_id or thread_id")
    return ("draft_id", draft_id) if draft_id is not None else ("thread_id", thread_id)


def stage(db, *, sha: str, name: str, type: str, size: int,
          draft_id: int | None = None, thread_id: int | None = None) -> int:
    """Attach a stored file to an unsent Draft or to one Thread's open reply."""
    column, owner = _owner(draft_id, thread_id)
    path = attachment_path(db, sha)
    if files_dir(db) is not None and (path is None or not path.is_file()):
        raise ValueError("stage needs the sha256 of a stored file")
    with db:
        return db.execute(f"INSERT INTO staged_attachments ({column}, sha, name, type, size, added_at)"
                          " VALUES (?, ?, ?, ?, ?, ?)", (owner, sha, name, type, size, _iso(_now()))).lastrowid


def staged(db, *, draft_id: int | None = None, thread_id: int | None = None) -> list[dict]:
    column, owner = _owner(draft_id, thread_id)
    return [dict(r) for r in db.execute(
        f"SELECT * FROM staged_attachments WHERE {column} = ? ORDER BY id", (owner,))]


def unstage(db, staged_id: int) -> None:
    with db:
        db.execute("DELETE FROM staged_attachments WHERE id = ?", (staged_id,))


def clear_staged(db, *, draft_id: int | None = None, thread_id: int | None = None) -> None:
    column, owner = _owner(draft_id, thread_id)
    with db:
        db.execute(f"DELETE FROM staged_attachments WHERE {column} = ?", (owner,))


EMAIL_ATOM = r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
EMAIL_DOT_ATOM = EMAIL_ATOM + r"(?:\." + EMAIL_ATOM + r")*"
EMAIL_ID = re.compile(r"(?<!<)<" + EMAIL_DOT_ATOM + r"@(?:" + EMAIL_DOT_ATOM
                      + r"|\[[A-Za-z0-9:.%-]+\])>(?!>)")


def _email_ids(values, single=False, ordered=False) -> set[str] | list[str]:
    """Exact bracketed Message-ID sequences; skip quoted prose and CFWS, optionally retain declared order."""
    text = values if isinstance(values, str) else " ".join(v for v in values if isinstance(v, str))
    clean, depth, quoted, escaped = [], 0, False, False
    for c in text:
        if escaped:
            escaped = False
        elif quoted:
            if c == "\\":
                escaped = True
            elif c == '"':
                quoted = False
        elif depth:
            if c == "\\":
                escaped = True
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
        elif c == "(":
            clean.append(" ")
            depth = 1
        elif c == '"':
            clean.append(" ")
            quoted = True
        elif c == "\\":
            clean.append(" ")
            escaped = True
        else:
            clean.append(c)
    text = "".join(clean).strip()
    ids = []
    if not (depth or quoted or escaped):
        if single:
            ids = [text] if EMAIL_ID.fullmatch(text) else []
        elif not EMAIL_ID.sub("", text).strip():
            ids = [m.group() for m in EMAIL_ID.finditer(text)]
    return ids if ordered else set(ids)


def _email_evidence(db):
    # ponytail: scan mailbox evidence per ingest; incremental components if mailbox volume makes this slow.
    rows = db.execute(
        "SELECT a.id, a.thread_id, a.native_id, a.data, t.channel_id FROM thread_activities a"
        " JOIN threads t ON t.id = a.thread_id JOIN channels ch ON ch.id = t.channel_id"
        " WHERE a.kind = 'MESSAGE' AND ch.kind = 'mailbox' ORDER BY a.id").fetchall()
    own, refs, known = {}, {a["id"]: set() for a in rows}, {}
    for a in rows:
        own[a["id"]] = _email_ids([a["native_id"]], single=True)
        for n in own[a["id"]]:
            known.setdefault(n, set()).add(a["channel_id"])
    for r in db.execute("SELECT activity_id, native_id FROM email_refs"):
        if r["activity_id"] in refs:
            refs[r["activity_id"]] |= _email_ids([r["native_id"]])
    return rows, own, refs, known


def _reconcile_email_threads(db, apply, channel_id, recover=False) -> dict:
    if channel_id is not None:
        ch = db.execute("SELECT kind FROM channels WHERE id = ?", (channel_id,)).fetchone()
        if ch is None or ch["kind"] != "mailbox":
            raise ValueError(f"channel {channel_id} is not a mailbox")
    rows, own, refs, known = _email_evidence(db)
    rows = [a for a in rows if channel_id is None or a["channel_id"] == channel_id]
    result = {"groups": [], "conflicts": [], "merged": 0, "references_added": 0,
              "messages": len(rows), "raw_headers": 0, "raw_missing": 0, "raw_invalid": 0,
              "held_references": 0}
    added = []
    if recover:
        d = raw_dir(db)
        for a in rows:
            data = json.loads(a["data"])
            sha = data.get("raw") if isinstance(data, dict) else None
            if not sha or d is None:
                result["raw_missing"] += 1
                continue
            if not isinstance(sha, str) or re.fullmatch(r"[0-9a-f]{64}", sha) is None or d.is_symlink():
                result["raw_invalid"] += 1
                continue
            path = d / f"{sha}.eml"
            try:
                if not path.is_file():
                    result["raw_missing"] += 1
                    continue
                with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as f:
                    raw = f.read()
                if hashlib.sha256(raw).hexdigest() != sha:
                    raise ValueError("raw hash mismatch")
                headers = BytesHeaderParser(policy=policy.default).parsebytes(raw)
                mids = headers.get_all("Message-ID", [])
                ids = _email_ids([str(v) for v in mids], single=True)
                if not ((len(mids) == 1 and len(ids) == 1 and ids == own[a["id"]])
                        or (not mids and a["native_id"] == f"<sha256-{sha}@inbox>")):
                    raise ValueError("raw Message-ID mismatch")
                recovered = _email_ids([str(v) for h in ("In-Reply-To", "References")
                                        for v in headers.get_all(h, [])])
            except (OSError, ValueError, TypeError, UnicodeError):
                result["raw_invalid"] += 1
                continue
            result["raw_headers"] += 1
            added += [(a["id"], n) for n in sorted(recovered - refs[a["id"]])]
            refs[a["id"]] |= recovered

    threads = {t["id"]: t for t in db.execute(
        "SELECT t.* FROM threads t JOIN channels ch ON ch.id = t.channel_id"
        " WHERE t.merged_into IS NULL AND ch.kind = 'mailbox'"
        + (" AND t.channel_id = ?" if channel_id is not None else ""),
        (channel_id,) if channel_id is not None else ())}
    parent, tokens = {tid: tid for tid in threads}, {}

    def root(tid):
        while parent[tid] != tid:
            parent[tid] = parent[parent[tid]]
            tid = parent[tid]
        return tid

    for a in rows:
        valid = {n for n in refs[a["id"]] if not (known.get(n, set()) - {a["channel_id"]})}
        result["held_references"] += len(refs[a["id"]] - valid)
        for n in sorted(own[a["id"]] | valid):
            key = a["channel_id"], n
            other = tokens.setdefault(key, a["thread_id"])
            left, right = root(a["thread_id"]), root(other)
            parent[max(left, right)] = min(left, right)
    components = {}
    for tid in sorted(threads):
        components.setdefault(root(tid), []).append(tid)
    for ids in components.values():
        if len(ids) < 2:
            continue
        case_ids = {threads[tid]["case_id"] for tid in ids} - {None}
        if len(case_ids) > 1:
            result["conflicts"].append(ids)
            continue
        source = max(ids, key=lambda tid: (datetime.fromisoformat(threads[tid]["status_changed_at"]), -tid))
        result["groups"].append({"keep": ids[0], "merge": ids[1:], "status_source": source,
                                 "case_id": next(iter(case_ids), None)})
    if apply:
        result["references_added"] = db.executemany(
            "INSERT OR IGNORE INTO email_refs (activity_id, native_id) VALUES (?, ?)", added).rowcount
        for g in result["groups"]:
            state = threads[g["status_source"]]
            db.execute(
                "UPDATE threads SET status = ?, status_detail = ?, status_changed_at = ?, snoozed_until = ?,"
                " detail_before_snooze = ?, follow_up_at = ?, case_id = ? WHERE id = ?",
                (state["status"], state["status_detail"], state["status_changed_at"], state["snoozed_until"],
                 state["detail_before_snooze"], state["follow_up_at"], g["case_id"], g["keep"]))
            marks = ",".join("?" * len(g["merge"]))
            db.execute(f"UPDATE threads SET merged_into = ? WHERE merged_into IN ({marks})",
                       (g["keep"], *g["merge"]))
            result["merged"] += db.execute(f"UPDATE threads SET merged_into = ? WHERE id IN ({marks})",
                                           (g["keep"], *g["merge"])).rowcount
    return result


def reconcile_email_threads(db, apply: bool = False, channel_id: int | None = None) -> dict:
    """Preview exact mailbox reference groups; explicit apply atomically indexes raw headers and aliases."""
    if not isinstance(apply, bool):
        raise ValueError("apply must be True or False")
    if channel_id is not None and type(channel_id) is not int:
        raise ValueError("channel_id must be a mailbox ID or None")
    if not apply:
        with _read_snapshot(db):
            return _reconcile_email_threads(db, False, channel_id, recover=True)
    with db:
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        return _reconcile_email_threads(db, True, channel_id, recover=True)


def ingest(db, inc: Incoming) -> Thread | None:
    """Store one message, edit, or deletion; returns its Thread, or None for a
    duplicate or an edit/deletion of a message that was never stored."""
    with db:
        return _ingest(db, inc)


def _ingest(db, inc: Incoming) -> Thread | None:
    """`ingest` inside the caller's transaction."""
    duplicate = db.execute(
        "SELECT a.id, t.channel_id FROM activities a JOIN threads t ON t.id = a.thread_id"
        " WHERE a.kind = ? AND a.native_id = ?", (inc.kind, inc.native_id)).fetchone()
    if duplicate:
        if inc.kind == "MESSAGE" and inc.channel_kind == "mailbox":
            ch = db.execute("SELECT id FROM channels WHERE connection_id = ? AND native_id = ?"
                            " AND kind = 'mailbox'", (inc.connection_id, inc.channel)).fetchone()
            if ch and ch["id"] == duplicate["channel_id"]:
                added = db.executemany(
                    "INSERT OR IGNORE INTO email_refs (activity_id, native_id) VALUES (?, ?)",
                    [(duplicate["id"], n) for n in sorted(_email_ids(inc.thread_ref))]).rowcount
                if added:
                    _reconcile_email_threads(db, True, ch["id"])
        return None
    channel_id = _channel_id(db, inc)
    sender = inc.sender
    if sender and sender.kind == "email" and db.execute(  # Jeremy's own address on any Connection (#117)
            f"SELECT 1 FROM connections WHERE platform IN ({','.join('?' * len(platforms.EMAIL))})"
            " AND lower(self_id) = ?",
            (*platforms.EMAIL, sender.value.lower())).fetchone():
        sender = None
    identity = _identity_for(db, sender) if sender else None
    identity_id = identity["id"] if identity else None
    at = inc.occurred_at

    target_id = None
    if inc.kind == "MESSAGE":
        thread_id = _thread_for_message(db, channel_id, inc, identity_id)
    else:
        target_native = inc.native_id if inc.kind == "DELETE" else inc.native_id.rsplit(":", 1)[0]
        target = db.execute("SELECT a.id, a.thread_id FROM thread_activities a"
                            " JOIN threads t ON t.id = a.thread_id WHERE t.channel_id = ?"
                            " AND a.kind = 'MESSAGE' AND a.native_id = ?",
                            (channel_id, target_native)).fetchone()
        if target is None:
            return None
        target_id, thread_id = target["id"], target["thread_id"]

    created = thread_id is None
    if thread_id is None:
        status = _initial_status(identity)
        title = inc.title or (inc.content or "").strip()[:80] or "(no subject)"
        until = _follow_up(status[1], at)
        thread_id = db.execute(
            "INSERT INTO threads (channel_id, native_thread_id, title, status, status_detail,"
            " status_changed_at, created_at, follow_up_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (channel_id, inc.native_id, title, *status, _iso(at), _iso(at), until)).lastrowid
    elif inc.kind == "MESSAGE" and identity is None and _echo_of_sent(db, thread_id, inc):
        return None

    activity_id = db.execute(
        "INSERT INTO activities (thread_id, kind, occurred_at, identity_id, native_id,"
        " target_id, content, data) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (thread_id, inc.kind, _iso(at), identity_id, inc.native_id, target_id,
         None if inc.kind == "DELETE" else inc.content, json.dumps(inc.data))).lastrowid
    if inc.kind == "MESSAGE" and inc.channel_kind == "mailbox":
        db.executemany("INSERT OR IGNORE INTO email_refs (activity_id, native_id) VALUES (?, ?)",
                       [(activity_id, n) for n in sorted(_email_ids(inc.thread_ref))])
        _reconcile_email_threads(db, True, channel_id)
    t = get_thread(db, thread_id)
    if not created or t.id != thread_id:
        nxt = _after_activity(t.status, t.status_detail, identity is None, inc.kind)
        if nxt:
            _set_status(db, t.id, *nxt, at)
    if inc.kind == "DELETE":  # FR-2a
        db.execute("UPDATE activities SET content = NULL WHERE id = ?"
                   " OR (kind = 'EDIT' AND target_id = ?)", (target_id, target_id))
    return get_thread(db, thread_id)


# --- act, reply, wake ------------------------------------------------------

def act(db, thread_id: int, action: Action) -> Thread:
    with db:
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        t = get_thread(db, thread_id)
        thread_id = t.id
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
    with _read_snapshot(db):
        t = get_thread(db, thread_id)
        thread_id = t.id
        history = [{"native_id": r["native_id"], "data": json.loads(r["data"]), "content": r["content"],
                    "at": r["occurred_at"]} for r in db.execute(
            "SELECT native_id, data, content, occurred_at FROM thread_activities WHERE thread_id = ? AND kind = 'MESSAGE'"
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
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        t = get_thread(db, thread_id)  # reconciliation may have happened while the Sender was in flight
        thread_id = t.id
        db.execute(
            "INSERT OR IGNORE INTO activities (thread_id, kind, occurred_at, native_id, content, data)"
            " VALUES (?, 'MESSAGE', ?, ?, ?, ?)",
            (thread_id, _iso(now), native_id, text, json.dumps({"to": list(to), "cc": list(cc)})))
        nxt = _after_activity(t.status, t.status_detail, True, "MESSAGE")
        if nxt:
            _set_status(db, thread_id, *nxt, now)
    return get_thread(db, thread_id)


def wake(db, now: datetime) -> int:
    """End timed snoozes that are due (FR-6d), and waits with no reply after WAIT (#116: back to Todo as
    Needs next response, to follow up); returns how many woke."""
    with db:
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        due = db.execute("SELECT id, status_detail, detail_before_snooze FROM threads"
                         " WHERE merged_into IS NULL AND"
                         " ((status_detail = 'WAITING_FOR_DURATION' AND snoozed_until <= ?)"
                         " OR (status_detail = 'WAITING_FOR_CUSTOMER' AND follow_up_at <= ?))",
                         (_iso(now), _iso(now))).fetchall()
        for r in due:
            if r["status_detail"] == "WAITING_FOR_CUSTOMER":
                detail = "NEW_REPLY"
            else:
                detail = r["detail_before_snooze"] or ("NEW_REPLY" if _has_replied(db, r["id"]) else "CREATED")
            _set_status(db, r["id"], "TODO", detail, now)
    return len(due)


# --- connection health inputs (NFR-2) ----------------------------------------

def set_connection_error(db, connection_id: int, error: str | None) -> None:
    with db:
        db.execute("UPDATE connections SET last_error = ? WHERE id = ?", (error, connection_id))


def set_auth(db, connection_id: int, ok: bool) -> None:
    with db:
        db.execute("UPDATE connections SET auth_ok = ? WHERE id = ?", (int(ok), connection_id))


def set_probe(db, connection_id: int, error: str | None) -> None:
    """Records a health check: its failure (None when it passed) and when it ran."""
    with db:
        db.execute("UPDATE connections SET probe_error = ?, probed_at = ? WHERE id = ?",
                   (error, _iso(_now()), connection_id))


# --- read side (records) ---------------------------------------------------

def person_threads(db, person_id: int) -> list[Thread]:
    with _read_snapshot(db):
        ids = [r[0] for r in db.execute(
            "SELECT DISTINCT a.thread_id FROM thread_activities a JOIN identities i ON i.id = a.identity_id"
            " WHERE i.person_id = ? ORDER BY a.thread_id", (person_id,))]
        return [get_thread(db, i) for i in ids]


def channel_threads(db, connection_id: int, native_id: str) -> list[Thread]:
    """Stored Threads of one Channel, newest first: a Slack Draft's captured history."""
    return [get_thread(db, r[0]) for r in db.execute(
        "SELECT t.id FROM threads t JOIN channels ch ON ch.id = t.channel_id"
        " WHERE ch.connection_id = ? AND ch.native_id = ? AND t.merged_into IS NULL ORDER BY t.created_at DESC",
        (connection_id, native_id))]


def recipient_threads(db, kind: str, value: str) -> list[Thread]:
    """Local captured history for one exact destination, not complete external contact history."""
    if type(kind) is not str or kind not in ("email", "phone") or type(value) is not str:
        raise ValueError("recipient history requires email or phone kind and a string value")
    if kind == "email":
        addresses = [a.lower() for _, a in getaddresses([value.strip()]) if a]
        if len(addresses) != 1 or "@" not in addresses[0]:
            return []
        value = addresses[0]
    elif re.fullmatch(r"\+[1-9][0-9]{1,14}", value) is None:
        return []
    formats = platforms.EMAIL if kind == "email" else tuple(
        n for n, p in platforms.PLATFORMS.items() if p.format == "sms")
    with _read_snapshot(db):
        # ponytail: scan captured envelopes; indexed recipient relation if history volume makes this slow.
        rows = db.execute(
            "SELECT a.thread_id, a.data, a.identity_id, i.kind AS identity_kind, i.value AS identity_value,"
            " ch.native_id AS channel_value, ch.kind AS channel_kind FROM thread_activities a"
            " JOIN threads t ON t.id = a.thread_id JOIN channels ch ON ch.id = t.channel_id"
            " JOIN connections c ON c.id = ch.connection_id LEFT JOIN identities i ON i.id = a.identity_id"
            f" WHERE a.kind = 'MESSAGE' AND c.platform IN ({','.join('?' * len(formats))})",
            formats)
        ids = set()
        for a in rows:
            candidates = [a["identity_value"]] if a["identity_kind"] == kind else []
            data = json.loads(a["data"])
            if isinstance(data, dict):
                keys = (("to", "cc") if a["identity_id"] is None else ("from",)) if kind == "email" else (
                    ("peer", "to") if a["identity_id"] is None else ("peer", "from"))
                for k in keys:
                    v = data.get(k)
                    if isinstance(v, str):
                        candidates.append(v)
                    elif isinstance(v, list):
                        candidates.extend(s for s in v if isinstance(s, str))
            if kind == "email":
                candidates = [addr.lower() for _, addr in getaddresses(candidates) if addr]
            elif a["channel_kind"] == "im":
                candidates.append(a["channel_value"])  # existing SMS replies retain the peer on the Channel
            if value in candidates:
                ids.add(a["thread_id"])
        return [get_thread(db, tid) for tid in sorted(ids)]


def person_organizations(db, person_id: int) -> list[str]:
    return [r[0] for r in db.execute(
        "SELECT DISTINCT o.domain FROM organizations o JOIN identities i"
        " ON i.kind = 'email' AND org_key(substr(i.value, instr(i.value, '@') + 1)) = o.domain"
        " WHERE i.person_id = ? ORDER BY o.domain", (person_id,))]


def set_case_description(db, case_id: int, description: str) -> None:
    with db:
        db.execute("UPDATE cases SET description = ? WHERE id = ?", (description, case_id))


def case_threads(db, case_id: int) -> list[Thread]:
    with _read_snapshot(db):
        return [get_thread(db, r[0]) for r in db.execute(
            "SELECT id FROM threads WHERE merged_into IS NULL AND case_id = ? ORDER BY id", (case_id,))]


# --- console read models (docs/design/console.md) ---------------------------

VIEWS = {
    "Todo": ("CREATED", "NEW_REPLY", "IN_PROGRESS"),
    "Snoozed": ("WAITING_FOR_CUSTOMER", "WAITING_FOR_DURATION", "WAITING_INDEFINITELY"),
    "Done": ("DONE_MANUALLY_SET",),
    "Ignored": ("IGNORED",),
}
VIEWS["All"] = tuple(d for details in VIEWS.values() for d in details)


def view_counts(db) -> dict[str, int]:
    rows = dict(db.execute("SELECT status_detail, count(*) FROM threads WHERE merged_into IS NULL"
                           " GROUP BY status_detail").fetchall())
    return {name: sum(rows.get(d, 0) for d in details) for name, details in VIEWS.items()}


CASE_VIEWS = ("Todo", "Snoozed", "Done", "Ignored")  # a Case's Status, earliest first (#120)


def case_status(details) -> str:
    """A Case's Status: the earliest of its Threads' views in CASE_VIEWS order; Todo with no Threads."""
    return next((v for v in CASE_VIEWS if any(d in VIEWS[v] for d in details)), "Todo")


def create_case(db, title: str) -> int:
    """A Case with no Threads yet (#120)."""
    with db:
        return db.execute("INSERT INTO cases (title, created_at) VALUES (?, ?)", (title, _iso(_now()))).lastrowid


def case_queue(db, view: str | None = None) -> list[dict]:
    """Cases with their rolled-up Status and Thread counts per view; only `view`'s Cases when given.
    Todo: longest-waiting first (when its oldest Todo Thread got there); other views: most recently changed
    first; no view: by title."""
    found = {}
    for r in db.execute("SELECT c.id, c.title, c.created_at, t.status_detail, t.status_changed_at FROM cases c"
                        " LEFT JOIN threads t ON t.case_id = c.id AND t.merged_into IS NULL"):
        c = found.setdefault(r["id"], {"id": r["id"], "title": r["title"], "created_at": r["created_at"], "threads": []})
        if r["status_detail"]:
            c["threads"].append((r["status_detail"], r["status_changed_at"]))
    out = []
    for c in found.values():
        details = [d for d, _ in c["threads"]]
        status = case_status(details)
        times = [at for d, at in c["threads"] if d in VIEWS[status]] or [c["created_at"]]
        out.append({"id": c["id"], "title": c["title"], "status": status,
                    "counts": {v: sum(d in VIEWS[v] for d in details) for v in CASE_VIEWS},
                    "since": min(times) if status == "Todo" else max(times)})
    if view is None:
        return sorted(out, key=lambda c: c["title"])
    return sorted((c for c in out if c["status"] == view), key=lambda c: c["since"], reverse=view != "Todo")


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
    dm = platforms.PLATFORMS.get(dict(channel).get("platform"))
    if kind == "im" and dm and dm.dm_where:  # the queue already shows the sender's number
        return dm.dm_where if identity else f"{dm.dm_where} · {channel['name'] or channel['native_id']}"
    if kind == "im":
        return f"DM · {channel['name']}" if channel["name"] else "Slack DM"
    if kind == "mpim":
        return channel["name"] or "Slack group"
    if kind in ("channel", "private"):
        return "#" + (channel["name"] or channel["native_id"])
    domain = identity["value"].rsplit("@", 1)[-1] if identity and identity["kind"] == "email" else ""
    return _org(domain) if domain and domain not in FREE_MAIL else "email"


def queue(db, view: str) -> list[dict]:
    """Rows for a Status view. Todo: longest-waiting first; others: most recent first."""
    details = VIEWS[view]
    rows = db.execute(
        "SELECT t.id, t.title, t.status, t.status_detail, t.case_id, t.follow_up_at, t.snoozed_until,"
        " ch.kind, ch.name, ch.native_id, c.platform,"
        " (SELECT max(occurred_at) FROM thread_activities a WHERE a.thread_id = t.id) AS last_at,"
        " (SELECT max(occurred_at) FROM thread_activities a WHERE a.thread_id = t.id AND a.identity_id IS NOT NULL) AS last_in,"
        " (SELECT identity_id FROM thread_activities a WHERE a.thread_id = t.id AND a.identity_id IS NOT NULL"
        "   ORDER BY occurred_at, id LIMIT 1) AS first_identity"
        f" FROM threads t JOIN channels ch ON ch.id = t.channel_id JOIN connections c ON c.id = ch.connection_id"
        f" WHERE t.merged_into IS NULL AND t.status_detail IN ({','.join('?' * len(details))})", details).fetchall()
    out = []
    for r in rows:
        label, identity = _sender(db, r["first_identity"]) if r["first_identity"] else ("You", None)
        out.append({"id": r["id"], "title": r["title"], "status": r["status"], "detail": r["status_detail"],
                    "case_id": r["case_id"], "until": r["follow_up_at"] or r["snoozed_until"], "who": label, "where": _where(r, identity), "last_at": r["last_at"],
                    "waiting_since": r["last_in"] or r["last_at"]})
    if view == "Todo":
        return sorted(out, key=lambda x: x["waiting_since"])
    return sorted(out, key=lambda x: x["last_at"], reverse=True)


def _shaped(m: dict, platform, channel) -> dict:
    """A message as the console shows it, whatever its Platform: raw source (email), recipients, a link that
    opens it in the service, and attachments as (name, type, bytes, url); `data` stays for email reply-all."""
    d = m["data"]
    m["link"] = platform.link(m, channel)
    urls = [u for _, u in d.get("media") or []]  # Quo media: each photo has its own signed URL
    m["attachments"] = [(*a[:3], urls[k] if k < len(urls) else m["link"])
                        for k, a in enumerate(d.get("attachments") or [])]
    m["raw"], m["to"], m["cc"], m["from"] = d.get("raw"), d.get("to") or [], d.get("cc") or [], d.get("from")
    return m


def thread_view(db, thread_id: int) -> dict:
    """Timeline (latest edit wins; deleted messages have no content) and the sender's records."""
    with _read_snapshot(db):
        t = get_thread(db, thread_id)
        thread_id = t.id
        ch = db.execute("SELECT ch.*, c.platform, c.external_id FROM channels ch"
                        " JOIN connections c ON c.id = ch.connection_id WHERE ch.id = ?", (t.channel_id,)).fetchone()
        acts = db.execute("SELECT * FROM thread_activities WHERE thread_id = ? ORDER BY occurred_at, id", (thread_id,)).fetchall()
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
        people, others, orgs = _participants(db, acts)
        case = db.execute("SELECT title FROM cases WHERE id = ?", (t.case_id,)).fetchone() if t.case_id else None
        platform = platforms.PLATFORMS[ch["platform"]]
        msgs = [_shaped(m, platform, ch) for m in msgs]
        return {"thread": t, "channel": dict(ch), "messages": msgs, "person": person, "people": people,
                "others": others, "organizations": orgs, "members": snapshot(db, thread_id)["_members"],
                "link": next((m["link"] for m in reversed(msgs) if m["link"]), None),
                "case": case["title"] if case else None, "where": _where(ch, None) if ch["kind"] != "mailbox" else "email"}


def _participants(db, acts) -> tuple[list[dict], list[str], list[str]]:
    """Everyone involved in a Thread, in order of appearance (Jeremy, 2026-10-12): Persons who wrote or were
    addressed (To/Cc), addresses that belong to no Person yet, and all their Organizations. Jeremy's own
    addresses, and his own company's domain, are left out."""
    mine = {r[0].lower() for r in db.execute("SELECT self_id FROM connections")}
    pids, others = [], []
    for a in acts:
        if a["kind"] != "MESSAGE":
            continue
        found = []
        if a["identity_id"]:
            found.append(db.execute("SELECT person_id FROM identities WHERE id = ?", (a["identity_id"],)).fetchone()[0])
        data = json.loads(a["data"])
        for addr in [*(data.get("to") or []), *(data.get("cc") or [])]:
            addr = addr.lower()
            if addr in mine:
                continue
            r = db.execute("SELECT person_id FROM identities WHERE kind = 'email' AND value = ?", (addr,)).fetchone()
            if r and r[0]:
                found.append(r[0])
            elif addr not in others:
                others.append(addr)
        pids += [p for p in found if p and p not in pids]
    people = [person_view(db, p) for p in pids]
    known = {d for p in people for d in p["organizations"]}
    extra = {_org(d) for a in others if "@" in a and (d := a.rsplit("@", 1)[1]) not in FREE_MAIL}
    own = {_org(m.rsplit("@", 1)[1]) for m in mine if "@" in m}  # Agent Maple is not a counterparty
    return people, others, sorted((known | extra) - own)


def person_view(db, person_id: int) -> dict:
    with _read_snapshot(db):
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
        "SELECT c.id, c.title, count(t.id) AS threads FROM cases c LEFT JOIN threads t"
        " ON t.case_id = c.id AND t.merged_into IS NULL"
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
    """Persons with at least one Identity: name, first address, Organizations, open (Todo) Thread count, and
    how many of their Identities are blocked."""
    with _read_snapshot(db):
        out = []
        for p in db.execute("SELECT p.id, p.display_name FROM persons p WHERE EXISTS"
                            " (SELECT 1 FROM identities i WHERE i.person_id = p.id) ORDER BY lower(p.display_name)"):
            ids = db.execute("SELECT value, blocked FROM identities WHERE person_id = ? ORDER BY kind, value",
                             (p["id"],)).fetchall()
            threads = person_threads(db, p["id"])
            out.append({"id": p["id"], "name": p["display_name"], "address": ids[0]["value"],
                        "organizations": person_organizations(db, p["id"]),
                        "todo": sum(t.status == "TODO" for t in threads), "threads": len(threads),
                        "identities": len(ids), "blocked": sum(r["blocked"] for r in ids)})
        return out


def organizations(db) -> list[dict]:
    with _read_snapshot(db):
        out = []
        for o in db.execute("SELECT id, domain, name FROM organizations ORDER BY domain"):
            view = organization_view(db, o["domain"])
            out.append({"id": o["id"], "domain": o["domain"], "people": len(view["people"]),
                        "todo": sum(t.status == "TODO" for t in view["threads"]), "threads": len(view["threads"])})
        return out


def organization_view(db, domain: str) -> dict:
    """An Organization's Persons (by email domain) and their Threads."""
    with _read_snapshot(db):
        pids = [r[0] for r in db.execute(
            "SELECT DISTINCT person_id FROM identities WHERE kind = 'email' AND person_id IS NOT NULL"
            " AND org_key(substr(value, instr(value, '@') + 1)) = ?", (domain,))]
        persons = [person_view(db, pid) for pid in pids]
        threads = {t.id: t for p in persons for t in p["threads"]}
        return {"domain": domain, "people": persons, "threads": sorted(threads.values(), key=lambda t: t.id)}


# --- undo (console: u) -------------------------------------------------------

def snapshot(db, thread_id: int) -> dict:
    """Current canonical triage state and Thread membership, so an action can be safely undone."""
    r = db.execute("SELECT id, status, status_detail, status_changed_at, snoozed_until, detail_before_snooze,"
                   " case_id, follow_up_at, (SELECT json_group_array(id) FROM"
                   " (SELECT id FROM threads WHERE id = t.id OR merged_into = t.id ORDER BY id)) AS _members"
                   " FROM threads t WHERE id ="
                   " (SELECT COALESCE(merged_into, id) FROM threads WHERE id = ?)", (thread_id,)).fetchone()
    if r is None:
        raise KeyError(f"thread {thread_id}")
    snap = dict(r)
    snap["_members"] = json.loads(snap["_members"])
    return snap


def restore(db, snap: dict) -> Thread:
    with db:
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        current = snapshot(db, snap["id"])
        thread_id = current["id"]
        if thread_id != snap["id"] or current["_members"] != snap.get("_members", [snap["id"]]):
            raise StaleThreadSnapshot("Thread merged since this undo snapshot; refresh before changing it.")
        db.execute("UPDATE threads SET status = ?, status_detail = ?, status_changed_at = ?, snoozed_until = ?,"
                   " detail_before_snooze = ?, case_id = ?, follow_up_at = ? WHERE id = ?",
                   (snap["status"], snap["status_detail"], snap["status_changed_at"], snap["snoozed_until"],
                    snap["detail_before_snooze"], snap["case_id"], snap["follow_up_at"], thread_id))
    return get_thread(db, thread_id)
