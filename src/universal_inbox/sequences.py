"""Sequences (.agents/prd/2026-10-16-sequencing.md, ADR 0003): Jeremy's outreach, run one Person at a time.

A Sequence is an ordered set of Steps. Enrolling a Person makes an Enrollment, which works each Step as a Task:
the next Task is created when the previous one is done or skipped, due its Step's gap in business days later
(Mon-Fri, Halifax time). An email Task's Draft is made when its day comes, from the Step's text with the
Person's fill-ins. Anything inbound from the Person pauses their Enrollments; finishing ends one as Completed,
Unsubscribed, or Canceled. Nothing here sends: email Tasks go out through Drafts and reply boxes.

Interface: create_sequence, rename_sequence, sequences, sequence_steps, archive_sequence, restore_sequence,
delete_sequence, add_step, save_step, move_step, delete_step, restore_step, validate, fill, enroll, materialize,
tasks_due, complete_task, skip_task, undo_task, pause_person, resume, finish, unsubscribe, restore_finish,
enrollments; hooks for inbox: task_sent, task_draft_deleted, task_draft_restored, send_problem.
"""

import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from . import inbox, platforms

LOCAL = ZoneInfo("America/Halifax")  # Jeremy's days (main.LOCAL_TZ)
KINDS = ("email", "call", "linkedin", "letter")
ACTIONS = ("connect", "message", "engage")
FILL_INS = ("first_name", "last_name", "company")
MARKER = re.compile(r"\{(\w+)\}")
STEP_FIELDS = ("kind", "gap_days", "at_time", "subject", "body", "voicemail", "action", "reply_to_step_id")


class Refused(ValueError):
    """An Enrollment change the rules do not allow; the message says why."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return inbox._iso(dt)


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _tx(db):
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")


# --- Sequences and Steps ------------------------------------------------------

def email_connections(db) -> list:
    return [c for c in db.execute("SELECT * FROM connections ORDER BY id")
            if platforms.PLATFORMS.get(c["platform"]) and platforms.PLATFORMS[c["platform"]].format == "email"]


def create_sequence(db, name: str, connection_id: int | None = None) -> int:
    """A new, empty Sequence; it sends from `connection_id`, else the only email Connection."""
    if not name.strip():
        raise ValueError("A Sequence needs a name.")
    if connection_id is None:
        mail = email_connections(db)
        connection_id = mail[0]["id"] if len(mail) == 1 else None
    at = _iso(_now())
    with db:
        return db.execute("INSERT INTO sequences (name, connection_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
                          (name.strip(), connection_id, at, at)).lastrowid


def rename_sequence(db, sequence_id: int, name: str) -> None:
    if not name.strip():
        raise ValueError("A Sequence needs a name.")
    with db:
        db.execute("UPDATE sequences SET name = ?, updated_at = ? WHERE id = ?", (name.strip(), _iso(_now()), sequence_id))


def set_connection(db, sequence_id: int, connection_id: int) -> None:
    with db:
        db.execute("UPDATE sequences SET connection_id = ?, updated_at = ? WHERE id = ?",
                   (connection_id, _iso(_now()), sequence_id))


def sequence_steps(db, sequence_id: int) -> list[dict]:
    """The live Steps, in order; deleted ones have no position."""
    return [dict(r) for r in db.execute("SELECT * FROM sequence_steps WHERE sequence_id = ? AND position IS NOT NULL"
                                        " ORDER BY position", (sequence_id,))]


def get_sequence(db, sequence_id: int) -> dict:
    r = db.execute("SELECT * FROM sequences WHERE id = ?", (sequence_id,)).fetchone()
    if r is None:
        raise KeyError(f"sequence {sequence_id}")
    return dict(r)


def sequences(db, *, now: datetime | None = None) -> list[dict]:
    """Every Sequence, active ones first, with its Steps, length in business days, Enrollment counts, and the
    Tasks due today."""
    today = (now or _now()).astimezone(LOCAL).date()
    out = []
    for s in db.execute("SELECT * FROM sequences ORDER BY archived_at IS NOT NULL, id"):
        steps = sequence_steps(db, s["id"])
        counts = {r[0]: r[1] for r in db.execute(
            "SELECT state, count(*) FROM enrollments WHERE sequence_id = ? GROUP BY state", (s["id"],))}
        due = sum(1 for t in db.execute(
            "SELECT t.due_at FROM tasks t JOIN enrollments e ON e.id = t.enrollment_id"
            " WHERE e.sequence_id = ? AND e.state = 'ACTIVE' AND t.state = 'DUE'", (s["id"],))
            if _at(t[0]).astimezone(LOCAL).date() <= today)
        out.append(dict(s) | dict(steps=len(steps), days=sum(st["gap_days"] for st in steps) + 1 if steps else 0,
                                  active=counts.get("ACTIVE", 0), paused=counts.get("PAUSED", 0),
                                  finished=counts.get("FINISHED", 0), due_today=due,
                                  used=bool(counts)))
    return out


def archive_sequence(db, sequence_id: int, cancel_live: bool, now: datetime | None = None) -> list[dict]:
    """A used Sequence is archived, never deleted: it takes no new Enrollments and can be restored.
    `cancel_live` finishes its live Enrollments as Canceled; returns what restore_sequence needs to undo that."""
    now = now or _now()
    undo = []
    with db:
        _tx(db)
        db.execute("UPDATE sequences SET archived_at = ?, updated_at = ? WHERE id = ?", (_iso(now), _iso(now), sequence_id))
        if cancel_live:
            for e in db.execute("SELECT id FROM enrollments WHERE sequence_id = ? AND state != 'FINISHED'",
                                (sequence_id,)).fetchall():
                undo.append(_finish(db, e["id"], "CANCELED", now))
    return undo


def restore_sequence(db, sequence_id: int, undo: list[dict] = ()) -> None:
    with db:
        _tx(db)
        db.execute("UPDATE sequences SET archived_at = NULL, updated_at = ? WHERE id = ?", (_iso(_now()), sequence_id))
        for u in undo:
            _restore_finish(db, u)


def delete_sequence(db, sequence_id: int) -> dict:
    """Only a Sequence that never had an Enrollment can be deleted; returns its rows for undo."""
    with db:
        _tx(db)
        if db.execute("SELECT 1 FROM enrollments WHERE sequence_id = ?", (sequence_id,)).fetchone():
            raise Refused("This Sequence has been used, so it can be archived but not deleted.")
        seq = get_sequence(db, sequence_id)
        steps = [dict(r) for r in db.execute("SELECT * FROM sequence_steps WHERE sequence_id = ? ORDER BY id", (sequence_id,))]
        db.execute("UPDATE sequence_steps SET reply_to_step_id = NULL WHERE sequence_id = ?", (sequence_id,))
        db.execute("DELETE FROM sequence_steps WHERE sequence_id = ?", (sequence_id,))
        db.execute("DELETE FROM sequences WHERE id = ?", (sequence_id,))
    return dict(sequence=seq, steps=steps)


def undelete_sequence(db, rows: dict) -> None:
    with db:
        _tx(db)
        _insert(db, "sequences", rows["sequence"])
        for st in rows["steps"]:
            _insert(db, "sequence_steps", st | dict(reply_to_step_id=None))
        for st in rows["steps"]:
            db.execute("UPDATE sequence_steps SET reply_to_step_id = ? WHERE id = ?", (st["reply_to_step_id"], st["id"]))


def _insert(db, table: str, row: dict) -> None:
    db.execute(f"INSERT INTO {table} ({', '.join(f'\"{c}\"' for c in row)}) VALUES ({', '.join('?' * len(row))})",
               tuple(row.values()))


def _touch(db, sequence_id):
    db.execute("UPDATE sequences SET updated_at = ? WHERE id = ?", (_iso(_now()), sequence_id))


def add_step(db, sequence_id: int, kind: str, after: int | None = None) -> int:
    """A new Step of `kind`, after position `after` (default: last); one business day after the previous Step."""
    if kind not in KINDS:
        raise ValueError(f"Step kind must be one of {', '.join(KINDS)}")
    with db:
        _tx(db)
        steps = sequence_steps(db, sequence_id)
        position = (after if after is not None else len(steps)) + 1
        for st in reversed(steps):  # make room, highest first so (sequence, position) stays unique
            if st["position"] >= position:
                db.execute("UPDATE sequence_steps SET position = position + 1 WHERE id = ?", (st["id"],))
        step_id = db.execute(
            "INSERT INTO sequence_steps (sequence_id, position, kind, gap_days, action) VALUES (?, ?, ?, ?, ?)",
            (sequence_id, position, kind, 0 if position == 1 else 1, "connect" if kind == "linkedin" else None)).lastrowid
        _touch(db, sequence_id)
    return step_id


def save_step(db, step_id: int, **fields) -> dict:
    """Edit a Step; Tasks already due keep their content (FR-SEQ-5)."""
    if set(fields) - set(STEP_FIELDS):
        raise ValueError(f"unknown Step fields: {', '.join(sorted(set(fields) - set(STEP_FIELDS)))}")
    if "kind" in fields and fields["kind"] not in KINDS:
        raise ValueError(f"Step kind must be one of {', '.join(KINDS)}")
    if "gap_days" in fields and (type(fields["gap_days"]) is not int or fields["gap_days"] < 0):
        raise ValueError("Gap is a whole number of business days, 0 or more.")
    if fields.get("at_time") and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", fields["at_time"]):
        raise ValueError("Time is HH:MM, 24-hour, or empty for any time.")
    if fields.get("action") and fields["action"] not in ACTIONS:
        raise ValueError(f"LinkedIn action is one of {', '.join(ACTIONS)}.")
    fields = {k: (v or None) if k in ("at_time", "action", "reply_to_step_id") else v for k, v in fields.items()}
    with db:
        _tx(db)
        row = db.execute("SELECT sequence_id FROM sequence_steps WHERE id = ?", (step_id,)).fetchone()
        if row is None:
            raise KeyError(f"step {step_id}")
        if fields:
            db.execute(f"UPDATE sequence_steps SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                       (*fields.values(), step_id))
            _touch(db, row["sequence_id"])
        return dict(db.execute("SELECT * FROM sequence_steps WHERE id = ?", (step_id,)).fetchone())


def move_step(db, step_id: int, delta: int) -> None:
    """Swap a Step with its neighbour (`delta` -1 up, +1 down)."""
    with db:
        _tx(db)
        st = db.execute("SELECT * FROM sequence_steps WHERE id = ?", (step_id,)).fetchone()
        other = db.execute("SELECT id, position FROM sequence_steps WHERE sequence_id = ? AND position = ?",
                           (st["sequence_id"], st["position"] + delta)).fetchone() if st and st["position"] else None
        if other is None:
            return
        db.execute("UPDATE sequence_steps SET position = NULL WHERE id = ?", (step_id,))
        db.execute("UPDATE sequence_steps SET position = ? WHERE id = ?", (st["position"], other["id"]))
        db.execute("UPDATE sequence_steps SET position = ? WHERE id = ?", (other["position"], step_id))
        _touch(db, st["sequence_id"])


def delete_step(db, step_id: int, now: datetime | None = None) -> tuple[int, dict]:
    """Delete a Step, live (FR-SEQ-24): Enrollments that have not reached it skip it; where it is the current
    Due Task, that Task and its unsent Draft go and the next Step's Task comes due by its gap. Returns how many
    Enrollments moved on, and what restore_step needs."""
    now = now or _now()
    with db:
        _tx(db)
        st = dict(db.execute("SELECT * FROM sequence_steps WHERE id = ?", (step_id,)).fetchone())
        open_tasks = [dict(r) for r in db.execute(
            "SELECT t.* FROM tasks t JOIN enrollments e ON e.id = t.enrollment_id"
            " WHERE t.step_id = ? AND t.state = 'DUE' AND e.state != 'FINISHED'", (step_id,))]
        drafts = []
        for t in open_tasks:
            drafts += _drop_task(db, t["id"])
        db.execute("UPDATE sequence_steps SET position = NULL WHERE id = ?", (step_id,))
        db.execute("UPDATE sequence_steps SET position = position - 1 WHERE sequence_id = ? AND position > ?",
                   (st["sequence_id"], st["position"]))
        db.execute("UPDATE sequence_steps SET reply_to_step_id = NULL WHERE reply_to_step_id = ?", (step_id,))
        _touch(db, st["sequence_id"])
        before = _max_task(db)
        states = [dict(db.execute("SELECT * FROM enrollments WHERE id = ?", (t["enrollment_id"],)).fetchone()) for t in open_tasks]
        materialize(db, now)
    return len(open_tasks), dict(step=st, tasks=open_tasks, drafts=drafts, after=before, enrollments=states)


def restore_step(db, undo: dict) -> None:
    """Put a deleted Step back, with the Tasks and Drafts it removed; Tasks made since for those Enrollments go."""
    st = undo["step"]
    with db:
        _tx(db)
        enrollments = {t["enrollment_id"] for t in undo["tasks"]}
        for t in db.execute("SELECT id, enrollment_id FROM tasks WHERE id > ? AND state = 'DUE'", (undo["after"],)).fetchall():
            if t["enrollment_id"] in enrollments:
                _drop_task(db, t["id"])
        for later in reversed(sequence_steps(db, st["sequence_id"])):
            if later["position"] >= st["position"]:
                db.execute("UPDATE sequence_steps SET position = position + 1 WHERE id = ?", (later["id"],))
        db.execute("UPDATE sequence_steps SET position = ? WHERE id = ?", (st["position"], st["id"]))
        for t in undo["tasks"]:
            _insert(db, "tasks", t)
        for d in undo["drafts"]:
            _insert(db, "composition_drafts", d)
        for e in undo.get("enrollments", ()):  # one the delete Completed is live again
            db.execute("UPDATE enrollments SET state = ?, reason = ?, finished_at = ? WHERE id = ?",
                       (e["state"], e["reason"], e["finished_at"], e["id"]))


def _max_task(db) -> int:
    return db.execute("SELECT coalesce(max(id), 0) FROM tasks").fetchone()[0]


def _drop_task(db, task_id: int) -> list[dict]:
    """Remove a Task not yet done, and its unsent Drafts; returns the Drafts."""
    drafts = [dict(r) for r in db.execute("SELECT * FROM composition_drafts WHERE task_id = ? AND sending_at IS NULL", (task_id,))]
    db.execute("DELETE FROM composition_drafts WHERE task_id = ? AND sending_at IS NULL", (task_id,))
    db.execute("UPDATE composition_drafts SET task_id = NULL WHERE task_id = ?", (task_id,))
    db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    return drafts


def validate(db, sequence_id: int) -> list[tuple[int, str]]:
    """Problems the editor shows on the Step row and detail pane (FR-SEQ-4)."""
    steps = sequence_steps(db, sequence_id)
    problems = []
    for i, st in enumerate(steps):
        for name in sorted({m for text in (st["subject"], st["body"], st["voicemail"]) for m in MARKER.findall(text)}
                           - set(FILL_INS)):
            problems.append((st["id"], f"{{{name}}} is not a fill-in. Use {{first_name}}, {{last_name}}, or {{company}}."))
        if st["reply_to_step_id"]:
            if i == 0:
                problems.append((st["id"], "Step 1 cannot reply to another Step: there is no earlier email. Clear Reply to."))
            elif st["reply_to_step_id"] not in {s["id"] for s in steps[:i] if s["kind"] == "email"}:
                problems.append((st["id"], "Reply to must name an earlier email Step."))
            if st["kind"] != "email":
                problems.append((st["id"], "Only an email Step can reply in an earlier email's Thread."))
    return problems


def person_fill(db, person_id: int) -> dict:
    name = db.execute("SELECT display_name FROM persons WHERE id = ?", (person_id,)).fetchone()[0]
    words = name.split()
    domains = inbox.person_organizations(db, person_id)
    org = db.execute("SELECT name FROM organizations WHERE domain = ?", (domains[0],)).fetchone() if domains else None
    return dict(first_name=words[0] if words else name, last_name=words[-1] if len(words) > 1 else "",
                company=org[0] if org else None)


def fill(text: str, values: dict) -> tuple[str, list[str]]:
    """Step text with the Person's fill-ins; a fill-in with no value keeps its {marker} and is listed."""
    missing = []

    def one(m):
        if m[1] in FILL_INS and values.get(m[1]):
            return values[m[1]]
        if m[1] in FILL_INS and m[1] not in missing:
            missing.append(m[1])
        return m[0]
    return MARKER.sub(one, text), missing


def markers(text: str) -> list[str]:
    """The fill-in markers still in a Task Draft; sending is refused while any remain (FR-SEQ-10)."""
    return [m for m in MARKER.findall(text or "") if m in FILL_INS]


# --- schedule -----------------------------------------------------------------

def _business(d: date, gap: int) -> date:
    """`gap` business days after `d`; with no gap, `d` itself, or the Monday after a weekend."""
    while gap == 0 and d.weekday() >= 5:
        d += timedelta(days=1)
    for _ in range(gap):
        d += timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
    return d


def due_date(prev: datetime, gap: int, at_time: str | None, tz=LOCAL) -> datetime:
    """`gap` business days (Mon-Fri, no holidays) after `prev`'s day in Jeremy's time; a weekend day rolls to
    Monday. At `at_time` that day, else from the start of it."""
    day = _business(prev.astimezone(tz).date(), gap)
    at = time.fromisoformat(at_time) if at_time else time(0, 0)
    return datetime.combine(day, at, tz).astimezone(timezone.utc)


def day_number(enrolled: datetime, due: datetime, tz=LOCAL) -> int:
    """Day N of the Sequence, counting business days from the enroll day as Day 1."""
    d, end, n = _business(enrolled.astimezone(tz).date(), 0), due.astimezone(tz).date(), 1
    while d < end:
        d = _business(d, 1)
        n += 1
    return n


# --- Enrollments and Tasks ----------------------------------------------------

def parse_start(text: str, now: datetime) -> date | None:
    """A Start date typed as `2026-12-01` or a number of days from today (FR-SEQ-25); None if it is neither."""
    text = text.strip()
    if text.isdigit():
        return now.astimezone(LOCAL).date() + timedelta(days=int(text))
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def enroll(db, person_id: int, sequence_id: int, now: datetime | None = None, start: date | None = None) -> int:
    """One Person into one Sequence (FR-SEQ-6); its first Task counts from the enroll day, or from a later
    Start date (FR-SEQ-25)."""
    now = now or _now()
    today = now.astimezone(LOCAL).date()
    if start and start < today:
        raise Refused(f"The Start date {start:%b} {start.day} is before today.")
    starts = _iso(datetime.combine(start, time(0, 0), LOCAL)) if start and start > today else None
    with db:
        _tx(db)
        p = db.execute("SELECT display_name, unsubscribed_at FROM persons WHERE id = ?", (person_id,)).fetchone()
        if p is None:
            raise KeyError(f"person {person_id}")
        s = get_sequence(db, sequence_id)
        if p["unsubscribed_at"]:
            raise Refused(f"{p['display_name']} unsubscribed on {_day(p['unsubscribed_at'])} and cannot be enrolled in any Sequence.")
        if s["archived_at"]:
            raise Refused(f"{s['name']} is archived; restore it to enroll People.")
        if not sequence_steps(db, sequence_id):
            raise Refused(f"{s['name']} has no Steps yet.")
        live = db.execute("SELECT state FROM enrollments WHERE person_id = ? AND sequence_id = ? AND state != 'FINISHED'",
                          (person_id, sequence_id)).fetchone()
        if live:
            raise Refused(f"{p['display_name']} is already in {s['name']} ({live[0].title()}).")
        eid = db.execute("INSERT INTO enrollments (person_id, sequence_id, state, enrolled_at, starts_at)"
                         " VALUES (?, ?, 'ACTIVE', ?, ?)", (person_id, sequence_id, _iso(now), starts)).lastrowid
        materialize(db, now)
    return eid


def first_due(db, enrollment_id: int, now: datetime | None = None) -> str:
    """When an Enrollment's first Task is due, for a confirmation: "today" or "on Nov 2"."""
    t = db.execute("SELECT due_at FROM tasks WHERE enrollment_id = ? ORDER BY id LIMIT 1", (enrollment_id,)).fetchone()
    if t is None or _at(t[0]).astimezone(LOCAL).date() <= (now or _now()).astimezone(LOCAL).date():
        return "today"
    return f"on {_day(t[0])}"


def unenroll(db, enrollment_id: int) -> None:
    """Undo an enroll before any Task is done: the Enrollment, its Tasks, and their unsent Drafts go."""
    with db:
        _tx(db)
        if db.execute("SELECT 1 FROM tasks WHERE enrollment_id = ? AND state != 'DUE'", (enrollment_id,)).fetchone():
            raise Refused("A Task is already done; finish the Enrollment instead.")
        for r in db.execute("SELECT id FROM tasks WHERE enrollment_id = ?", (enrollment_id,)).fetchall():
            _drop_task(db, r["id"])
        db.execute("DELETE FROM enrollments WHERE id = ?", (enrollment_id,))


def task_new_thread(db, task_id: int) -> int:
    """A reply Task whose target was skipped starts a new Thread instead (FR-SEQ-8): a new email Draft with the
    replied-to Step's subject. Returns the Draft's id."""
    with db:
        _tx(db)
        t = db.execute("SELECT t.*, s.body, s.reply_to_step_id, e.person_id, e.sequence_id FROM tasks t"
                       " JOIN sequence_steps s ON s.id = t.step_id JOIN enrollments e ON e.id = t.enrollment_id"
                       " WHERE t.id = ?", (task_id,)).fetchone()
        target = db.execute("SELECT subject FROM sequence_steps WHERE id = ?", (t["reply_to_step_id"],)).fetchone()
        db.execute("UPDATE tasks SET flag = NULL WHERE id = ?", (task_id,))
        row = dict(t) | dict(reply_to_step_id=None, subject=target["subject"] if target else "")
        _task_draft(db, row)
        made = db.execute("SELECT id FROM composition_drafts WHERE task_id = ?", (task_id,)).fetchone()
        if made is None:
            raise Refused(db.execute("SELECT flag FROM tasks WHERE id = ?", (task_id,)).fetchone()[0] or "No Draft made.")
        return made[0]


def _day(iso: str) -> str:
    d = _at(iso).astimezone(LOCAL)
    return f"{d:%b} {d.day}"


def materialize(db, now: datetime | None = None) -> int:
    """Make each Active Enrollment's next Task, and the Draft of each email Task whose day has come; finish an
    Enrollment whose last Task is done. Idempotent (one Task per Enrollment and Step, one Draft per Task), so it
    runs every minute from wake. Returns how many Tasks or Drafts it made."""
    now = now or _now()
    made = 0
    with db:
        _tx(db)
        for e in db.execute("SELECT * FROM enrollments WHERE state = 'ACTIVE'").fetchall():
            if db.execute("SELECT 1 FROM tasks WHERE enrollment_id = ? AND state = 'DUE'", (e["id"],)).fetchone():
                continue
            made += _next_task(db, e, now) is not None
        today = now.astimezone(LOCAL).date()
        for t in db.execute(
                "SELECT t.*, s.kind, s.reply_to_step_id, s.subject, s.body, e.person_id, e.sequence_id FROM tasks t"
                " JOIN sequence_steps s ON s.id = t.step_id JOIN enrollments e ON e.id = t.enrollment_id"
                " WHERE t.state = 'DUE' AND e.state = 'ACTIVE' AND s.kind = 'email' AND t.flag IS NULL"
                " AND NOT EXISTS (SELECT 1 FROM composition_drafts d WHERE d.task_id = t.id)").fetchall():
            if _at(t["due_at"]).astimezone(LOCAL).date() <= today:
                made += _task_draft(db, t)
    return made


def _next_task(db, e, now: datetime, due: datetime | None = None) -> int | None:
    """The Task for the Step after this Enrollment's last one, or Completed when there is none."""
    last = db.execute("SELECT t.done_at, s.position FROM tasks t JOIN sequence_steps s ON s.id = t.step_id"
                      " WHERE t.enrollment_id = ? ORDER BY t.id DESC LIMIT 1", (e["id"],)).fetchone()
    taken = {r[0] for r in db.execute("SELECT step_id FROM tasks WHERE enrollment_id = ?", (e["id"],))}
    after = last["position"] if last and last["position"] is not None else 0  # a deleted last Step: the first not taken
    step = next((s for s in sequence_steps(db, e["sequence_id"]) if s["position"] > after and s["id"] not in taken), None)
    if step is None:
        db.execute("UPDATE enrollments SET state = 'FINISHED', reason = 'COMPLETED', finished_at = ? WHERE id = ?",
                   (_iso(now), e["id"]))
        return None
    base = _at(last["done_at"]) if last else _at(e["starts_at"] or e["enrolled_at"])
    due = due or due_date(base, step["gap_days"], step["at_time"])
    return db.execute("INSERT OR IGNORE INTO tasks (enrollment_id, step_id, due_at, state) VALUES (?, ?, ?, 'DUE')",
                      (e["id"], step["id"], _iso(due))).lastrowid


def _task_draft(db, t) -> int:
    """An email Task's Draft (FR-SEQ-8): a new email, or the reply Draft in the Thread of the Step it replies to."""
    values = person_fill(db, t["person_id"])
    body, missing = fill(t["body"], values)
    if t["reply_to_step_id"]:
        target = db.execute("SELECT thread_id FROM tasks WHERE enrollment_id = ? AND step_id = ?",
                            (t["enrollment_id"], t["reply_to_step_id"])).fetchone()
        if target is None or target["thread_id"] is None:
            db.execute("UPDATE tasks SET flag = 'reply target skipped' WHERE id = ?", (t["id"],))
            return 0
        if db.execute("SELECT 1 FROM composition_drafts WHERE thread_id = ?", (target["thread_id"],)).fetchone():
            db.execute("UPDATE tasks SET flag = 'reply Draft already there' WHERE id = ?", (t["id"],))
            return 0
        d = inbox.save_reply_draft(db, target["thread_id"], body, task_id=t["id"])
        if d is None:  # the Step has no text yet
            db.execute("UPDATE tasks SET flag = 'Step has no text' WHERE id = ?", (t["id"],))
            return 0
    else:
        seq = get_sequence(db, t["sequence_id"])
        conn = db.execute("SELECT * FROM connections WHERE id = ?", (seq["connection_id"],)).fetchone()
        address = db.execute("SELECT value FROM identities WHERE person_id = ? AND kind = 'email' ORDER BY id LIMIT 1",
                             (t["person_id"],)).fetchone()
        if conn is None or address is None:
            db.execute("UPDATE tasks SET flag = ? WHERE id = ?",
                       ("no email address" if address is None else "Sequence has no Connection", t["id"]))
            return 0
        subject, more = fill(t["subject"], values)
        missing += [m for m in more if m not in missing]
        d = inbox.save_draft(db, dict(connection_id=conn["id"], kind="email", sender=conn["self_id"], to=address[0],
                                      cc="", subject=subject, body=body), task_id=t["id"])
    if missing:
        db.execute("UPDATE tasks SET flag = ? WHERE id = ?", (" ".join(f"{{{m}}}" for m in missing), t["id"]))
    return 1


def _snapshot(db, task_id: int) -> dict:
    t = dict(db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone())
    e = dict(db.execute("SELECT * FROM enrollments WHERE id = ?", (t["enrollment_id"],)).fetchone())
    drafts = [dict(r) for r in db.execute("SELECT * FROM composition_drafts WHERE task_id = ?", (task_id,))]
    return dict(task=t, enrollment=e, drafts=drafts, after=_max_task(db))


def _close(db, task_id: int, state: str, now: datetime, thread_id: int | None = None) -> dict:
    undo = _snapshot(db, task_id)
    db.execute("UPDATE tasks SET state = ?, done_at = ?, thread_id = coalesce(?, thread_id) WHERE id = ? AND state = 'DUE'",
               (state, _iso(now), thread_id, task_id))
    return undo


def complete_task(db, task_id: int, now: datetime | None = None) -> dict:
    """Jeremy did a non-email Task (FR-SEQ-9); the next Step's Task follows. Returns what undo_task needs."""
    now = now or _now()
    with db:
        _tx(db)
        kind = db.execute("SELECT s.kind FROM tasks t JOIN sequence_steps s ON s.id = t.step_id WHERE t.id = ?", (task_id,)).fetchone()
        if kind and kind[0] == "email":
            raise Refused("An email Task is done when its Draft sends.")
        undo = _close(db, task_id, "DONE", now)
        materialize(db, now)
    return undo


def skip_task(db, task_id: int, now: datetime | None = None) -> dict:
    """Skip a Task; its unsent Draft goes. A skip counts as done for the next Task's gap."""
    now = now or _now()
    with db:
        _tx(db)
        undo = _close(db, task_id, "SKIPPED", now)
        db.execute("DELETE FROM composition_drafts WHERE task_id = ? AND sending_at IS NULL", (task_id,))
        materialize(db, now)
    return undo


def undo_task(db, undo: dict) -> None:
    """Back to before complete_task or skip_task: the Task due again, its Draft and Enrollment as they were."""
    with db:
        _tx(db)
        for t in db.execute("SELECT id FROM tasks WHERE enrollment_id = ? AND id > ?",
                            (undo["task"]["enrollment_id"], undo["after"])).fetchall():
            _drop_task(db, t["id"])
        db.execute("UPDATE tasks SET state = ?, done_at = ?, thread_id = ?, flag = ? WHERE id = ?",
                   (undo["task"]["state"], undo["task"]["done_at"], undo["task"]["thread_id"], undo["task"]["flag"],
                    undo["task"]["id"]))
        e = undo["enrollment"]
        db.execute("UPDATE enrollments SET state = ?, reason = ?, finished_at = ? WHERE id = ?",
                   (e["state"], e["reason"], e["finished_at"], e["id"]))
        have = {r[0] for r in db.execute("SELECT id FROM composition_drafts WHERE task_id = ?", (undo["task"]["id"],))}
        for d in undo["drafts"]:
            if d["id"] not in have:
                _insert(db, "composition_drafts", d)


def tasks_due(db, now: datetime | None = None) -> list[dict]:
    """The Tasks page (FR-SEQ-15): Active Enrollments' Tasks whose day has come, grouped Overdue, Today, and Later
    today (a fixed time not yet reached), oldest first."""
    now = now or _now()
    today = now.astimezone(LOCAL).date()
    out = []
    for r in db.execute(
            "SELECT t.*, s.kind, s.position, s.at_time, s.subject, s.body, s.voicemail, s.action, s.reply_to_step_id,"
            " e.person_id, e.sequence_id, coalesce(e.starts_at, e.enrolled_at) AS starts_at, q.name AS sequence, p.display_name AS person,"
            " (SELECT count(*) FROM sequence_steps x WHERE x.sequence_id = q.id AND x.position IS NOT NULL) AS steps,"
            " (SELECT id FROM composition_drafts d WHERE d.task_id = t.id) AS draft_id"
            " FROM tasks t JOIN sequence_steps s ON s.id = t.step_id JOIN enrollments e ON e.id = t.enrollment_id"
            " JOIN sequences q ON q.id = e.sequence_id JOIN persons p ON p.id = e.person_id"
            " WHERE t.state = 'DUE' AND e.state = 'ACTIVE' ORDER BY t.due_at, t.id"):
        due = _at(r["due_at"])
        day = due.astimezone(LOCAL).date()
        if day > today:
            continue
        group = "overdue" if day < today else "later today" if r["at_time"] and due > now else "due today"
        org = person_fill(db, r["person_id"])["company"]
        out.append(dict(r) | dict(group=group, organization=org, day=day_number(_at(r["starts_at"]), due),
                                  age=(today - day).days))
    order = {"overdue": 0, "due today": 1, "later today": 2}
    return sorted(out, key=lambda t: (order[t["group"]], t["due_at"], t["id"]))


# --- hooks from inbox ---------------------------------------------------------

def send_problem(db, draft: dict, text: str | None = None) -> str | None:
    """Why a Task Draft cannot send yet: a fill-in marker left, or its Enrollment is Paused or Finished."""
    if not draft.get("task_id"):
        return None
    e = db.execute("SELECT e.state, p.display_name FROM tasks t JOIN enrollments e ON e.id = t.enrollment_id"
                   " JOIN persons p ON p.id = e.person_id WHERE t.id = ?", (draft["task_id"],)).fetchone()
    left = markers((draft.get("subject") or "") + " " + (draft["body"] if text is None else text))
    if left:
        return f"Not sent: replace {' '.join('{' + m + '}' for m in dict.fromkeys(left))} first."
    if e and e["state"] == "PAUSED" and text is None:
        return f"Not sent: {e['display_name']}'s Enrollment is Paused. Resume it on their Person page (r) to send."
    if e and e["state"] == "FINISHED" and text is None:
        return "Not sent: this Enrollment has finished."
    return None


def task_sent(db, draft: dict, thread_id: int, now: datetime) -> None:
    """An email Task's Draft went out (inside the send's transaction): the Task is done in that Thread. A reply
    sent while the Enrollment is Paused is done too; the next Task waits for Resume."""
    if not draft.get("task_id"):
        return
    state = db.execute("SELECT e.state FROM tasks t JOIN enrollments e ON e.id = t.enrollment_id WHERE t.id = ?",
                       (draft["task_id"],)).fetchone()
    _close(db, draft["task_id"], "DONE", now, thread_id)  # it went out, so it is done even while Paused
    if state and state[0] == "ACTIVE":
        materialize(db, now)


def task_draft_deleted(db, draft: dict, now: datetime | None = None) -> None:
    """Deleting an email Task's Draft skips the Task (FR-SEQ-9)."""
    if draft.get("task_id"):
        now = now or _now()
        db.execute("UPDATE tasks SET state = 'SKIPPED', done_at = ? WHERE id = ? AND state = 'DUE'",
                   (_iso(now), draft["task_id"]))
        materialize(db, now)


def task_draft_restored(db, draft: dict) -> None:
    """Undoing that delete makes the Task due again, and drops any Task made after it."""
    if not draft.get("task_id"):
        return
    t = db.execute("SELECT * FROM tasks WHERE id = ?", (draft["task_id"],)).fetchone()
    if t and t["state"] == "SKIPPED":
        for later in db.execute("SELECT id FROM tasks WHERE enrollment_id = ? AND id > ? AND state = 'DUE'",
                                (t["enrollment_id"], t["id"])).fetchall():
            _drop_task(db, later["id"])
        db.execute("UPDATE tasks SET state = 'DUE', done_at = NULL WHERE id = ?", (t["id"],))
        db.execute("UPDATE enrollments SET state = 'ACTIVE', reason = NULL, finished_at = NULL"
                   " WHERE id = ? AND reason = 'COMPLETED'", (t["enrollment_id"],))


# --- pause, resume, finish ----------------------------------------------------

def pause_person(db, person_id: int, at: datetime, why: str) -> int:
    """Anything inbound from the Person, or a connected call, pauses their Active Enrollments (FR-SEQ-12);
    messages older than the Enrollment (imports) do not. Runs inside ingest's transaction."""
    if not db.execute("SELECT 1 FROM sqlite_schema WHERE name = 'enrollments'").fetchone():
        return 0  # a database still being built up to schema 17 (migration tests ingest along the way)
    return db.execute("UPDATE enrollments SET state = 'PAUSED', paused_at = ?, pause_reason = ?"
                      " WHERE person_id = ? AND state = 'ACTIVE' AND enrolled_at <= ?",
                      (_iso(at), why, person_id, _iso(at))).rowcount


def resume(db, enrollment_id: int, now: datetime | None = None) -> None:
    """Paused to Active; the next Task is due at once (FR-SEQ-13)."""
    now = now or _now()
    with db:
        _tx(db)
        e = db.execute("SELECT * FROM enrollments WHERE id = ?", (enrollment_id,)).fetchone()
        if e is None or e["state"] != "PAUSED":
            raise Refused("Only a Paused Enrollment resumes.")
        db.execute("UPDATE enrollments SET state = 'ACTIVE', paused_at = NULL, pause_reason = NULL WHERE id = ?",
                   (enrollment_id,))
        if db.execute("SELECT 1 FROM tasks WHERE enrollment_id = ? AND state = 'DUE'", (enrollment_id,)).fetchone():
            db.execute("UPDATE tasks SET due_at = ? WHERE enrollment_id = ? AND state = 'DUE' AND due_at > ?",
                       (_iso(now), enrollment_id, _iso(now)))
        else:
            _next_task(db, db.execute("SELECT * FROM enrollments WHERE id = ?", (enrollment_id,)).fetchone(), now, due=now)
        materialize(db, now)


def _finish(db, enrollment_id: int, reason: str, now: datetime) -> dict:
    e = dict(db.execute("SELECT * FROM enrollments WHERE id = ?", (enrollment_id,)).fetchone())
    drafts = [dict(r) for r in db.execute(
        "SELECT d.* FROM composition_drafts d JOIN tasks t ON t.id = d.task_id"
        " WHERE t.enrollment_id = ? AND d.sending_at IS NULL", (enrollment_id,))]
    for d in drafts:
        db.execute("DELETE FROM composition_drafts WHERE id = ?", (d["id"],))
    db.execute("UPDATE enrollments SET state = 'FINISHED', reason = ?, finished_at = ? WHERE id = ?",
               (reason, _iso(now), enrollment_id))
    return dict(enrollment=e, drafts=drafts)


def _restore_finish(db, undo: dict) -> None:
    e = undo["enrollment"]
    db.execute("UPDATE enrollments SET state = ?, reason = ?, finished_at = ?, paused_at = ?, pause_reason = ? WHERE id = ?",
               (e["state"], e["reason"], e["finished_at"], e["paused_at"], e["pause_reason"], e["id"]))
    for d in undo["drafts"]:
        _insert(db, "composition_drafts", d)


def finish(db, enrollment_id: int, reason: str, now: datetime | None = None) -> list[dict]:
    """End an Enrollment as Completed, Unsubscribed, or Canceled (FR-SEQ-14); its unsent Task Drafts go.
    Unsubscribed also marks the Person and ends every Sequence they are in. Returns what restore_finish needs."""
    if reason not in ("COMPLETED", "UNSUBSCRIBED", "CANCELED"):
        raise ValueError("reason is COMPLETED, UNSUBSCRIBED, or CANCELED")
    now = now or _now()
    with db:
        _tx(db)
        e = db.execute("SELECT * FROM enrollments WHERE id = ?", (enrollment_id,)).fetchone()
        if e is None or e["state"] == "FINISHED":
            raise Refused("This Enrollment has already finished.")
        if reason == "UNSUBSCRIBED":
            return _unsubscribe(db, e["person_id"], now)
        return [_finish(db, enrollment_id, reason, now)]


def unsubscribe(db, person_id: int, now: datetime | None = None) -> list[dict]:
    now = now or _now()
    with db:
        _tx(db)
        return _unsubscribe(db, person_id, now)


def _unsubscribe(db, person_id: int, now: datetime) -> list[dict]:
    before = db.execute("SELECT unsubscribed_at FROM persons WHERE id = ?", (person_id,)).fetchone()[0]
    db.execute("UPDATE persons SET unsubscribed_at = coalesce(unsubscribed_at, ?) WHERE id = ?", (_iso(now), person_id))
    undo = [_finish(db, r[0], "UNSUBSCRIBED", now) for r in db.execute(
        "SELECT id FROM enrollments WHERE person_id = ? AND state != 'FINISHED'", (person_id,)).fetchall()]
    return [dict(person=person_id, unsubscribed_at=before)] + undo


def restore_finish(db, undo: list[dict]) -> None:
    with db:
        _tx(db)
        for u in undo:
            if "person" in u:
                db.execute("UPDATE persons SET unsubscribed_at = ? WHERE id = ?", (u["unsubscribed_at"], u["person"]))
            else:
                _restore_finish(db, u)


def merge_people(db, keep: int, drop: int) -> None:
    """Inside merge_persons: `drop`'s Enrollments move to `keep`; where both are live in one Sequence, `drop`'s
    is Canceled. Unsubscribed carries over."""
    now = _now()
    for e in db.execute("SELECT e.id FROM enrollments e WHERE e.person_id = ? AND e.state != 'FINISHED' AND EXISTS"
                        " (SELECT 1 FROM enrollments k WHERE k.person_id = ? AND k.sequence_id = e.sequence_id"
                        " AND k.state != 'FINISHED')", (drop, keep)).fetchall():
        _finish(db, e["id"], "CANCELED", now)
    db.execute("UPDATE enrollments SET person_id = ? WHERE person_id = ?", (keep, drop))
    db.execute("UPDATE persons SET unsubscribed_at = coalesce(unsubscribed_at,"
               " (SELECT unsubscribed_at FROM persons WHERE id = ?)) WHERE id = ?", (drop, keep))


def enrollments(db, person_id: int, now: datetime | None = None) -> list[dict]:
    """A Person's Enrollments, live first then newest, each with its Tasks timeline (FR-SEQ-19)."""
    now = now or _now()
    out = []
    for e in db.execute("SELECT e.*, q.name AS sequence FROM enrollments e JOIN sequences q ON q.id = e.sequence_id"
                        " WHERE e.person_id = ? ORDER BY e.state = 'FINISHED', e.id DESC", (person_id,)).fetchall():
        enrolled = _at(e["starts_at"] or e["enrolled_at"])
        tasks = {t["step_id"]: dict(t) for t in db.execute("SELECT * FROM tasks WHERE enrollment_id = ?", (e["id"],))}
        steps = [s for s in db.execute("SELECT * FROM sequence_steps WHERE sequence_id = ? ORDER BY position IS NULL, position",
                                       (e["sequence_id"],)) if s["position"] is not None or s["id"] in tasks]
        timeline, base, nxt = [], None, None
        for s in steps:
            t = tasks.get(s["id"])
            if t:
                due = _at(t["due_at"])
                base = _at(t["done_at"]) if t["done_at"] else None
                entry = dict(step=dict(s), task=t, day=day_number(enrolled, due),
                             state=t["state"].lower() if t["state"] != "DUE" else "due")
                if t["state"] == "DUE":
                    nxt = entry
            else:
                due = due_date(base or (now if timeline else enrolled), s["gap_days"], s["at_time"])
                base = due
                entry = dict(step=dict(s), task=None, day=day_number(enrolled, due), state="upcoming")
            timeline.append(entry)
        total = sum(s["gap_days"] for s in steps if s["position"] is not None) + 1
        out.append(dict(e) | dict(timeline=timeline, next=nxt or next((x for x in timeline if x["state"] == "upcoming"), None),
                                  day=nxt["day"] if nxt else (timeline[-1]["day"] if timeline else 1), days=total))
    return out


def thread_person(db, thread_id: int) -> int | None:
    """The Person a Thread is with, for its Sequence section: the first other party in it."""
    r = db.execute("SELECT i.person_id FROM thread_activities a JOIN identities i ON i.id = a.identity_id"
                   " WHERE a.thread_id = ? AND i.person_id IS NOT NULL ORDER BY a.id LIMIT 1",
                   (inbox.get_thread(db, thread_id).id,)).fetchone()
    return r[0] if r else None


def pauses_since(db, since: str) -> list[dict]:
    """Enrollments paused after `since`, for the console's toast (FR-SEQ-20)."""
    return [dict(r) for r in db.execute(
        "SELECT e.id, e.paused_at, e.pause_reason, p.display_name AS person, q.name AS sequence FROM enrollments e"
        " JOIN persons p ON p.id = e.person_id JOIN sequences q ON q.id = e.sequence_id"
        " WHERE e.state = 'PAUSED' AND e.paused_at > ? ORDER BY e.paused_at", (since,))]
