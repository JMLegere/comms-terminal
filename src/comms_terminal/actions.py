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
