"""Comms Terminal's Local core (ADR 0013): the laptop copy, the write queue and the sync with the Service, served to
the App as the Local API.

The core reuses the Console's machinery unchanged: `remote.Link`, `remote.Remote` and `remote.Syncer` keep the copy
current (ADR 0012), and `remote.proxy` applies each write to the copy at once and queues it for the Service. The core
plays the part the Console plays for them (`call_from_thread`, `notify`, `apply_snapshot`, `check_copy`,
`set_online`), running all database work on one worker thread, as the Console runs it on its app thread.

The Local API is JSON-RPC 2.0, one object per line, on a user-only Unix socket. Values use `remote.encode`'s tags.
Every call names its Origin: "view" (the App's own screens, on Jeremy's input) or "panel" (Interface-file scripts,
which agents may write). Panels may run queries and the commands in `agent.COMMANDS`; sending, scheduling, Task
completion, undo, Respond, reactions and upload links stay with views. `rpc.discover` returns the OpenRPC document;
`schema/local-api.openrpc.json` is a copy that tests keep current and grow-only.
"""

import base64
import inspect
import json
import logging
import os
import queue
import shlex
import socketserver
import threading
import time
import urllib.request
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from importlib import resources
from pathlib import Path

from . import agent, calendar, commands, files, inbox, pipeline, remote, render, sequences, writers

LOG = logging.getLogger("comms_terminal.core")
GENERATION = 1  # the Local API's protocol generation: a breaking change starts the next one (ADR 0013)
FILES = ("127.0.0.1", 8789)  # the Service's file server, through the link's forward (remote.FILES_FORWARD)
HEARTBEAT = 15  # seconds between heartbeat events
BACKLOG = 1000  # events a slow subscriber may fall behind before it gets "missed"
MAX_IMAGE = 5 * 1024 * 1024  # a remote image larger than this stays as its alt text
ORIGINS = ("view", "panel")
VIEW_ONLY = set(agent.CONSOLE_ONLY) | {"commands.undo"}  # a Panel may not send, do Tasks, undo, or make upload links
DOMAIN_ERRORS = tuple(remote.ERRORS.values())


def default_listen() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/comms-terminal-{os.getuid()}"
    return Path(base) / "comms-terminal" / "core.sock"


class Refused(Exception):
    """A call the Origin may not make."""


class ApiError(Exception):
    def __init__(self, code: int, message: str, data=None):
        super().__init__(message)
        self.code, self.data = code, data


# --- queries: one per view (PRD Phase 0.5) ---------------------------------------------------------------------------

QUERIES: dict = {}  # name -> (fn(core, db, **params), {param: json type}, summary)


def query(method, **params):
    def mark(fn):
        QUERIES[method] = (fn, params, " ".join((fn.__doc__ or "").split()))
        return fn
    return mark


@query("rail.summary")
def _rail(core, db):
    """Counts and lists the rail shows: Status views, Cases, People, Today, Connections, Sequences, due Tasks."""
    return {"counts": inbox.view_counts(db), "cases": inbox.case_queue(db), "people": inbox.people_count(db),
            "connections": inbox.connections(db), "sequences": sequences.sequences(db),
            "tasks": sequences.tasks_due(db)}


@query("shell.summary")
def _shell(core, db):
    """What the Console's rail and bottom bar show beyond rail.summary: Today, Drafts, Organizations and Channels
    counts, Case Status counts, Pipeline verdicts, Connection health, the Event now and next, and the build."""
    from collections import Counter
    from . import calendar as cal, clock, gcal
    from .tui import PLATFORMS, build_version, conn_name, health_ok, kind_of, rail_share
    t = _shared(core, "today", inbox.today, db)
    now = clock.local()
    current, nxt = cal.now_and_next(db, now)
    short = lambda e: e["title"] if len(e["title"]) <= 24 else e["title"][:23] + "…"  # noqa: E731
    events = {}
    if current:
        events["now"] = {"title": short(current), "left": int((clock.local(current["end"]) - now).total_seconds() // 60)}
    if nxt:
        start = clock.local(nxt["start"])
        days = (start.date() - now.date()).days
        events["next"] = {"title": short(nxt), "when": f"{start:%H:%M}" if days == 0 else f"tomorrow {start:%H:%M}"
                          if days == 1 else f"{start:%a %H:%M}" if days < 7 else f"{start:%b %-d %H:%M}"}
    return {"today": sum(len(t[k]) for k in ("threads", "tasks", "drafts", "paused", "unattended")),
            "drafts": db.execute("SELECT count(*) FROM composition_drafts").fetchone()[0],
            "organizations": db.execute("SELECT count(*) FROM organizations").fetchone()[0],
            "issues": db.execute("SELECT count(*) FROM issues WHERE completed_at IS NULL AND canceled_at IS NULL"
                                 ).fetchone()[0],
            "actions": db.execute("SELECT count(*) FROM actions WHERE state IN ('scheduled', 'running', 'unknown')"
                                  ).fetchone()[0],
            "channels": db.execute("SELECT count(*) FROM channels WHERE kind != 'mailbox'").fetchone()[0],
            "cases": dict(Counter(c["status"] for c in inbox.case_queue(db))),
            "pipelines": {p.lower(): rail_share(db, p) for p in pipeline.PIPELINES},
            "connections": [{"id": c["id"], "platform": c["platform"], "kind": kind_of(c), "name": conn_name(c), "ok": health_ok(c),
                             "writes": (c["last_error"] or "").startswith(gcal.WRITES_BROKEN),
                             "supported": c["platform"] in PLATFORMS} for c in inbox.connections(db)],
            "calendar_broken": bool(cal.broken(db)), **events, "build": build_version(),
            "modules": sorted(inbox.modules_on(db))}


@query("search", text="string", commands="array", recent="array")
def _search(core, db, text, commands, recent):
    """The Palette's rows (FR-BAR-5): the App's `commands` ([{id, label, key, group}]) and the copy's People,
    Organizations, Cases, Sequences, Connections, Threads, Drafts, Messages and Events, ranked in one list with
    typos forgiven; `recent` holds the ids of recent picks, newest first. The index is rebuilt after the copy
    changes."""
    from . import search
    seq = getattr(core, "seq", None)
    kept = getattr(core, "search_index", None)
    # ponytail: a full rebuild (~0.8 s on 5,000 records) on the first search after any event; patch the index per
    # change if that lag shows while typing.
    if kept is None or kept[0] != seq or seq is None:
        kept = (seq, search.build(db))
        if core is not None:
            core.search_index = kept
    return search.search(kept[1], text, commands, recent)


@query("sequences.pauses_since", since="string")
def _pauses(core, db, since):
    """Enrollments paused after `since` (ISO time), for the pause toast (FR-SEQ-20)."""
    return sequences.pauses_since(db, since)


@query("today.page")
def _today(core, db):
    """Today: Todo Threads, due Tasks (an email Task with its Draft until sent) and the Drafts list, each item once
    (each Draft with `recipient`, as drafts.list),
    Paused Enrollments (each with `finishing`, the label of the reason it finishes as, or null) and Unattended
    Organizations; and today's Planned Issues (FR-UI-2) with the day's `issues_capacity`."""
    from . import issues
    t = dict(_shared(core, "today", inbox.today, db))
    day = issues.view(db, {"grouping": "none", "completed": "none"}, day_only=issues.today().isoformat())
    t["issues"] = [i for _, i in issues.flat(day)]
    t["issues_capacity"] = issues.capacity(db, issues.today())
    t["drafts"] = [d | {"recipient": inbox.recipient_names(db, d["to"] or "", d["kind"])} for d in t["drafts"]]
    t["paused"] = [e | {"finishing": sequences.REASON_LABEL[e["reason"]] if e["reason"] else None} for e in t["paused"]]
    return t


@query("threads.queue", view="string")
def _queue(core, db, view):
    """The rows of one Status view: Todo, Snoozed, Done, Ignored or All."""
    if view not in inbox.VIEWS:
        raise ApiError(-32602, f"view must be one of {', '.join(inbox.VIEWS)}")
    return inbox.queue(db, view)


@query("thread.view", thread_id="string")
def _thread(core, db, thread_id):
    """One Thread: timeline, senders, its Reply Draft, files staged on it, the Cases it may join, and every saved
    reply Draft of the Thread and its merged aliases."""
    view = inbox.thread_view(db, thread_id, files=FILES)
    slack = view["channel"]["platform"] == "slack"
    for m in view["messages"]:  # the Console's text body: what shows, and the Trimmed content folded behind •••
        if m["content"] is not None:
            body = render.from_slack(m["content"]) if slack else render.from_text(m["content"])
            m["shown"], m["trimmed"] = render.plain(body.text), render.plain(body.trimmed)
    return {"view": view, "senders": inbox.thread_senders(db, thread_id),
            "reply_draft": inbox.reply_draft(db, thread_id), "staged": inbox.staged(db, thread_id=thread_id),
            "cases": inbox.cases(db),
            "drafts": [d for m in inbox.snapshot(db, thread_id)["_members"]
                       if (d := inbox.reply_draft(db, m)) and (d.get("body") or "").strip()]}


@query("thread.snapshot", thread_id="string")
def _snapshot(core, db, thread_id):
    """A Thread's triage state and Case, for undoing an action with `command.inbox.restore`."""
    return inbox.snapshot(db, thread_id)


@query("thread.markdown", thread_id="string")
def _thread_markdown(core, db, thread_id):
    """One Thread as the Markdown `comms-terminal thread` prints (without Context sources), for an agent."""
    v = inbox.thread_view(db, thread_id)
    return agent.thread_text(v, files.raw_dir(db), {}) + f"\nFresh copy: `comms-terminal thread {v['thread'].id}`\n"


@query("draft.markdown", draft_id="string")
def _draft_markdown(core, db, draft_id):
    """One Draft as the Markdown `comms-terminal draft` prints, for an agent."""
    return agent.draft(db, draft_id, {}, None, None)


@query("reply.envelope", thread_id="string", mode="string")
def _envelope(core, db, thread_id, mode):
    """Where a reply goes: `mode` "all" or "sender"; the From, To, Cc and Subject an email reply starts with (empty
    lists elsewhere), whether the Thread takes replies at all, its native link, and the send-later times."""
    from . import clock, email, platforms
    from .tui import native_link
    v = inbox.thread_view(db, thread_id)
    p = platforms.PLATFORMS[v["channel"]["platform"]]
    to = cc = []
    if p.format == "email":
        history = [{"native_id": m["native_id"], "data": m["data"]} for m in v["messages"]]
        to, cc = (email.reply_all if mode == "all" else email.reply_sender)(history, v["channel"]["external_id"])
    title = v["thread"].title or ""
    now = clock.now()
    return {"email": p.format == "email", "takes_replies": p.takes_replies, "sends_files": p.sends_files,
            "from": v["channel"]["external_id"], "where": v["where"], "to": list(to), "cc": list(cc),
            "subject": title if title.lower().startswith("re:") else f"Re: {title}", "link": native_link(v),
            "later": {k: inbox.send_time(k, now).isoformat() for k in ("tomorrow", "monday")}}


@query("thread.forward", thread_id="string")
def _forward(core, db, thread_id):
    """The newest message as a new email Draft's values, and its stored files to stage on it (FR-A11)."""
    from .tui import forward_values
    v = inbox.thread_view(db, thread_id)
    m = next((m for m in reversed(v["messages"]) if not m["deleted"]), None)
    if m is None:
        return None
    files_ = [{k: a[k] for k in ("sha", "name", "type", "size")} for a in m["attachments"] if not a["inline"]]
    return {"values": forward_values(v, m, inbox.connections(db)), "files": files_}


@query("send.later")
def _later(core, db):
    """The send-later times a send menu offers: tomorrow and next Monday, at a random minute from 7 to 9 am."""
    from . import clock
    now = clock.now()
    return {k: inbox.send_time(k, now).isoformat() for k in ("tomorrow", "monday")}


@query("enroll.choices", person_id="string", organization_id="string")
def _enroll_choices(core, db, person_id, organization_id):
    """What enrolling one Person asks (FR-SEQ-6, -36): active Sequences with the Person's live state in each, the
    Organizations it can be for, the addresses it can send to at `organization_id` ("" for none), and the
    reasons an Enrollment can finish with."""
    live = {e["sequence_id"]: e["state"] for e in sequences.enrollments(db, person_id) if e["state"] != "FINISHED"}
    return {"sequences": [q | {"live": live.get(q["id"])} for q in sequences.sequences(db) if not q["archived_at"]],
            "organizations": sequences.organization_choices(db, person_id),
            "addresses": sequences.address_choices(db, person_id, organization_id or None),
            "reasons": sequences.REASON_LABEL, "finishing": list(sequences.FINISHING_REASONS),
            "finish_reasons": [list(r) for r in FINISH_REASONS()]}


def FINISH_REASONS():  # noqa: N802 - the Console's list, with each reason's explanation
    from .tui import FINISH_REASONS as reasons
    return reasons


@query("address.complete", value="string", kind="string")
def _address_complete(core, db, value, kind):
    """The Console's To/Cc completion from local addresses: [ghost text, value Tab accepts], or null."""
    from types import SimpleNamespace

    from . import tui
    field = SimpleNamespace(address_kind=lambda: kind, conversations=lambda: None, app=SimpleNamespace(db=db))
    hit = tui.LocalAddressSuggester(field).resolve(value)
    return list(hit) if hit else None


@query("cases.list")
def _cases(core, db):
    """Cases with their rolled-up Status."""
    return inbox.case_queue(db)


@query("case.page", case_id="string")
def _case(core, db, case_id):
    """One Case: its title and description, its Activity feed and its Threads."""
    row = db.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    return {"case": dict(row) if row else None, "feed": inbox.case_feed(db, case_id),
            "threads": inbox.case_threads(db, case_id)}


@query("cases.view", view="string")
def _cases_view(core, db, view):
    """One Case Status view's Cases (Todo oldest first, the others most recently changed), as the Console lists them."""
    if view not in inbox.CASE_VIEWS:
        raise ApiError(-32602, f"view must be one of {', '.join(inbox.CASE_VIEWS)}")
    return inbox.case_queue(db, view)


@query("case.view", case_id="string")
def _case_view(core, db, case_id):
    """One Case as the Console's CasePage lays it out: `head` and `props` as segment lines, the `description`, one
    `timeline` of Activity lines and Thread cards (each card after the line that last added its Thread), each card's
    messages with the Console's text body, and the `emails` and `domains` for its Context sections."""
    from datetime import datetime

    from types import SimpleNamespace

    from .tui import ICON, PLATFORMS, VIEW_ICON, CasePage, case_feed_markup, escape, status_markup, thread_addresses
    case = db.execute("SELECT title, description FROM cases WHERE id = ?", (case_id,)).fetchone()
    if case is None:
        raise ApiError(-32602, f"no Case {case_id}")
    feed = inbox.case_feed(db, case_id)
    at = lambda iso: datetime.fromisoformat(iso).astimezone() if iso else datetime.min.astimezone()  # noqa: E731
    added = {e["thread_id"]: (at(e["at"]), i, 1) for i, e in enumerate(feed) if e["kind"] == "added"}
    order = lambda v: added.get(v["thread"].id) or (at(v["messages"][0]["at"] if v["messages"] else None), -1, 1)  # noqa: E731
    views = sorted((inbox.thread_view(db, t.id, files=FILES) for t in inbox.case_threads(db, case_id)), key=order)
    threads = [v["thread"] for v in views]
    status = inbox.rollup([t.status_detail for t in threads])
    icon, color = VIEW_ICON[status]
    head = (f"[b #45707a]{escape(case['title'])}[/]\n[{color}]{ICON[icon]} {status}[/]"
            f"[#928374] · {len(threads)} thread{'s' * (len(threads) != 1)}[/]")
    cards = []
    for v in views:
        t, slack = v["thread"], v["channel"]["platform"] == "slack"
        msgs = []
        for m in v["messages"]:
            body = (render.from_slack(m["content"]) if slack else render.from_text(m["content"])) if m["content"] is not None else None
            msgs.append({"id": m["native_id"], "who": m["who"], "at": m["at"], "deleted": m["content"] is None,
                         "shown": render.plain(body.text) if body else "", "trimmed": render.plain(body.trimmed) if body else ""})
        cards.append({"id": t.id, "head": segments(f"{ICON[PLATFORMS[v['channel']['platform']].icon]} [b]{escape(t.title)}[/]"
                                                   f" [#928374]· {escape(v['where'])}[/]"),
                      "status": segments(status_markup(t)), "messages": msgs})
    timeline = sorted([((at(e["at"]), i, 0), {"line": segments(case_feed_markup(e))}) for i, e in enumerate(feed)]
                      + [(order(v), {"card": i}) for i, v in enumerate(views)], key=lambda x: x[0])
    emails, domains = thread_addresses(views)
    return {"title": case["title"], "description": case["description"] or "", "head": segments(head),
            "props": segments(CasePage.props_markup(SimpleNamespace(views=views))),
            "cards": cards, "timeline": [x for _, x in timeline], "emails": emails, "domains": domains,
            "orgs": _org_ids(db, sorted({o for v in views if v["person"] for o in v["person"]["organizations"]}))}


@query("context.page", source="string", state="string", checked="string")
def _context_page(core, db, source, state=None, checked=None):
    """One Context source as the Console's ContextSourcePage draws it (FR-MOD-16), as segment lines. Its state and the
    time of its last check are the App's own (each Client checks its sources)."""
    from .tui import CONTEXT_SOURCES, context_markup
    if source not in CONTEXT_SOURCES:
        raise ApiError(-32602, f"no Context source {source}")
    return {"lines": segments(context_markup(source, state, checked, "cases" in inbox.modules_on(db)))}


@query("connection.page", connection_id="string")
def _connection_page(core, db, connection_id):
    """One Connection as the Console's ConnectionPage draws it (health, the Platform's guidance, account, connected,
    last check, problem), as segment lines; and the Slack Connection's id that channel rules belong to (or null)."""
    from .tui import connection_markup
    c = next((c for c in inbox.connections(db) if str(c["id"]) == str(connection_id)), None)
    if c is None:
        raise ApiError(-32602, f"no Connection {connection_id}")
    slack = db.execute("SELECT id FROM connections WHERE platform = 'slack'").fetchone()
    return {"lines": segments(connection_markup(c)), "slack": slack[0] if slack else None}


@query("drafts.list")
def _drafts(core, db):
    """The Drafts list, each with `recipient`: its To as People names, as the Console's Drafts list shows it. An
    email Task's Draft is listed through its Task until sent (Jeremy, 2026-11-04; inbox.list_drafts)."""
    return [d | {"recipient": inbox.recipient_names(db, d["to"] or "", d["kind"])}
            for d in inbox.list_drafts(db, tasks=False)]


@query("draft.view", draft_id="string")
def _draft(core, db, draft_id):
    """One Draft and the files staged on it, with what the Console's editor shows beside it: its Connection label,
    sender and recipient labels, a Reply Draft's Thread title, its Captured history (null until To is exact), and
    the Connections it may send from."""
    from .tui import FORMATS, PLATFORMS, conn_icon, conn_name, connection_label, draft_history
    d = inbox.get_draft(db, draft_id)
    conns = inbox.connections(db)
    c = next((c for c in conns if c["id"] == d["connection_id"]), None)
    try:
        views = draft_history(db, d["kind"], d["connection_id"], d["to"] or "")
        history = None if views is None else [{"id": v["thread"].id, "title": v["thread"].title,
                                                "status": v["thread"].status, "detail": v["thread"].status_detail,
                                                "messages": [{k: m[k] for k in ("who", "at", "content")} | _text(m, v)
                                                             for m in v["messages"]]}
                                               for v in views]
        note = "Choose an exact recipient." if views is None else "No captured match · prior contact unknown." if not views \
            else "Captured local history"
    except Exception:
        history, note = [], "Captured history unavailable · lookup failed."
    return {"draft": d, "staged": inbox.staged(db, draft_id=draft_id),
            "connection": connection_label(c) if c else ("Connection unavailable" if d["connection_id"] is not None
                                                         else "Choose Connection"),
            "sender": conn_name(c) if d["kind"] == "slack" and c else d["sender"],
            "recipient": inbox.recipient_names(db, d["to"] or "", "slack") if d["kind"] == "slack" else d["to"],
            "channel": FORMATS[d["kind"]].label, "new_title": FORMATS[d["kind"]].new_title,
            "thread_title": inbox.get_thread(db, d["thread_id"]).title if d["thread_id"] else None,
            "history": history, "history_note": note,
            "connections": [{"id": x["id"], "label": f"{conn_icon(x)} {PLATFORMS[x['platform']].title} · {conn_name(x)}",
                             "kind": PLATFORMS[x["platform"]].format, "self_id": x["self_id"] or "",
                             "kind_label": FORMATS[PLATFORMS[x["platform"]].format].label}
                            for x in conns if x["platform"] in PLATFORMS and PLATFORMS[x["platform"]].format in FORMATS]}


@query("draft.problem", values="object")
def _draft_problem(core, db, values):
    """What a Draft's values still need before it can send (the Console's pre-check), or null when complete."""
    return inbox.draft_problem(values)


@query("today.side", kind="string", id="string")
def _today_side(core, db, kind, id):
    """The right pane for a Today Paused row (kind "paused", a Person id) or Unattended row ("unattended", an
    Organization id; "unattended_person", a Person id), as the Console fills it: lines of [text, colour or null,
    bold] segments."""
    from rich.console import Console
    from rich.text import Text

    from .tui import pipeline_section, sequence_section
    if kind == "paused":
        p = db.execute("SELECT display_name FROM persons WHERE id = ?", (id,)).fetchone()
        markup = f"[b]{p[0] if p else ''}[/]\n\n" + sequence_section(db, id, keys=False) \
            + "\n\n[#928374]r resume · f finish · enter opens the Person[/]"
    elif kind == "unattended_person":
        p = db.execute("SELECT display_name FROM persons WHERE id = ?", (id,)).fetchone()
        markup = f"[b]{p[0] if p else ''}[/]\n\n" + pipeline_section(db, id, person=True)
    else:
        o = db.execute("SELECT name, domain FROM organizations WHERE id = ?", (id,)).fetchone()
        domain = f"  [#928374]{o['domain']}[/]" if o and o["domain"] != o["name"] else ""
        markup = f"[b]{o['name'] if o else ''}[/]{domain}\n\n" + pipeline_section(db, id)
    return segments(markup)


def segments(markup: str) -> list:
    """Console markup as lines of [text, colour or null, bold, link or null] segments, for a view to draw as the
    Console does. A link is "person:<id>", "org:<domain>", "thread:<id>" (the Console's open_person, open_org and
    open_thread) or a URL."""
    import re

    from rich.console import Console
    from rich.text import Text
    con = Console(color_system="truecolor")

    def link(s):
        click = (s.meta or {}).get("@click") if s else None
        if click and (m := re.match(r"app\.open_(person|org|thread)\('?([^')]*)'?\)", click if isinstance(click, str) else "")):
            return f"{m[1]}:{m[2]}"
        if isinstance(click, tuple) and click and click[0] in ("app.open_person", "app.open_org", "app.open_thread"):
            return f"{click[0][9:]}:{click[1][0] if click[1] else ''}"
        return s.link if s and s.link else None

    style = lambda s: (s.color.get_truecolor().hex if s and s.color else None, bool(s and s.bold), link(s))  # noqa: E731
    return [[[seg.text, *style(seg.style)] for seg in line.render(con, end="") if seg.text]
            for line in Text.from_markup(markup).split("\n", allow_blank=True)]


@query("calendar.page", start="string", end="string")
def _calendar_page(core, db, start, end):
    """The Calendar on the local days [start, end), as the Console's CalendarPage: its `banner` (no provider yet, or
    the feed is broken and this is the copy as of its last good fetch) as segment lines, and per day (YYYY-MM-DD) the
    cell's Events (start and mark, title, colour), the `more` count, the Calendar Item `tally`, and the day pane's
    `items` with what each opens."""
    from datetime import date

    from .tui import CAL_COUNTS, CAL_LINES, ICON, PALETTE, cal_by_day, cal_line, cal_mark, cal_style, cal_time, clock, escape
    p, why = calendar.provider(db), calendar.broken(db)
    banner = None
    if p is None:
        banner = f"[{PALETTE['grey1']}]No Event provider yet: add the google-calendar-ics credential (runbook §3g).[/]"
    elif why:
        since = clock.local(datetime.fromisoformat(p["ok_at"])).strftime("%a %H:%M") if p["ok_at"] else "earlier"
        banner = (f"[b {PALETTE['red']}]{ICON['broken']} Google Calendar unreachable[/]"
                  f" [{PALETTE['grey1']}]{escape(why)} · showing the copy as of {since}[/]")
    days = {}
    for day, items in cal_by_day(calendar.items(db, date.fromisoformat(start), date.fromisoformat(end))).items():
        events = [i for i in items if i["kind"] == "event"]
        shown = events if len(events) <= CAL_LINES else events[:CAL_LINES - 1]
        counts = {k: sum(1 for i in items if i["kind"] == k) for k, _ in CAL_COUNTS}
        days[day.isoformat()] = {
            "events": [{"text": f"{'' if e['all_day'] else cal_time(e['start']) + ' '}{cal_mark(e)}{e['title']}",
                        "color": cal_style(e).split()[-1], "struck": bool(e["deleted_at"])} for e in shown],
            "more": len(events) - len(shown),
            "tally": " ".join(f"{ICON[icon]}{counts[kind]}" for kind, icon in CAL_COUNTS if counts[kind]),
            "items": [{"line": segments(cal_line(i)), "kind": i["kind"], "ref": i.get("ref"), "join_url": i.get("join_url"),
                       "event": {"uid": i["uid"], "id": i["id"]} if i["kind"] == "event" else None} for i in items]}
    return {"banner": segments(banner) if banner else None, "days": days}


@query("event.page", uid="string", id="string")
def _event_page(core, db, uid, id):
    """One Event as the Console's EventPage draws it: `body` as segment lines (People and Organizations linked),
    its `join_url`, `uid`, `repeats` and `deleted`, and the `emails` and `domains` for its Context sections; or
    `available` false with the reason."""
    from .tui import PALETTE, clock, escape, event_markup
    e = calendar.current(db, {"uid": uid, "id": id})
    if e is None or e["deleted_at"]:
        reason = "This Event is deleted." if e else "This Event is unavailable."
        title = e["title"] if e else ""
        return {"available": False, "reason": reason, "body": segments(
            f"[b {PALETTE['blue']}]{escape(title)}[/]\n[{PALETTE['grey1']}]Event unavailable · {reason}[/]")}
    e = {**e, "kind": "event", "type": e["kind"], "at": clock.local(e["start"])}
    markup, emails, known = event_markup(db, e)
    return {"available": True, "body": segments(markup), "join_url": e["join_url"], "uid": e["uid"], "id": e["id"],
            "repeats": bool(e["repeats"]), "emails": emails, "domains": known, "orgs": _org_ids(db, known)}


def _org_ids(db, domains) -> dict:
    """Organization ids by domain, for a view to open an "org:<domain>" link."""
    marks = ",".join("?" * len(domains))
    return {r[0]: r[1] for r in db.execute(f"SELECT domain, id FROM organizations WHERE domain IN ({marks})", list(domains))} \
        if domains else {}


@query("task.pane", task_id="string")
def _task_pane(core, db, task_id):
    """One due Task's pane as the Console's TaskPane draws it: `head`, `body` and `props` as segment lines, its
    `hint`, the Person's `emails` and Organization `domains` for its Context sections, and its `draft` (id and
    Thread: a New Thread Draft shows in the Draft editor in place, a Reply Draft opens its Thread's reply box)."""
    from .tui import KIND_LABEL, escape, task_hint, task_pane_markup, task_props
    t = next((t for t in sequences.tasks_due(db, scheduled=True) if t["id"] == task_id), None)
    if t is None:
        raise ApiError(-32602, f"no due Task {task_id}")
    return {"head": segments(f"[b #45707a]Day {t['day']} · {KIND_LABEL[t['kind']]} · {escape(t['person'])}[/]"),
            "body": segments("\n".join(task_pane_markup(db, t))), "props": segments(task_props(t)), "hint": task_hint(t),
            "emails": [r[0] for r in db.execute("SELECT value FROM identities WHERE person_id = ? AND kind = 'email'",
                                                (t["person_id"],))],
            "domains": inbox.person_organizations(db, t["person_id"]),
            "draft": {"id": t["draft_id"], "thread_id": inbox.get_draft(db, t["draft_id"])["thread_id"]} if t["draft_id"] else None}


@query("tasks.list")
def _tasks(core, db):
    """The Tasks page: due Tasks, then scheduled email Tasks in group "scheduled" with their `send_at` (#146)."""
    return sequences.tasks_due(db, scheduled=True)


@query("issues.view", options="object", today="boolean")
def _issues_view(core, db, options=None, today=False):
    """The Issues page (FR-UI-1) as groups of Issues under the Display options given (each missing one at its
    default), or with `today` today's Planned Issues (FR-UI-2); each Issue has its urgency, size, warnings and
    `not_saved`, and a Planned day group its capacity. `rows` is the same list flattened, for a virtual list."""
    from . import issues
    v = issues.view(db, {"grouping": "none", "completed": "none"} if today else options,
                    day_only=issues.today().isoformat() if today else None)
    if today:
        v["capacity"] = issues.capacity(db, issues.today())
    v["rows"] = [{"kind": kind, **({"issue": x} if kind == "issue" else {k: x.get(k) for k in ("title", "count",
                                                                                              "capacity")})}
                 for kind, x in issues.flat(v)]
    return v


@query("issue.detail", issue_id="string")
def _issue_detail(core, db, issue_id):
    """One Issue as the Console's right pane draws it (issue_markup), as segment lines, and its team's statuses for
    the status picker."""
    from . import issues
    from .tui import issue_markup
    rows = [x for _, x in issues.flat(issues.view(db, {"grouping": "none", "completed": "month", "sub_issues": True}))
            if x.get("id") == issue_id]
    if not rows:
        raise ApiError(-32602, f"no Issue {issue_id}")
    i = rows[0]
    statuses = [dict(r) for r in db.execute("SELECT id, name FROM issue_statuses WHERE team_id = ? ORDER BY position",
                                            (i["team_id"],))]
    return {"lines": segments(issue_markup(i)), "issue": i, "statuses": statuses}


@query("actions.queue")
def _actions_queue(core, db):
    """The Action Queue page (FR-UI-4): Actions not yet confirmed or confirmed today, and what is scheduled to
    leave in the next week, by day."""
    from . import actions
    return actions.queue(db)


@query("undo.newest")
def _undo_newest(core, db):
    """The Command z would undo (ADR 0021): {id, label, source, at}, or null when there is nothing to undo."""
    return commands.newest(db)


@query("history.page", service="boolean")
def _history(core, db, service):
    """History (ADR 0021): the Command log, newest first, each with its Source and whether it was undone; `service`
    false leaves out the Service's own work."""
    return commands.history(db, service=service)


@query("task.markdown", task_id="string")
def _task_markdown(core, db, task_id):
    """One due Task as the Markdown `comms-terminal tasks` prints, for an agent."""
    from .tui import task_markdown
    t = next((t for t in sequences.tasks_due(db, scheduled=True) if t["id"] == task_id), None)
    if t is None:
        raise ApiError(-32602, f"no due Task {task_id}")
    return task_markdown(db, t)


@query("sequences.list")
def _sequences(core, db):
    """Every Sequence."""
    return sequences.sequences(db)


@query("sequence.page", sequence_id="string")
def _sequence(core, db, sequence_id):
    """One Sequence: its Steps, their outline (each Step row with the Console's `label`, ↩ `reply` Step number,
    `problem` flag and `what` summary), what would stop it running, and its side pane (`props`) as segment lines."""
    from .tui import KIND_LABEL, sequence_props_markup
    steps = sequences.sequence_steps(db, sequence_id)
    problems = sequences.validate(db, sequence_id)
    flagged = {sid for sid, _ in problems}
    by_id = {s["id"]: s for s in steps}
    names = dict(db.execute("SELECT id, name FROM sequences").fetchall())
    outline = sequences.outline(steps)
    for line in outline:
        st = line["step"]
        if st is None:
            continue
        line["label"] = sequences.ACTION_LABEL.get(st["action_kind"]) if st["kind"] == "action" else \
            f"If · {sequences.CONDITION_LABEL[st['condition']]}" if st["kind"] == "if" else KIND_LABEL[st["kind"]]
        line["reply"] = by_id[st["reply_to_step_id"]]["position"] if st["reply_to_step_id"] in by_id else None
        line["problem"] = st["id"] in flagged
        what = sequences.step_summary(st, names)
        line["what"] = what.splitlines()[0][:60] if what else ""
    q = next((q for q in sequences.sequences(db) if str(q["id"]) == str(sequence_id)), None)
    return {"sequence": sequences.get_sequence(db, sequence_id), "steps": steps, "outline": outline, "problems": problems,
            "props": segments(sequence_props_markup(db, q)) if q else []}


@query("step.editor", step_id="string")
def _step_editor(core, db, step_id):
    """One Step as the Console's StepEditor shows it: `head`, `problems` and `preview` as segment lines, its `fields`
    for this kind of Step as [label, text, placeholder], the body's label and text, the voicemail (a call), and whether
    it is an Enroll Action (its own hint)."""
    from .tui import STEP_BODY_LABEL, step_field_values, step_fields, step_markup, step_placeholder
    st = dict(db.execute("SELECT * FROM sequence_steps WHERE id = ?", (step_id,)).fetchone())
    values = step_field_values(db, st)
    head, problems, preview = step_markup(db, st)
    return {"step": st, "head": segments(head), "problems": segments(problems) if problems else [],
            "preview": segments(preview) if preview else [],
            "fields": [[label, values[label], step_placeholder(st, label)] for label in step_fields(st)],
            "body_label": STEP_BODY_LABEL[st["kind"]], "body": st["body"],
            "voicemail": st["voicemail"] if st["kind"] == "call" else None, "enroll": st["action_kind"] == "enroll"}


@query("step.values", step_id="string", fields="object", body="string", voicemail="string")
def _step_values(core, db, step_id, fields, body, voicemail):
    """The Step editor's typed fields (by label) as save_step's `values`, with `same` true when nothing changed;
    or `error`, the Console's "Not saved" reason. `voicemail` is "" for a Step without one."""
    from .tui import step_values
    st = dict(db.execute("SELECT * FROM sequence_steps WHERE id = ?", (step_id,)).fetchone())
    try:
        v = step_values(db, st, fields, body, voicemail if st["kind"] == "call" else None)
        if type(v.get("gap_days", 0)) is not int:
            raise ValueError("Gap is a whole number of business days, 0 or more.")
        if (sid := v.get("enroll_sequence_id")) is not None and not db.execute("SELECT 1 FROM sequences WHERE id = ?", (sid,)).fetchone():
            raise ValueError(f"No Sequence is named {sid}.")
    except (ValueError, pipeline.Refused) as e:
        return {"error": str(e)}
    blank = ("at_time", "linkedin_move", "reply_to_step_id", "pipeline", "stage")
    same = all((st.get(k) or None) == (val or None) if k in blank else st.get(k) == val for k, val in v.items())
    return {"values": v, "same": same}


@query("step.choices")
def _step_choices(core, db):
    """What a new Action can do and what a new If can check, with their labels."""
    return {"actions": sequences.ACTION_LABEL, "conditions": sequences.CONDITION_LABEL}


@query("paused.list")
def _paused(core, db):
    """Paused Enrollments."""
    return sequences.paused(db)


@query("people.list")
def _people(core, db):
    """Every Person."""
    return inbox.people(db)


@query("people.find", text="string")
def _find(core, db, text):
    """People whose name or Identity matches `text`, as [id, name] pairs."""
    return inbox.find_people(db, text)


@query("people.merge_preview", keep="string", drop="string")
def _merge_preview(core, db, keep, drop):
    """What merging `drop` into `keep` would make, without changing anything: name, Identities, Organizations, counts."""
    return inbox.merge_preview(db, keep, drop)


@query("person.page", person_id="string")
def _person(core, db, person_id):
    """One Person: Identities, Threads, Organizations and Enrollments; and as the Console's PersonPage draws it, the
    `head` and the `rest` (Sequence section, Organizations, Threads, Cases) as segment lines, the `orgs` ids for its
    links, and the `emails` and `domains` for its Context sections."""
    from .tui import ICON, escape, glyph, pipeline_section, sequence_section
    p = inbox.person_view(db, person_id)
    threads = "\n".join(f"[@click=app.open_thread({t.id!r})]{glyph(t.status_detail)} [underline]{escape(t.title)}[/][/]"
                        for t in p["threads"]) or "—"
    orgs = ", ".join(f"[@click=app.open_org('{o}')][underline]{escape(o)}[/][/]" for o in p["organizations"]) or "—"
    on = inbox.modules_on(db)  # an off Module's sections are not drawn (ADR 0017)
    section = sequence_section(db, person_id)
    current = pipeline.current(db, person_id=person_id) if "pipelines" in on else {}  # Fundraising, Talent (ADR 0016)
    cases = f"\n\n[#928374]{ICON['cases']} cases[/]\n{escape(', '.join(p['cases']) or '—')}" if "cases" in on else ""
    rest = ((f"\n{pipeline_section(db, person_id, person=True)}\n" if current else "")
            + (f"\n{section}\n" if section else "") + f"\n[#928374]{ICON['organizations']} organizations[/]\n{orgs}\n\n"
            f"[#928374]{ICON['threads']} threads[/]\n{threads}" + cases)
    return {"person": p, "organizations": inbox.person_organizations(db, person_id),
            "enrollments": sequences.enrollments(db, person_id) if "sequences" in on else [],
            "head": segments(f"[b #45707a]{ICON['person']} {escape(p['name'])}[/]\n\n[#928374]{ICON['identity']} identities[/]"),
            "rest": segments(rest), "orgs": _org_ids(db, p["organizations"]),
            "emails": [i["value"] for i in p["identities"] if i["kind"] == "email"], "domains": p["organizations"],
            "stages": _stages_of(current, pipeline.PEOPLE)}


def _stages_of(current: dict, pipelines) -> dict:
    """A page's `stages`: the Pipelines it holds a Stage in, its current Stage in each, and the Stages and names of
    the Pipelines it can be in (an Organization: Revenue; a Person: Fundraising and Talent, ADR 0016)."""
    return {"held": [p for p, st in current.items() if st["stage"]], "current": {p: st["stage"] for p, st in current.items()},
            "all": {p: list(pipeline.STAGES[p]) for p in pipelines}, "names": pipeline.NAMES}


@query("enroll.start", text="string")
def _enroll_start(core, db, text):
    """A typed start for an Enrollment (YYYY-MM-DD or a number of days) as YYYY-MM-DD, or null when it is neither."""
    from . import clock
    day = sequences.parse_start(text, clock.now())
    return day.isoformat() if day else None


@query("enrollment.first_due", enrollment_id="string")
def _first_due(core, db, enrollment_id):
    """When an Enrollment's first Task is due, as the Console's enroll toast says it."""
    return sequences.first_due(db, enrollment_id)


@query("organizations.list")
def _organizations(core, db):
    """Every Organization, with each Organization's Pipeline Stages."""
    return {"organizations": inbox.organizations(db), "states": pipeline.states(db)}


@query("organization.page", domain="string")
def _organization(core, db, domain):
    """One Organization by its domain; and as the Console's OrganizationPage shows it: `side` (its Pipeline section and
    People with their identities) as segment lines, `org` (id, name, domain), `emails` for its Context sections, and
    `stages`: the Pipelines it holds a Stage in, its current Stage in each, every Pipeline's Stages and names."""
    from .tui import ICON, IDENTITY_ICON, escape, pipeline_section
    v = inbox.organization_view(db, domain)
    r = db.execute("SELECT id, domain, name FROM organizations WHERE domain = ?", (domain,)).fetchone()
    o = dict(r) if r else None
    people = v["people"]
    pipe = pipeline_section(db, o["id"]) if o else ""  # empty while Pipelines is off (ADR 0017)
    side = (pipe + "\n\n" if pipe else "") + ("\n\n".join(
        f"[@click=app.open_person({p['id']!r})][b underline]{escape(p['name'])}[/][/]\n"
        + "\n".join(f"[#928374]{ICON[IDENTITY_ICON[i['kind']]]} {escape(i['value'])}[/]" for i in p["identities"])
        for p in people) or "[#928374]No people yet.[/]")
    current = pipeline.current(db, o["id"]) if o and pipe else {}
    return v | {"side": segments(side), "org": o,
                "emails": [i["value"] for p in people for i in p["identities"] if i["kind"] == "email"],
                "stages": _stages_of(current, (pipeline.REVENUE,))}


@query("people.find", text="string")
def _people_find(core, db, text):
    """People matching an address, number or name, as [id, name] pairs (the Console's find_people)."""
    return [list(m) for m in inbox.find_people(db, text)]


@query("amount.parse", text="string")
def _amount(core, db, text):
    """A typed Value (90k, 1.5m, 85000) as a whole number of dollars, or null when it is not one."""
    try:
        return pipeline.parse_amount(text)
    except Exception:
        return None


@query("pipeline.page", name="string")
def _pipeline(core, db, name):
    """One Pipeline (revenue, fundraising or talent): its math, its verdict on the Target, and its board as Markdown."""
    p = pipeline.parse_pipeline(name)
    m = pipeline.math(db, p) if p in pipeline.MATH else None
    verdict = pipeline.on_track(m) if m else None
    return {"math": m, "verdict": list(verdict) if verdict else None, "board": pipeline.board_markdown(db, p)}


@query("pipeline.board", name="string")
def _pipeline_board(core, db, name):
    """One Pipeline as the Console's PipelinePage lays it out: the THIS WEEK, PROGRESS and CONSTRAINT boxes, the
    ORGANIZATIONS or PEOPLE board (`noun`; a column per Stage, its header with count and conversion, the Constraint's
    marked; each card's Organization or Person (`person` true, ADR 0016) and lines), the WEEKLY REVIEW rows and legend, all as segment lines; `board_only` for Talent;
    and `copy`, the Markdown y copies for an agent."""
    from .tui import (PALETTE, card, column_head, escape, pipeline_constraint, pipeline_progress, pipeline_week)
    p = pipeline.parse_pipeline(name)
    m = pipeline.math(db, p)
    board_only = p not in pipeline.MATH
    g = PALETTE["grey1"]
    rows = {r["stage"]: r for r in m["stages"] + m["ending"]}
    c = m["summary"]["constraint"]
    columns = []
    for stage in pipeline.OPEN[p] + tuple(label for label, _ in pipeline.END_ROWS[p]):
        r = rows[stage]
        hot = not board_only and c is not None and bool(r.get("constraint") or (r.get("measure") and r["measure"] == c["stage"]))
        head = f"[b #654735]{r['stage']}[/]\n[b]{len(r['orgs'])}[/]" if board_only else column_head(r, hot)
        columns.append({"stage": stage, "head": segments(head), "cards": [
            {"org": {"id": o["id"], "domain": o["domain"], "name": o["name"], "person": o["person"]},
             "lines": segments(card(o, m["now"], not board_only))}
            for o in r["orgs"]]})
    weekly = []
    for r in m["stages"]:
        convert = pipeline.pct(r["rate"]) if r["rate"] is not None else \
            f"[{g}]{pipeline.pct(r['used'])} (benchmark)[/]" if r["assumed"] else f"[{g}]no data[/]"
        days = f"{r['time']:.0f}" if r["time"] is not None else "—"
        stage = f"[b {PALETTE['red']}]{r['stage']} ◀[/]" if r["constraint"] else escape(r["stage"])
        short = f"[{PALETTE['red']}]{pipeline.num(-r['pace'])}[/]" if r["pace"] is not None and r["pace"] < 0 else f"[{g}]—[/]"
        weekly.append([segments(x)[0] if x else [] for x in (stage, str(len(r["orgs"])), convert, pipeline.pct(r["benchmark"]), days,
                       pipeline.pct(r["to_paying"]), pipeline.num(r["need"]), pipeline.num(r["entries"]), short)])
    for r in m["ending"]:
        if r["measure"]:
            word = "keep" if r["measure"] == "GRR" else "grow"
            rate = f"{word} {pipeline.pct(r['rate'])}" if r["rate"] is not None else f"[{g}]no data[/]"
            weekly.append([segments(x)[0] if x else [] for x in (escape(r["stage"]), str(len(r["orgs"])), rate,
                                                                  pipeline.pct(r["benchmark"]), "", "", "", "", "")])
    paid = pipeline.PAYING[p][0]
    copy = (pipeline.board_markdown(db, p) if board_only else pipeline.markdown(m)) + \
        f"\nFresh copy: `comms-terminal pipeline {pipeline.NAMES[p].lower()}`\n"
    noun = pipeline.NOUN[p]
    return {"name": pipeline.NAMES[p], "board_only": board_only, "noun": noun,
            "week": segments(pipeline_week(m)), "progress": segments(pipeline_progress(m)),
            "constraint": segments(pipeline_constraint(m)), "columns": columns,
            "weekly_head": ["Stage", noun, "Convert", "Benchmark", "Days in Stage", f"Reach {paid}", "Need a week",
                            "Getting a week", "Short by"], "weekly": weekly,
            "legend": f"Convert: share of {noun} that moved on to the next Stage in the last 90 days · Benchmark:"
                      f" the same rate over the last 2 years · Need and Getting: {noun} entering the Stage a week"
                      " · Short by: how many more a week the Target needs", "copy": copy}


@query("pipeline.stages")
def _stages(core, db):
    """Each Pipeline's name and Stages in order (open, paying, ending), keyed by its code (REVENUE, …)."""
    return {p: {"name": pipeline.NAMES[p], "stages": list(pipeline.STAGES[p]), "open": list(pipeline.OPEN[p]),
                "paying": list(pipeline.PAYING[p]), "ending": list(pipeline.ENDING[p]), "math": p in pipeline.MATH}
            for p in pipeline.PIPELINES}


@query("organization.history", org_id="string")
def _org_history(core, db, org_id):
    """One Organization's Stage and Value changes, newest first."""
    return pipeline.history(db, org_id)


@query("pipeline.unattended")
def _unattended(core, db):
    """Organizations and People (person_id set) in an open Stage with nothing bringing them back."""
    return pipeline.unattended(db)


@query("calendar.events", start="string", end="string")
def _events(core, db, start, end):
    """Calendar Events overlapping the local days [start, end), both YYYY-MM-DD, in start order."""
    from datetime import date
    found = calendar.expand(db, date.fromisoformat(start), date.fromisoformat(end), deleted=False)
    return [{**e, "start": e["start"].isoformat(), "end": e["end"].isoformat()} for e in found]


@query("connections.list")
def _connections(core, db):
    """Every Connection."""
    return inbox.connections(db)


@query("channels.list", connection_id="string")
def _channels(core, db, connection_id):
    """One Connection's channel rules."""
    return inbox.channel_rules(db, connection_id)


def _text(m, view) -> dict:
    """A message's text body as the Console shows it: `shown`, and the quoted or signature text folded behind •••
    (`trimmed`); both empty for a deleted message."""
    if m["content"] is None:
        return {"shown": "", "trimmed": ""}
    body = render.from_slack(m["content"]) if view["channel"]["platform"] == "slack" else render.from_text(m["content"])
    return {"shown": render.plain(body.text), "trimmed": render.plain(body.trimmed)}


def _shared(core, name, fn, db):
    """A value several queries of one refresh read (Today's for today.page and shell.summary), computed once per
    event with Core._answer's answers. Readers must not change it."""
    answers = getattr(core, "answers", None)
    if answers is None:  # a bare caller, not a Core
        return fn(db)
    key = ("shared", name, _minute())
    if key not in answers:
        answers[key] = fn(db)
    return answers[key]


def _minute() -> int:
    return int(time.time() // 60)


def _plain_dates(v):
    """Dates (a Pipeline's math has them) as YYYY-MM-DD: remote.encode tags datetimes only."""
    from datetime import date, datetime
    if isinstance(v, date) and not isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _plain_dates(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return type(v)(_plain_dates(x) for x in v) if isinstance(v, list) else tuple(_plain_dates(x) for x in v)
    return v


# --- the core ------------------------------------------------------------------------------------------------------

class Subscriber:
    """One connection's event stream: sent in order on its own thread; a full backlog becomes one "missed"."""

    def __init__(self, write):
        self.write, self.q, self.alive = write, queue.Queue(maxsize=BACKLOG), True
        threading.Thread(target=self._run, daemon=True).start()

    def push(self, message):
        try:
            self.q.put_nowait(message)
        except queue.Full:
            while True:
                try:
                    self.q.get_nowait()
                except queue.Empty:
                    break
            self.q.put_nowait({"jsonrpc": "2.0", "method": "event", "params": {"kind": "missed", "data": None,
                                                                               "seq": message["params"]["seq"]}})

    def _run(self):
        while self.alive:
            try:
                self.write(self.q.get())
            except OSError:
                self.alive = False


class Core:
    """The Local core. With `remote` and `syncer` it works on the laptop copy (ADR 0012); without them, on the
    database file directly, as the Console does on the Service's host."""

    def __init__(self, db_path: str, socket=None, remote_=None, syncer=None):
        self.db_path, self.socket, self.remote, self.syncer = db_path, socket, remote_, syncer
        self.service = None  # the Service's name for the status bar (run() sets it)
        self.jobs = queue.Queue()
        self.worker = threading.Thread(target=self._work, daemon=True)
        self.lock, self.seq, self.subscribers = threading.Lock(), 0, []
        self.answers, self.answers_seq = {}, 0  # query answers since event `answers_seq` (_answer)
        self.proxies = {m: remote.proxy(remote_, mod, m) for m, mod in remote.MODULES.items()} if remote_ else None
        self.worker.start()
        self.db = self.call_from_thread(inbox.connect, db_path)
        self.raw = self.call_from_thread(files.raw_dir, self.db)
        if syncer is not None and syncer.base is not None:  # loaded from base.db on the caller's thread
            self.call_from_thread(_rebase, syncer)
        if remote_ is not None:
            remote_.app = self
            remote_.source = "app"  # the App's writes are App Commands on the Service too (ADR 0021)
        threading.Thread(target=self._heartbeat, daemon=True).start()

    # The interface Remote and Syncer report to, as the Console's app does.

    def _work(self):
        while True:
            fn, args, kwargs, done = self.jobs.get()
            try:
                done.set_result(fn(*args, **kwargs))
            except BaseException as e:  # noqa: BLE001 - handed back to the caller's thread
                done.set_exception(e)

    def call_from_thread(self, fn, *args, **kwargs):
        if threading.current_thread() is self.worker:
            return fn(*args, **kwargs)
        done = Future()
        self.jobs.put((fn, args, kwargs, done))
        return done.result()

    def notify(self, message, severity="information", timeout=None):
        self.publish("toast", {"message": message, "severity": severity, "timeout": timeout})

    def apply_snapshot(self, syncer, body):
        if syncer.apply(self.db, body):
            self.check_copy()
            changed = syncer.changed
            self.publish("rows", {"changed": None if changed is None else [list(c) for c in changed]})

    def check_copy(self):
        """Writes stay off while the copy's schema or the Service's build differ from this core's code."""
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        service, mine = getattr(self.syncer, "service_build", None), build()
        if version != inbox.SCHEMA_VERSION or (service and mine and service != mine):
            self.set_online("Update ready · restart Comms Terminal to load the service's version; read-only.")
            if service and mine and service != mine:
                self.publish("update", {"service_build": service, "build": mine})
        elif self.syncer is not None and self.syncer.confirmed and self.remote.link.ready():
            self.set_online(None)

    def set_online(self, problem):
        r = self.remote
        if r is None or (r.problem == problem and r.online == (problem is None)):
            return
        r.online, r.problem = problem is None, problem
        self.publish("status", self.status())

    # Events.

    def publish(self, kind, data):
        with self.lock:
            self.seq += 1
            message = {"jsonrpc": "2.0", "method": "event", "params": {"seq": self.seq, "kind": kind, "data": data}}
            self.subscribers = [s for s in self.subscribers if s.alive]
            for s in self.subscribers:
                s.push(message)

    def subscribe(self, write) -> dict:
        with self.lock:
            self.subscribers.append(Subscriber(write))
            seq = self.seq
        return {"seq": seq, "status": self.status()}

    def _heartbeat(self):
        while True:
            threading.Event().wait(HEARTBEAT)
            self.publish("heartbeat", None)

    def status(self) -> dict:
        r = self.remote
        return {"generation": GENERATION, "online": True if r is None else r.online,
                "problem": None if r is None else r.problem, "build": build(),
                "service_build": getattr(self.syncer, "service_build", None), "schema": inbox.SCHEMA_VERSION,
                "pending": 0 if r is None else r.busy, "seq": self.seq, "service": self.service}

    # The Local API.

    def handle(self, req: dict, write=None) -> dict | None:
        rid = req.get("id")
        try:
            if req.get("jsonrpc") != "2.0" or not isinstance(req.get("method"), str):
                raise ApiError(-32600, "invalid request")
            params = req.get("params") or {}
            if not isinstance(params, dict):
                raise ApiError(-32602, "params must be an object")
            result = self.dispatch(req["method"], dict(params), write)
            return None if rid is None else {"jsonrpc": "2.0", "id": rid, "result": result}
        except ApiError as e:
            error = {"code": e.code, "message": str(e)} | ({"data": e.data} if e.data is not None else {})
        except Refused as e:
            error = {"code": -32012, "message": str(e)}
        except remote.Offline as e:
            error = {"code": -32011, "message": str(e)}
        except DOMAIN_ERRORS as e:
            kind = next(n for n, c in remote.ERRORS.items() if isinstance(e, c))
            error = {"code": -32010, "message": str(e.args[0]) if e.args else kind, "data": {"type": kind}}
        except Exception as e:  # noqa: BLE001 - every call gets an answer
            LOG.info("call %s failed: %s", req.get("method"), e)
            error = {"code": -32000, "message": f"{type(e).__name__}: {e}"}
        return None if rid is None else {"jsonrpc": "2.0", "id": rid, "error": error}

    def dispatch(self, method: str, params: dict, write=None):
        origin = params.pop("origin", "panel")  # least privilege: only the App's views say "view"
        if origin not in ORIGINS:
            raise ApiError(-32602, "origin must be view or panel")
        if method == "rpc.discover":
            return openrpc()
        if method == "core.status":
            return self.status()
        if method == "events.subscribe":
            if write is None:
                raise ApiError(-32601, "events.subscribe needs a connection")
            return self.subscribe(write)
        if method in QUERIES:
            fn, spec, _ = QUERIES[method]
            _require(params, spec)
            return self.call_from_thread(self._answer, method, fn, params)
        if method in ("message.body", "message.load_images"):
            raw = self._raw(params.get("raw"))
            return render.safe_html(raw, fetch=fetch_image if method == "message.load_images" else None)
        if method.startswith("command."):
            return self.command(method.removeprefix("command."), params, origin)
        if method in SERVICE:
            if origin != "view":
                raise Refused(f"{method} runs only from the App's views")
            fn, spec, _ = SERVICE[method]
            missing = {p for p, (_, req) in spec.items() if req} - set(params)
            if missing or set(params) - set(spec):
                raise ApiError(-32602, f"expected params {sorted(spec)}, got {sorted(params)}")
            return fn(self, **params)
        raise ApiError(-32601, f"no method {method}")

    def _answer(self, method, fn, params):
        """A query's answer, on the worker. The Views reload together after each event and several ask the same
        query, so an answer is kept until the next event (or the next minute: Today's groups and "now/next" move
        with the clock) and computed once."""
        key = (method, json.dumps(params, sort_keys=True), _minute())
        if self.answers_seq != self.seq:
            self.answers, self.answers_seq = {}, self.seq
        if key not in self.answers:
            self.answers[key] = remote.encode(_plain_dates(fn(self, self.db, **params)))
        return self.answers[key]

    def command(self, name: str, params: dict, origin: str):
        if name not in writers.WRITERS:
            raise ApiError(-32601, f"no command {name}")
        if origin == "panel" and name in VIEW_ONLY:
            raise Refused(f"{name} runs only from the App's views: {agent.CONSOLE_ONLY[name]}")
        module, fn = name.split(".")
        real = getattr(remote.MODULES[module], fn)
        kwargs = remote.decode(params)
        try:
            inspect.signature(real).bind(None, **kwargs)
        except TypeError as e:
            raise ApiError(-32602, str(e)) from None
        target = getattr(self.proxies[module], fn) if self.proxies else real

        def run():
            try:
                inbox.admit(self.db, name, (), kwargs)  # a call into an off Module is refused (ADR 0017)
                with commands.source("app"):  # the App's Commands (ADR 0021)
                    result = target(self.db, **kwargs)
                if self.proxies is None:
                    self.db.commit()
            finally:
                self.answers = {}  # a query run before this write's event must not get an answer from before it
            return result
        result = self.call_from_thread(run)
        self.publish("rows", {"changed": None, "cause": name})
        return remote.encode(result)

    def _raw(self, name) -> bytes:
        if not isinstance(name, str) or len(name) != 64 or any(c not in "0123456789abcdef" for c in name):
            raise ApiError(-32602, "raw must be a message's sha256")
        if self.raw is None:
            raise ApiError(-32000, "no raw messages beside an in-memory database")
        try:
            return (self.raw / f"{name}.eml").read_bytes()
        except FileNotFoundError:
            raise ApiError(-32004, "raw message not on this machine") from None

    # View-only operations that reach the Service directly, never queued or retried (ADR 0011).

    def send_draft(self, draft_id, revision, again=False):
        return remote.socket_send_draft(self._socket())(draft_id, revision, again)

    def send_reply(self, thread_id, text, to=None, cc=None):
        return remote.socket_reply(self._socket())(thread_id, text, to, cc)

    def respond(self, payload):
        return remote.socket_respond(self._socket())(payload)

    def slack_reactions(self, payload):
        return remote.socket_slack_reactions(self._socket())(payload)

    def context(self, source, emails, domains):
        """A Context source's answer for these addresses, as the Console's ContextSection.fetch: `answer` is None
        when the source is not connected (or no Service answers), `error` says why a connected one failed."""
        if source not in ("attio", "tana", "linear"):
            raise ApiError(-32602, "source must be attio, tana or linear")
        try:
            socket = self._socket()
        except Exception:
            socket = None
        if not socket:
            return {"answer": None, "error": None}
        try:
            return {"answer": remote.socket_context(socket, source)(list(emails), list(domains)), "error": None}
        except remote.ReplyError as e:
            return {"answer": None, "error": str(e)}

    def context_section(self, source, emails, domains):
        """A Context source's section as the Console's ContextSection draws it: its title and segment lines; null
        when the source is not connected or no Service answers (the Console hides it then)."""
        from .tui import CONTEXT_SOURCES, ICON
        r = self.context(source, emails, domains)
        if r["answer"] is None and r["error"] is None:
            return None
        title, icon, markup = CONTEXT_SOURCES[source]
        return {"title": f"{ICON[icon]} {title.lower()}", "lines": segments(markup(r["answer"], r["error"], width=40))}

    def upload(self, owner_kind, owner_id, path):
        """Stage a file from this machine's disk on a Draft or Thread, through the Service's upload page (FR-A9)."""
        if owner_kind not in ("draft", "thread"):
            raise ApiError(-32602, "owner_kind must be draft or thread")
        data, name = Path(path).read_bytes(), Path(path).name
        url = self.call_from_thread(self._upload_url, (owner_kind, owner_id))
        boundary = uuid.uuid4().hex
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
                f"Content-Type: application/octet-stream\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(url, body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            out = json.loads(resp.read())
        self.publish("rows", {"changed": None, "cause": "upload"})
        return out

    def _upload_url(self, owner):
        target = self.proxies["files"] if self.proxies else files
        return target.upload_url(self.db, owner, *FILES)

    def _socket(self):
        return self.remote.link.command() if self.remote is not None else self.socket


def _rebase(syncer):
    """Load the Syncer's base again on the worker thread: SQLite refuses a connection from another thread, and a
    restarted core's Syncer read base.db on the main thread."""
    import sqlite3
    base = remote.mirror()
    saved = sqlite3.connect(syncer.base_path)
    try:
        saved.backup(base)
    finally:
        saved.close()
    syncer.base = base


SERVICE = {  # method -> (Core method, {param: (json type, required)}, summary)
    "send.draft": (Core.send_draft, {"draft_id": ("string", True), "revision": ("integer", True),
                                     "again": ("boolean", False)}, "Send a Draft now (Jeremy only)."),
    "send.reply": (Core.send_reply, {"thread_id": ("string", True), "text": ("string", True),
                                     "to": ("array", False), "cc": ("array", False)}, "Send a reply (Jeremy only)."),
    "respond": (Core.respond, {"payload": ("object", True)}, "Respond to an invite (FR-13e; Jeremy only)."),
    "slack.reactions": (Core.slack_reactions, {"payload": ("object", True)},
                        "Read, add or remove Jeremy's own Slack reaction (FR-R)."),
    "context": (Core.context, {"source": ("string", True), "emails": ("array", True), "domains": ("array", True)},
                "A Context source's read-only answer (Attio, Tana, Linear) for these addresses and domains."),
    "context.section": (Core.context_section, {"source": ("string", True), "emails": ("array", True),
                                                "domains": ("array", True)},
                        "A Context source's section (Attio, Tana, Linear) drawn as the Console draws it, or null."),
    "upload": (Core.upload, {"owner_kind": ("string", True), "owner_id": ("string", True), "path": ("string", True)},
               "Stage a file from this machine on a Draft or Thread."),
}
# Calls that wait on the Service or a remote server, or render a large body without the copy: each connection
# answers them on their own threads, so its other calls never queue behind them. Everything else keeps its order.
NETWORK = set(SERVICE) | {"message.body", "message.load_images"}


def _require(params: dict, spec: dict):
    missing, extra = set(spec) - set(params), set(params) - set(spec)
    if missing or extra:
        raise ApiError(-32602, f"expected params {sorted(spec)}, got {sorted(params)}")


def build() -> str:
    """The commit baked in at install (build_hook.py); '' when running from source."""
    try:
        return (resources.files("comms_terminal") / "BUILD").read_text().split()[1]
    except (OSError, IndexError):
        return ""


def fetch_image(url: str) -> str | None:
    """A remote image as a data: URI, fetched on Jeremy's request ("Load images"); None if it is not one."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": files.USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as resp:
            ctype = resp.headers.get_content_type()
            data = resp.read(MAX_IMAGE + 1)
    except (OSError, ValueError):
        return None
    if not ctype.startswith("image/") or len(data) > MAX_IMAGE:
        return None
    return f"data:{ctype};base64,{base64.b64encode(data).decode()}"


# --- the OpenRPC document ------------------------------------------------------------------------------------------

def _param(name, schema, required, summary=None):
    return {"name": name, "required": required, "schema": schema} | ({"summary": summary} if summary else {})


ORIGIN = _param("origin", {"enum": list(ORIGINS)}, False, "view or panel; panel when omitted")


def openrpc() -> dict:
    methods = [
        {"name": "rpc.discover", "summary": "This document.", "params": []},
        {"name": "core.status", "summary": "Online or read-only, builds, schema, pending writes, event sequence.",
         "params": []},
        {"name": "events.subscribe", "summary": "Status now, then events on this connection: rows, status, toast,"
                                                " update, heartbeat, missed.", "params": []},
        {"name": "message.body", "summary": "A message's HTML with every remote fetch removed (FR-7).",
         "params": [_param("raw", {"type": "string"}, True)]},
        {"name": "message.load_images", "summary": "The same, with remote images fetched by the core as data: URIs.",
         "params": [_param("raw", {"type": "string"}, True)]},
    ]
    for name, (_, spec, summary) in QUERIES.items():
        methods.append({"name": name, "summary": summary,
                        "params": [_param(p, {"type": t}, True) for p, t in spec.items()]})
    for name in writers.WRITERS:
        module, fn = name.split(".")
        params = []
        for p in list(inspect.signature(getattr(remote.MODULES[module], fn)).parameters.values())[1:]:
            if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
                continue
            params.append(_param(p.name, {}, p.default is p.empty))
        methods.append({"name": f"command.{name}", "summary": f"The {name} writer ({writers.WRITERS[name]}).",
                        "params": params, "x-panel": name not in VIEW_ONLY})
    for name, (_, spec, summary) in SERVICE.items():
        methods.append({"name": name, "summary": summary, "x-panel": False,
                        "params": [_param(p, {"type": t}, req) for p, (t, req) in spec.items()]})
    for m in methods:
        m["params"].append(ORIGIN)
        m["result"] = {"name": "result", "schema": {}}
    return {"openrpc": "1.3.2", "info": {"title": "Comms Terminal Local API", "version": f"{GENERATION}.0.0",
                                         "description": "ADR 0013. Values use remote.encode's tags ($dt, $dc, $tuple,"
                                                        " $bytes, $dict)."},
            "methods": sorted(methods, key=lambda m: m["name"])}


def breaks(released: dict, current: dict) -> list[str]:
    """What `current` breaks for Clients of `released`: a removed method, a removed parameter, or a parameter
    that is new and required or became required. Empty when current only grows (NFR-6)."""
    now = {m["name"]: {p["name"]: p["required"] for p in m["params"]} for m in current["methods"]}
    out = []
    for m in released["methods"]:
        if m["name"] not in now:
            out.append(f"{m['name']} removed")
            continue
        was = {p["name"]: p["required"] for p in m["params"]}
        out += [f"{m['name']} lost {p}" for p in was if p not in now[m["name"]]]
        out += [f"{m['name']} requires {p}" for p, req in now[m["name"]].items() if req and not was.get(p, False)]
    return out


# --- the socket ----------------------------------------------------------------------------------------------------

class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def serve(core: Core, path: Path) -> _Server:
    """The Local API on a user-only socket: the directory 0700, the socket 0600."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.unlink(missing_ok=True)
    # ponytail: one pool for every connection; per-source limits if Attio or Tana start refusing parallel lookups
    network = ThreadPoolExecutor(max_workers=8, thread_name_prefix="core-network")
    old = os.umask(0o177)
    try:
        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                lock = threading.Lock()

                def write(message):
                    with lock:
                        self.wfile.write(json.dumps(message).encode() + b"\n")
                        self.wfile.flush()
                def answer_later(req):
                    answer = core.handle(req, write)
                    if answer is not None:
                        write(answer)
                for line in self.rfile:
                    try:
                        req = json.loads(line)
                    except ValueError:
                        write({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
                        continue
                    if isinstance(req, dict) and req.get("method") in NETWORK:
                        # Answered by id when the Service replies; calls after it are not held up (NFR-APP-1).
                        network.submit(answer_later, req)
                        continue
                    answer = core.handle(req, write) if isinstance(req, dict) else \
                        {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
                    if answer is not None:
                        write(answer)
        return _Server(str(path), Handler)
    finally:
        os.umask(old)


def run(db_path: str, socket_path: str, ssh_args: str | None, listen: Path):
    """`comms-terminal core`: on a laptop copy with --remote (ADR 0012), else on the database file directly."""
    rem = syncer = None
    if ssh_args:
        from .main import start_remote
        _, rem, syncer = start_remote(ssh_args, db_path, console=False)
    core = Core(db_path, socket_path, rem, syncer)
    # COMMS_TERMINAL_SERVICE_NAME (the launcher sets "Oracle" for Jeremy's), else the ssh destination's host.
    core.service = os.environ.get("COMMS_TERMINAL_SERVICE_NAME") or (
        shlex.split(ssh_args)[-1].rsplit("@", 1)[-1] if ssh_args else None)
    if syncer is not None:
        syncer.start()
    server = serve(core, listen)
    print(f"Comms Terminal Local core on {listen}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        listen.unlink(missing_ok=True)
        if rem is not None:
            rem.drain(30)  # writes made just before a quit still reach the Service
            rem.link.stop()
