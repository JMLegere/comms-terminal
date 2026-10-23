"""Pipelines (ADR 0006, .agents/prd/2026-10-23-pipeline.md): each Organization's Stage and Value in Revenue and
Fundraising, and the Bowtie math worked backwards from each Pipeline's Target.

Every Stage and Value change is appended to `pipeline_log`; the current Stage and Value are the latest entries
not undone. Rates follow Metabase's method (BI cards 59, 60, 86): a trailing 90-day cohort, an Organization's
highest Stage counts as passing every earlier one, and a rate with no data is None (shown `—`) with no fallback.
Planning priors stand in only for Required volume and the Constraint. Nothing here sets Tasks.

Interface: parse_pipeline, parse_stage, parse_amount, find_org, set_stage, set_value, undo, set_target, target,
set_prior, priors, current, history, math, markdown.
"""

import math as _m
import re
from datetime import date, datetime, time, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo

from . import inbox

LOCAL = ZoneInfo("America/Halifax")  # Jeremy's days (main.LOCAL_TZ)
REVENUE, FUNDRAISING = "REVENUE", "FUNDRAISING"
PIPELINES = (REVENUE, FUNDRAISING)
NAMES = {REVENUE: "Revenue", FUNDRAISING: "Fundraising"}
OPEN = {REVENUE: ("Awareness", "Education", "Prioritization", "Selection", "Commit", "Onboarding"),
        FUNDRAISING: ("Research", "Outreach", "Meeting", "Diligence", "Soft commit")}
PAYING = {REVENUE: ("Impact", "Expansion"), FUNDRAISING: ("Closed",)}
ENDING = {REVENUE: ("Lost", "Churned"), FUNDRAISING: ("Passed",)}
STAGES = {p: OPEN[p] + PAYING[p] + ENDING[p] for p in PIPELINES}
# Rows after the Stage table: counts only (design § Pipeline)
END_ROWS = {REVENUE: (("Impact/Expansion", ("Impact", "Expansion")), ("Lost", ("Lost",)), ("Churned", ("Churned",))),
            FUNDRAISING: (("Closed", ("Closed",)), ("Passed", ("Passed",)))}
WINDOW = timedelta(days=90)


class Refused(ValueError):
    """A Pipeline change or input the rules do not allow; the message says why."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _tx(db):
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")


def parse_pipeline(text: str) -> str:
    p = text.strip().upper()
    if p not in PIPELINES:
        raise Refused(f"no Pipeline {text!r}: revenue or fundraising")
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


def _effective(at) -> str:
    """Now, or a backdated date or time; a future one is refused."""
    if at is None:
        return inbox._iso(_now())
    if isinstance(at, str):
        at = date.fromisoformat(at) if len(at) == 10 else datetime.fromisoformat(at)
    if not isinstance(at, datetime):
        at = datetime.combine(at, time(), LOCAL)
    if at > _now():
        raise Refused(f"{at.date()} is in the future")
    return inbox._iso(at.astimezone(timezone.utc))


def _append(db, org_id, pipeline, kind, at=None, stage=None, value=None, undoes=None) -> int:
    with db:
        _tx(db)
        return db.execute(
            "INSERT INTO pipeline_log (organization_id, pipeline, kind, stage, value, undoes, effective_at,"
            " recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (org_id, pipeline, kind, stage, value, undoes, _effective(at), inbox._iso(_now()))).lastrowid


def set_stage(db, org_id: int, pipeline: str, stage: str, at=None) -> int | None:
    """Move an Organization to a Stage; returns the log entry, or None when it is already there."""
    stage = parse_stage(pipeline, stage)
    if current(db, org_id).get(pipeline, {}).get("stage") == stage:
        return None
    return _append(db, org_id, pipeline, "STAGE", at, stage=stage)


def set_value(db, org_id: int, pipeline: str, value, at=None) -> int | None:
    """Set the Organization's Revenue Value or Fundraising Value; it belongs to the Pipeline, not the Stage."""
    value = parse_amount(value)
    if current(db, org_id).get(pipeline, {}).get("value") == value:
        return None
    return _append(db, org_id, pipeline, "VALUE", at, value=value)


def undo(db, entry_id: int) -> None:
    """Cancel one Stage or Value entry; the math and history skip both (FR-PL-4)."""
    e = db.execute("SELECT * FROM pipeline_log WHERE id = ?", (entry_id,)).fetchone()
    if e is None or e["kind"] == "UNDO":
        raise Refused("nothing to undo")
    if db.execute("SELECT 1 FROM pipeline_log WHERE undoes = ?", (entry_id,)).fetchone():
        raise Refused("already undone")
    _append(db, e["organization_id"], e["pipeline"], "UNDO", undoes=entry_id)


def set_target(db, pipeline: str, amount, due) -> None:
    due = date.fromisoformat(due) if isinstance(due, str) else due
    with db:
        db.execute("INSERT OR REPLACE INTO pipeline_targets (pipeline, amount, due) VALUES (?, ?, ?)",
                   (pipeline, parse_amount(amount), due.isoformat()))


def target(db, pipeline: str) -> dict | None:
    r = db.execute("SELECT amount, due FROM pipeline_targets WHERE pipeline = ?", (pipeline,)).fetchone()
    return {"amount": r["amount"], "due": date.fromisoformat(r["due"])} if r else None


def set_prior(db, pipeline: str, stage: str, rate) -> None:
    """A Planning prior in percent (`20`), or None to clear it."""
    stage = parse_stage(pipeline, stage)
    if stage not in OPEN[pipeline]:
        raise Refused(f"priors are for open Stages: {', '.join(OPEN[pipeline])}")
    with db:
        if rate is None:
            db.execute("DELETE FROM planning_priors WHERE pipeline = ? AND stage = ?", (pipeline, stage))
            return
        r = float(rate)
        if not 0 < r <= 100:
            raise Refused("a prior is a percentage above 0 and at most 100")
        db.execute("INSERT OR REPLACE INTO planning_priors (pipeline, stage, rate) VALUES (?, ?, ?)",
                   (pipeline, stage, r / 100))


def priors(db, pipeline: str) -> dict:
    return {r["stage"]: r["rate"] for r in
            db.execute("SELECT stage, rate FROM planning_priors WHERE pipeline = ?", (pipeline,))}


def _entries(db, pipeline=None, org_id=None) -> list:
    """Stage and Value entries not undone, oldest first."""
    sql = ("SELECT * FROM pipeline_log l WHERE kind != 'UNDO'"
           " AND NOT EXISTS (SELECT 1 FROM pipeline_log u WHERE u.undoes = l.id)")
    args = []
    if pipeline:
        sql, args = sql + " AND pipeline = ?", args + [pipeline]
    if org_id is not None:
        sql, args = sql + " AND organization_id = ?", args + [org_id]
    return db.execute(sql + " ORDER BY effective_at, id", args).fetchall()


def _state(rows) -> dict:
    """{(org, pipeline): {stage, since, value}} from entries oldest first."""
    out = {}
    for e in rows:
        s = out.setdefault((e["organization_id"], e["pipeline"]), {"stage": None, "since": None, "value": None})
        if e["kind"] == "STAGE":
            s["stage"], s["since"] = e["stage"], datetime.fromisoformat(e["effective_at"])
        else:
            s["value"] = e["value"]
    return out


def current(db, org_id: int) -> dict:
    """{pipeline: {stage, since, value}} for each Pipeline the Organization has an entry in."""
    return {p: s for (_, p), s in _state(_entries(db, org_id=org_id)).items()}


def history(db, org_id: int) -> list[dict]:
    """Stage and Value changes, newest first, each with what it replaced."""
    out, last = [], {}
    for e in _entries(db, org_id=org_id):
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


def _last_messages(db) -> dict:
    """{domain: latest message time} over Threads with anyone at that Organization."""
    rows = db.execute(
        "SELECT org_key(substr(i.value, instr(i.value, '@') + 1)) AS domain, max(m.occurred_at) AS at"
        " FROM identities i JOIN activities a ON a.identity_id = i.id"
        " JOIN activities m ON m.thread_id = a.thread_id AND m.kind = 'MESSAGE'"
        " WHERE i.kind = 'email' GROUP BY 1")
    return {r["domain"]: datetime.fromisoformat(r["at"]) for r in rows}


def math(db, pipeline: str, now: datetime | None = None) -> dict:
    """Everything a Pipeline page shows. A pure function of the log, Target, priors, and `now`."""
    now = now or _now()
    today = now.astimezone(LOCAL).date()
    chain, opens = _chain(pipeline), OPEN[pipeline]
    rows = _entries(db, pipeline)
    state = {o: s for (o, _), s in _state(rows).items() if s["stage"]}
    orgs = {r["id"]: r for r in db.execute("SELECT * FROM organizations")}
    last = _last_messages(db)

    # When each Organization first reached each chain Stage; a jump reaches the skipped Stages at the same time
    reached, ends = {}, {}  # org -> {index: time}; org -> time it left the chain (Lost, Passed)
    for e in rows:
        if e["kind"] != "STAGE":
            continue
        o, at, i = e["organization_id"], datetime.fromisoformat(e["effective_at"]), _index(pipeline, e["stage"])
        r = reached.setdefault(o, {})
        if i is None:
            ends.setdefault(o, at)
            continue
        for j in range(i + 1):
            r.setdefault(j, at)
    start = now - WINDOW
    cohort = {e["organization_id"] for e in rows if datetime.fromisoformat(e["effective_at"]) >= start} & set(reached)

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
        entries[stage] = sum(1 for o in reached if i in reached[o] and reached[o][i] >= start) / (WINDOW.days / 7)

    def product(stages, rate_of):
        p = 1.0
        for s in stages:
            r = rate_of(s)
            if r is None:
                return None
            p *= r
        return p

    to_paying = {s: product(opens[i:], rates.get) for i, s in enumerate(opens)}
    prior = priors(db, pipeline)
    used = {s: (rates[s], False) if rates[s] is not None else (prior.get(s), s in prior) for s in opens}

    paying = [o for o, s in state.items() if s["stage"] in PAYING[pipeline]]
    paying_value = sum(state[o]["value"] or 0 for o in paying)
    weighted = sum((state[o]["value"] or 0) * (to_paying[state[o]["stage"]] or 0)
                   for o in state if state[o]["stage"] in opens)
    average = paying_value / len(paying) if paying and paying_value else None
    goal = target(db, pipeline)
    summary = {"target": goal, "paying": paying_value, "paying_count": len(paying), "weighted": weighted,
               "projected": paying_value + weighted, "average": average}
    need = {s: None for s in opens}
    if goal:
        deadline = _deadline(pipeline, goal["due"])
        gap = max(0, goal["amount"] - summary["projected"])
        customers = _m.ceil(gap / average) if average else None
        weeks = (deadline - today).days / 7
        summary.update(gap=gap, customers=customers, deadline=deadline, weeks=weeks,
                       share=summary["projected"] / goal["amount"])
        for i, s in enumerate(opens):
            through = product(opens[i:], lambda x: used[x][0])
            ahead = sum(times[x] if times[x] is not None else 1 for x in opens[i:]) / 7
            if customers is None or not through:
                continue
            need[s] = "late" if weeks - ahead <= 0 else customers / through / (weeks - ahead)
    rated = [(used[s][0], i, s) for i, s in enumerate(opens) if used[s][0] is not None]
    constraint = min(rated)[2] if rated else None
    summary["constraint"] = constraint and {"stage": constraint, "next": chain[opens.index(constraint) + 1],
                                            "rate": used[constraint][0], "prior": used[constraint][1]}

    def org_row(o):
        s = state[o]
        return {"id": o, "domain": orgs[o]["domain"], "name": orgs[o]["name"], "value": s["value"],
                "days": (now - s["since"]).days, "last_message": last.get(orgs[o]["domain"])}

    def members(stages):
        return sorted((org_row(o) for o in state if state[o]["stage"] in stages), key=lambda r: (-r["days"], r["name"]))

    stages = [{"stage": s, "orgs": members((s,)), "rate": rates[s], "prior": used[s][0] if used[s][1] else None,
               "time": times[s], "to_paying": to_paying[s], "entries": entries[s], "need": need[s],
               "pace": entries[s] - need[s] if isinstance(need[s], float) else None,
               "constraint": s == constraint} for s in opens]
    ending = [{"stage": label, "orgs": members(group)} for label, group in END_ROWS[pipeline]]
    return {"pipeline": pipeline, "name": NAMES[pipeline], "paying_stage": chain[-1], "now": now,
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
    return f"{x:.0f}" if abs(x) >= 10 else f"{x:.1f}".replace("0.", ".") if abs(x) < 1 else f"{x:.1f}"


def signed(x) -> str:
    return "—" if x is None else ("+" if x >= 0 else "-") + num(abs(x))


def markdown(m: dict) -> str:
    """The Pipeline page as Markdown, for `y` and `comms-terminal pipeline` (FR-PL-31)."""
    s, name = m["summary"], m["name"]
    lines = [f"# {name} Pipeline", ""]
    t = s["target"]
    if not t:
        lines.append(f"No Target yet: comms-terminal pipeline target {name.lower()} AMOUNT --by YYYY-MM-DD")
    else:
        what = "TTM revenue" if m["pipeline"] == REVENUE else "round"
        lines += [f"- Target: CAD {t['amount']:,} {what} by {t['due']}",
                  f"- Projected: CAD {s['projected']:,.0f} ({s['share'] * 100:.0f}%): paying CAD {s['paying']:,}"
                  f" ({s['paying_count']}), weighted CAD {s['weighted']:,.0f}",
                  f"- Gap: CAD {s['gap']:,.0f}"
                  + (f" = {s['customers']} new at CAD {s['average']:,.0f}" if s["customers"] is not None else ""),
                  f"- Must reach {m['paying_stage']} by {s['deadline']} ({s['weeks']:.0f} weeks)"]
    c = s["constraint"]
    if c:
        lines.append(f"- Constraint: {c['stage']} → {c['next']} (CR {pct(c['rate'])}{'p' if c['prior'] else ''})")
    lines += ["", f"| Stage | Orgs | CR | Time | →{m['paying_stage']} | in/wk | need/wk | pace |",
              "|---|---|---|---|---|---|---|---|"]
    for r in m["stages"]:
        cr = pct(r["rate"]) if r["rate"] is not None else (pct(r["prior"]) + "p" if r["prior"] else "—")
        lines.append(f"| {r['stage']}{' ◀ constraint' if r['constraint'] else ''} | {len(r['orgs'])} | {cr} |"
                     f" {weeks(r['time'])} | {pct(r['to_paying'])} | {num(r['entries'])} | {num(r['need'])} |"
                     f" {signed(r['pace'])} |")
    for r in m["ending"]:
        lines.append(f"| {r['stage']} | {len(r['orgs'])} | | | | | | |")
    lines += ["", "trailing 90 days · p Planning prior · — no data", ""]
    for r in m["stages"] + m["ending"]:
        if r["orgs"]:
            lines.append(f"## {r['stage']}")
            lines += [f"- {o['name']} ({o['domain']}): CAD {money(o['value'])}, {o['days']}d in Stage" for o in r["orgs"]]
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
