"""The Action Queue (ADR 0019; .agents/prd/2026-11-07-issues-and-action-queue.md FR-AQ-1–6): everything Comms
Terminal carries out itself. A writer changes the copy at once and enqueues an Action; the Service runs Actions in
order per Connection. Confirmed: the copy already matches. Refused: the record takes its old values back and shows
"Not saved: reason". A refused key: the Connection is broken and its Actions wait. No answer: an Action safe to
repeat is sent again; one that is not keeps the unknown lock.

Interface: enqueue, run, latest_refusal. Each kind's carrier lives with its records (CARRIERS)."""

import json
from datetime import datetime

from . import clock, inbox

WRITES_BROKEN = "Writes refused: "  # a Connection's Last Error while the Platform refuses its key


def enqueue(db, connection_id: str, kind: str, target: str, payload: dict, previous: dict | None, safe: bool,
            now: datetime) -> str:
    """Record one Action, due now. The caller has already changed the copy (optimistic)."""
    return db.execute("INSERT INTO actions (connection_id, kind, target, payload, previous, safe, state, due_at,"
                      " created_at) VALUES (?, ?, ?, ?, ?, ?, 'scheduled', ?, ?) RETURNING id",
                      (connection_id, kind, target, json.dumps(payload, sort_keys=True),
                       None if previous is None else json.dumps(previous, sort_keys=True), int(safe),
                       clock.iso(now), clock.iso(now))).fetchone()[0]


def _due(db, now: datetime) -> list:
    return db.execute("SELECT a.*, c.platform FROM actions a JOIN connections c ON c.id = a.connection_id"
                      " WHERE (a.state = 'scheduled' OR (a.state = 'unknown' AND a.safe = 1)) AND a.due_at <= ?"
                      " ORDER BY a.created_at, a.id", (clock.iso(now),)).fetchall()


def _carriers():
    from . import issues
    return {"issue_update": issues.carry_update, "issue_create": issues.carry_create}


def run(db, clients: dict, now: datetime) -> int:
    """One pass: each due Action in order, per Connection; a broken or unanswered one holds that Connection's later
    Actions to keep their order. `clients` maps a Platform to its writer. Returns how many were carried out."""
    with db:  # a pass that died mid-Action left it running: whether it happened is unknown
        db.execute("UPDATE actions SET state = 'unknown', error = 'interrupted' WHERE state = 'running'")
    carriers, held, done = _carriers(), set(), 0
    for a in _due(db, now):
        client = clients.get(a["platform"])
        if a["connection_id"] in held or client is None:
            continue
        with db:
            db.execute("UPDATE actions SET state = 'running', attempts = attempts + 1 WHERE id = ?", (a["id"],))
        out = carriers[a["kind"]](db, a, client, now)
        outcome, error = out["outcome"], out.get("error")
        with db:
            if outcome in ("confirmed", "refused"):
                db.execute("UPDATE actions SET state = ?, error = ?, done_at = ? WHERE id = ?",
                           (outcome, error, clock.iso(now), a["id"]))
                done += 1
            elif outcome == "broken":
                db.execute("UPDATE actions SET state = 'scheduled', error = ? WHERE id = ?", (error, a["id"]))
            else:
                db.execute("UPDATE actions SET state = 'unknown', error = ? WHERE id = ?", (error, a["id"]))
        last = db.execute("SELECT last_error FROM connections WHERE id = ?", (a["connection_id"],)).fetchone()[0]
        if outcome == "broken":
            inbox.set_connection_error(db, a["connection_id"], WRITES_BROKEN + error)
            held.add(a["connection_id"])
        elif outcome == "unknown":
            held.add(a["connection_id"])
        elif (last or "").startswith(WRITES_BROKEN):  # a key the Platform takes again clears it
            inbox.set_connection_error(db, a["connection_id"], None)
    return done


def latest_refusal(db, target: str) -> str | None:
    """"Not saved: reason" for a record whose latest Action was refused."""
    row = db.execute("SELECT state, error FROM actions WHERE target = ? ORDER BY created_at DESC, id DESC LIMIT 1",
                     (target,)).fetchone()
    return f"Not saved: {row['error']}" if row and row["state"] == "refused" else None


# --- The Action Queue page (FR-UI-4): Actions, and what is scheduled to leave, as Lemlist's Outreach queue -------

LABEL = {"issue_update": "Issue change", "issue_create": "New Issue"}


def queue(db, now: datetime | None = None, days: int = 7) -> list[dict]:
    """Rows by day for the Action Queue page: every Action not yet confirmed, those confirmed today, and what is
    scheduled to leave in the next `days` (scheduled Drafts, coming Tasks, Enrollment starts). Each row: day, at,
    what, target, source, connection, state, error."""
    from datetime import timedelta

    from .sequences import LOCAL
    now = now or clock.now()
    day = now.astimezone(LOCAL).date()
    until = clock.iso(datetime.combine(day + timedelta(days=days), datetime.min.time(), LOCAL))
    local_day = lambda at: datetime.fromisoformat(at).astimezone(LOCAL).date().isoformat()  # noqa: E731
    rows = []
    for a in db.execute(
            "SELECT a.*, c.platform, c.label, i.identifier, i.title FROM actions a"
            " JOIN connections c ON c.id = a.connection_id LEFT JOIN issues i ON i.id = a.target"
            " WHERE a.state != 'confirmed' OR a.done_at >= ? ORDER BY a.created_at, a.id",
            (clock.iso(datetime.combine(day, datetime.min.time(), LOCAL)),)):
        rows.append({"kind": "action", "id": a["id"], "day": local_day(a["due_at"]), "at": a["due_at"],
                     "what": LABEL.get(a["kind"], a["kind"]), "target": f"{a['identifier'] or ''} {a['title'] or ''}".strip(),
                     "target_id": a["target"], "source": "Issue", "connection": a["label"], "state": a["state"],
                     "error": a["error"]})
    for d in db.execute('SELECT d.id, d.subject, d."to", d.send_at, c.label FROM composition_drafts d'
                        " LEFT JOIN connections c ON c.id = d.connection_id WHERE d.send_at IS NOT NULL AND d.send_at < ?",
                        (until,)):
        rows.append({"kind": "draft", "id": d["id"], "day": local_day(d["send_at"]), "at": d["send_at"],
                     "what": "Send", "target": d["subject"] or f"message to {d['to']}", "target_id": d["id"],
                     "source": "Draft", "connection": d["label"], "state": "scheduled", "error": None})
    for t in db.execute("SELECT t.id, t.due_at, s.kind, p.display_name, q.name FROM tasks t"
                        " JOIN sequence_steps s ON s.id = t.step_id JOIN enrollments e ON e.id = t.enrollment_id"
                        " JOIN sequences q ON q.id = e.sequence_id JOIN persons p ON p.id = e.person_id"
                        " WHERE t.state = 'DUE' AND t.due_at < ?", (until,)):
        rows.append({"kind": "task", "id": t["id"], "day": max(local_day(t["due_at"]), day.isoformat()),
                     "at": t["due_at"], "what": f"{t['kind'].title()} Task", "target": t["display_name"],
                     "target_id": t["id"], "source": t["name"], "connection": None, "state": "scheduled", "error": None})
    for e in db.execute("SELECT e.id, e.starts_at, p.display_name, q.name FROM enrollments e JOIN persons p"
                        " ON p.id = e.person_id JOIN sequences q ON q.id = e.sequence_id"
                        " WHERE e.starts_at IS NOT NULL AND e.state != 'FINISHED' AND e.starts_at < ?", (until,)):
        rows.append({"kind": "enrollment", "id": e["id"], "day": max(local_day(e["starts_at"]), day.isoformat()),
                     "at": e["starts_at"], "what": "Enrollment starts", "target": e["display_name"],
                     "target_id": e["id"], "source": e["name"], "connection": None, "state": "scheduled",
                     "error": None})
    return sorted(rows, key=lambda r: (r["day"], r["at"]))
