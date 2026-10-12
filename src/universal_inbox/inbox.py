"""The inbox module: Threads, Status, Activities, Persons, Organizations, Blocks, and Cases.

Interface: ingest, act, reply, wake, reconcile_email_threads; people operations block, add_identity, remove_identity.
Design: docs/design/api-and-database.md.
"""

import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from email import policy
from email.parser import BytesHeaderParser
from typing import Protocol

SCHEMA_VERSION = 4
WAIT = timedelta(days=5)  # Waiting for them ends after this with no reply: back to Todo to follow up (#116)
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


class StaleThreadSnapshot(ValueError):
    """The snapshot predates a merge of its Thread; restoring it would overwrite another triage state."""


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


# --- storage ---------------------------------------------------------------

def connect(path: str = ":memory:") -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA busy_timeout = 5000")  # the service and the console share the file
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
    try:
        db.executescript(f"BEGIN IMMEDIATE;\n{sql}\nPRAGMA user_version = {SCHEMA_VERSION};\nCOMMIT;")
    except Exception:
        db.rollback()
        raise


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


def _set_status(db, thread_id, status, detail, at, snoozed_until=None, before=None):
    follow_up = _iso(at + WAIT) if detail == "WAITING_FOR_CUSTOMER" else None
    db.execute(
        "UPDATE threads SET status = ?, status_detail = ?, status_changed_at = ?,"
        " snoozed_until = ?, detail_before_snooze = ?, follow_up_at = ? WHERE id = ?",
        (status, detail, _iso(at), snoozed_until, before, follow_up, thread_id))


def _has_replied(db, thread_id) -> bool:
    return db.execute(
        "SELECT 1 FROM thread_activities WHERE thread_id = ? AND kind = 'MESSAGE'"
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


def _org(domain: str) -> str:
    """The organization a domain belongs to: accounts.google.com -> google.com."""
    # ponytail: last two labels; misreads two-part suffixes (example.co.uk), add a suffix list if that matters
    return ".".join(domain.lower().split(".")[-2:])


def _company_domains(db, person_id, automated: bool = False) -> set[str]:
    """The organizations a Person writes from: humans' company domains, or (automated=True) the domains of
    automated senders, which only ever match other automated senders."""
    return {_org(r[0].rsplit("@", 1)[1]) for r in db.execute(
        "SELECT value FROM identities WHERE person_id = ? AND kind = 'email'", (person_id,))
        if bool(AUTOMATED.match(r[0])) == automated and r[0].rsplit("@", 1)[1] not in FREE_MAIL}


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
    if domain in FREE_MAIL or (not automated and len(key.split()) < 2):
        return None
    for p in db.execute("SELECT id, display_name FROM persons").fetchall():
        if _name_key(p["display_name"]) == key and _org(domain) in _company_domains(db, p["id"], automated):
            return p["id"]
    return None


def merge_persons(db, keep: int, drop: int) -> None:
    """One Person from two: `drop`'s Identities (and so its Threads) move to `keep`, which keeps the
    fuller name."""
    if keep == drop:
        return
    with db:
        names = [r[0] for r in db.execute("SELECT display_name FROM persons WHERE id IN (?, ?) ORDER BY id = ?",
                                          (keep, drop, drop))]
        best = max(names, key=lambda n: ("@" not in n, len(_name_key(n).split()), len(n)))
        db.execute("UPDATE identities SET person_id = ? WHERE person_id = ?", (keep, drop))
        db.execute("DELETE FROM persons WHERE id = ?", (drop,))
        db.execute("UPDATE persons SET display_name = ? WHERE id = ?", (person_name(best), keep))


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
            if a["id"] in gone or b["id"] in gone or _name_key(a["display_name"]) != _name_key(b["display_name"]):
                continue
            people_match = (len(_name_key(a["display_name"]).split()) >= 2
                            and _company_domains(db, a["id"]) & _company_domains(db, b["id"]))
            senders_match = _company_domains(db, a["id"], True) & _company_domains(db, b["id"], True)
            if people_match or senders_match:
                merge_persons(db, a["id"], b["id"])
                gone.add(b["id"])
                merged.append((a["display_name"], b["display_name"]))
    return merged


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
    if inc.channel_kind in ("channel", "private"):
        # Slack channels mirror Slack threads one to one, as Plain's "one-to-one" ingestion mode (#115).
        return None
    last = db.execute(
        "SELECT a.thread_id, a.identity_id, a.occurred_at FROM thread_activities a"
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
                "SELECT 1 FROM connections WHERE platform = 'email' AND lower(self_id) = ?",
                (sender.value.lower(),)).fetchone():
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
            if identity is None:
                status = ("SNOOZED", "WAITING_FOR_CUSTOMER")  # decision #61
            elif identity["blocked"]:
                status = ("DONE", "IGNORED")  # FR-7a
            else:
                status = ("TODO", "CREATED")  # FR-6a
            title = inc.title or (inc.content or "").strip()[:80] or "(no subject)"
            until = _iso(at + WAIT) if status[1] == "WAITING_FOR_CUSTOMER" else None
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


# --- read side (records) ---------------------------------------------------

def person_threads(db, person_id: int) -> list[Thread]:
    with _read_snapshot(db):
        ids = [r[0] for r in db.execute(
            "SELECT DISTINCT a.thread_id FROM thread_activities a JOIN identities i ON i.id = a.identity_id"
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
        return f"DM · {channel['name']}" if channel["name"] else "Slack DM"
    if kind == "mpim":
        return channel["name"] or "Slack group"
    if kind in ("channel", "private"):
        return "#" + (channel["name"] or channel["native_id"])
    domain = identity["value"].rsplit("@", 1)[-1] if identity and identity["kind"] == "email" else ""
    return domain if domain and domain not in FREE_MAIL else "email"


def queue(db, view: str) -> list[dict]:
    """Rows for a Status view. Todo: longest-waiting first; others: most recent first."""
    details = VIEWS[view]
    rows = db.execute(
        "SELECT t.id, t.title, t.status, t.status_detail, t.case_id, ch.kind, ch.name, ch.native_id,"
        " (SELECT max(occurred_at) FROM thread_activities a WHERE a.thread_id = t.id) AS last_at,"
        " (SELECT max(occurred_at) FROM thread_activities a WHERE a.thread_id = t.id AND a.identity_id IS NOT NULL) AS last_in,"
        " (SELECT identity_id FROM thread_activities a WHERE a.thread_id = t.id AND a.identity_id IS NOT NULL"
        "   ORDER BY occurred_at, id LIMIT 1) AS first_identity"
        f" FROM threads t JOIN channels ch ON ch.id = t.channel_id"
        f" WHERE t.merged_into IS NULL AND t.status_detail IN ({','.join('?' * len(details))})", details).fetchall()
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
        return {"thread": t, "channel": dict(ch), "messages": msgs, "person": person, "people": people,
                "others": others, "organizations": orgs, "_members": snapshot(db, thread_id)["_members"],
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
    extra = {a.rsplit("@", 1)[1] for a in others if "@" in a} - FREE_MAIL
    own = {m.rsplit("@", 1)[1] for m in mine if "@" in m}  # Agent Maple is not a counterparty
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
    """Persons with at least one Identity: name, first address, Organizations, open (Todo) Thread count."""
    with _read_snapshot(db):
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
            " AND substr(value, instr(value, '@') + 1) = ?", (domain,))]
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
