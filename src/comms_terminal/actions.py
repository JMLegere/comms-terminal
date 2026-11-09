"""The Action Queue (ADR 0020; .agents/prd/2026-11-07-issues-and-action-queue.md FR-AQ-1–6): everything Comms
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


CARRIED = ("issue_update", "issue_create", "send", "respond")  # every Action that reaches a Platform (ADR 0022)
PRESSED = ("send", "respond")  # started on Jeremy's word and carried at once (start, carry); a stuck one is swept late
STUCK = 600  # seconds a pressed Action may run before a pass calls it interrupted


def _due(db, now: datetime) -> list:
    return db.execute("SELECT a.*, c.platform FROM actions a JOIN connections c ON c.id = a.connection_id"
                      " WHERE (a.state = 'scheduled' OR (a.state = 'unknown' AND a.safe = 1)) AND a.due_at <= ?"
                      f" AND a.kind IN ({', '.join('?' * len(CARRIED))}) ORDER BY a.created_at, a.id",
                      (clock.iso(now), *CARRIED)).fetchall()


def start(db, connection_id: str, kind: str, target: str, payload: dict, safe: bool, now: datetime) -> str:
    """An Action Jeremy's press carries at once (a send, a Respond): recorded running, so run() never picks it up
    while carry() works it. The caller writes it in the same transaction as its claim."""
    return db.execute("INSERT INTO actions (connection_id, kind, target, payload, safe, state, attempts, due_at,"
                      " created_at) VALUES (?, ?, ?, ?, ?, 'running', 1, ?, ?) RETURNING id",
                      (connection_id, kind, str(target), json.dumps(payload, sort_keys=True), int(safe),
                       clock.iso(now), clock.iso(now))).fetchone()[0]


def carry(db, action_id: str, client, now: datetime) -> dict:
    """Carry out one started Action now with its carrier and record the outcome; returns the carrier's answer, so
    the press that started it can answer as it always has (ADR 0022)."""
    a = db.execute("SELECT a.*, c.platform FROM actions a JOIN connections c ON c.id = a.connection_id WHERE a.id = ?",
                   (action_id,)).fetchone()
    try:
        out = _carriers()[a["kind"]](db, a, client, now)
    except Exception as e:
        _finish(db, action_id, {"outcome": "unknown", "error": str(e)[:200]}, clock.now())
        raise
    _finish(db, action_id, out, clock.now())
    return out


def _finish(db, action_id: str, out: dict, now: datetime) -> None:
    outcome, error = out["outcome"], out.get("error")
    with db:
        if outcome in ("confirmed", "refused"):
            db.execute("UPDATE actions SET state = ?, error = ?, done_at = ? WHERE id = ?",
                       (outcome, error, clock.iso(now), action_id))
        elif outcome == "broken":
            db.execute("UPDATE actions SET state = 'scheduled', error = ? WHERE id = ?", (error, action_id))
        else:
            db.execute("UPDATE actions SET state = 'unknown', error = ? WHERE id = ?", (error, action_id))


def settle(db, action_id: str, outcome: str, error: str | None, now: datetime) -> None:
    with db:
        db.execute("UPDATE actions SET state = ?, error = ?, done_at = ? WHERE id = ?",
                   (outcome, error, clock.iso(now) if outcome in ("confirmed", "refused") else None, action_id))


def _carriers():
    from . import issues, meetings
    return {"issue_update": issues.carry_update, "issue_create": issues.carry_create, "send": inbox.carry_send,
            "respond": meetings.carry_respond}


def run(db, clients: dict, now: datetime, senders=None) -> int:
    """One pass: each due Action in order, per Connection; a broken or unanswered one holds that Connection's later
    Actions to keep their order. `clients` maps a Platform to its writer; `senders` (serve.Senders) gives a send its
    Connection's sender. Returns how many were carried out."""
    from datetime import timedelta
    with db:  # a pass that died mid-Action left it running: whether it happened is unknown
        db.execute("UPDATE actions SET state = 'unknown', error = 'interrupted' WHERE state = 'running'"
                   " AND (kind NOT IN (?, ?) OR due_at < ?)", (*PRESSED, clock.iso(now - timedelta(seconds=STUCK))))
    carriers, held, done = _carriers(), set(), 0
    for a in _due(db, now):
        if a["kind"] == "send":
            row = db.execute("SELECT * FROM connections WHERE id = ?", (a["connection_id"],)).fetchone()
            client = senders.get(row) if senders is not None else None
        else:
            client = clients.get(a["platform"])
        if a["connection_id"] in held or client is None:
            continue
        with db:
            db.execute("UPDATE actions SET state = 'running', attempts = attempts + 1 WHERE id = ?", (a["id"],))
        out = carriers[a["kind"]](db, a, client, now)
        outcome, error = out["outcome"], out.get("error")
        _finish(db, a["id"], out, now)
        done += outcome in ("confirmed", "refused")
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


# --- The Action Queue page (ADR 0022): one timeline of Actions that wait for a time ------------------------------

LABEL = {"issue_update": "Issue change", "issue_create": "New Issue", "send": "Send", "respond": "Respond"}
KINDS = ("email", "sms", "slack", "wake", "enrol", "step")  # what each day counts, in this order
SENT_DAYS = 7  # Sent reaches back this far; Upcoming has no end
SECTIONS = ("upcoming", "in_flight", "sent")
_MESSAGE_KIND = {"gmail": "email", "fastmail": "email", "quo": "sms", "slack": "slack"}
_WAKE = {"WAITING_FOR_DURATION": "Snooze ends", "WAITING_FOR_CUSTOMER": "Follow-up"}


def queue(db, now: datetime | None = None) -> list[dict]:
    """The Action Queue (ADR 0022): every Action that waits for a time and will happen with no act from Jeremy (a
    scheduled send, a Snooze wake, a follow-up return, an Enrollment start, an Action Step), and each that happened
    in the last SENT_DAYS, read from the records that already hold its time and outcome. Writes nothing.

    Each row: key, section (upcoming, in_flight, sent), kind (KINDS), at, day (local date), what, target, target_kind
    (draft, thread, person, task), target_id, outcome (confirmed, refused, unknown, or None), error. Sorted as one
    timeline: Upcoming furthest ahead first, then In flight, then Sent newest first."""
    from datetime import timedelta

    from .sequences import ACTION_LABEL, LOCAL
    now = now or clock.now()
    at_now, since = clock.iso(now), clock.iso(now - timedelta(days=SENT_DAYS))
    rows = []

    def add(key, section, kind, at, what, target, target_kind, target_id, outcome=None, error=None):
        rows.append({"key": key, "section": section, "kind": kind, "at": at,
                     "day": datetime.fromisoformat(at).astimezone(LOCAL).date().isoformat(), "what": what,
                     "target": target, "target_kind": target_kind, "target_id": target_id, "outcome": outcome,
                     "error": error})

    def ahead(at):
        return "upcoming" if at > at_now else "in_flight"

    for d in db.execute('SELECT d.id, d.kind, d.subject, d."to", d.send_at, d.thread_id, t.title FROM composition_drafts d'
                        " LEFT JOIN threads t ON t.id = d.thread_id WHERE d.send_at IS NOT NULL"):
        add(f"draft:{d['id']}", ahead(d["send_at"]), d["kind"], d["send_at"], "Send",  # a Reply Draft opens as its Thread
            " · ".join(x for x in (d["subject"] or d["title"], d["to"]) if x),
            *(("thread", d["thread_id"]) if d["thread_id"] else ("draft", d["id"])))
    for a in db.execute("SELECT a.*, c.platform FROM actions a JOIN connections c ON c.id = a.connection_id"
                        " WHERE a.kind = 'send' AND (a.state IN ('scheduled', 'running') OR a.created_at >= ?)",
                        (since,)):
        done = a["state"] in ("confirmed", "refused", "unknown")
        draft = db.execute("SELECT id, thread_id FROM composition_drafts WHERE id = ?", (a["target"],)).fetchone()
        opens = (("thread", draft["thread_id"]) if draft["thread_id"] else ("draft", draft["id"])) if draft else (None, None)
        add(f"action:{a['id']}", "sent" if done else "in_flight", _MESSAGE_KIND.get(a["platform"], "email"),
            a["done_at"] or a["due_at"], "Send", json.loads(a["payload"]).get("label", ""), *opens,
            a["state"] if done else None, a["error"])
    for t in db.execute("SELECT id, title, status_detail, snoozed_until, follow_up_at FROM threads"
                        " WHERE merged_into IS NULL AND ((status_detail = 'WAITING_FOR_DURATION' AND snoozed_until"
                        " IS NOT NULL) OR (status_detail = 'WAITING_FOR_CUSTOMER' AND follow_up_at IS NOT NULL))"):
        at = t["snoozed_until"] if t["status_detail"] == "WAITING_FOR_DURATION" else t["follow_up_at"]
        add(f"wake:{t['id']}", ahead(at), "wake", at, _WAKE[t["status_detail"]], t["title"], "thread", t["id"])
    for w in db.execute("SELECT a.id, a.occurred_at, a.old_detail, t.id AS thread_id, t.title FROM audit_log a"
                        " JOIN threads t ON t.id = a.thread_id WHERE a.cause = 'woke' AND a.occurred_at >= ?",
                        (since,)):
        add(f"woke:{w['id']}", "sent", "wake", w["occurred_at"], _WAKE.get(w["old_detail"], "Snooze ends"),
            w["title"], "thread", w["thread_id"], "confirmed")
    if "sequences" in inbox.modules_on(db):
        for e in db.execute("SELECT e.id, e.state, e.starts_at, e.person_id, p.display_name, q.name FROM enrollments e"
                            " JOIN persons p ON p.id = e.person_id JOIN sequences q ON q.id = e.sequence_id"
                            " WHERE e.starts_at IS NOT NULL AND e.state != 'PAUSED' AND e.starts_at >= ?", (since,)):
            started = e["starts_at"] <= at_now  # a start just happens: its first Task counts from it
            if not started and e["state"] != "ACTIVE":
                continue
            add(f"enrol:{e['id']}", "sent" if started else "upcoming", "enrol", e["starts_at"],
                "Enrollment starts", f"{e['display_name']} · {e['name']}", "person", e["person_id"],
                "confirmed" if started else None)
        for k in db.execute("SELECT k.id, k.state, k.due_at, k.done_at, s.action_kind, p.display_name, q.name"
                            " FROM tasks k JOIN sequence_steps s ON s.id = k.step_id"
                            " JOIN enrollments e ON e.id = k.enrollment_id JOIN persons p ON p.id = e.person_id"
                            " JOIN sequences q ON q.id = e.sequence_id WHERE s.kind = 'action'"
                            " AND ((k.state = 'DUE' AND k.flag IS NULL) OR (k.state = 'DONE' AND k.done_at >= ?))",
                            (since,)):
            done = k["state"] == "DONE"
            add(f"step:{k['id']}", "sent" if done else ahead(k["due_at"]), "step",
                k["done_at"] if done else k["due_at"], ACTION_LABEL.get(k["action_kind"], "Action Step"),
                f"{k['display_name']} · {k['name']}", "task", k["id"], "confirmed" if done else None)
    rank = {s: i for i, s in enumerate(SECTIONS)}
    rows.sort(key=lambda r: r["at"], reverse=True)
    return sorted(rows, key=lambda r: rank[r["section"]])


def counts(rows: list[dict]) -> dict[str, dict[str, int]]:
    """Each day's Actions by kind, in KINDS order: {day: {kind: n}}."""
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        day = out.setdefault(r["day"], {})
        day[r["kind"]] = day.get(r["kind"], 0) + 1
    return {d: {k: c[k] for k in KINDS if k in c} for d, c in out.items()}


def badge(rows: list[dict]) -> tuple[int, int]:
    """The rail's two numbers: Upcoming, then In flight."""
    return (sum(r["section"] == "upcoming" for r in rows), sum(r["section"] == "in_flight" for r in rows))
