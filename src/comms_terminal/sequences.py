"""Sequences (.agents/prd/2026-10-16-sequencing.md, 2026-10-26-sequence-flow.md; ADR 0003, 0008): Jeremy's
outreach, run one Person at a time, for one Organization.

A Sequence is a tree of Steps: On start, then the body, where an If ends its path and opens a true path and a
false path, and every body path ends in its own On completion; paths never rejoin. Enrolling a Person makes an
Enrollment, which works each Step as a Task: the next Task is created when the previous one is done or skipped,
due its Step's gap in business days later (Mon-Fri, Halifax time). An email Task's Draft is made when its day
comes, from the Step's text with the Person's fill-ins. The console carries out Actions (set Stage, enroll, end
for others, set Value) and Ifs by itself; an If that asks Jeremy waits for his pick. Anything inbound from the
Person pauses their Enrollments. When a body path runs out, or Jeremy finishes it as Goal met, Not Right Now, or
Lost, the Enrollment is Finishing and works that path's On completion, then is Finished; Canceled and
Unsubscribed end it at once. Nothing here sends: email Tasks go out through Drafts and reply boxes.

Interface: create_sequence, rename_sequence, describe_sequence, sequences, sequence_steps, archive_sequence, restore_sequence,
delete_sequence, add_step, save_step, move_step, delete_step, restore_step, validate, fill, enroll,
default_organization, materialize, check, current_path, tasks_due, complete_task, skip_task, pick, undo_task,
pause_person, resume, reschedule, finish, unsubscribe, restore_finish, enrollments; paths: path_of, on_path, completion_of;
hooks for inbox: task_sent, task_draft_deleted, task_draft_restored, send_problem.
"""

import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from . import clock, inbox, platforms
from . import pipeline as pipeline_mod
from .writers import writer

LOCAL = ZoneInfo("America/Halifax")  # Jeremy's days (main.LOCAL_TZ)
KINDS = ("email", "call", "linkedin", "letter", "action", "if")
TOUCHES = ("email", "call", "linkedin", "letter")  # Steps Jeremy does; an Action or If the console does
MOVES = ("connect", "message", "engage")  # a LinkedIn Step's move
ACTION_KINDS = ("set_stage", "enroll", "end_for_others", "set_value")  # FR-SEQ-27
CONDITIONS = ("end_reason", "has_channel", "stage", "jeremy_picks")  # FR-SEQ-29
CHANNELS = ("email", "phone")  # ponytail: Identities hold no LinkedIn profile, so has_channel cannot check one
SECTIONS = ("start", "body", "completion")
FINISHING_REASONS = ("COMPLETED", "GOAL_MET", "NOT_RIGHT_NOW", "LOST")  # these run the path's On completion
NO_ORG = "no Organization: set the one this Enrollment is for"  # an Action waiting for one; it retries each run
END_REASONS = FINISHING_REASONS + ("CANCELED", "UNSUBSCRIBED")  # these two end at once, with no On completion
REASON_LABEL = {"COMPLETED": "Completed", "GOAL_MET": "Goal met", "NOT_RIGHT_NOW": "Not Right Now", "LOST": "Lost",
                "CANCELED": "Canceled", "UNSUBSCRIBED": "Unsubscribed"}
FILL_INS = ("first_name", "last_name", "company")
MARKER = re.compile(r"\{(\w+)\}")
STEP_FIELDS = ("kind", "gap_days", "at_time", "subject", "body", "voicemail", "linkedin_move", "reply_to_step_id",
               "enroll_sequence_id", "action_kind", "pipeline", "stage", "value", "condition", "operand", "true_label",
               "false_label", "needs_edit")


class Refused(ValueError):
    """A Sequence or Enrollment change the rules do not allow; the message says why."""





def _tx(db):
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")


# --- Sequences and Steps ------------------------------------------------------

def email_connections(db) -> list:
    return [c for c in db.execute("SELECT * FROM connections ORDER BY id")
            if platforms.PLATFORMS.get(c["platform"]) and platforms.PLATFORMS[c["platform"]].format == "email"]


@writer("create")
def create_sequence(db, name: str, connection_id: int | None = None, *, now: datetime | None = None) -> int:
    """A new, empty Sequence; it sends from `connection_id`, else the only email Connection."""
    if not name.strip():
        raise ValueError("A Sequence needs a name.")
    if connection_id is None:
        mail = email_connections(db)
        connection_id = mail[0]["id"] if len(mail) == 1 else None
    at = clock.iso((now or clock.now()))
    with inbox._write_snapshot(db):
        return db.execute("INSERT INTO sequences (name, connection_id, created_at, updated_at) VALUES (?, ?, ?, ?) RETURNING id",
                          (name.strip(), connection_id, at, at)).fetchone()[0]


@writer("write")
def rename_sequence(db, sequence_id: int, name: str, *, now: datetime | None = None) -> None:
    if not name.strip():
        raise ValueError("A Sequence needs a name.")
    with db:
        db.execute("UPDATE sequences SET name = ?, updated_at = ? WHERE id = ?", (name.strip(), clock.iso((now or clock.now())), sequence_id))


@writer("write")
def describe_sequence(db, sequence_id: int, description: str, *, now: datetime | None = None) -> None:
    """Its one-line "use when", shown in the Sequence list and the enroll picker."""
    with inbox._write_snapshot(db):
        db.execute("UPDATE sequences SET description = ?, updated_at = ? WHERE id = ?",
                   (" ".join(description.split()), clock.iso((now or clock.now())), sequence_id))


@writer("write")
def set_connection(db, sequence_id: int, connection_id: int, *, now: datetime | None = None) -> None:
    """Its email Tasks send from `connection_id`; Due Tasks flagged for having none get their Drafts on the next
    materialize."""
    with db:
        db.execute("UPDATE sequences SET connection_id = ?, updated_at = ? WHERE id = ?",
                   (connection_id, clock.iso((now or clock.now())), sequence_id))
        db.execute("UPDATE tasks SET flag = NULL WHERE flag = 'Sequence has no Connection' AND state = 'DUE'"
                   " AND enrollment_id IN (SELECT id FROM enrollments WHERE sequence_id = ?)", (sequence_id,))


@writer("create")
def replace_sequence(db, plan: dict, dry_run: bool = False, *, now: datetime | None = None) -> list[str]:
    """Reconcile a complete Sequence tree, resolve its links, and validate the exact stored result atomically.
    `plan` has name, description, start/body/completion lists; Steps use STEP_FIELDS, source `line`, an `enroll`
    name, a `reply` email number, and an If's yes/no and yes_done/no_done lists. Kept Steps retain their IDs and
    Due Tasks retain their Drafts. Caller transactions keep ownership; dry runs write only an isolated copy."""
    if dry_run:
        trial = inbox.connect()
        try:
            if db.in_transaction:
                # backup waits on this connection's pending writes; a dump includes them, including on WAL.
                trial.execute("PRAGMA foreign_keys = OFF")
                trial.executescript("\n".join(db.iterdump()))
                trial.execute("PRAGMA foreign_keys = ON")
            else:
                db.backup(trial)
            return replace_sequence(trial, plan, now=now)
        finally:
            trial.close()
    with inbox._write_snapshot(db):
        return _replace_sequence(db, plan)


def _replace_sequence(db, plan) -> list[str]:
    row = db.execute("SELECT id FROM sequences WHERE name = ? ORDER BY archived_at IS NOT NULL, id", (plan["name"],)).fetchone()
    names = {r["name"].lower(): r["id"] for r in db.execute("SELECT id, name FROM sequences ORDER BY archived_at IS NOT NULL DESC, id DESC")}
    changes = [] if row else [f"+ Sequence {plan['name']}"]
    sid = row[0] if row else create_sequence(db, plan["name"])
    names[plan["name"].lower()] = sid
    if db.execute("SELECT description FROM sequences WHERE id = ?", (sid,)).fetchone()[0] != plan["description"]:
        describe_sequence(db, sid, plan["description"])
        changes.append(f"~ Use when: {plan['description'] or '(none)'}")
    emails, pending = [], []  # email Step IDs in document order; (Step ID, email number) reply links
    for path in (("start", None, None), ("body", None, None), ("completion", None, None)):
        _sync_steps(db, sid, path, plan[path[0]], changes, names, emails, pending)
    for step_id, n in pending:
        if not 1 <= n <= len(emails):
            raise Refused(f"reply {n}: the file has {len(emails)} emails")
        if db.execute("SELECT reply_to_step_id FROM sequence_steps WHERE id = ?", (step_id,)).fetchone()[0] != emails[n - 1]:
            save_step(db, step_id, reply_to_step_id=emails[n - 1])
            changes.append(f"~ email: reply {n}")
    problems = validate(db, sid)
    if problems:
        by_id = {st["id"]: st for st in sequence_steps(db, sid)}
        raise Refused("; ".join(f"Step {by_id[i]['position']} ({by_id[i]['kind']}): {m}" for i, m in problems))
    return changes


def _sync_steps(db, sid, path, wanted, changes, names, emails, pending):
    have = on_path(sequence_steps(db, sid), path)
    where = {"start": "On start", "body": "Steps", "completion": "On completion"}[path[0]]
    for i, w in enumerate(wanted):
        fields = {k: w[k] for k in STEP_FIELDS[1:] if k in w}
        if w["kind"] == "action" and w["action_kind"] == "enroll":
            target = names.get((w["enroll"] or "").lower())
            if target is None:
                raise Refused(f"line {w['line']}: no Sequence named {w['enroll']!r}")
            fields["enroll_sequence_id"] = target
        old = have[i] if i < len(have) and have[i]["kind"] == w["kind"] else None
        if old is not None and old["kind"] == "if" and old["condition"] != w["condition"]:
            old = None
        if old is None:
            if i < len(have):
                delete_step(db, have[i]["id"])
                changes.append(f"- {where}: {have[i]['kind']} (was Step {have[i]['position']})")
                have = on_path(sequence_steps(db, sid), path)
            after = None
            if i > 0:
                after = on_path(sequence_steps(db, sid), path)[i - 1]["position"]
            step_id = add_step(db, sid, w["kind"], after, section=path[0], parent_step_id=path[1], branch=path[2],
                               action_kind=w["action_kind"] or "enroll", condition=w["condition"] or "jeremy_picks",
                               front=i == 0)
            save_step(db, step_id, **{k: v for k, v in fields.items() if k != "reply_to_step_id"})
            changes.append(f"+ {where}: {w['kind']} line {w['line']}")
        else:
            step_id = old["id"]
            diff = {k: v for k, v in fields.items() if k != "reply_to_step_id" and (old.get(k) or None) != (v or None)}
            if diff:
                save_step(db, step_id, **diff)
                changes.append(f"~ {where}: {w['kind']} line {w['line']}: {', '.join(sorted(diff))}")
        have = on_path(sequence_steps(db, sid), path)
        if w["kind"] == "email":
            emails.append(step_id)
            current = db.execute("SELECT reply_to_step_id FROM sequence_steps WHERE id = ?", (step_id,)).fetchone()[0]
            if w["reply"]:
                pending.append((step_id, w["reply"]))
            elif current:
                save_step(db, step_id, reply_to_step_id=None)
                changes.append(f"~ {where}: email line {w['line']}: reply_to_step_id")
        if w["kind"] == "if":
            for key, branch in (("yes", 1), ("no", 0)):
                child = (path[0], step_id, branch)
                _sync_steps(db, sid, child, w[key], changes, names, emails, pending)
                if path[0] == "body":
                    _sync_steps(db, sid, completion_of(child), w[f"{key}_done"], changes, names, emails, pending)
    have = on_path(sequence_steps(db, sid), path)
    for extra in have[len(wanted):][::-1]:
        delete_step(db, extra["id"])
        changes.append(f"- {where}: {extra['kind']} (was Step {extra['position']})")


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
    today = (now or clock.now()).astimezone(LOCAL).date()
    out = []
    for s in db.execute("SELECT * FROM sequences ORDER BY archived_at IS NOT NULL, id"):
        steps = sequence_steps(db, s["id"])
        counts = {r[0]: r[1] for r in db.execute(
            "SELECT state, count(*) FROM enrollments WHERE sequence_id = ? GROUP BY state", (s["id"],))}
        due = sum(1 for t in db.execute(
            "SELECT t.due_at FROM tasks t JOIN enrollments e ON e.id = t.enrollment_id"
            " WHERE e.sequence_id = ? AND e.state IN ('ACTIVE', 'FINISHING') AND t.state = 'DUE'", (s["id"],))
            if datetime.fromisoformat(t[0]).astimezone(LOCAL).date() <= today)
        out.append(dict(s) | dict(steps=len(steps), days=sum(st["gap_days"] for st in steps) + 1 if steps else 0,
                                  active=counts.get("ACTIVE", 0) + counts.get("FINISHING", 0), paused=counts.get("PAUSED", 0),
                                  finished=counts.get("FINISHED", 0), due_today=due,
                                  used=bool(counts)))
    return out


@writer("write")
def archive_sequence(db, sequence_id: int, cancel_live: bool, now: datetime | None = None) -> list[dict]:
    """A used Sequence is archived, never deleted: it takes no new Enrollments and can be restored.
    `cancel_live` finishes its live Enrollments as Canceled; returns what restore_sequence needs to undo that."""
    now = now or clock.now()
    undo = []
    with db:
        _tx(db)
        db.execute("UPDATE sequences SET archived_at = ?, updated_at = ? WHERE id = ?", (clock.iso(now), clock.iso(now), sequence_id))
        if cancel_live:
            for e in db.execute("SELECT id FROM enrollments WHERE sequence_id = ? AND state != 'FINISHED'",
                                (sequence_id,)).fetchall():
                undo.append(_finish(db, e["id"], "CANCELED", now))
    return undo


@writer("write")
def restore_sequence(db, sequence_id: int, undo: list[dict] = (), *, now: datetime | None = None) -> None:
    with db:
        _tx(db)
        db.execute("UPDATE sequences SET archived_at = NULL, updated_at = ? WHERE id = ?", (clock.iso((now or clock.now())), sequence_id))
        for u in undo:
            _restore_finish(db, u)


@writer("write")
def delete_sequence(db, sequence_id: int, *, now: datetime | None = None) -> dict:
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


@writer("write")
def undelete_sequence(db, rows: dict, *, now: datetime | None = None) -> None:
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


def _touch(db, sequence_id, *, now: datetime | None = None):
    db.execute("UPDATE sequences SET updated_at = ? WHERE id = ?", (clock.iso((now or clock.now())), sequence_id))


# --- the Step tree (.agents/prd/2026-10-26-sequence-flow.md) ------------------------------------------------
# Positions run through the whole Sequence; a Step's path is (section, the If it hangs under, true/false). The next
# Step on a path is the next position on the same path. An If ends its path and opens two, each of which ends in
# its own On completion; paths never rejoin.

def path_of(st) -> tuple:
    return (st["section"], st["parent_step_id"], st["branch"])


def completion_of(path: tuple) -> tuple:
    """The On completion that ends a body path."""
    return ("completion", path[1], path[2])


def on_path(steps: list[dict], path: tuple) -> list[dict]:
    return [st for st in steps if path_of(st) == path]


def _below(steps: list[dict], step_id: int) -> list[dict]:
    """Every Step under an If: its two paths, their On completions, and Ifs below them."""
    out, todo = [], [step_id]
    while todo:
        parent = todo.pop()
        kids = [st for st in steps if st["parent_step_id"] == parent]
        out += kids
        todo += [k["id"] for k in kids if k["kind"] == "if"]
    return out


def outline(steps: list[dict]) -> list[dict]:
    """The Steps pane's rows (FR-SEQ-43): a header for each path, then its Steps, indented under the If they hang
    from. Each row is {step, path, depth, label}; a header row has no step, and `n` on it adds to its path."""
    rows = []

    def head(path, depth, label):
        rows.append(dict(step=None, path=path, depth=depth, label=label))

    def walk(path, depth, completes):
        mine = on_path(steps, path)
        for st in mine:
            rows.append(dict(step=st, path=path, depth=depth, label=None))
            if st["kind"] == "if":
                for branch, label in ((1, st["true_label"] or "true"), (0, st["false_label"] or "false")):
                    child = (st["section"], st["id"], branch)
                    head(child, depth + 1, f"{'✓' if branch else '✗'} {label}")
                    walk(child, depth + 1, st["section"] == "body")
        done = completion_of(path) if completes else None
        if completes and (on_path(steps, done) or not (mine and mine[-1]["kind"] == "if")):  # finishing early runs it
            head(done, depth, "On completion")
            walk(done, depth, False)

    head(("start", None, None), 0, "On start")
    walk(("start", None, None), 0, False)
    head(("body", None, None), 0, "Steps")
    walk(("body", None, None), 0, True)
    return rows


ACTION_LABEL = {"set_stage": "Set Stage", "enroll": "Enroll", "end_for_others": "End for others", "set_value": "Set Value"}
CONDITION_LABEL = {"end_reason": "How it finished", "has_channel": "Has channel", "stage": "Stage",
                   "jeremy_picks": "You pick"}


def step_summary(st: dict, names: dict) -> str:
    """One line for a Step's What column and a Task: what it says or does."""
    if st["kind"] == "action":
        kind = st["action_kind"]
        if kind == "enroll":
            return f"enroll in {names.get(st['enroll_sequence_id'], '?')}"
        if kind == "set_stage":
            return f"{(st['pipeline'] or '?').title()} → {st['stage'] or '?'}"
        if kind == "set_value":
            return f"{(st['pipeline'] or '?').title()} Value {st['value'] if st['value'] is not None else '?'}"
        return "end this Sequence for the Organization's other People"
    if st["kind"] == "if":
        cond = st["condition"]
        if cond == "end_reason":
            return "finished as " + " or ".join(REASON_LABEL.get(r.strip(), r) for r in st["operand"].split(",") if r.strip())
        if cond == "has_channel":
            return f"has {st['operand'] or '?'}"
        if cond == "stage":
            return f"{(st['pipeline'] or '?').title()} Stage is {st['stage'] or '?'}"
        return st["subject"] or "you pick"
    return (st["subject"] if st["kind"] == "email" else (st["linkedin_move"] or "") if st["kind"] == "linkedin"
            else "") or st["body"]


@writer("create")
def add_step(db, sequence_id: int, kind: str, after: int | None = None, *, section: str = "body",
             parent_step_id: int | None = None, branch: int | None = None, action_kind: str = "enroll",
             condition: str = "jeremy_picks", front: bool = False, now: datetime | None = None) -> int:
    """A new Step of `kind` right after the Step at position `after`, on that Step's path; with no `after`, last on
    the path named by `section`, `parent_step_id`, and `branch` (default: the body), or first on it with `front`. A touch is due one business day
    after the previous Step, the first on its path, an Action, and an If at once."""
    if kind not in KINDS:
        raise ValueError(f"Step kind must be one of {', '.join(KINDS)}")
    if section not in SECTIONS:
        raise ValueError(f"A section is one of {', '.join(SECTIONS)}.")
    with inbox._write_snapshot(db):
        steps = sequence_steps(db, sequence_id)
        if after is not None:
            anchor = next((st for st in steps if st["position"] == after), None)
            if anchor is None:
                raise KeyError(f"no Step at position {after}")
            if anchor["kind"] == "if":
                raise Refused("Nothing follows an If on its path: add the Step to one of its paths.")
            path = path_of(anchor)
        else:
            path = (section, parent_step_id, branch)
            if (parent_step_id is None) != (branch is None):
                raise ValueError("A path under an If names the If and its branch.")
            mine = on_path(steps, path)
            if front and mine:
                after = mine[0]["position"] - 1
            elif mine and mine[-1]["kind"] == "if":
                raise Refused("Nothing follows an If on its path: add the Step to one of its paths.")
            else:
                after = mine[-1]["position"] if mine else (max((st["position"] for st in steps), default=0))
        position = after + 1
        for st in reversed(steps):  # make room, highest first so (sequence, position) stays unique
            if st["position"] >= position:
                db.execute("UPDATE sequence_steps SET position = position + 1 WHERE id = ?", (st["id"],))
        first = not on_path(steps, path)
        step_id = db.execute(
            "INSERT INTO sequence_steps (sequence_id, position, section, parent_step_id, branch, kind, gap_days,"
            " linkedin_move, action_kind, condition) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (sequence_id, position, *path, kind, 0 if first or kind in ("action", "if") else 1,
             "connect" if kind == "linkedin" else None, action_kind if kind == "action" else None,
             condition if kind == "if" else None)).fetchone()[0]
        _touch(db, sequence_id, now=now)
    return step_id


@writer("write")
def save_step(db, step_id: int, *, now: datetime | None = None, **fields) -> dict:
    """Edit a Step; Tasks already due keep their content (FR-SEQ-5)."""
    if set(fields) - set(STEP_FIELDS):
        raise ValueError(f"unknown Step fields: {', '.join(sorted(set(fields) - set(STEP_FIELDS)))}")
    if "kind" in fields and fields["kind"] not in KINDS:
        raise ValueError(f"Step kind must be one of {', '.join(KINDS)}")
    if "gap_days" in fields and (type(fields["gap_days"]) is not int or fields["gap_days"] < 0):
        raise ValueError("Gap is a whole number of business days, 0 or more.")
    if fields.get("at_time") and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", fields["at_time"]):
        raise ValueError("Time is HH:MM, 24-hour, or empty for any time.")
    if fields.get("linkedin_move") and fields["linkedin_move"] not in MOVES:
        raise ValueError(f"LinkedIn move is one of {', '.join(MOVES)}.")
    if fields.get("action_kind") is not None and fields["action_kind"] not in ACTION_KINDS:
        raise ValueError(f"An Action is one of {', '.join(ACTION_KINDS)}.")
    if fields.get("condition") is not None and fields["condition"] not in CONDITIONS:
        raise ValueError(f"An If checks one of {', '.join(CONDITIONS)}.")
    if fields.get("pipeline") and fields["pipeline"] not in pipeline_mod.PIPELINES:
        raise ValueError(f"A Pipeline is one of {', '.join(p.title() for p in pipeline_mod.PIPELINES)}.")
    if "needs_edit" in fields and fields["needs_edit"] not in (0, 1):
        raise ValueError("Needs edit is 1 (its Drafts must be changed before sending) or 0.")
    fields = {k: (v or None) if k in ("at_time", "linkedin_move", "reply_to_step_id", "enroll_sequence_id", "pipeline",
                                       "stage") else v
              for k, v in fields.items()}
    with inbox._write_snapshot(db):
        row = db.execute("SELECT sequence_id FROM sequence_steps WHERE id = ?", (step_id,)).fetchone()
        if row is None:
            raise KeyError(f"step {step_id}")
        if fields:
            db.execute(f"UPDATE sequence_steps SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                       (*fields.values(), step_id))
            _touch(db, row["sequence_id"], now=now)
        return dict(db.execute("SELECT * FROM sequence_steps WHERE id = ?", (step_id,)).fetchone())


@writer("write")
def move_step(db, step_id: int, delta: int, *, now: datetime | None = None) -> None:
    """Swap a Step with its neighbour on its path (`delta` -1 up, +1 down); an If stays last on its path."""
    with db:
        _tx(db)
        st = db.execute("SELECT * FROM sequence_steps WHERE id = ?", (step_id,)).fetchone()
        if st is None or st["position"] is None:
            return
        mine = on_path(sequence_steps(db, st["sequence_id"]), path_of(st))
        i = next(i for i, x in enumerate(mine) if x["id"] == step_id)
        if not 0 <= i + delta < len(mine) or "if" in (mine[i]["kind"], mine[i + delta]["kind"]):
            return
        other = mine[i + delta]
        db.execute("UPDATE sequence_steps SET position = NULL WHERE id = ?", (step_id,))
        db.execute("UPDATE sequence_steps SET position = ? WHERE id = ?", (st["position"], other["id"]))
        db.execute("UPDATE sequence_steps SET position = ? WHERE id = ?", (other["position"], step_id))
        _touch(db, st["sequence_id"], now=now)


@writer("write")
def delete_step(db, step_id: int, now: datetime | None = None) -> tuple[int, dict]:
    """Delete a Step, live (FR-SEQ-24): Enrollments that have not reached it skip it; where it is the current
    Due Task, that Task and its unsent Draft go and the next Step's Task comes due by its gap. Deleting an If
    deletes its paths and their On completions too. Returns how many Enrollments moved on, and what restore_step
    needs."""
    now = now or clock.now()
    with inbox._write_snapshot(db):
        st = dict(db.execute("SELECT * FROM sequence_steps WHERE id = ?", (step_id,)).fetchone())
        gone = [st] + (_below(sequence_steps(db, st["sequence_id"]), step_id) if st["kind"] == "if" else [])
        ids = [g["id"] for g in gone]
        marks = ",".join("?" * len(ids))
        open_tasks = [dict(r) for r in db.execute(
            "SELECT t.* FROM tasks t JOIN enrollments e ON e.id = t.enrollment_id"
            f" WHERE t.step_id IN ({marks}) AND t.state = 'DUE' AND e.state != 'FINISHED'", ids)]
        drafts = []
        for t in open_tasks:
            drafts += _drop_task(db, t["id"])
        for g in sorted(gone, key=lambda g: -g["position"]):
            db.execute("UPDATE sequence_steps SET position = NULL WHERE id = ?", (g["id"],))
            db.execute("UPDATE sequence_steps SET position = position - 1 WHERE sequence_id = ? AND position > ?",
                       (st["sequence_id"], g["position"]))
        db.execute(f"UPDATE sequence_steps SET reply_to_step_id = NULL WHERE reply_to_step_id IN ({marks})", ids)
        _touch(db, st["sequence_id"], now=now)
        before = _max_task(db)
        states = [dict(db.execute("SELECT * FROM enrollments WHERE id = ?", (t["enrollment_id"],)).fetchone()) for t in open_tasks]
        materialize(db, now)
    return len(open_tasks), dict(step=st, steps=gone, tasks=open_tasks, drafts=drafts, after=before, enrollments=states)


@writer("write")
def restore_step(db, undo: dict, *, now: datetime | None = None) -> None:
    """Put a deleted Step back (an If with its paths), with the Tasks and Drafts it removed; Tasks made since for
    those Enrollments go."""
    gone = undo.get("steps") or [undo["step"]]
    with db:
        _tx(db)
        enrollments = {t["enrollment_id"] for t in undo["tasks"]}
        for t in db.execute("SELECT id, enrollment_id FROM tasks WHERE id > ? AND state = 'DUE'", (undo["after"],)).fetchall():
            if t["enrollment_id"] in enrollments:
                _drop_task(db, t["id"])
        for st in sorted(gone, key=lambda g: g["position"]):
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
    """Problems the editor shows on the Step row and detail pane (FR-SEQ-4, FR-SEQ-32); apply refuses a Sequence
    with any."""
    steps = sequence_steps(db, sequence_id)
    byid = {st["id"]: st for st in steps}
    problems = []
    for i, st in enumerate(steps):
        say = lambda text: problems.append((st["id"], text))  # noqa: E731
        for name in sorted({m for text in (st["subject"], st["body"], st["voicemail"]) for m in MARKER.findall(text)}
                           - set(FILL_INS)):
            say(f"{{{name}}} is not a fill-in. Use {{first_name}}, {{last_name}}, or {{company}}.")
        if st["reply_to_step_id"]:
            if i == 0:
                say("Step 1 cannot reply to another Step: there is no earlier email. Clear Reply to.")
            elif st["reply_to_step_id"] not in {s["id"] for s in steps[:i] if s["kind"] == "email"}:
                say("Reply to must name an earlier email Step.")
            if st["kind"] != "email":
                say("Only an email Step can reply in an earlier email's Thread.")
        mine = on_path(steps, path_of(st))
        if st["kind"] == "if" and mine[-1]["id"] != st["id"]:
            say("Nothing follows an If on its path: move the Steps after it onto one of its paths.")
        if st["kind"] == "action":
            problems += [(st["id"], text) for text in _action_problems(db, st, sequence_id, mine)]
        if st["kind"] == "if":
            problems += [(st["id"], text) for text in _if_problems(st, byid)]
    return problems


def _stage_problem(st) -> str | None:
    if st["pipeline"] not in pipeline_mod.PIPELINES:
        return "Name the Pipeline: Revenue, Fundraising, or Talent."
    try:
        pipeline_mod.parse_stage(st["pipeline"], st["stage"] or "")
    except pipeline_mod.Refused as e:
        return f"{e}."
    return None


def _action_problems(db, st, sequence_id, mine) -> list[str]:
    kind = st["action_kind"]
    if kind == "set_stage":
        return [p for p in [_stage_problem(st)] if p]
    if kind == "set_value":
        return [] if st["pipeline"] in pipeline_mod.PIPELINES and st["value"] is not None else \
            ["Name the Pipeline and the Value to set."]
    if kind == "enroll":
        target = db.execute("SELECT * FROM sequences WHERE id = ?", (st["enroll_sequence_id"],)).fetchone()
        if target is None:
            return ["Name the Sequence this Action enrolls them in."]
        if target["id"] == sequence_id and not (st["section"] == "completion" and mine[-1]["id"] == st["id"]):
            return ["An Action that repeats this Sequence must be the last Step of an On completion."]
        if target["archived_at"]:
            return [f"{target['name']} is archived; restore it, or enrolling waits for you."]
    return []


def _if_problems(st, byid) -> list[str]:
    cond = st["condition"]
    if st["section"] == "start":
        return ["An If cannot be in On start; put it in the Steps."]
    if cond == "end_reason":
        if st["section"] != "completion":
            return ["How it finished is known only in an On completion; move this If there."]
        reasons = [r.strip() for r in st["operand"].split(",") if r.strip()]
        if not reasons or set(reasons) - set(FINISHING_REASONS):
            return ["Name how it finished: " + ", ".join(REASON_LABEL[r] for r in FINISHING_REASONS) + "."]
    if cond == "has_channel" and st["operand"] not in CHANNELS:
        return ["Name the channel: email or phone."]
    if cond == "stage":
        problem = _stage_problem(st)
        return [problem] if problem else []
    if cond == "jeremy_picks" and not (st["subject"].strip() and st["true_label"].strip() and st["false_label"].strip()):
        return ["Write the question and a label for each path."]
    return []


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


@writer("create")
def enroll(db, person_id: int, sequence_id: int, now: datetime | None = None, start: date | None = None,
           organization_id: int | None = None, identity_id: int | None = None) -> int:
    """One Person into one Sequence (FR-SEQ-6), for one Organization (FR-SEQ-36; default: their only one), sending
    to one of their email addresses (default: the first of `address_choices`); its first Task counts from the
    enroll day, or from a later Start date (FR-SEQ-25). On start runs first."""
    now = now or clock.now()
    today = now.astimezone(LOCAL).date()
    if start and start < today:
        raise Refused(f"The Start date {start:%b} {start.day} is before today.")
    starts = clock.iso(datetime.combine(start, time(0, 0), LOCAL)) if start and start > today else None
    with db:
        _tx(db)
        eid = _enroll(db, person_id, sequence_id, now, starts,
                      organization_id if organization_id is not None else default_organization(db, person_id),
                      identity_id)
        materialize(db, now)
    return eid


def organization_choices(db, person_id: int) -> list[dict]:
    """The Organizations an Enrollment can be for (FR-SEQ-36): the Person's, the one their latest message came
    from first; the enroll dialog adds "New sole proprietorship"."""
    orgs = [dict(r) for d in inbox.person_organizations(db, person_id)
            for r in db.execute("SELECT id, name, domain FROM organizations WHERE domain = ?", (d,))]
    last = db.execute("SELECT i.value FROM thread_activities a JOIN identities i ON i.id = a.identity_id"
                      " WHERE i.person_id = ? AND i.kind = 'email' ORDER BY a.occurred_at DESC, a.id DESC LIMIT 1",
                      (person_id,)).fetchone()
    key = inbox._org(last[0].split("@")[-1]) if last else None
    return sorted(orgs, key=lambda o: o["domain"] != key)


def address_choices(db, person_id: int, organization_id: int | None = None) -> list[dict]:
    """The email addresses an Enrollment can send to, the default first: the one at its Organization, else the
    one added last (Jeremy, 2026-10-27)."""
    return [dict(r) for r in db.execute(
        "SELECT i.id, i.value FROM identities i LEFT JOIN organizations o ON o.id = ?"
        " WHERE i.person_id = ? AND i.kind = 'email' AND NOT i.blocked"
        " ORDER BY i.value LIKE '%@' || o.domain DESC, i.id DESC", (organization_id, person_id))]


def default_organization(db, person_id: int) -> int | None:
    """The Organization an Enrollment is for when none is named: the Person's only one, else none."""
    domains = inbox.person_organizations(db, person_id)
    if len(domains) != 1:
        return None
    row = db.execute("SELECT id FROM organizations WHERE domain = ?", (domains[0],)).fetchone()
    return row[0] if row else None


def first_due(db, enrollment_id: int, now: datetime | None = None) -> str:
    """When an Enrollment's first Task is due, for a confirmation: "today" or "on Nov 2"."""
    t = db.execute("SELECT due_at FROM tasks WHERE enrollment_id = ? ORDER BY id LIMIT 1", (enrollment_id,)).fetchone()
    if t is None or datetime.fromisoformat(t[0]).astimezone(LOCAL).date() <= (now or clock.now()).astimezone(LOCAL).date():
        return "today"
    return f"on {_day(t[0])}"


def _enroll(db, person_id: int, sequence_id: int, now: datetime, starts: str | None = None,
            organization_id: int | None = None, identity_id: int | None = None) -> int:
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
    if identity_id is not None and not db.execute("SELECT 1 FROM identities WHERE id = ? AND person_id = ?"
                                                  " AND kind = 'email'", (identity_id, person_id)).fetchone():
        raise Refused(f"That email address is not {p['display_name']}'s.")
    return db.execute("INSERT INTO enrollments (person_id, sequence_id, state, enrolled_at, starts_at, organization_id,"
                      " identity_id) VALUES (?, ?, 'ACTIVE', ?, ?, ?, ?) RETURNING id",
                      (person_id, sequence_id, clock.iso(now), starts, organization_id, identity_id)).fetchone()[0]


@writer("write")
def unenroll(db, enrollment_id: int, *, now: datetime | None = None) -> None:
    """Undo an enroll before any Task is done: the Enrollment, its Tasks, and their unsent Drafts go."""
    with db:
        _tx(db)
        if db.execute("SELECT 1 FROM tasks WHERE enrollment_id = ? AND state != 'DUE'", (enrollment_id,)).fetchone():
            raise Refused("A Task is already done; finish the Enrollment instead.")
        for r in db.execute("SELECT id FROM tasks WHERE enrollment_id = ?", (enrollment_id,)).fetchall():
            _drop_task(db, r["id"])
        db.execute("DELETE FROM enrollments WHERE id = ?", (enrollment_id,))


@writer("create")
def task_new_thread(db, task_id: int, *, now: datetime | None = None) -> int:
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
    d = datetime.fromisoformat(iso).astimezone(LOCAL)
    return f"{d:%b} {d.day}"


def materialize(db, now: datetime | None = None) -> int:
    """Make each running Enrollment's next Task, carry out each Action and If whose time has come, and make the
    Draft of each email Task whose day has come; an Enrollment whose path runs out becomes Finishing and works that
    path's On completion, then Finished. Idempotent (one Task per Enrollment and Step, one Draft per Task), so it
    runs every minute from wake. Returns how many Tasks or Drafts it made."""
    now = now or clock.now()
    made = 0
    with inbox._write_snapshot(db):
        moved = 1
        while moved:  # an Action or If done, or an Enrollment it made, gets its next Task in the same run
            moved = 0
            for e in db.execute("SELECT * FROM enrollments WHERE state IN ('ACTIVE', 'FINISHING')").fetchall():
                if db.execute("SELECT 1 FROM tasks WHERE enrollment_id = ? AND state = 'DUE'", (e["id"],)).fetchone():
                    continue
                moved += _next_task(db, e, now) is not None
            moved += _auto_tasks(db, now)
            made += moved
        today = now.astimezone(LOCAL).date()
        for t in db.execute(
                "SELECT t.*, s.kind, s.reply_to_step_id, s.subject, s.body, s.needs_edit, e.person_id, e.sequence_id FROM tasks t"
                " JOIN sequence_steps s ON s.id = t.step_id JOIN enrollments e ON e.id = t.enrollment_id"
                " WHERE t.state = 'DUE' AND e.state IN ('ACTIVE', 'FINISHING') AND s.kind = 'email' AND t.flag IS NULL"
                " AND NOT EXISTS (SELECT 1 FROM composition_drafts d WHERE d.task_id = t.id)").fetchall():
            if datetime.fromisoformat(t["due_at"]).astimezone(LOCAL).date() <= today:
                made += _task_draft(db, t)
    return made


def _last_task(db, enrollment_id: int):
    return db.execute("SELECT t.*, s.position AS step_position, s.section, s.parent_step_id, s.branch, s.kind"
                      " FROM tasks t JOIN sequence_steps s ON s.id = t.step_id"
                      " WHERE t.enrollment_id = ? ORDER BY t.id DESC LIMIT 1", (enrollment_id,)).fetchone()


def _enter(steps, path, taken):
    """The first Step not yet worked on a path, else what follows the path's end."""
    mine = [st for st in on_path(steps, path) if st["id"] not in taken]
    return mine[0] if mine else _end(steps, path, taken)


def _end(steps, path, taken):
    """Past the end of a path: On start runs on into the body; a body path ends ("end", path), which opens its On
    completion; an On completion's end finishes the Enrollment."""
    if path[0] == "start":
        return _enter(steps, ("body", None, None), taken)
    return ("end", path) if path[0] == "body" else ("finished",)


def _successor(steps, last, taken):
    if last is None:
        return _enter(steps, ("start", None, None), taken)
    if last["kind"] == "if" and last["choice"] is not None:
        return _enter(steps, (last["section"], last["step_id"], last["choice"]), taken)
    path = (last["section"], last["parent_step_id"], last["branch"])
    rest = [st for st in on_path(steps, path) if st["id"] not in taken
            and (last["step_position"] is None or st["position"] > last["step_position"])]
    return rest[0] if rest else _end(steps, path, taken)


def current_path(db, enrollment_id: int) -> tuple:
    """The path an Enrollment is on now: its Due Task's, else its last Task's; On start counts as the body."""
    last = _last_task(db, enrollment_id)
    if last is None or last["section"] == "start":
        return ("body", None, None)
    if last["kind"] == "if" and last["choice"] is not None:
        return (last["section"], last["step_id"], last["choice"])
    return (last["section"], last["parent_step_id"], last["branch"])


def _next_task(db, e, now: datetime, due: datetime | None = None) -> int | None:
    """The Task for the Step after this Enrollment's last one. At the end of a body path the Enrollment becomes
    Finishing (Completed, unless Jeremy named how it finished) and works that path's On completion; at the end
    of that it is Finished."""
    steps = sequence_steps(db, e["sequence_id"])
    last = _last_task(db, e["id"])
    taken = {r[0] for r in db.execute("SELECT step_id FROM tasks WHERE enrollment_id = ?", (e["id"],))}
    step = _successor(steps, last, taken)
    if isinstance(step, tuple) and step[0] == "end":
        db.execute("UPDATE enrollments SET state = 'FINISHING', reason = coalesce(reason, 'COMPLETED') WHERE id = ?",
                   (e["id"],))
        step = _enter(steps, completion_of(step[1]), taken)
    if isinstance(step, tuple):
        db.execute("UPDATE enrollments SET state = 'FINISHED', reason = coalesce(reason, 'COMPLETED'), finished_at = ?"
                   " WHERE id = ?", (clock.iso(now), e["id"]))
        return None
    base = datetime.fromisoformat(last["done_at"]) if last else datetime.fromisoformat(e["starts_at"] or e["enrolled_at"])
    due = due or due_date(base, step["gap_days"], step["at_time"])
    row = db.execute("INSERT OR IGNORE INTO tasks (enrollment_id, step_id, due_at, state) VALUES (?, ?, ?, 'DUE')"
                     " RETURNING id", (e["id"], step["id"], clock.iso(due))).fetchone()
    return row[0] if row else None


def _auto_tasks(db, now: datetime) -> int:
    """Carry out the Actions and Ifs whose time has come (FR-SEQ-27, FR-SEQ-29). An Action that cannot run waits
    for Jeremy (flag); one waiting for an Organization is tried again each run. An If asking Jeremy waits for his
    pick."""
    done = 0
    for t in db.execute(
            "SELECT t.id, t.due_at, t.enrollment_id, s.id AS step_id FROM tasks t"
            " JOIN sequence_steps s ON s.id = t.step_id JOIN enrollments e ON e.id = t.enrollment_id"
            " WHERE t.state = 'DUE' AND e.state IN ('ACTIVE', 'FINISHING') AND (t.flag IS NULL OR t.flag = ?)"
            " AND (s.kind = 'action' OR (s.kind = 'if' AND s.condition != 'jeremy_picks'))", (NO_ORG,)).fetchall():
        if datetime.fromisoformat(t["due_at"]) > now:
            continue
        st = dict(db.execute("SELECT * FROM sequence_steps WHERE id = ?", (t["step_id"],)).fetchone())
        e = dict(db.execute("SELECT * FROM enrollments WHERE id = ?", (t["enrollment_id"],)).fetchone())
        if st["kind"] == "if":
            db.execute("UPDATE tasks SET choice = ? WHERE id = ?", (int(check(db, st, e)), t["id"]))
            _close(db, t["id"], "DONE", now)
        elif flag := _run_action(db, t["id"], st, e, now):
            db.execute("UPDATE tasks SET flag = ? WHERE id = ? AND flag IS NOT ?", (flag, t["id"], flag))
            continue
        done += 1
    return done


def check(db, st: dict, e: dict) -> bool:
    """An If's condition for one Enrollment (FR-SEQ-29)."""
    cond = st["condition"]
    if cond == "end_reason":
        return (e["reason"] or "") in {r.strip() for r in st["operand"].split(",")}
    if cond == "has_channel":
        return db.execute("SELECT 1 FROM identities WHERE person_id = ? AND kind = ?",
                          (e["person_id"], st["operand"])).fetchone() is not None
    if cond == "stage":
        held = pipeline_mod.current(db, e["organization_id"]) if e["organization_id"] else {}
        return held.get(st["pipeline"], {}).get("stage") == st["stage"]
    raise ValueError(f"{cond} is not checked by itself")


def _run_action(db, task_id: int, st: dict, e: dict, now: datetime) -> str | None:
    """Carry out one Action and close its Task; returns why it waits for Jeremy instead. An Enrollment that names
    no Organization takes the Person's only one, as Enroll does (FR-SEQ-36), once they have it."""
    org, kind = e["organization_id"], st["action_kind"]
    if org is None and (org := default_organization(db, e["person_id"])) is not None:
        db.execute("UPDATE enrollments SET organization_id = ? WHERE id = ?", (org, e["id"]))
        db.execute("UPDATE tasks SET flag = NULL WHERE id = ?", (task_id,))
        e["organization_id"] = org
    if kind in ("set_stage", "set_value", "end_for_others") and org is None:
        return NO_ORG
    try:
        if kind == "set_stage":
            pipeline_mod.set_stage(db, org, st["pipeline"], st["stage"])
        elif kind == "set_value":
            pipeline_mod.set_value(db, org, st["pipeline"], st["value"])
        elif kind == "end_for_others":
            for other in db.execute("SELECT id FROM enrollments WHERE sequence_id = ? AND organization_id = ?"
                                    " AND person_id != ? AND state != 'FINISHED'",
                                    (e["sequence_id"], org, e["person_id"])).fetchall():
                _finish(db, other["id"], "CANCELED", now)
        elif kind == "enroll":
            return _enroll_action(db, task_id, st, e, now)
    except (pipeline_mod.Refused, Refused) as x:
        return str(x)
    _close(db, task_id, "DONE", now)
    return None


def _enroll_action(db, task_id: int, st: dict, e: dict, now: datetime) -> str | None:
    """Enroll the Person in the Action's Sequence, for the same Organization. One who responded during this
    Enrollment (outside an On completion, where Jeremy already said how it finished), or went through that
    Sequence before, waits for Jeremy; the second rule also ends a loop of Sequences that enroll each other. A
    Sequence enrolling them in itself repeats it: this Enrollment finishes first (FR-SEQ-40)."""
    target = db.execute("SELECT name FROM sequences WHERE id = ?", (st["enroll_sequence_id"],)).fetchone()
    repeat = st["enroll_sequence_id"] == e["sequence_id"]
    before = not repeat and db.execute("SELECT 1 FROM enrollments WHERE person_id = ? AND sequence_id = ?",
                                       (e["person_id"], st["enroll_sequence_id"])).fetchone()
    flag = ("Action names no Sequence" if target is None
            else f"they responded {_day(e['responded_at'])}" if e["responded_at"] and not repeat and st["section"] != "completion"
            else f"already went through {target['name']}" if before else None)
    if flag:
        return flag
    _close(db, task_id, "DONE", now)
    if repeat:
        db.execute("UPDATE enrollments SET state = 'FINISHED', reason = coalesce(reason, 'COMPLETED'), finished_at = ?"
                   " WHERE id = ?", (clock.iso(now), e["id"]))
    try:
        _enroll(db, e["person_id"], st["enroll_sequence_id"], now, organization_id=e["organization_id"],
                identity_id=e["identity_id"])
    except Refused as x:
        db.execute("UPDATE tasks SET state = 'DUE', done_at = NULL WHERE id = ?", (task_id,))
        if repeat:
            db.execute("UPDATE enrollments SET state = ?, finished_at = NULL WHERE id = ?", (e["state"], e["id"]))
        return str(x)
    return None


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
        e = db.execute("SELECT identity_id, organization_id FROM enrollments WHERE id = ?", (t["enrollment_id"],)).fetchone()
        choices = address_choices(db, t["person_id"], e["organization_id"])
        address = next((c["value"] for c in choices if c["id"] == e["identity_id"]), choices[0]["value"] if choices else None)
        if conn is None or address is None:
            db.execute("UPDATE tasks SET flag = ? WHERE id = ?",
                       ("no email address" if address is None else "Sequence has no Connection", t["id"]))
            return 0
        subject, more = fill(t["subject"], values)
        missing += [m for m in more if m not in missing]
        d = inbox.save_draft(db, dict(connection_id=conn["id"], kind="email", sender=conn["self_id"], to=address,
                                      cc="", subject=subject, body=body), task_id=t["id"])
    if t["needs_edit"]:
        db.execute("UPDATE composition_drafts SET needs_edit = 1 WHERE id = ?", (d["id"],))
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
               (state, clock.iso(now), thread_id, task_id))
    return undo


@writer("create")
def complete_task(db, task_id: int, now: datetime | None = None) -> dict:
    """Jeremy did a non-email Task (FR-SEQ-9); an enroll Action enrolls the Person now, whatever its flag said.
    The next Step's Task follows. Returns what undo_task needs."""
    now = now or clock.now()
    with db:
        _tx(db)
        t = db.execute("SELECT s.kind, s.action_kind, s.condition, s.enroll_sequence_id, e.person_id, e.organization_id"
                       " FROM tasks t JOIN sequence_steps s ON s.id = t.step_id"
                       " JOIN enrollments e ON e.id = t.enrollment_id WHERE t.id = ?", (task_id,)).fetchone()
        if t and t["kind"] == "email":
            raise Refused("An email Task is done when its Draft sends.")
        if t and t["kind"] == "if":
            raise Refused("Pick a path for this If: 1 or 2.")
        enrolling = t and t["action_kind"] == "enroll"
        if enrolling and t["enroll_sequence_id"] is None:
            raise Refused("This Action names no Sequence; name one in its Step, or skip the Task.")
        enrolled = _enroll(db, t["person_id"], t["enroll_sequence_id"], now,
                           organization_id=t["organization_id"]) if enrolling else None
        undo = _close(db, task_id, "DONE", now) | dict(enrolled=enrolled)
        materialize(db, now)
    return undo


@writer("create")
def skip_task(db, task_id: int, now: datetime | None = None) -> dict:
    """Skip a Task; its unsent Draft goes. A skip counts as done for the next Task's gap."""
    now = now or clock.now()
    with db:
        _tx(db)
        undo = _close(db, task_id, "SKIPPED", now)
        db.execute("DELETE FROM composition_drafts WHERE task_id = ? AND sending_at IS NULL", (task_id,))
        materialize(db, now)
    return undo


@writer("write")
def undo_task(db, undo: dict, *, now: datetime | None = None) -> None:
    """Back to before complete_task or skip_task: the Task due again, its Draft and Enrollment as they were."""
    with db:
        _tx(db)
        for t in db.execute("SELECT id FROM tasks WHERE enrollment_id IN (?, ?) AND id > ?",
                            (undo["task"]["enrollment_id"], undo.get("enrolled"), undo["after"])).fetchall():
            _drop_task(db, t["id"])
        if undo.get("enrolled"):  # the Enrollment an enroll Action made
            db.execute("DELETE FROM enrollments WHERE id = ?", (undo["enrolled"],))
        db.execute("UPDATE tasks SET state = ?, done_at = ?, thread_id = ?, flag = ?, choice = ? WHERE id = ?",
                   (undo["task"]["state"], undo["task"]["done_at"], undo["task"]["thread_id"], undo["task"]["flag"],
                    undo["task"].get("choice"), undo["task"]["id"]))
        e = undo["enrollment"]
        db.execute("UPDATE enrollments SET state = ?, reason = ?, finished_at = ? WHERE id = ?",
                   (e["state"], e["reason"], e["finished_at"], e["id"]))
        have = {r[0] for r in db.execute("SELECT id FROM composition_drafts WHERE task_id = ?", (undo["task"]["id"],))}
        for d in undo["drafts"]:
            if d["id"] not in have:
                _insert(db, "composition_drafts", d)


def tasks_due(db, now: datetime | None = None) -> list[dict]:
    """The Tasks page (FR-SEQ-15): Active Enrollments' Tasks whose day has come, grouped Overdue, Today, and Later
    today (a fixed time not yet reached), oldest first. A Task whose Draft is scheduled to send is left out until it
    sends, or until the send fails or is unscheduled (Jeremy, 2026-10-20)."""
    now = now or clock.now()
    today = now.astimezone(LOCAL).date()
    out = []
    for r in db.execute(
            "SELECT t.*, s.kind, s.action_kind, s.condition, s.true_label, s.false_label, s.position, s.at_time, s.subject,"
            " s.body, s.voicemail, s.linkedin_move, s.reply_to_step_id,"
            " e.person_id, e.sequence_id, coalesce(e.starts_at, e.enrolled_at) AS starts_at, q.name AS sequence, p.display_name AS person,"
            " (SELECT count(*) FROM sequence_steps x WHERE x.sequence_id = q.id AND x.position IS NOT NULL) AS steps,"
            " (SELECT id FROM composition_drafts d WHERE d.task_id = t.id) AS draft_id,"
            " (SELECT name FROM sequences x WHERE x.id = s.enroll_sequence_id) AS enroll_sequence"
            " FROM tasks t JOIN sequence_steps s ON s.id = t.step_id JOIN enrollments e ON e.id = t.enrollment_id"
            " JOIN sequences q ON q.id = e.sequence_id JOIN persons p ON p.id = e.person_id"
            " WHERE t.state = 'DUE' AND e.state IN ('ACTIVE', 'FINISHING') AND NOT EXISTS (SELECT 1 FROM composition_drafts d"
            " WHERE d.task_id = t.id AND d.send_at IS NOT NULL AND d.send_error IS NULL) ORDER BY t.due_at, t.id"):
        due = datetime.fromisoformat(r["due_at"])
        day = due.astimezone(LOCAL).date()
        if day > today:
            continue
        group = "overdue" if day < today else "later today" if r["at_time"] and due > now else "due today"
        org = person_fill(db, r["person_id"])["company"]
        out.append(dict(r) | dict(group=group, organization=org, day=day_number(datetime.fromisoformat(r["starts_at"]), due),
                                  age=(today - day).days))
    order = {"overdue": 0, "due today": 1, "later today": 2}
    return sorted(out, key=lambda t: (order[t["group"]], t["due_at"], t["id"]))


# --- hooks from inbox ---------------------------------------------------------

def send_problem(db, draft: dict, text: str | None = None) -> str | None:
    """Why a Draft cannot send yet: it Needs edit and its text is unchanged (`text` is a reply box's), or, for a
    Task Draft, a fill-in marker is left or its Enrollment is Paused or Finished."""
    if draft.get("needs_edit") and (text is None or text.strip() == draft["body"].strip()):
        return "Not sent: this Draft Needs edit. Change its text first."
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
    if state and state[0] in ("ACTIVE", "FINISHING"):
        materialize(db, now)


def task_draft_deleted(db, draft: dict, now: datetime | None = None) -> None:
    """Deleting an email Task's Draft skips the Task (FR-SEQ-9)."""
    if draft.get("task_id"):
        now = now or clock.now()
        db.execute("UPDATE tasks SET state = 'SKIPPED', done_at = ? WHERE id = ? AND state = 'DUE'",
                   (clock.iso(now), draft["task_id"]))
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
    """Anything inbound from the Person, or a connected call, pauses their Active and Finishing Enrollments
    (FR-SEQ-12, FR-SEQ-35); a Finishing one keeps how it finished. Messages older than the Enrollment (imports)
    do not. Runs inside ingest's transaction."""
    if not any(c[1] == "responded_at" for c in db.execute("PRAGMA table_info(enrollments)")):
        return 0  # a database still being built up to schema 20 (migration tests ingest along the way)
    db.execute("UPDATE enrollments SET responded_at = ? WHERE person_id = ? AND state != 'FINISHED'"
               " AND responded_at IS NULL AND enrolled_at <= ?", (clock.iso(at), person_id, clock.iso(at)))
    return db.execute("UPDATE enrollments SET state = 'PAUSED', paused_at = ?, pause_reason = ?"
                      " WHERE person_id = ? AND state IN ('ACTIVE', 'FINISHING') AND enrolled_at <= ?",
                      (clock.iso(at), why, person_id, clock.iso(at))).rowcount


@writer("write")
def resume(db, enrollment_id: int, now: datetime | None = None) -> None:
    """Paused to Active, or back to Finishing when it was paused there; the next Task is due at once (FR-SEQ-13)."""
    now = now or clock.now()
    with db:
        _tx(db)
        e = db.execute("SELECT * FROM enrollments WHERE id = ?", (enrollment_id,)).fetchone()
        if e is None or e["state"] != "PAUSED":
            raise Refused("Only a Paused Enrollment resumes.")
        db.execute("UPDATE enrollments SET state = CASE WHEN reason IS NULL THEN 'ACTIVE' ELSE 'FINISHING' END,"
                   " paused_at = NULL, pause_reason = NULL WHERE id = ?", (enrollment_id,))
        if db.execute("SELECT 1 FROM tasks WHERE enrollment_id = ? AND state = 'DUE'", (enrollment_id,)).fetchone():
            db.execute("UPDATE tasks SET due_at = ? WHERE enrollment_id = ? AND state = 'DUE' AND due_at > ?",
                       (clock.iso(now), enrollment_id, clock.iso(now)))
        else:
            _next_task(db, db.execute("SELECT * FROM enrollments WHERE id = ?", (enrollment_id,)).fetchone(), now, due=now)
        materialize(db, now)


@writer("write")
def reschedule(db, enrollment_id: int, start: date, now: datetime | None = None) -> dict:
    """Put a running Enrollment on an agreed date (G1, .agents/discovery/2026-10-26-mainline-sequences.md): its Due
    Task comes due that day, at its Step's time; before any touch is done, the Start date moves too, so Day numbers
    count from it. For a warm run that a chain started at once. Returns the old values."""
    now = now or clock.now()
    if start < now.astimezone(LOCAL).date():
        raise Refused(f"{start:%b} {start.day} is before today.")
    with db:
        _tx(db)
        e = db.execute("SELECT * FROM enrollments WHERE id = ?", (enrollment_id,)).fetchone()
        if e is None or e["state"] == "FINISHED":
            raise Refused("Only a running Enrollment can be rescheduled.")
        touched = db.execute("SELECT 1 FROM tasks t JOIN sequence_steps s ON s.id = t.step_id WHERE t.enrollment_id = ?"
                             f" AND t.state != 'DUE' AND s.kind IN ({','.join('?' * len(TOUCHES))})",
                             (enrollment_id, *TOUCHES)).fetchone()
        old = dict(starts_at=e["starts_at"], tasks={})
        if not touched:
            db.execute("UPDATE enrollments SET starts_at = ? WHERE id = ?",
                       (clock.iso(datetime.combine(start, time(0, 0), LOCAL)), enrollment_id))
        for t in db.execute("SELECT t.id, t.due_at, s.at_time FROM tasks t JOIN sequence_steps s ON s.id = t.step_id"
                            " WHERE t.enrollment_id = ? AND t.state = 'DUE'", (enrollment_id,)).fetchall():
            old["tasks"][t["id"]] = t["due_at"]
            due = datetime.combine(start, time.fromisoformat(t["at_time"]) if t["at_time"] else time(0, 0), LOCAL)
            db.execute("UPDATE tasks SET due_at = ? WHERE id = ?", (clock.iso(due.astimezone(timezone.utc)), t["id"]))
    return old


def _finish(db, enrollment_id: int, reason: str, now: datetime) -> dict:
    e = dict(db.execute("SELECT * FROM enrollments WHERE id = ?", (enrollment_id,)).fetchone())
    drafts = [dict(r) for r in db.execute(
        "SELECT d.* FROM composition_drafts d JOIN tasks t ON t.id = d.task_id"
        " WHERE t.enrollment_id = ? AND d.sending_at IS NULL", (enrollment_id,))]
    for d in drafts:
        db.execute("DELETE FROM composition_drafts WHERE id = ?", (d["id"],))
    db.execute("UPDATE enrollments SET state = 'FINISHED', reason = ?, finished_at = ? WHERE id = ?",
               (reason, clock.iso(now), enrollment_id))
    return dict(enrollment=e, drafts=drafts)


def _restore_finish(db, undo: dict) -> None:
    e = undo["enrollment"]
    db.execute("UPDATE enrollments SET state = ?, reason = ?, finished_at = ?, paused_at = ?, pause_reason = ? WHERE id = ?",
               (e["state"], e["reason"], e["finished_at"], e["paused_at"], e["pause_reason"], e["id"]))
    for d in undo["drafts"]:
        _insert(db, "composition_drafts", d)


@writer("write")
def finish(db, enrollment_id: int, reason: str, now: datetime | None = None) -> list[dict]:
    """End an Enrollment (FR-SEQ-33, FR-SEQ-34). Goal met, Not Right Now, Lost, or Completed make it Finishing: the
    current Task and its unsent Draft go, and the On completion of the path it was on runs. Canceled and
    Unsubscribed end it at once with no On completion; Unsubscribed also marks the Person and ends every Sequence
    they are in. Returns what restore_finish needs."""
    if reason not in END_REASONS:
        raise ValueError(f"reason is one of {', '.join(END_REASONS)}")
    now = now or clock.now()
    with db:
        _tx(db)
        e = db.execute("SELECT * FROM enrollments WHERE id = ?", (enrollment_id,)).fetchone()
        if e is None or e["state"] == "FINISHED":
            raise Refused("This Enrollment has already finished.")
        if reason == "UNSUBSCRIBED":
            return _unsubscribe(db, e["person_id"], now)
        if reason == "CANCELED":
            return [_finish(db, enrollment_id, reason, now)]
        if e["reason"] is not None:
            raise Refused(f"It is already finishing as {REASON_LABEL[e['reason']]}; Cancel it to stop its On completion.")
        mark = _mark(db)
        path = current_path(db, enrollment_id)
        for t in db.execute("SELECT id FROM tasks WHERE enrollment_id = ? AND state = 'DUE'", (enrollment_id,)).fetchall():
            _drop_task(db, t["id"])
        db.execute("UPDATE enrollments SET state = 'FINISHING', reason = ?, paused_at = NULL, pause_reason = NULL"
                   " WHERE id = ?", (reason, enrollment_id))
        steps = sequence_steps(db, e["sequence_id"])
        taken = {r[0] for r in db.execute("SELECT step_id FROM tasks WHERE enrollment_id = ?", (enrollment_id,))}
        first = _enter(steps, completion_of(path), taken)
        if isinstance(first, tuple):
            db.execute("UPDATE enrollments SET state = 'FINISHED', finished_at = ? WHERE id = ?",
                       (clock.iso(now), enrollment_id))
        else:
            db.execute("INSERT INTO tasks (enrollment_id, step_id, due_at, state) VALUES (?, ?, ?, 'DUE')",
                       (enrollment_id, first["id"], clock.iso(due_date(now, first["gap_days"], first["at_time"]))))
        materialize(db, now)
        return [dict(rollback=mark)]


def _mark(db) -> dict:
    """What a finish with On completion may change, so restore_finish can put it back: every Enrollment, the Due
    Tasks and their Drafts, and the highest ids. ponytail: snapshots whole small tables; one user, a few dozen rows."""
    due = [dict(r) for r in db.execute("SELECT * FROM tasks WHERE state = 'DUE'")]
    return dict(enrollments=[dict(r) for r in db.execute("SELECT * FROM enrollments")], tasks=due,
                drafts=[dict(r) for r in db.execute("SELECT * FROM composition_drafts WHERE task_id IN"
                                                    " (SELECT id FROM tasks WHERE state = 'DUE')")],
                max_task=_max_task(db), max_enrollment=db.execute("SELECT coalesce(max(id), 0) FROM enrollments").fetchone()[0],
                max_log=db.execute("SELECT coalesce(max(id), 0) FROM pipeline_log").fetchone()[0])


def _rollback(db, mark: dict) -> None:
    for t in db.execute("SELECT id FROM tasks WHERE id > ?", (mark["max_task"],)).fetchall():
        _drop_task(db, t["id"])
    for e in db.execute("SELECT id FROM enrollments WHERE id > ?", (mark["max_enrollment"],)).fetchall():
        for t in db.execute("SELECT id FROM tasks WHERE enrollment_id = ?", (e["id"],)).fetchall():
            _drop_task(db, t["id"])
        db.execute("DELETE FROM enrollments WHERE id = ?", (e["id"],))
    for r in mark["enrollments"]:
        db.execute(f"UPDATE enrollments SET {', '.join(f'{k} = ?' for k in r if k != 'id')} WHERE id = ?",
                   (*(v for k, v in r.items() if k != "id"), r["id"]))
    for t in mark["tasks"]:
        if db.execute("SELECT 1 FROM tasks WHERE id = ?", (t["id"],)).fetchone():
            db.execute("UPDATE tasks SET state = 'DUE', done_at = NULL, flag = ?, choice = ? WHERE id = ?",
                       (t["flag"], t["choice"], t["id"]))
        else:
            _insert(db, "tasks", t)
    have = {r[0] for r in db.execute("SELECT id FROM composition_drafts")}
    for d in mark["drafts"]:
        if d["id"] not in have:
            _insert(db, "composition_drafts", d)
    for r in db.execute("SELECT id FROM pipeline_log WHERE id > ? AND kind != 'UNDO'", (mark["max_log"],)).fetchall():
        pipeline_mod.undo(db, r["id"])


@writer("create")
def pick(db, task_id: int, choice: int, now: datetime | None = None) -> dict:
    """Jeremy answers an If that asks him (FR-SEQ-31): 1 takes its true path, 0 its false path. Returns what
    undo_task needs."""
    now = now or clock.now()
    if choice not in (0, 1):
        raise ValueError("choice is 1 (the first path) or 0 (the second)")
    with db:
        _tx(db)
        t = db.execute("SELECT t.state, s.kind, s.condition FROM tasks t JOIN sequence_steps s ON s.id = t.step_id"
                       " WHERE t.id = ?", (task_id,)).fetchone()
        if t is None or t["kind"] != "if" or t["condition"] != "jeremy_picks" or t["state"] != "DUE":
            raise Refused("Only a Due If that asks you takes a pick.")
        undo = _close(db, task_id, "DONE", now)
        db.execute("UPDATE tasks SET choice = ? WHERE id = ?", (choice, task_id))
        materialize(db, now)
    return undo


def unsubscribe(db, person_id: int, now: datetime | None = None) -> list[dict]:
    now = now or clock.now()
    with db:
        _tx(db)
        return _unsubscribe(db, person_id, now)


def _unsubscribe(db, person_id: int, now: datetime) -> list[dict]:
    before = db.execute("SELECT unsubscribed_at FROM persons WHERE id = ?", (person_id,)).fetchone()[0]
    db.execute("UPDATE persons SET unsubscribed_at = coalesce(unsubscribed_at, ?) WHERE id = ?", (clock.iso(now), person_id))
    undo = [_finish(db, r[0], "UNSUBSCRIBED", now) for r in db.execute(
        "SELECT id FROM enrollments WHERE person_id = ? AND state != 'FINISHED'", (person_id,)).fetchall()]
    return [dict(person=person_id, unsubscribed_at=before)] + undo


@writer("write")
def restore_finish(db, undo: list[dict], *, now: datetime | None = None) -> None:
    with db:
        _tx(db)
        for u in undo:
            if "rollback" in u:
                _rollback(db, u["rollback"])
            elif "person" in u:
                db.execute("UPDATE persons SET unsubscribed_at = ? WHERE id = ?", (u["unsubscribed_at"], u["person"]))
            else:
                _restore_finish(db, u)


def merge_people(db, keep: int, drop: int, *, now: datetime | None = None) -> None:
    """Inside merge_persons: `drop`'s Enrollments move to `keep`; where both are live in one Sequence, `drop`'s
    is Canceled. Unsubscribed carries over."""
    now = (now or clock.now())
    for e in db.execute("SELECT e.id FROM enrollments e WHERE e.person_id = ? AND e.state != 'FINISHED' AND EXISTS"
                        " (SELECT 1 FROM enrollments k WHERE k.person_id = ? AND k.sequence_id = e.sequence_id"
                        " AND k.state != 'FINISHED')", (drop, keep)).fetchall():
        _finish(db, e["id"], "CANCELED", now)
    db.execute("UPDATE enrollments SET person_id = ? WHERE person_id = ?", (keep, drop))
    db.execute("UPDATE persons SET unsubscribed_at = coalesce(unsubscribed_at,"
               " (SELECT unsubscribed_at FROM persons WHERE id = ?)) WHERE id = ?", (drop, keep))


def enrollments(db, person_id: int, now: datetime | None = None) -> list[dict]:
    """A Person's Enrollments, live first then newest, each with its Tasks timeline (FR-SEQ-19)."""
    now = now or clock.now()
    out = []
    for e in db.execute("SELECT e.*, q.name AS sequence FROM enrollments e JOIN sequences q ON q.id = e.sequence_id"
                        " WHERE e.person_id = ? ORDER BY e.state = 'FINISHED', e.id DESC", (person_id,)).fetchall():
        enrolled = datetime.fromisoformat(e["starts_at"] or e["enrolled_at"])
        tasks = {t["step_id"]: dict(t) for t in db.execute("SELECT * FROM tasks WHERE enrollment_id = ?", (e["id"],))}
        steps = [s for s in db.execute("SELECT * FROM sequence_steps WHERE sequence_id = ? ORDER BY position IS NULL, position",
                                       (e["sequence_id"],)) if s["position"] is not None or s["id"] in tasks]
        timeline, base, nxt = [], None, None
        for s in steps:
            t = tasks.get(s["id"])
            if t:
                due = datetime.fromisoformat(t["due_at"])
                base = datetime.fromisoformat(t["done_at"]) if t["done_at"] else None
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


def paused(db) -> list[dict]:
    """Today's Paused list (FR-SEQ-39): Paused Enrollments, oldest pause first, each waiting for Jeremy to resume
    or finish it."""
    return [dict(r) for r in db.execute(
        "SELECT e.id, e.person_id, e.paused_at, e.pause_reason, e.reason, p.display_name AS person, q.name AS sequence,"
        " o.name AS organization FROM enrollments e JOIN persons p ON p.id = e.person_id"
        " JOIN sequences q ON q.id = e.sequence_id LEFT JOIN organizations o ON o.id = e.organization_id"
        " WHERE e.state = 'PAUSED' ORDER BY e.paused_at, e.id")]


def pauses_since(db, since: str) -> list[dict]:
    """Enrollments paused after `since`, for the console's toast (FR-SEQ-20)."""
    return [dict(r) for r in db.execute(
        "SELECT e.id, e.paused_at, e.pause_reason, p.display_name AS person, q.name AS sequence FROM enrollments e"
        " JOIN persons p ON p.id = e.person_id JOIN sequences q ON q.id = e.sequence_id"
        " WHERE e.state = 'PAUSED' AND e.paused_at > ? ORDER BY e.paused_at", (since,))]
