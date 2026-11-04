"""Pipelines (ADR 0006, .agents/prd/2026-10-23-pipeline.md): each Organization's Stage and Value in Revenue, each
Person's in Fundraising and Talent (ADR 0016), and the Bowtie math worked backwards from each Pipeline's Target.
A Pipeline's subject is what it holds: an Organization id in Revenue, a Person id in Fundraising and Talent.

Every Stage and Value change is appended to `pipeline_log`; the current Stage and Value are the latest entries
not undone. The math follows Winning by Design's "The Bowtie: A Proposed Standard" v1.0: conversion rates
(CR = output ÷ input) over the last 90 days, an Organization's highest Stage counting as passing every earlier
one, retention (GRR, CR7) and expansion (CR8) from Value changes, annualized, NRR = GRR × (1 + expansion), and
the backward plan from the Target. Each rate's Benchmark is the same rate over the last 2 years: Agent Maple's
own history, no external table. A rate with no 90-day data falls back to its Benchmark, marked as such.
Nothing here sets Tasks.

Interface: parse_pipeline, parse_stage, parse_amount, find_org, find_person, find_subject, set_stage, set_value, undo, set_target, target,
current, states, history, math, this_week, markdown.
"""

import math as _m
import re
from datetime import date, datetime, time, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo

from . import calendar as cal, clock, inbox
from .writers import writer

LOCAL = ZoneInfo("America/Halifax")  # Jeremy's days (main.LOCAL_TZ)
REVENUE, FUNDRAISING, TALENT = "REVENUE", "FUNDRAISING", "TALENT"
PIPELINES = (REVENUE, FUNDRAISING, TALENT)
MATH = (REVENUE, FUNDRAISING)  # Pipelines with a Target and the Bowtie math; Talent is a board only (FR-SEQ-42)
PEOPLE = (FUNDRAISING, TALENT)  # Pipelines that hold People (ADR 0016); Revenue holds Organizations
NOUN = {REVENUE: "Organizations", FUNDRAISING: "People", TALENT: "People"}
NAMES = {REVENUE: "Revenue", FUNDRAISING: "Fundraising", TALENT: "Talent"}
OPEN = {REVENUE: ("Awareness", "Education", "Prioritization", "Selection", "Commit", "Onboarding"),
        FUNDRAISING: ("Research", "Outreach", "Meeting", "Diligence", "Soft commit"),
        TALENT: ("Explore", "Role fit", "Offer", "Start")}
PAYING = {REVENUE: ("Impact", "Expansion"), FUNDRAISING: ("Closed",), TALENT: ("Joined",)}
ENDING = {REVENUE: ("Lost", "Churned"), FUNDRAISING: ("Passed",), TALENT: ("Lost",)}
STAGES = {p: OPEN[p] + PAYING[p] + ENDING[p] for p in PIPELINES}
# Rows after the Stage table (design § Pipeline); Impact and Expansion carry retention and expansion
END_ROWS = {REVENUE: (("Impact", ("Impact",)), ("Expansion", ("Expansion",)), ("Lost", ("Lost",)),
                      ("Churned", ("Churned",))),
            FUNDRAISING: (("Closed", ("Closed",)), ("Passed", ("Passed",))),
            TALENT: (("Joined", ("Joined",)), ("Lost", ("Lost",)))}
WINDOW = timedelta(days=90)  # the rates in use: the last 90 days
BENCH_WINDOW = timedelta(days=730)  # each rate's Benchmark: the same rate over the last 2 years


class Refused(ValueError):
    """A Pipeline change or input the rules do not allow; the message says why."""



def parse_pipeline(text: str) -> str:
    p = text.strip().upper()
    if p not in PIPELINES:
        raise Refused(f"no Pipeline {text!r}: revenue, fundraising, or talent")
    return p


def parse_stage(pipeline: str, text: str) -> str:
    for s in STAGES[pipeline]:
        if s.lower() == text.strip().lower():
            return s
    raise Refused(f"no {NAMES[pipeline]} Stage {text!r}: {', '.join(STAGES[pipeline])}")


def parse_amount(text) -> int:
    """`90000`, `90,000`, `90k`, `1.2m`, or `CAD 90k` as whole dollars."""
    if isinstance(text, int):
        n = text
    else:
        m = re.fullmatch(r"(?:cad\s*)?\$?\s*([\d.]+)\s*([km]?)", text.strip().lower().replace(",", ""))
        if not m:
            raise Refused(f"not an amount: {text!r} (try 90000 or 90k)")
        n = round(float(m[1]) * {"": 1, "k": 1_000, "m": 1_000_000}[m[2]])
    if n < 0:
        raise Refused("an amount cannot be negative")
    return n


def find_org(db, text: str):
    """An Organization by domain, else by exact name (any case)."""
    t = text.strip().lower()
    rows = db.execute("SELECT * FROM organizations WHERE domain = ?", (t,)).fetchall() or \
        db.execute("SELECT * FROM organizations WHERE lower(name) = ?", (t,)).fetchall()
    if len(rows) != 1:
        raise Refused(f"no Organization {text!r}" if not rows else f"{text!r} names {len(rows)} Organizations;"
                      " use the domain")
    return rows[0]


def find_person(db, text: str):
    """A Person by an address or number they write from, else by exact name (any case)."""
    t = text.strip().lower()
    rows = db.execute("SELECT DISTINCT p.* FROM persons p JOIN identities i ON i.person_id = p.id"
                      " WHERE lower(i.value) = ?", (t,)).fetchall() or \
        db.execute("SELECT * FROM persons WHERE lower(display_name) = ?", (t,)).fetchall()
    if len(rows) != 1:
        raise Refused(f"no Person {text!r}" if not rows else f"{text!r} names {len(rows)} People; use an address")
    return rows[0]


def find_subject(db, pipeline: str, text: str) -> dict:
    """What a Pipeline holds, by text: a Person in Fundraising and Talent, an Organization in Revenue; as
    {id, name, domain} (domain None for a Person)."""
    if pipeline in PEOPLE:
        r = find_person(db, text)
        return {"id": r["id"], "name": r["display_name"], "domain": None}
    r = find_org(db, text)
    return {"id": r["id"], "name": r["name"], "domain": r["domain"]}


def _column(pipeline: str) -> str:
    return "person_id" if pipeline in PEOPLE else "organization_id"


def _subject(e):
    return e["person_id"] if e["pipeline"] in PEOPLE else e["organization_id"]


def _effective(at, *, now: datetime | None = None) -> str:
    """Now, or a backdated date or time; a future one is refused."""
    if at is None:
        return clock.iso((now or clock.now()))
    if isinstance(at, str):
        at = date.fromisoformat(at) if len(at) == 10 else datetime.fromisoformat(at)
    if not isinstance(at, datetime):
        at = datetime.combine(at, time(), LOCAL)
    if at > (now or clock.now()):
        raise Refused(f"{at.date()} is in the future")
    return clock.iso(at.astimezone(timezone.utc))


def _append(db, subject_id, pipeline, kind, at=None, stage=None, value=None, undoes=None, *, now: datetime | None = None) -> int:
    with inbox._write_snapshot(db):
        return db.execute(
            f"INSERT INTO pipeline_log ({_column(pipeline)}, pipeline, kind, stage, value, undoes, effective_at,"
            " recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (subject_id, pipeline, kind, stage, value, undoes, _effective(at, now=now), clock.iso((now or clock.now())))).fetchone()[0]


def _held(db, subject_id, pipeline: str) -> dict:
    """The subject's {stage, since, value} in one Pipeline, or {}."""
    return _state(_entries(db, pipeline, **{_column(pipeline): subject_id})).get((subject_id, pipeline), {})


@writer("create")
def set_stage(db, org_id: int, pipeline: str, stage: str, at=None, *, now: datetime | None = None) -> int | None:
    """Move an Organization (Revenue) or a Person (Fundraising, Talent) to a Stage; returns the log entry, or None
    when it is already there. `org_id` is the Pipeline's subject, a Person's id in Fundraising and Talent: the
    Local API only grows (NFR-6), so the name stays."""
    stage = parse_stage(pipeline, stage)
    if _held(db, org_id, pipeline).get("stage") == stage:
        return None
    return _append(db, org_id, pipeline, "STAGE", at, stage=stage, now=now)


@writer("create")
def set_value(db, org_id: int, pipeline: str, value, at=None, *, now: datetime | None = None) -> int | None:
    """Set an Organization's Revenue Value or a Person's Fundraising Value; it belongs to the Pipeline, not the Stage.
    `org_id` is the Pipeline's subject, as in set_stage."""
    value = parse_amount(value)
    if _held(db, org_id, pipeline).get("value") == value:
        return None
    return _append(db, org_id, pipeline, "VALUE", at, value=value, now=now)


@writer("write")
def undo(db, entry_id: int, *, now: datetime | None = None) -> None:
    """Cancel one Stage or Value entry; the math and history skip both (FR-PL-4)."""
    e = db.execute("SELECT * FROM pipeline_log WHERE id = ?", (entry_id,)).fetchone()
    if e is None or e["kind"] == "UNDO":
        raise Refused("nothing to undo")
    if db.execute("SELECT 1 FROM pipeline_log WHERE undoes = ?", (entry_id,)).fetchone():
        raise Refused("already undone")
    _append(db, _subject(e), e["pipeline"], "UNDO", undoes=entry_id, now=now)


def set_target(db, pipeline: str, amount, due) -> None:
    if pipeline not in MATH:
        raise Refused(f"{NAMES[pipeline]} has no Target")
    due = date.fromisoformat(due) if isinstance(due, str) else due
    with db:
        db.execute("INSERT OR REPLACE INTO pipeline_targets (pipeline, amount, due) VALUES (?, ?, ?)",
                   (pipeline, parse_amount(amount), due.isoformat()))


def target(db, pipeline: str) -> dict | None:
    r = db.execute("SELECT amount, due FROM pipeline_targets WHERE pipeline = ?", (pipeline,)).fetchone()
    return {"amount": r["amount"], "due": date.fromisoformat(r["due"])} if r else None


def _entries(db, pipeline=None, organization_id=None, person_id=None) -> list:
    """Stage and Value entries not undone, oldest first."""
    sql = ("SELECT * FROM pipeline_log l WHERE kind != 'UNDO'"
           " AND NOT EXISTS (SELECT 1 FROM pipeline_log u WHERE u.undoes = l.id)")
    args = []
    if pipeline:
        sql, args = sql + " AND pipeline = ?", args + [pipeline]
    if organization_id is not None:
        sql, args = sql + " AND organization_id = ?", args + [organization_id]
    if person_id is not None:
        sql, args = sql + " AND person_id = ?", args + [person_id]
    return db.execute(sql + " ORDER BY effective_at, id", args).fetchall()


def _state(rows) -> dict:
    """{(subject, pipeline): {stage, since, value}} from entries oldest first."""
    out = {}
    for e in rows:
        s = out.setdefault((_subject(e), e["pipeline"]), {"stage": None, "since": None, "value": None})
        if e["kind"] == "STAGE":
            s["stage"], s["since"] = e["stage"], datetime.fromisoformat(e["effective_at"])
        else:
            s["value"] = e["value"]
    return out


def current(db, org_id: int | None = None, *, person_id: int | None = None) -> dict:
    """{pipeline: {stage, since, value}} for each Pipeline the Organization (Revenue) or Person (Fundraising,
    Talent) has an entry in."""
    return {p: s for (_, p), s in _state(_entries(db, organization_id=org_id, person_id=person_id)).items()}


def states(db, people: bool = False) -> dict:
    """{id: {pipeline: {stage, since, value}}} for every Organization with an entry (Organizations list), or with
    people=True every Person."""
    out = {}
    for (o, p), s in _state(_entries(db)).items():
        if (p in PEOPLE) == people:
            out.setdefault(o, {})[p] = s
    return out


def history(db, org_id: int | None = None, *, person_id: int | None = None) -> list[dict]:
    """Stage and Value changes, newest first, each with what it replaced."""
    out, last = [], {}
    for e in _entries(db, organization_id=org_id, person_id=person_id):
        key = (e["pipeline"], e["kind"])
        new = e["stage"] if e["kind"] == "STAGE" else e["value"]
        out.append({"id": e["id"], "pipeline": e["pipeline"], "kind": e["kind"], "old": last.get(key), "new": new,
                    "at": datetime.fromisoformat(e["effective_at"])})
        last[key] = new
    return out[::-1]


# --- math (FR-PL-6..14) ----------------------------------------------------------

def _chain(pipeline):
    """The Stages a customer passes, ending at the first paying Stage."""
    return OPEN[pipeline] + PAYING[pipeline][:1]


def _index(pipeline, stage):
    chain = _chain(pipeline)
    if stage in chain:
        return chain.index(stage)
    if stage in PAYING[pipeline] or stage == "Churned":  # paid at some point
        return len(chain) - 1
    return None  # Lost, Passed


def _deadline(pipeline, due: date) -> date:
    """Revenue's Target is TTM revenue at `due`, so new customers must pay from the window's first day."""
    if pipeline != REVENUE:
        return due
    try:
        return due.replace(year=due.year - 1) + timedelta(days=1)
    except ValueError:  # 29 February
        return due.replace(year=due.year - 1, day=28) + timedelta(days=1)


ROLLUP = ("Todo", "Snoozed", "Done")  # an Organization's roll-up order: an Ignored Thread holds nothing open


def attention(db, people: bool = False) -> dict:
    """Each Organization in a Pipeline, or with people=True each Person in one, read from its People's Threads and
    Enrollments (FR-OA-4, FR-OA-5; never stored): {id: {rollup, wakes, last, unattended, stages}}.

    rollup: the earliest of Todo, Snoozed, Done over the Threads its People wrote in or were addressed in (a
    Person's own), and the Enrollments for it (FR-SEQ-38): an Active or Finishing one counts as Snoozed until its
    next Task, a Paused one as Todo (it waits for Jeremy); an Enrollment that names no Organization counts for each
    of its Person's; a Person counts all of theirs. Done with no Threads. wakes: the earliest time a Snoozed one
    comes back (None for Todo, Done, or a Snooze with no date). last: the latest message in those Threads.
    unattended: in an open or paying Stage, Done, and with none of its People on an upcoming Event (FR-OA-5).
    stages: {pipeline: Stage}."""
    held = {}
    for (o, p), st in _state(_entries(db)).items():
        if st["stage"] and (p in PEOPLE) == people:
            held.setdefault(o, {})[p] = st["stage"]
    if not held:
        return {}
    members, threads = inbox._members(db), inbox.person_thread_ids(db)
    status = {r["id"]: r for r in db.execute(  # one grouped scan: a subquery per Thread on the view took 390 ms live
        "SELECT t.id, t.status_detail, COALESCE(t.follow_up_at, t.snoozed_until) AS wakes, m.last FROM threads t"
        " LEFT JOIN (SELECT thread_id, max(occurred_at) AS last FROM thread_activities WHERE kind = 'MESSAGE'"
        " GROUP BY thread_id) m ON m.thread_id = t.id WHERE t.merged_into IS NULL")}
    by_org: dict[int, list] = {}  # org -> (state, next Task due) of each unfinished Enrollment for it
    by_person: dict[int, list] = {}  # the same, for Enrollments that name no Organization
    every: dict[int, list] = {}  # the same, for every Enrollment of a Person
    for pid, org, state, due in db.execute(
            "SELECT e.person_id, e.organization_id, e.state, (SELECT min(due_at) FROM tasks t WHERE t.enrollment_id = e.id"
            " AND t.state = 'DUE') FROM enrollments e WHERE e.state != 'FINISHED'"):
        (by_org.setdefault(org, []) if org is not None else by_person.setdefault(pid, [])).append((state, due))
        every.setdefault(pid, []).append((state, due))
    if people:
        subjects = {s: ({s}, every.get(s, [])) for s in held}
    else:
        subjects = {}
        for o, domain in db.execute("SELECT id, domain FROM organizations"):
            if o in held:
                pids = members.get(domain, set())
                subjects[o] = (pids, by_org.get(o, []) + [r for p in pids for r in by_person.get(p, [])])
    meeting = _meeting_people(db)
    out = {}
    for o, (pids, runs) in subjects.items():
        rows = [status[t] for t in set().union(*(threads.get(p, set()) for p in pids)) if t in status]
        found = {r["status_detail"] for r in rows} | {"NEW_REPLY" if st == "PAUSED" else "WAITING_FOR_DURATION"
                                                       for st, _ in runs}
        rollup = inbox.rollup(found, ROLLUP, "Done")
        snoozed = inbox.VIEWS["Snoozed"]
        wakes = [datetime.fromisoformat(r["wakes"]) for r in rows if r["status_detail"] in snoozed and r["wakes"]] + \
            [datetime.fromisoformat(d) for st, d in runs if d and st != "PAUSED"]
        last = [r["last"] for r in rows if r["last"]]
        live = any(st in OPEN[p] + PAYING[p] for p, st in held[o].items())
        out[o] = {"rollup": rollup, "wakes": min(wakes) if rollup == "Snoozed" and wakes else None,
                  "last": datetime.fromisoformat(max(last, key=datetime.fromisoformat)) if last else None,
                  "unattended": live and rollup == "Done" and not pids & meeting, "stages": held[o]}
    return out


def _meeting_people(db) -> set[int]:
    """People on an Event that has not ended, within a year, as organizer or a guest who has not declined; Events
    Jeremy declined and Deleted Events do not count. Read from the calendar, so Events booked before go-live count."""
    now = clock.local()
    emails = set()
    for e in cal.expand(db, now.date() - timedelta(days=1), now.date() + timedelta(days=366), deleted=False):
        if e["state"] != "DECLINED" and clock.local(e["end"]) > now:
            emails |= {a["email"] for a in e["attendees"] if a["state"] != "DECLINED"}
            emails |= {e["organizer"]["email"]} if e["organizer"] else set()
    if not emails:
        return set()
    return {r[0] for r in db.execute(f"SELECT person_id FROM identities WHERE kind = 'email' AND person_id IS NOT NULL"
                                     f" AND value IN ({','.join('?' * len(emails))})", list(emails))}


def _subjects(db, people: bool) -> dict:
    """{id: {id, name, domain, organizations}} for every Organization, or every Person with their Organizations'
    names by Membership (Sole proprietorships aside) and domain None."""
    if not people:
        return {r["id"]: {"id": r["id"], "name": r["name"], "domain": r["domain"], "organizations": []}
                for r in db.execute("SELECT id, name, domain FROM organizations")}
    names = dict(db.execute("SELECT domain, name FROM organizations").fetchall())
    orgs: dict = {}
    for domain, pids in inbox._members(db).items():
        if domain in names and not inbox.is_sole_key(domain):
            for pid in pids:
                orgs.setdefault(pid, []).append(names[domain])
    return {r["id"]: {"id": r["id"], "name": r["display_name"], "domain": None,
                      "organizations": sorted(orgs.get(r["id"], []), key=str.lower)}
            for r in db.execute("SELECT id, display_name FROM persons")}


def label(s: dict) -> str:
    """How a Pipeline's subject reads in Markdown: 'Acme (acme.com)' for an Organization, 'Laïla Thériault (NBIF)'
    or 'Ana' for a Person."""
    if s["domain"] is not None:
        return f"{s['name']} ({s['domain']})"
    return f"{s['name']} ({', '.join(s['organizations'])})" if s["organizations"] else s["name"]


def unattended(db) -> list[dict]:
    """Organizations and People in an open or paying Stage with nothing open (Today's group), by name: id, domain
    (None for a Person), name, person_id (a Person's id, else None), organizations, stages, last."""
    out = []
    for people in (False, True):
        att = attention(db, people)
        found = {k: v for k, v in att.items() if v["unattended"]}
        subjects = _subjects(db, people) if found else {}
        out += [{**subjects[k], "person_id": k if people else None, "stages": a["stages"], "last": a["last"]}
                for k, a in found.items() if k in subjects]
    return sorted(out, key=lambda o: o["name"].lower())


def _measure(pipeline, rows, reached, ends, now, window):
    """Rates, Times in Stage, weekly entries, and annualized GRR and expansion for Organizations active in the
    window: (rates, times, entries, grr, expansion)."""
    opens, start = OPEN[pipeline], now - window
    cohort = {_subject(e) for e in rows if datetime.fromisoformat(e["effective_at"]) >= start} & set(reached)

    def top(o):
        return max(reached[o]) if reached[o] else -1

    rates, times, entries = {}, {}, {}
    for i, stage in enumerate(opens):
        at_i = [o for o in cohort if top(o) >= i]
        rates[stage] = sum(top(o) >= i + 1 for o in at_i) / len(at_i) if at_i else None
        days = []
        for o in at_i:
            t0 = reached[o][i]
            t1 = reached[o].get(i + 1)
            if t1 is None:  # still there, or left the chain (Lost) from here
                t1 = ends[o] if ends.get(o, t0) > t0 else now
            span = (t1 - t0).total_seconds() / 86400
            skipped = i + 1 in reached[o] and reached[o][i + 1] == t0
            days.append(0 if skipped else max(1, span))
        times[stage] = median(days) if days else None
        entries[stage] = sum(1 for o in reached if i in reached[o] and reached[o][i] >= start) / (window.days / 7)

    # Retention (CR7, GRR) and expansion (CR8): Value held by customers paying at the window's start against
    # what they hold now; churned or lost customers hold nothing (the standard's p. 21). Both are annualized
    # so a 90-day and a 2-year rate compare, and NRR compounds by year.
    state = {o: s for (o, _), s in _state(rows).items()}
    then = _state([e for e in rows if datetime.fromisoformat(e["effective_at"]) <= start])
    base = retained = grown = 0
    for (o, _), st in then.items():
        if st["stage"] in PAYING[pipeline] and st["value"]:
            now_st = state.get(o, {})
            v = now_st.get("value") or 0 if now_st.get("stage") in PAYING[pipeline] else 0
            base, retained, grown = base + st["value"], retained + min(v, st["value"]), grown + max(0, v - st["value"])
    year = 365 / window.days
    grr = (retained / base) ** year if base else None
    expansion = (1 + grown / retained) ** year - 1 if retained else None
    return rates, times, entries, grr, expansion


def math(db, pipeline: str, now: datetime | None = None, orgs: bool = True) -> dict:
    """Everything a Pipeline page shows. A pure function of the log, Target, and `now`.
    `orgs=False` skips each Organization's row details (the rail needs only the summary)."""
    now = now or clock.now()
    today = now.astimezone(LOCAL).date()
    chain, opens = _chain(pipeline), OPEN[pipeline]
    rows = _entries(db, pipeline)
    state = {o: s for (o, _), s in _state(rows).items() if s["stage"]}
    want_rows, people = orgs, pipeline in PEOPLE
    orgs = _subjects(db, people) if want_rows else {}
    att = attention(db, people) if want_rows and state else {}

    # When each Organization first reached each chain Stage; a jump reaches the skipped Stages at the same time
    reached, ends = {}, {}  # org -> {index: time}; org -> time it left the chain (Lost, Passed)
    for e in rows:
        if e["kind"] != "STAGE":
            continue
        o, at, i = _subject(e), datetime.fromisoformat(e["effective_at"]), _index(pipeline, e["stage"])
        r = reached.setdefault(o, {})
        if i is None:
            ends.setdefault(o, at)
            continue
        for j in range(i + 1):
            r.setdefault(j, at)
    rates, times, entries, grr, expansion = _measure(pipeline, rows, reached, ends, now, WINDOW)
    b_rates, _, _, b_grr, b_expansion = _measure(pipeline, rows, reached, ends, now, BENCH_WINDOW)
    bench = {k: v for k, v in (b_rates | {"GRR": b_grr, "Expansion": b_expansion}).items() if v is not None}

    def product(stages, rate_of):
        p = 1.0
        for s in stages:
            r = rate_of(s)
            if r is None:
                return None
            p *= r
        return p

    to_paying = {s: product(opens[i:], rates.get) for i, s in enumerate(opens)}

    paying = [o for o, st in state.items() if st["stage"] in PAYING[pipeline]]
    paying_value = sum(state[o]["value"] or 0 for o in paying)
    average = paying_value / len(paying) if paying and paying_value else None
    used = {s: (rates[s], False) if rates[s] is not None else (bench.get(s), s in bench) for s in opens}
    used["GRR"] = (grr, False) if grr is not None else (bench.get("GRR"), "GRR" in bench)
    used["Expansion"] = (expansion, False) if expansion is not None else (bench.get("Expansion"), "Expansion" in bench)
    nrr = used["GRR"][0] * (1 + used["Expansion"][0]) if used["GRR"][0] is not None and \
        used["Expansion"][0] is not None else None

    weighted = sum((state[o]["value"] or 0) * (to_paying[state[o]["stage"]] or 0)
                   for o in state if state[o]["stage"] in opens)
    goal = target(db, pipeline)
    summary = {"target": goal, "paying": paying_value, "paying_count": len(paying), "weighted": weighted,
               "average": average, "grr": used["GRR"], "expansion": used["Expansion"], "nrr": nrr,
               "nrr_benchmark": used["GRR"][1] or used["Expansion"][1], "kept": paying_value,
               "projected": paying_value + weighted}
    need = {s: None for s in opens}
    if goal:
        deadline = _deadline(pipeline, goal["due"])
        # Value is next-12-month revenue: the window's year is that times NRR for each year until it starts
        years = max(0.0, (deadline - today).days / 365)
        kept = paying_value * nrr ** years if nrr is not None and pipeline == REVENUE else paying_value
        projected = kept + weighted
        gap = max(0, goal["amount"] - projected)
        customers = _m.ceil(gap / average) if average else None
        weeks = (deadline - today).days / 7
        new = goal["amount"] - kept  # what open and future Organizations must add
        summary.update(kept=kept, projected=projected, gap=gap, customers=customers, deadline=deadline,
                       weeks=weeks, years=years, share=projected / goal["amount"], new=max(0, new),
                       coverage=weighted / new if new > 0 else None)
        for i, s in enumerate(opens):
            through = product(opens[i:], lambda x: used[x][0])
            ahead = sum(times[x] if times[x] is not None else 1 for x in opens[i:]) / 7
            if customers is None or not through:
                continue
            need[s] = "late" if weeks - ahead <= 0 else customers / through / (weeks - ahead)

    # The constraint: the lever with the largest multiplier on what reaches the paying Stage. The rates multiply,
    # so a 10% lift anywhere adds the same 10%; what differs is the lift on offer. A 90-day rate below its
    # 2-year Benchmark offers Benchmark ÷ rate (expansion: (1 + Benchmark) ÷ (1 + rate)); new Organizations at
    # the top Stage below Need offer Need ÷ Getting. None when every lever is at or above its mark.
    measured = [(s, rates[s]) for s in opens if rates[s] is not None] + \
        [(k, v) for k, v in (("GRR", grr), ("Expansion", expansion)) if v is not None]

    def lift(have, want):
        return want / have if have else _m.inf

    top_need = need[opens[0]]
    levers = [(lift(entries[opens[0]], top_need), "volume", opens[0])] \
        if isinstance(top_need, float) and entries[opens[0]] < top_need else []
    levers += [(lift(1 + r, 1 + bench[k]) if k == "Expansion" else lift(r, bench[k]), "rate", k)
               for k, r in measured if bench.get(k) and r < bench[k]]
    best = max(levers, key=lambda x: x[0]) if levers else None  # max keeps the first of a tie: upstream wins
    constraint = best and best[2]
    nxt = {s: chain[opens.index(s) + 1] for s in opens} | {"GRR": "retained", "Expansion": "expanded"}
    summary["constraint"] = best and (
        {"kind": "volume", "stage": constraint, "next": nxt[constraint], "getting": entries[constraint],
         "need": top_need, "lift": best[0]} if best[1] == "volume" else
        {"kind": "rate", "stage": constraint, "next": nxt[constraint], "rate": dict(measured)[constraint],
         "benchmark": bench[constraint], "lift": best[0]})

    def org_row(o):
        s = state[o]
        a = att.get(o, {})
        return {"id": o, "domain": orgs[o]["domain"], "name": orgs[o]["name"], "person": people,
                "organizations": orgs[o]["organizations"], "value": s["value"],
                "days": (now - s["since"]).days, "last_message": a.get("last"), "rollup": a.get("rollup"),
                "wakes": a.get("wakes"), "unattended": a.get("unattended", False)}

    def members(stages):
        if not want_rows:
            return [{"id": o} for o in state if state[o]["stage"] in stages]
        return sorted((org_row(o) for o in state if state[o]["stage"] in stages), key=lambda r: (-r["days"], r["name"]))

    stages = [{"stage": s, "orgs": members((s,)), "rate": rates[s], "benchmark": bench.get(s),
               "used": used[s][0], "assumed": used[s][1], "time": times[s], "to_paying": to_paying[s],
               "entries": entries[s], "need": need[s],
               "pace": entries[s] - need[s] if isinstance(need[s], float) else None,
               "constraint": s == constraint} for s in opens]
    right = {"Impact": "GRR", "Expansion": "Expansion"}  # rows that carry retention and expansion
    ending = [{"stage": label, "orgs": members(group), "measure": right.get(label),
               "rate": dict(measured).get(right.get(label)), "benchmark": bench.get(right.get(label)),
               "constraint": right.get(label) == constraint and constraint is not None}
              for label, group in END_ROWS[pipeline]]
    return {"pipeline": pipeline, "name": NAMES[pipeline], "noun": NOUN[pipeline], "paying_stage": chain[-1], "now": now,
            "summary": summary, "stages": stages, "ending": ending}


# --- display ---------------------------------------------------------------------

def money(n) -> str:
    """`CAD 180k`-style short amounts for tables."""
    if n is None:
        return "—"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}".rstrip("0").rstrip(".") + "M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return f"{n:.0f}"


def pct(r) -> str:
    return "—" if r is None else f"{r * 100:.0f}%"


def weeks(days) -> str:
    return "—" if days is None else f"{days / 7:.0f}w" if days >= 7 else f"{days:.0f}d"


def num(x) -> str:
    if x is None:
        return "—"
    if isinstance(x, str):
        return x
    if round(x, 1) == 0:
        return "0"
    return f"{x:.0f}" if abs(x) >= 10 else f"{x:.1f}"


def signed(x) -> str:
    return "—" if x is None else ("+" if x >= 0 else "-") + num(abs(x))


def rate_text(rate, used, assumed) -> str:
    """A 90-day rate, else its 2-year Benchmark marked `b`, else `—`."""
    return pct(rate) if rate is not None else f"{pct(used)}b" if assumed and used is not None else "—"


def marked(pair) -> str:
    """(rate, from a 2-year Benchmark) as `96%` or `96%b`."""
    rate, assumed = pair
    return pct(rate) + ("b" if assumed and rate is not None else "")


def constraint_text(c, noun: str = "Organizations") -> str:
    if c["kind"] == "volume":
        return f"New {c['stage']} {noun}: {num(c['getting'])} a week, need {num(c['need'])}"
    name = {"GRR": "Retention (GRR)", "Expansion": "Expansion"}.get(c["stage"], f"{c['stage']} → {c['next']}")
    return f"{name}: {pct(c['rate'])} vs {pct(c['benchmark'])} benchmark"


def names(orgs, most=3) -> str:
    """'A, B and C', or 'A, B, C and 2 more'."""
    n = [o["name"] for o in orgs]
    if len(n) > most:
        n = n[:most] + [f"{len(orgs) - most} more"]
    return n[0] if len(n) == 1 else ", ".join(n[:-1]) + " and " + n[-1]


def on_track(m: dict) -> tuple[str, str] | None:
    """The verdict on the Target: the top Stage's Pace, since the backward plan already counts the open
    Organizations and the rates below it. None without a Target."""
    s, top, noun = m["summary"], m["stages"][0], NOUN[m["pipeline"]]
    if not s["target"]:
        return None
    if s["gap"] == 0:
        return "On track", f"customers and open {noun} cover the Target"
    if top["need"] == "late":
        return "Off track", f"new {top['stage']} {noun} can no longer reach {m['paying_stage']} by {s['deadline']}"
    if top["pace"] is None:
        return "Pace unknown", "not enough Stage history or Value yet"
    verdict = "On pace" if top["pace"] >= 0 else "Behind pace"
    return verdict, f"need {num(top['need'])} new {top['stage']} {noun} a week, getting {num(top['entries'])}"


def this_week(m: dict) -> list[tuple[str, str]]:
    """What the math says to do this week, as (action, why) pairs in plain words (never Tasks): the
    constraint's lever, the input the backward plan needs at the top Stage, and Organizations or People the math
    cannot count."""
    s, out, noun = m["summary"], [], NOUN[m["pipeline"]]
    if not s["target"]:
        out.append(("Set a Target", f"comms-terminal pipeline target {m['name'].lower()} AMOUNT --by DATE"))
    c = s["constraint"]
    vs = f" (benchmark {pct(c['benchmark'])})" if c and c["kind"] == "rate" else ""
    if c and c["kind"] == "volume":
        pass  # the top Stage's Add line below is the constraint's lever
    elif c and c["stage"] == "GRR":
        out.append(("Protect retention", f"customers kept {pct(c['rate'])} of their Value{vs}"))
    elif c and c["stage"] == "Expansion":
        out.append(("Grow existing customers", f"expansion {pct(c['rate'])}{vs}"))
    elif c:
        row = next(r for r in m["stages"] if r["stage"] == c["stage"])
        out.append((f"Move {names(row['orgs'])}", f"{c['stage']} → {c['next']}, the constraint") if row["orgs"]
                   else (f"Find {noun} for {c['stage']}", f"it is the constraint: {pct(c['rate'])} move on{vs}"))
    top = m["stages"][0]
    if top["need"] == "late":
        out.append((f"Too late for new {top['stage']} {noun}",
                    f"they can no longer reach {m['paying_stage']} by {s['deadline']}"))
    elif isinstance(top["need"], float) and top["pace"] < 0:
        out.append((f"Add {_m.ceil(top['need'] - top['entries'])} {noun} to {top['stage']}",
                    f"need {num(top['need'])} a week, getting {num(top['entries'])}"
                    + (", the constraint" if c and c["kind"] == "volume" else "")))
    live = m["stages"] + [r for r in m["ending"] if r["stage"] not in ENDING[m["pipeline"]]]  # not Lost, Churned
    missing = [o for r in live for o in r["orgs"] if o.get("value") is None and "name" in o]
    if missing:
        out.append((f"Set a Value for {names(missing)}", "the math leaves them out until then"))
    return out


def board_markdown(db, pipeline: str, now: datetime | None = None) -> str:
    """A Pipeline with no math (Talent, FR-SEQ-42): its People (or Organizations) under each Stage, longest in Stage
    first."""
    now = now or clock.now()
    names = {k: s["name"] if s["domain"] else label(s) for k, s in _subjects(db, pipeline in PEOPLE).items()}
    held = {o: st for (o, p), st in _state(_entries(db, pipeline)).items() if st["stage"]}
    lines = [f"# {NAMES[pipeline]} Pipeline", ""]
    for stage in STAGES[pipeline]:
        here = sorted((st["since"], names[o]) for o, st in held.items() if st["stage"] == stage)
        lines.append(f"## {stage} ({len(here)})")
        lines += [f"- {name}: {(now - since).days}d in Stage" for since, name in here] or ["- none"]
        lines.append("")
    return "\n".join(lines)


def markdown(m: dict) -> str:
    """The Pipeline page as Markdown, for `y` and `comms-terminal pipeline` (FR-PL-31)."""
    s, name = m["summary"], m["name"]
    verdict = on_track(m)
    lines = [f"# {name} Pipeline", ""] + ([f"**{verdict[0]}**: {verdict[1]}", ""] if verdict else []) + ["This week:"] + [f"- {a} ({why})" for a, why in this_week(m)] + [""]
    t = s["target"]
    if not t:
        lines.append(f"No Target yet: comms-terminal pipeline target {name.lower()} AMOUNT --by DATE")
    else:
        what = "TTM revenue" if m["pipeline"] == REVENUE else "round"
        lines += [f"- Target: CAD {t['amount']:,} {what} by {t['due']}",
                  f"- Projected: CAD {s['projected']:,.0f} ({s['share'] * 100:.0f}%): paying CAD {s['paying']:,}"
                  f" ({s['paying_count']}) → CAD {s['kept']:,.0f} in the window, weighted new CAD {s['weighted']:,.0f}",
                  f"- Coverage: weighted open {m['noun']} cover {pct(s['coverage'])} of the CAD {s['new']:,.0f}"
                  f" still to add",
                  f"- Gap: CAD {s['gap']:,.0f}"
                  + (f" = {s['customers']} new at CAD {s['average']:,.0f}" if s["customers"] is not None else ""),
                  f"- Must reach {m['paying_stage']} by {s['deadline']} ({s['weeks']:.0f} weeks)"]
    if m["pipeline"] == REVENUE:
        b = "b" if s["nrr_benchmark"] else ""
        lines.append(f"- Retention: GRR {marked(s['grr'])}, expansion {marked(s['expansion'])}, NRR {pct(s['nrr'])}{b}")
    if s["constraint"]:
        lines.append(f"- Constraint: {constraint_text(s['constraint'], m['noun'])}")
    lines += ["", f"| Stage | {'Orgs' if m['pipeline'] == REVENUE else m['noun']} | CR | bench | Time | →{m['paying_stage']} | in/wk | need/wk | pace |",
              "|---|---|---|---|---|---|---|---|---|"]
    for r in m["stages"]:
        lines.append(f"| {r['stage']}{' ◀ constraint' if r['constraint'] else ''} | {len(r['orgs'])} |"
                     f" {rate_text(r['rate'], r['used'], r['assumed'])} | {pct(r['benchmark'])} |"
                     f" {weeks(r['time'])} | {pct(r['to_paying'])} | {num(r['entries'])} | {num(r['need'])} |"
                     f" {signed(r['pace'])} |")
    for r in m["ending"]:
        measure = {"GRR": "GRR ", "Expansion": "expansion "}.get(r["measure"], "")
        rate = f"{measure}{pct(r['rate'])}" if r["measure"] else ""
        bench = pct(r["benchmark"]) if r["measure"] else ""
        lines.append(f"| {r['stage']}{' ◀ constraint' if r['constraint'] else ''} | {len(r['orgs'])} | {rate} |"
                     f" {bench} | | | | | |")
    lines += ["", "CR last 90 days · bench the same rate over 2 years · b a 2-year rate in use · — no data", ""]
    for r in m["stages"] + m["ending"]:
        if r["orgs"]:
            lines.append(f"## {r['stage']}")
            lines += [f"- {label(o)}: CAD {money(o['value'])}, {o['days']}d in Stage" for o in r["orgs"]]
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
