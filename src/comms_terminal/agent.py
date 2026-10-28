"""Commands for a terminal agent: Threads, Drafts, and Due Tasks as Markdown, changes to a Draft, enrolling a
Person in a Sequence, writing Sequences (whole, as Markdown, or Step by Step; FR-SEQ-44), Jeremy's calendar Events,
Enrollments (list, resume, start on an agreed date, finish; Jeremy, 2026-10-26, superseding FR-SEQ-22), and Pipelines (show,
Target, Stage and Value). Nothing here sends or completes a Task; Jeremy does that from the console."""

import json
import sys
from datetime import date, timedelta

from . import calendar, clock, files, inbox, pipeline, sequence_md, sequences
from .platforms import PLATFORMS
from .tui import (CONTEXT_SOURCES, DRAFT_FIELDS, ReplyError, connection_label, draft_history, draft_text,
                  socket_context, staged_files, task_markdown, thread_addresses, thread_text)


def add_parsers(sub):
    t = sub.add_parser("threads", help="list Threads in a Status view: id, status detail, who and where, title")
    t.add_argument("view", nargs="?", default="Todo", choices=list(inbox.VIEWS))
    t = sub.add_parser("thread", help="one Thread as Markdown, with its Attio and Tana context")
    t.add_argument("id", type=int)
    sub.add_parser("drafts", help="list Drafts, then the Connections a new Draft can use")
    d = sub.add_parser("draft", help="one Draft as Markdown; with --to/--cc/--subject/--body, change it first (never sends)")
    d.add_argument("id", help="a Draft id, or `new` (needs --connection for a new Thread, or --thread for a reply)")
    d.add_argument("--revision", type=int, help="the revision you read; required to change an existing Draft")
    d.add_argument("--connection", type=int, help="new Draft: the Connection id it sends from")
    d.add_argument("--thread", type=int, help="new Draft: the Thread it replies to; its reply box sends it")
    for field in ("to", "cc", "subject"):
        d.add_argument(f"--{field}")
    d.add_argument("--body", help="the body, or - to read it from stdin")
    d.add_argument("--needs-edit", action="store_true",
                   help="mark it Needs edit, after any changes: it cannot send until its text is changed")
    sub.add_parser("tasks", help="Due Tasks as Markdown, each with its Person and Draft (Sequences)")
    e = sub.add_parser("enroll", help="enroll one Person in a Sequence; its first Task is due today, or from --start")
    e.add_argument("person", help="a Person id, email address, or exact name")
    e.add_argument("sequence", help="a Sequence id or exact name")
    e.add_argument("--start", help="a later Start date: YYYY-MM-DD, or a number of days from today")
    e.add_argument("--org", help="the Organization it is for: a domain or exact name, or `sole` for a new Sole"
                                 " proprietorship (default: their only Organization)")
    e.add_argument("--to", help="the email address its emails go to; a new one is added to the Person (default: the"
                                " one at its Organization, else the one added last)")
    q = sub.add_parser("sequence", help="Sequences: list, show or apply one as Markdown, new, rename, archive")
    qs = q.add_subparsers(dest="sequence_cmd", required=True)
    qs.add_parser("list", help="every Sequence: id, name, Steps, Enrollments, use when")
    x = qs.add_parser("show", help="one Sequence as Markdown (the shape `apply` reads)")
    x.add_argument("sequence", help="a Sequence id or exact name")
    x = qs.add_parser("apply", help="create or update a Sequence from Markdown; prints each change")
    x.add_argument("file", help="a Markdown file, or - for stdin (through the Workbox wrapper, always -)")
    x.add_argument("--dry-run", action="store_true", help="print the changes and write nothing")
    x = qs.add_parser("new", help="an empty Sequence")
    x.add_argument("name")
    x = qs.add_parser("rename", help="rename a Sequence")
    x.add_argument("sequence")
    x.add_argument("name")
    x = qs.add_parser("sends-from", help="the email Connection a Sequence's email Tasks send from (ids: `drafts`)")
    x.add_argument("sequence")
    x.add_argument("connection", type=int)
    x = qs.add_parser("archive", help="archive a Sequence; its live Enrollments carry on")
    x.add_argument("sequence")
    en = sub.add_parser("enrollment", help="Enrollments: list, resume a Paused one, finish one with a reason")
    es = en.add_subparsers(dest="enrollment_cmd", required=True)
    x = es.add_parser("list", help="live Enrollments: id, Person, Sequence, state, how it is finishing")
    x.add_argument("person", nargs="?", help="only this Person (id, email address, or exact name)")
    x = es.add_parser("resume", help="resume a Paused Enrollment; its next Task is due now")
    x.add_argument("id", type=int)
    x = es.add_parser("start", help="put a running Enrollment on the agreed date: its Due Task comes due that day")
    x.add_argument("id", type=int)
    x.add_argument("date", help="YYYY-MM-DD, or a number of days from today")
    x = es.add_parser("finish", help="finish an Enrollment; Goal met, Not Right Now, and Lost run its On completion")
    x.add_argument("id", type=int)
    x.add_argument("reason", type=str.upper, choices=sequences.END_REASONS)
    st = sub.add_parser("step", help="one Step of a Sequence, by its number: add, set, move, delete")
    ss = st.add_subparsers(dest="step_cmd", required=True)
    x = ss.add_parser("add", help="a new Step: after Step --after, else last on the path you name")
    x.add_argument("sequence")
    x.add_argument("kind", choices=sequences.KINDS)
    x.add_argument("--after", type=int, help="the Step number it follows (on that Step's path)")
    x.add_argument("--section", choices=sequences.SECTIONS, default="body")
    x.add_argument("--under", type=int, help="the If (by Step number) whose path it goes on")
    x.add_argument("--branch", choices=("yes", "no"), help="that If's yes (true) or no (false) path")
    x.add_argument("--action", choices=sequences.ACTION_KINDS, default="enroll", help="what an Action does")
    x.add_argument("--checks", choices=sequences.CONDITIONS, default="jeremy_picks", help="what an If checks")
    x = ss.add_parser("set", help="change a Step's fields: field=value ... (enroll=<Sequence name> for an Action)")
    x.add_argument("sequence")
    x.add_argument("step", type=int)
    x.add_argument("fields", nargs="+", help=f"one of {', '.join(sequences.STEP_FIELDS)}, or enroll")
    x = ss.add_parser("move", help="move a Step up or down its path")
    x.add_argument("sequence")
    x.add_argument("step", type=int)
    x.add_argument("direction", choices=("up", "down"))
    x = ss.add_parser("delete", help="delete a Step (an If with its paths)")
    x.add_argument("sequence")
    x.add_argument("step", type=int)
    v = sub.add_parser("events", help="Jeremy's calendar Events, repeats expanded, local time (ADR 0005)")
    v.add_argument("--from", dest="start", type=date.fromisoformat, help="first day, YYYY-MM-DD (default today)")
    v.add_argument("--to", dest="end", type=date.fromisoformat, help="last day, YYYY-MM-DD (default 7 days on)")
    v.add_argument("--attendee", help="only Events with this address, or anyone at this domain")
    v.add_argument("--query", help="only Events whose title contains this text")
    v.add_argument("--last", type=int, metavar="N", help="the last N Events that already started, newest first")
    v.add_argument("--deleted", action="store_true", help="include Deleted Events (gone from Google, kept here)")
    v.add_argument("--json", action="store_true", help="one JSON object per line")
    p = sub.add_parser("pipeline", help="a Pipeline's math as Markdown, or set a Target, Stage, or Value")
    ps = p.add_subparsers(dest="pipeline_cmd", required=True)
    for name in ("revenue", "fundraising"):
        ps.add_parser(name, help=f"the {name.title()} Pipeline as Markdown")
    ps.add_parser("talent", help="the Talent Pipeline's Organizations by Stage as Markdown (no math)")
    t = ps.add_parser("target", help="set a Pipeline's Target: TTM revenue (Revenue) or round size (Fundraising)")
    t.add_argument("pipeline")
    t.add_argument("amount", help="CAD, e.g. 1000000 or 1m")
    t.add_argument("--by", required=True, type=date.fromisoformat, help="due date, YYYY-MM-DD")
    for kind, what in (("stage", "move an Organization to a Stage"), ("value", "set an Organization's Value")):
        t = ps.add_parser(kind, help=what)
        t.add_argument("org", help="a domain or exact Organization name")
        t.add_argument("pipeline")
        t.add_argument(kind, help="a Stage name" if kind == "stage" else "CAD, e.g. 90000 or 90k")
        t.add_argument("--at", help="backdate it: YYYY-MM-DD (seeding past Stages)")


def run(a, db):
    try:
        if a.cmd == "threads":
            for t in inbox.queue(db, a.view):
                print(f"{t['id']}\t{t['detail']}\t{t['who']} · {t['where']}\t{t['title']}")
        elif a.cmd == "thread":
            print(thread(db, a.id, a.socket), end="")
        elif a.cmd == "drafts":
            for d in inbox.list_drafts(db):
                kind = f"Reply Draft · Thread {d['thread_id']}" if d["thread_id"] else "New Thread Draft"
                print(f"{d['id']}\t{d['kind']} {kind}\t{d['to'] or '-'}\t{d['subject'] or ''}")
            print("\nConnections (draft new --connection ID):")
            for c in inbox.connections(db):
                print(f"{c['id']}\t{connection_label(c)}")
        elif a.cmd == "tasks":
            due = sequences.tasks_due(db)
            print("".join(task_markdown(db, t) + "\n" for t in due) or "No Tasks due.\n", end="")
        elif a.cmd == "enroll":
            print(enroll(db, a.person, a.sequence, getattr(a, "start", None), getattr(a, "org", None),
                         getattr(a, "to", None)))
        elif a.cmd == "sequence":
            print(sequence_cmd(db, a), end="")
        elif a.cmd == "step":
            print(step_cmd(db, a), end="")
        elif a.cmd == "enrollment":
            print(enrollment_cmd(db, a), end="")
        elif a.cmd == "events":
            print(events(db, a), end="")
        elif a.cmd == "pipeline":
            print(pipeline_cmd(db, a), end="")
        else:
            changes = {k: getattr(a, k) for k in ("to", "cc", "subject", "body") if getattr(a, k) is not None}
            if changes.get("body") == "-":
                changes["body"] = sys.stdin.read()
            print(draft(db, a.id, changes, a.revision, a.connection, getattr(a, "thread", None),
                        getattr(a, "needs_edit", False)), end="")
    except (KeyError, ValueError, inbox.DraftConflict) as e:  # Refused and sequence_md.Invalid are ValueErrors
        raise SystemExit(f"comms-terminal {a.cmd}: {e.args[0] if e.args else e}") from e


def pipeline_cmd(db, a) -> str:
    c = a.pipeline_cmd
    if c == "talent":
        return pipeline.board_markdown(db, pipeline.TALENT)
    if c in ("revenue", "fundraising"):
        return pipeline.markdown(pipeline.math(db, pipeline.parse_pipeline(c)))
    p = pipeline.parse_pipeline(a.pipeline)
    if c == "target":
        pipeline.set_target(db, p, a.amount, a.by)
        t = pipeline.target(db, p)
        return f"{pipeline.NAMES[p]} Target: CAD {t['amount']:,} by {t['due']}\n"
    org = pipeline.find_org(db, a.org)
    if c == "stage":
        done = pipeline.set_stage(db, org["id"], p, a.stage, a.at)
    else:
        done = pipeline.set_value(db, org["id"], p, a.value, a.at)
    now = pipeline.current(db, org["id"])[p]
    value = "no Value" if now["value"] is None else f"CAD {now['value']:,}"
    return (f"{org['name']} ({org['domain']}) {pipeline.NAMES[p]}: {now['stage'] or 'no Stage'}, {value}"
            + ("" if done else " (unchanged)") + "\n")


def thread(db, thread_id, socket) -> str:
    v = inbox.thread_view(db, thread_id)
    emails, domains = thread_addresses([v])
    context = {}
    for source in CONTEXT_SOURCES if emails or domains else ():
        try:
            context[source] = (socket_context(socket, source)(emails, domains), None)
        except ReplyError as e:
            context[source] = (None, str(e))
    return thread_text(v, files.raw_dir(db), context)


def draft(db, ref, changes, revision, connection_id, thread_id=None, needs_edit=False) -> str:
    if ref == "new" and thread_id is not None:  # a reply Draft: the Thread gives its envelope
        if old := inbox.reply_draft(db, thread_id):
            raise ValueError(f"Thread {thread_id} already has reply Draft {old['id']}: change it with "
                             f"`comms-terminal draft {old['id']} --revision {old['revision']}`")
        d = inbox.save_reply_draft(db, thread_id, changes.pop("body", ""))
        if d is None:
            raise ValueError("a reply Draft needs --body")
        if changes:
            d = inbox.save_draft(db, {k: d[k] for k in DRAFT_FIELDS} | changes, draft_id=d["id"],
                                 expected_revision=d["revision"])
    elif ref == "new":
        c = next((c for c in inbox.connections(db) if c["id"] == connection_id), None)
        if c is None:
            raise ValueError("a new Draft needs --connection: an id from `comms-terminal drafts`")
        kind = PLATFORMS[c["platform"]].format
        if kind is None:
            raise ValueError(f"{PLATFORMS[c['platform']].title} sends nothing: pick an email, SMS, or Slack Connection")
        values = dict(connection_id=c["id"], kind=kind, sender=c["self_id"] or "", to="", cc="",
                      subject="" if kind == "email" else None, body="")
        d = inbox.save_draft(db, values | changes)
    else:
        d = inbox.get_draft(db, int(ref))
        if changes:
            if revision is None:
                raise ValueError(f"pass --revision {d['revision']} (the revision you read) to change Draft {d['id']}")
            d = inbox.save_draft(db, {k: d[k] for k in DRAFT_FIELDS} | changes, draft_id=d["id"],
                                 expected_revision=revision)
    if needs_edit:
        d = inbox.mark_needs_edit(db, d["id"])
    c = next((c for c in inbox.connections(db) if c["id"] == d["connection_id"]), None)
    history = draft_history(db, d["kind"], d["connection_id"], d["to"]) or []
    return draft_text(d, connection_label(c), history, files.raw_dir(db), (d["id"], d["revision"]),
                      staged_files(db, ("draft", d["id"])))


def enroll(db, person: str, sequence: str, start: str | None = None, org: str | None = None,
           to: str | None = None) -> str:
    rows = db.execute("SELECT p.id FROM persons p WHERE CAST(p.id AS TEXT) = ? OR lower(p.display_name) = lower(?)"
                      " UNION SELECT person_id FROM identities WHERE lower(value) = lower(?) AND person_id IS NOT NULL",
                      (person, person, person)).fetchall()
    if len(rows) != 1:
        raise ValueError(f"{'no' if not rows else 'more than one'} Person matches {person!r}")
    match = [q for q in sequences.sequences(db) if str(q["id"]) == sequence or q["name"].lower() == sequence.lower()]
    if len(match) != 1:
        raise ValueError(f"no Sequence matches {sequence!r}")
    now = clock.now()
    day = sequences.parse_start(start, now) if start else None
    if start and day is None:
        raise ValueError(f"--start {start!r} is neither YYYY-MM-DD nor a number of days")
    org_id = None
    if org == "sole":
        org_id = inbox.create_sole_proprietorship(db, rows[0][0])
    elif org:
        org_id = pipeline.find_org(db, org)["id"]
    identity_id = None
    if to:
        if "@" not in to:
            raise ValueError(f"--to {to!r} is not an email address")
        try:
            inbox.add_identity(db, rows[0][0], "email", to)
        except inbox.IdentityTaken:
            raise ValueError(f"{to} belongs to another Person") from None
        identity_id = db.execute("SELECT id FROM identities WHERE kind = 'email' AND value = ?", (to.lower(),)).fetchone()[0]
    eid = sequences.enroll(db, rows[0][0], match[0]["id"], now, start=day, organization_id=org_id, identity_id=identity_id)
    sent = f", sending to {to.lower()}" if to else ""
    return f"Enrolled in {match[0]['name']}{sent}: the first Task is due {sequences.first_due(db, eid, now)}."


def find_person(db, person: str) -> int:
    rows = db.execute("SELECT p.id FROM persons p WHERE CAST(p.id AS TEXT) = ? OR lower(p.display_name) = lower(?)"
                      " UNION SELECT person_id FROM identities WHERE lower(value) = lower(?) AND person_id IS NOT NULL",
                      (person, person, person)).fetchall()
    if len(rows) != 1:
        raise ValueError(f"{'no' if not rows else 'more than one'} Person matches {person!r}")
    return rows[0][0]


def enrollment_cmd(db, a) -> str:
    c = a.enrollment_cmd
    if c == "list":
        who = find_person(db, a.person) if a.person else None
        rows = db.execute(
            "SELECT e.id, e.state, e.reason, p.display_name, q.name, o.name AS org FROM enrollments e"
            " JOIN persons p ON p.id = e.person_id JOIN sequences q ON q.id = e.sequence_id"
            " LEFT JOIN organizations o ON o.id = e.organization_id"
            " WHERE e.state != 'FINISHED' AND (? IS NULL OR e.person_id = ?) ORDER BY e.id", (who, who)).fetchall()
        return "".join(f"{r['id']}\t{r['display_name']}\t{r['name']}\t{r['state'].title()}"
                       + (f" as {sequences.REASON_LABEL[r['reason']]}" if r["reason"] else "")
                       + f"\t{r['org'] or 'no Organization'}\n" for r in rows) or "No live Enrollments.\n"
    e = db.execute("SELECT e.*, q.name FROM enrollments e JOIN sequences q ON q.id = e.sequence_id WHERE e.id = ?",
                   (a.id,)).fetchone()
    if e is None:
        raise ValueError(f"no Enrollment {a.id}")
    if c == "resume":
        sequences.resume(db, a.id)
        return f"Resumed {e['name']}: the next Task is due now.\n"
    if c == "start":
        day = sequences.parse_start(a.date, clock.now())
        if day is None:
            raise ValueError(f"{a.date!r} is neither YYYY-MM-DD nor a number of days")
        sequences.reschedule(db, a.id, day)
        return f"{e['name']} now starts {day:%a %b} {day.day}: its Due Task comes due that day.\n"
    sequences.finish(db, a.id, a.reason)
    runs = " Its On completion ran." if a.reason in sequences.FINISHING_REASONS else ""
    return f"Finished {e['name']}: {sequences.REASON_LABEL[a.reason]}.{runs}\n"


def find_sequence(db, text: str) -> dict:
    match = [q for q in sequences.sequences(db) if str(q["id"]) == text or q["name"].lower() == text.lower()]
    if len(match) != 1:
        raise ValueError(f"no Sequence matches {text!r}")
    return match[0]


def sequence_cmd(db, a) -> str:
    c = a.sequence_cmd
    if c == "list":
        return "".join(f"{q['id']}\t{q['name']}\t{q['steps']} Steps\t{q['active']} active · {q['paused']} paused"
                       + (" · archived" if q["archived_at"] else "") + (f"\t{q['description']}" if q["description"] else "")
                       + "\n" for q in sequences.sequences(db))
    if c == "apply":
        text = sys.stdin.read() if a.file == "-" else open(a.file, encoding="utf-8").read()
        changes = sequence_md.apply(db, text, dry_run=a.dry_run)
        head = "Would change" if a.dry_run else "Changed"
        return (f"{head}:\n" + "".join(f"{x}\n" for x in changes)) if changes else "No changes.\n"
    if c == "new":
        return f"Sequence {sequences.create_sequence(db, a.name)}: {a.name}\n"
    q = find_sequence(db, a.sequence)
    if c == "show":
        return sequence_md.render(db, q["id"])
    if c == "sends-from":
        conn = next((m for m in sequences.email_connections(db) if m["id"] == a.connection), None)
        if conn is None:
            raise ValueError(f"Connection {a.connection} is not an email Connection")
        sequences.set_connection(db, q["id"], a.connection)
        return f"{q['name']} sends from {connection_label(conn)}.\n"
    if c == "rename":
        sequences.rename_sequence(db, q["id"], a.name)
        return f"Renamed {q['name']} to {a.name}.\n"
    sequences.archive_sequence(db, q["id"], cancel_live=False)
    return f"Archived {q['name']}; its live Enrollments carry on.\n"


def step_cmd(db, a) -> str:
    q = find_sequence(db, a.sequence)
    steps = {st["position"]: st for st in sequences.sequence_steps(db, q["id"])}

    def at(n):
        if n not in steps:
            raise ValueError(f"{q['name']} has no Step {n}")
        return steps[n]
    c = a.step_cmd
    if c == "add":
        under = at(a.under)["id"] if a.under else None
        if (under is None) != (a.branch is None):
            raise ValueError("--under and --branch go together")
        section = at(a.under)["section"] if a.under else a.section
        sid = sequences.add_step(db, q["id"], a.kind, a.after, section=section, parent_step_id=under,
                                 branch=None if a.branch is None else int(a.branch == "yes"),
                                 action_kind=a.action, condition=a.checks)
        pos = db.execute("SELECT position FROM sequence_steps WHERE id = ?", (sid,)).fetchone()[0]
        return f"Added Step {pos} ({a.kind}).\n" + _problems(db, q["id"])
    st = at(a.step)
    if c == "move":
        sequences.move_step(db, st["id"], -1 if a.direction == "up" else 1)
        return f"Moved Step {a.step} {a.direction}.\n"
    if c == "delete":
        moved, _ = sequences.delete_step(db, st["id"])
        return f"Deleted Step {a.step}; {moved} Enrollment(s) on it moved on.\n"
    fields = {}
    for pair in a.fields:
        key, sep, value = pair.partition("=")
        if not sep:
            raise ValueError(f"{pair!r} is not field=value")
        if key == "enroll":
            key, value = "enroll_sequence_id", find_sequence(db, value)["id"]
        elif key in ("gap_days", "needs_edit"):
            value = int(value)
        elif key == "value":
            value = pipeline.parse_amount(value) if value else None
        elif key == "pipeline":
            value = value.upper() or None
        fields[key] = value
    sequences.save_step(db, st["id"], **fields)
    return f"Saved Step {a.step}.\n" + _problems(db, q["id"])


def _problems(db, sequence_id: int) -> str:
    steps = {st["id"]: st["position"] for st in sequences.sequence_steps(db, sequence_id)}
    return "".join(f"Problem · Step {steps[i]}: {m}\n" for i, m in sequences.validate(db, sequence_id))


def events(db, a) -> str:
    today = date.today()
    if a.last:
        start, end = date(1970, 1, 1), today + timedelta(days=1)
    else:
        start, end = a.start or today, (a.end or (a.start or today) + timedelta(days=7)) + timedelta(days=1)
    found = calendar.expand(db, start, end, deleted=a.deleted)
    if a.attendee:
        key = a.attendee.lower().lstrip("@")
        hit = lambda m: m == key or m.endswith("@" + key)  # noqa: E731
        found = [e for e in found if any(hit(p["email"]) for p in e["attendees"] + [e["organizer"] or {"email": ""}])]
    if a.query:
        found = [e for e in found if a.query.lower() in e["title"].lower()]
    if a.last:
        now = clock.local()
        found = [e for e in found if clock.local(e["start"]) <= now][::-1][:a.last]
    if a.json:
        return "".join(json.dumps({**{k: e[k] for k in ("id", "kind", "all_day", "title", "location", "join_url",
                                                          "organizer", "attendees", "state", "repeats", "deleted_at")},
                                   "start": e["start"].isoformat(), "end": e["end"].isoformat()}) + "\n" for e in found)
    return "".join(event_line(e) + "\n" for e in found) or "No Events.\n"


def event_line(e) -> str:
    start, end = clock.local(e["start"]), clock.local(e["end"])
    when = f"{start:%Y-%m-%d %a} all day" if e["all_day"] else f"{start:%Y-%m-%d %a %H:%M}-{end:%H:%M}"
    who = e["organizer"]["email"] + " (organizer)" if e["organizer"] else ""
    more = f" +{len(e['attendees']) - 1}" if len(e["attendees"]) > 1 else ""
    gone = f"\tdeleted {e['deleted_at'][:10]}" if e["deleted_at"] else ""
    return f"{when}\t{e['title']}\t[{e['state'].lower()}]\t{who}{more}{gone}"
