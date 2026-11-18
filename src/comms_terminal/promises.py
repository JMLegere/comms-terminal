"""Promises (ADR 0024, 0027; .agents/prd/2026-11-18-promises.md): what specific People expect Jeremy to do by a
time. Only an agent changes them, through the CLI, and every change cites an Origin (FR-PR-3, FR-PR-4): an
Activity, a Customer request, a Tana node, or Jeremy's own recorded words. Slack, At risk and Overdue are worked
out from the time, the work and the travel (FR-PR-7), never stored.

A Promise is Tentative, Open, Fulfilled or Cancelled. Cancelled needs an Origin showing the Person knows, or, for a
Tentative promise, evidence it was never owed (FR-PR-5; GLOSSARY). Linking an Enrollment lets the Promise's time set
that Enrollment's start (FR-PR-14).

The "captured up to" mark (FR-PR-10) is the last Activity whose Promise changes the capture agent has written; it
only moves forward, in Activity insertion order, so a late Call transcript (an EDIT Activity) is still read.

Interface: record, firm, change, fulfil, cancel, link, advance_capture (writers); find, promise, view, slack,
capture_mark, pending (readers).
"""

import json
from datetime import date, datetime, time, timedelta, timezone

from . import clock, commands
from .writers import writer

STATES = ("TENTATIVE", "OPEN", "FULFILLED", "CANCELLED")
OPEN_STATES = ("TENTATIVE", "OPEN")
ORIGINS = ("activity", "customer_request", "tana", "words")
LINKS = ("thread", "case", "issue", "draft", "enrollment", "tana", "url")
DUE_TIME = time(17)  # a Promise given only a day is due at 17:00 Jeremy's time
NO_UNDO = "Promises change only by an agent with an Origin (ADR 0024); record a new change instead"
SECTIONS = ("overdue", "at_risk", "today", "this_week", "later", "tentative")


class Refused(ValueError):
    """A Promise change the rules do not allow; the message says why."""


def _local():
    from .sequences import LOCAL
    return LOCAL


def due_from(text: str) -> datetime:
    """A time as an agent writes it: YYYY-MM-DD (17:00 Jeremy's time), YYYY-MM-DD HH:MM (his time), or ISO with an
    offset."""
    text = text.strip()
    try:
        day = date.fromisoformat(text)
    except ValueError:
        when = datetime.fromisoformat(text.replace(" ", "T"))
        return (when if when.tzinfo else when.replace(tzinfo=_local())).astimezone(timezone.utc)
    return datetime.combine(day, DUE_TIME, _local()).astimezone(timezone.utc)


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).isoformat()


def origin_from(text: str) -> dict:
    """An Origin as an agent writes it: activity:ID, customer_request:ID, tana:ID, or words:TEXT."""
    kind, sep, value = (text or "").partition(":")
    if not sep or kind not in ORIGINS or not value.strip():
        raise Refused("an Origin is activity:ID, customer_request:ID, tana:ID or words:TEXT")
    return {"kind": kind, "words": value.strip()} if kind == "words" else {"kind": kind, "ref": value.strip()}


def _agent_only() -> None:
    """Jeremy reads Promises in the Console and the App; only an agent's CLI or the Service changes them (ADR 0024)."""
    if commands.current_source() in ("console", "app"):
        raise Refused("only an agent changes Promises; tell an agent what to change (ADR 0024)")


def _origin(db, origin: dict | None) -> tuple[str, str | None, str | None]:
    _agent_only()
    if not origin or origin.get("kind") not in ORIGINS:
        raise Refused("every change to a Promise cites its Origin (ADR 0024)")
    kind = origin["kind"]
    if kind == "words":
        words = (origin.get("words") or "").strip()
        if not words:
            raise Refused("an Origin of Jeremy's words needs the words")
        return kind, None, words
    ref = (origin.get("ref") or "").strip()
    if not ref:
        raise Refused(f"a {kind} Origin needs its id")
    if kind == "activity" and not db.execute("SELECT 1 FROM activities WHERE id = ?", (ref,)).fetchone():
        raise Refused(f"no Activity {ref}")
    return kind, ref, None


def _log(db, promise_id: str, change: str, origin: tuple, now: datetime, **detail) -> None:
    kind, ref, words = origin
    db.execute("INSERT INTO promise_changes (promise_id, at, change, detail, origin_kind, origin_ref, words)"
               " VALUES (?, ?, ?, ?, ?, ?, ?)", (promise_id, _iso(now), change, json.dumps(detail, default=str),
                                                 kind, ref, words))


def find(db, promise_id: str):
    row = db.execute("SELECT * FROM promises WHERE id = ?", (promise_id,)).fetchone()
    if row is None:
        raise KeyError(f"no Promise {promise_id}")
    return row


def _set_people(db, promise_id: str, people: list[str]) -> None:
    if not people:
        raise Refused("a Promise is owed to one or more People")
    for person_id in people:
        if not db.execute("SELECT 1 FROM persons WHERE id = ?", (person_id,)).fetchone():
            raise Refused(f"no Person {person_id}")
    db.execute("DELETE FROM promise_people WHERE promise_id = ?", (promise_id,))
    db.executemany("INSERT INTO promise_people (promise_id, person_id) VALUES (?, ?)",
                   [(promise_id, p) for p in dict.fromkeys(people)])


def _starts(db, promise_id: str, due_at: str, now: datetime) -> None:
    """Each linked Enrollment starts on the Promise's day (FR-PR-14)."""
    from . import sequences
    day = datetime.fromisoformat(due_at).astimezone(_local()).date()
    for (eid,) in db.execute("SELECT target FROM promise_links WHERE promise_id = ? AND kind = 'enrollment'",
                             (promise_id,)).fetchall():
        sequences.reschedule(db, eid, day, now=now)


def _label(row, words: str) -> None:
    commands.label(f"Promise {words}: {row['what']}")
    commands.target("promises", row["id"])
    commands.not_undoable(NO_UNDO)


@writer("write")
def record(db, what: str, people: list[str], due_at: datetime, origin: dict, *, inferred: bool = False,
           work_minutes: int = 0, travel_minutes: int = 0, tentative: bool = False,
           now: datetime | None = None) -> str:
    """A new Promise (FR-PR-1); Tentative when the agent is not yet sure it is owed."""
    now = now or clock.now()
    o = _origin(db, origin)
    if not (what or "").strip():
        raise Refused("a Promise says what is owed")
    if work_minutes < 0 or travel_minutes < 0:
        raise Refused("work and travel are minutes, 0 or more")
    pid = db.execute("INSERT INTO promises (what, due_at, due_inferred, work_minutes, travel_minutes, state,"
                     " created_at) VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id",
                     (what.strip(), _iso(due_at), int(inferred), work_minutes, travel_minutes,
                      "TENTATIVE" if tentative else "OPEN", _iso(now))).fetchone()[0]
    _set_people(db, pid, people)
    _log(db, pid, "recorded", o, now, due_at=_iso(due_at), tentative=tentative)
    _label(find(db, pid), "recorded")
    return pid


def _open(db, promise_id: str):
    row = find(db, promise_id)
    if row["state"] not in OPEN_STATES:
        raise Refused(f"the Promise is already {row['state'].title()}")
    return row


@writer("write")
def firm(db, promise_id: str, origin: dict, *, now: datetime | None = None) -> None:
    """A Tentative promise is owed after all."""
    now = now or clock.now()
    o = _origin(db, origin)
    row = find(db, promise_id)
    if row["state"] != "TENTATIVE":
        raise Refused("only a Tentative promise firms up")
    db.execute("UPDATE promises SET state = 'OPEN' WHERE id = ?", (promise_id,))
    _log(db, promise_id, "firmed", o, now)
    _label(row, "firmed")


@writer("write")
def change(db, promise_id: str, origin: dict, *, what: str | None = None, due_at: datetime | None = None,
           inferred: bool | None = None, work_minutes: int | None = None, travel_minutes: int | None = None,
           people: list[str] | None = None, now: datetime | None = None) -> None:
    """Changes what, the time, work, travel or People of an owed Promise; the history keeps the old values."""
    now = now or clock.now()
    o = _origin(db, origin)
    row = _open(db, promise_id)
    fields, before = {}, {}
    for name, value in (("what", what), ("due_at", _iso(due_at) if due_at else None),
                        ("due_inferred", None if inferred is None else int(inferred)),
                        ("work_minutes", work_minutes), ("travel_minutes", travel_minutes)):
        if value is not None and value != row[name]:
            if name.endswith("_minutes") and value < 0:
                raise Refused("work and travel are minutes, 0 or more")
            fields[name], before[name] = value, row[name]
    if fields:
        db.execute(f"UPDATE promises SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                   (*fields.values(), promise_id))
    if people is not None:
        before["people"] = [r[0] for r in db.execute("SELECT person_id FROM promise_people WHERE promise_id = ?",
                                                     (promise_id,))]
        _set_people(db, promise_id, people)
        fields["people"] = people
    if not fields:
        raise Refused("nothing to change")
    _log(db, promise_id, "changed", o, now, before=before, after=fields)
    if "due_at" in fields:
        _starts(db, promise_id, fields["due_at"], now)
    _label(row, "changed")


@writer("write")
def fulfil(db, promise_id: str, origin: dict, *, at: datetime | None = None, now: datetime | None = None) -> None:
    """The Promise was kept, as its Origin shows; late when after its time."""
    now = now or clock.now()
    o = _origin(db, origin)
    row = _open(db, promise_id)
    kept = _iso(at or now)
    db.execute("UPDATE promises SET state = 'FULFILLED', closed_at = ? WHERE id = ?", (kept, promise_id))
    _log(db, promise_id, "fulfilled", o, now, at=kept, late=kept > row["due_at"])
    _label(row, "fulfilled")


@writer("write")
def cancel(db, promise_id: str, origin: dict, *, now: datetime | None = None) -> None:
    """No longer owed: the Person knows, or a Tentative promise was never owed. Never silent (FR-PR-5)."""
    now = now or clock.now()
    o = _origin(db, origin)
    row = _open(db, promise_id)
    db.execute("UPDATE promises SET state = 'CANCELLED', closed_at = ? WHERE id = ?", (_iso(now), promise_id))
    _log(db, promise_id, "cancelled", o, now)
    _label(row, "cancelled")


@writer("write")
def link(db, promise_id: str, kind: str, target: str, origin: dict, *, now: datetime | None = None) -> None:
    """Adds context (FR-PR-6). An Enrollment link starts that Enrollment on the Promise's day (FR-PR-14)."""
    now = now or clock.now()
    o = _origin(db, origin)
    row = find(db, promise_id)
    if kind not in LINKS:
        raise Refused(f"a link is one of {', '.join(LINKS)}")
    if not (target or "").strip():
        raise Refused("a link needs its target")
    db.execute("INSERT OR IGNORE INTO promise_links (promise_id, kind, target) VALUES (?, ?, ?)",
               (promise_id, kind, target.strip()))
    _log(db, promise_id, "linked", o, now, kind=kind, target=target.strip())
    if kind == "enrollment" and row["state"] in OPEN_STATES:
        _starts(db, promise_id, row["due_at"], now)
    _label(row, f"linked to {kind}")


def capture_mark(db) -> dict | None:
    row = db.execute("SELECT * FROM promise_capture WHERE id = 1").fetchone()
    return dict(row) if row else None


@writer("write")
def advance_capture(db, activity_id: str, *, now: datetime | None = None) -> None:
    """Moves "captured up to" to this Activity, once its Promise changes are written; never backwards."""
    now = now or clock.now()
    _agent_only()
    found = db.execute("SELECT rowid FROM activities WHERE id = ?", (activity_id,)).fetchone()
    if found is None:
        raise Refused(f"no Activity {activity_id}")
    mark = capture_mark(db)
    if mark and found[0] <= mark["activity_rowid"]:
        raise Refused("captured up to is already at or past that Activity")
    db.execute("INSERT INTO promise_capture (id, activity_rowid, activity_id, at) VALUES (1, ?, ?, ?)"
               " ON CONFLICT (id) DO UPDATE SET activity_rowid = excluded.activity_rowid,"
               " activity_id = excluded.activity_id, at = excluded.at", (found[0], activity_id, _iso(now)))
    commands.label("Promises captured up to a new Activity")
    commands.not_undoable(NO_UNDO)


def pending(db, limit: int = 50) -> list[dict]:
    """The Activities after "captured up to", oldest first, with who wrote each (None = Jeremy)."""
    mark = capture_mark(db)
    rows = db.execute(
        "SELECT a.rowid AS seq, a.id, a.kind, a.occurred_at, a.thread_id, t.title, a.identity_id, a.target_id,"
        " p.display_name AS author, a.content FROM activities a JOIN threads t ON t.id = a.thread_id"
        " LEFT JOIN identities i ON i.id = a.identity_id LEFT JOIN persons p ON p.id = i.person_id"
        " WHERE a.rowid > ? ORDER BY a.rowid LIMIT ?", (mark["activity_rowid"] if mark else 0, limit)).fetchall()
    return [dict(r) for r in rows]


def slack(row, now: datetime) -> int:
    """Minutes left before its time, less its work and travel (FR-PR-7); negative when it can no longer fit."""
    left = (datetime.fromisoformat(row["due_at"]) - now).total_seconds() // 60
    return int(left) - row["work_minutes"] - row["travel_minutes"]


def risk(row, now: datetime) -> str | None:
    """'overdue' past its time, 'at_risk' when Slack is less than its work and travel (FR-PR-7), else None."""
    if row["state"] not in OPEN_STATES:
        return None
    if datetime.fromisoformat(row["due_at"]) <= now:
        return "overdue"
    return "at_risk" if slack(row, now) < row["work_minutes"] + row["travel_minutes"] else None


def promise(db, promise_id: str, now: datetime | None = None) -> dict:
    """One Promise with its People, links, Slack, risk, whether a Draft is ready, and its history."""
    now = now or clock.now()
    row = find(db, promise_id)
    people = db.execute("SELECT p.id, p.display_name FROM promise_people pp JOIN persons p ON p.id = pp.person_id"
                        " WHERE pp.promise_id = ? ORDER BY p.display_name", (promise_id,)).fetchall()
    links = db.execute("SELECT kind, target FROM promise_links WHERE promise_id = ? ORDER BY kind, target",
                       (promise_id,)).fetchall()
    history = db.execute("SELECT at, change, detail, origin_kind, origin_ref, words FROM promise_changes"
                         " WHERE promise_id = ? ORDER BY at, rowid", (promise_id,)).fetchall()
    threads = [l["target"] for l in links if l["kind"] == "thread"]
    drafts = [l["target"] for l in links if l["kind"] == "draft"]
    ready = bool(db.execute(
        f"SELECT 1 FROM composition_drafts WHERE thread_id IN ({','.join('?' * len(threads)) or 'NULL'})"
        f" OR id IN ({','.join('?' * len(drafts)) or 'NULL'}) LIMIT 1", (*threads, *drafts)).fetchone())
    return dict(row) | {"people": [dict(p) for p in people], "links": [dict(l) for l in links],
                        "history": [dict(h) | {"detail": json.loads(h["detail"])} for h in history],
                        "slack": slack(row, now), "risk": risk(row, now), "draft_ready": ready}


def section(row, now: datetime) -> str:
    if row["state"] == "TENTATIVE":
        return "tentative"
    r = risk(row, now)
    if r:
        return r
    day = datetime.fromisoformat(row["due_at"]).astimezone(_local()).date()
    today = now.astimezone(_local()).date()
    return "today" if day <= today else "this_week" if day < today + timedelta(days=7) else "later"


def view(db, now: datetime | None = None) -> dict:
    """The Promises page (FR-PR-8): owed Promises by section, soonest first, and "captured up to"."""
    now = now or clock.now()
    out = {s: [] for s in SECTIONS}
    for (pid,) in db.execute("SELECT id FROM promises WHERE state IN ('TENTATIVE', 'OPEN') ORDER BY due_at, id"):
        p = promise(db, pid, now)
        out[section(p, now)].append(p)
    return {"sections": out, "captured": capture_mark(db),
            "count": sum(len(v) for k, v in out.items() if k != "tentative")}
