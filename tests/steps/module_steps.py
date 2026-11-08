"""Step definitions for Modules (features/modules.feature; PRD 2026-11-07-modules, ADR 0017). The inbox steps'
`ctx` holds the database; Console steps run the Console on a seeded file once their Given steps are set."""

import asyncio
import contextlib
import io
from datetime import timedelta

import re

from pytest_bdd import given, parsers, then, when

from comms_terminal import agent, core, inbox, pipeline, remote, sequences, tui
from steps.inbox_steps import email_in, keep, tick

NAMES = {name: m for m, name in inbox.MODULES.items()}  # "Cases" -> "cases"


# --- the Module setting ---------------------------------------------------------------------------------------

@given(parsers.parse("{name} is on"))
def module_is_on(ctx, name):
    inbox.set_module(ctx.db, NAMES[name], True)


@given(parsers.parse("{name} is off"))
def module_is_off(ctx, name):
    inbox.set_module(ctx.db, NAMES[name], False)


@when(parsers.parse("Set Module turns {name} {state}"))
def set_module(ctx, name, state):
    ctx.tasks_before = ctx.db.execute("SELECT count(*) FROM tasks").fetchone()[0]
    inbox.set_module(ctx.db, NAMES[name], state == "on")


# --- AS-MOD-1: a new Service's rail ----------------------------------------------------------------------------

def console(path, steps):
    """Run the Console on `path`, calling each step(app) after it settles; returns what the steps returned."""
    out = []

    async def go():
        app = tui.Console(path)
        async with app.run_test(size=(150, 40)) as pilot:
            await pilot.pause()
            for step in steps:
                out.append(step(app))
                await pilot.pause()
    asyncio.run(go())
    return out


def seeded(tmp_path):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import seed_demo
    path = str(tmp_path / "service.db")
    seed_demo.seed(inbox.connect(path))
    return path


@given("a new Service with no Module setting")
def new_service(ctx, tmp_path):
    ctx.path = seeded(tmp_path)
    with inbox.connect(ctx.path) as db:
        db.execute("DELETE FROM modules")


@when("the Console opens")
def console_opens(ctx):
    def rail(app):
        r = app.main.rail
        return "\n".join(r.render_line(y).text for y in range(r.content_size.height))

    def rows(app):  # every rail row, also those below the fold
        r = app.main.rail
        return [str(r.get_option_at_index(i).prompt) for i in range(r.option_count)]
    ctx.rail, ctx.rail_rows = console(ctx.path, [rail, rows])


@then(parsers.parse("the rail shows {names}"))
def rail_shows(ctx, names):
    if names.startswith("no "):
        for name in names[3:].replace(" or Pipeline rows", "").replace('"', "").split(", "):
            assert name not in ctx.rail, name
        for name in pipeline.NAMES.values():  # "Pipeline rows"
            assert name not in ctx.rail, name
        return
    for name in names.replace(" and ", ", ").replace('"', "").split(", "):
        assert name in ctx.rail, name


# --- ADR 0018: the rail groups Connections by kind ----------------------------------------------------------

@given("the demo Service with a Gmail, a Slack and a Linear Issue connection")
def demo_service(ctx, tmp_path):
    ctx.path = seeded(tmp_path)  # the demo seeds Gmail, Slack and Linear's Issue connection


@then(parsers.parse("the rail lists {pairs}"))
def rail_lists_under(ctx, pairs):
    lines = [re.sub(r"\[[^\]]*\]", "", row).strip() for row in ctx.rail_rows]  # markup off
    for name, kind in re.findall(r'"([^"]+)" under "([^"]+)"', pairs):
        at = next(i for i, line in enumerate(lines) if line.endswith(f" {kind}") and len(line.split()) == 2)
        group = lines[at + 1:next((j for j in range(at + 1, len(lines)) if not lines[j]), len(lines))]
        assert any(name in row for row in group), (kind, group)


# --- Issues as a Module (ADR 0017) --------------------------------------------------------------------------

@given("the demo Service with Issues off")
def demo_issues_off(ctx, tmp_path):
    ctx.path = seeded(tmp_path)
    with inbox.connect(ctx.path) as db:
        inbox.set_module(db, "issues", False)


@then(parsers.parse('the rail keeps "{kept}" and drops "{gone}"'))
def rail_keeps_and_drops(ctx, kept, gone):
    rows = [re.sub(r"\[[^\]]*\]", "", row).split() for row in ctx.rail_rows]
    assert any(kept.split()[0] in row for row in rows), kept
    assert not any(gone in row for row in rows), gone


@given("an unfinished Issue planned for last Friday")
def issue_planned_friday(ctx):
    from datetime import datetime, timezone
    from comms_terminal import issues
    now = datetime(2026, 11, 6, 14, tzinfo=timezone.utc)
    cid = inbox.ensure_connection(ctx.db, "linear", "issue", now)
    issues.upsert(ctx.db, cid, {"id": "id-BIZ-1", "identifier": "BIZ-1", "title": "T", "description": None, "url": None,
                                "team_id": "team", "state_id": "todo", "priority": 0, "estimate": None,
                                "due_date": None, "created_at": "2026-11-01T12:00:00.000Z",
                                "updated_at": "2026-11-06T12:00:00.000Z", "completed_at": None, "canceled_at": None,
                                "data": {}}, now)
    with ctx.db:
        ctx.db.execute("UPDATE issues SET planned_day = '2026-11-06'")


@when("Rollover runs on Monday")
def rollover_monday(ctx):
    from datetime import datetime, timezone
    from comms_terminal import issues
    ctx.moved = issues.rollover(ctx.db, datetime(2026, 11, 9, 14, tzinfo=timezone.utc))


@then("the Issue is still planned for last Friday and nothing is queued for Linear")
def rollover_waited(ctx):
    assert ctx.moved == 0
    assert ctx.db.execute("SELECT planned_day FROM issues").fetchone()[0] == "2026-11-06"
    assert ctx.db.execute("SELECT count(*) FROM actions").fetchone()[0] == 0


# --- AS-MOD-2: Cases off --------------------------------------------------------------------------------------

@given(parsers.parse('an email Thread {n:d} in the Case "{title}"'))
def thread_in_case(ctx, n, title):
    t = keep(ctx, inbox.ingest(ctx.db, email_in(ctx, f"t{n}@acme.example")))
    ctx.threads[n] = t.id
    case = inbox.create_case(ctx.db, title)
    inbox.act(ctx.db, t.id, inbox.SetCase(case_id=case), now=tick(ctx))


@then(parsers.parse("the Todo view lists Thread {n:d} under its Status Detail header with no Case header"))
def no_case_header(ctx, n):
    (row,) = [r for r in inbox.queue(ctx.db, "Todo") if r["id"] == ctx.threads[n]]
    assert row["case_id"] is None  # both Clients draw a Case header only for a row with a case_id


@then('the Thread page offers no "case" key')
def no_case_key(ctx):
    view = inbox.thread_view(ctx.db, ctx.thread.id)
    assert view["cases_on"] is False and view["case"] is None and tui.MODULE_ACTIONS["case"] == "cases"


@when(parsers.parse('the CLI runs "{line}"'))
def cli_runs(ctx, line):
    from test_cli_parity import parser
    words = [str(ctx.threads.get(int(w), w)) if w.isdigit() and w == line.split()[1] else w for w in line.split()]
    err, out = io.StringIO(), io.StringIO()
    ctx.exit = 0
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
        try:
            agent.run(parser().parse_args(words), ctx.db)
        except SystemExit as e:
            ctx.exit = e.code
    ctx.stderr, ctx.printed = err.getvalue(), out.getvalue()


@then(parsers.parse('the CLI prints "{text}"'))
def cli_prints(ctx, text):
    assert text in ctx.stderr + ctx.printed  # an error to stderr, an answer (undo --show) to stdout


@then(parsers.parse("the CLI exits with code {code:d}"))
def cli_exits(ctx, code):
    assert ctx.exit == code


@then(parsers.parse('Thread {n:d} is still in the Case "{title}"'))
def still_in_case(ctx, n, title):
    assert ctx.db.execute("SELECT c.title FROM threads t JOIN cases c ON c.id = t.case_id WHERE t.id = ?",
                          (ctx.threads[n],)).fetchone()[0] == title


# --- AS-MOD-3, AS-MOD-4: Sequences off ------------------------------------------------------------------------

def a_sequence(ctx):
    s = sequences.create_sequence(ctx.db, "Pilot")
    step = sequences.add_step(ctx.db, s, "email")
    sequences.save_step(ctx.db, step, subject="Pilot", body="Hi {first_name}", needs_edit=0)
    sequences.add_step(ctx.db, s, "call")
    return s


@given(parsers.parse('"{addr}" has an Active Enrollment whose next Step is due tomorrow'))
def enrolled_tomorrow(ctx, addr):
    keep(ctx, inbox.ingest(ctx.db, email_in(ctx, addr)))
    ctx.person = ctx.db.execute("SELECT person_id FROM identities WHERE value = ?", (addr,)).fetchone()[0]
    ctx.since = (ctx.clock - timedelta(days=1)).isoformat()
    sequences.enroll(ctx.db, ctx.person, a_sequence(ctx), start=(ctx.clock + timedelta(days=1)).date(), now=ctx.clock)


@given(parsers.parse("an Active Enrollment whose next Step fell due {days:d} days ago"))
def enrolled_days_ago(ctx, days):
    keep(ctx, inbox.ingest(ctx.db, email_in(ctx, "ana@acme.example")))
    ctx.person = ctx.db.execute("SELECT person_id FROM identities WHERE value = 'ana@acme.example'").fetchone()[0]
    ctx.due = ctx.clock - timedelta(days=days)
    sequences.enroll(ctx.db, ctx.person, a_sequence(ctx), start=ctx.due.date(), now=ctx.due)  # the Service's own call


@then(parsers.parse('the Enrollment is "{state}"'))
def enrollment_is(ctx, state):
    (e,) = sequences.enrollments(ctx.db, ctx.person)
    assert e["state"] == state


@then("no Enrollment-paused toast shows")
def no_pause_toast(ctx):
    assert sequences.pauses_since(ctx.db, ctx.since) == []


@when("Materialize runs the day after tomorrow")
def materialize_later(ctx):
    ctx.made = sequences.materialize(ctx.db, ctx.clock + timedelta(days=2))


@when("Materialize runs")
def materialize_now(ctx):
    ctx.made = sequences.materialize(ctx.db, ctx.clock)


@then("no Task or Draft is made for the Enrollment")
def nothing_made(ctx):
    assert ctx.made == 0 and ctx.db.execute("SELECT count(*) FROM tasks").fetchone()[0] == ctx.tasks_before


@then(parsers.parse('the Enrollment has a Task with State "{state}" due {days:d} days ago'))
def task_due(ctx, state, days):
    (t,) = ctx.db.execute("SELECT state, due_at FROM tasks").fetchall()
    assert t["state"] == state
    assert sequences.datetime.fromisoformat(t["due_at"]).astimezone(sequences.LOCAL).date() == \
        (ctx.clock - timedelta(days=days)).astimezone(sequences.LOCAL).date()


@then(parsers.parse('Today shows "{n:d} overdue"'))
def overdue(ctx, n):
    assert sum(t["group"] == "overdue" for t in sequences.tasks_due(ctx.db, ctx.clock)) == n


# --- AS-MOD-5: a page of a Module turned off elsewhere -----------------------------------------------------------

@given(parsers.parse('the Console shows the Case "{title}"'))
def console_on_case(ctx, title, tmp_path):
    ctx.path = seeded(tmp_path)
    with inbox.connect(ctx.path) as db:
        inbox.set_module(db, "cases", True)
        t = db.execute("SELECT id FROM threads ORDER BY id LIMIT 1").fetchone()[0]
        inbox.act(db, t, inbox.SetCase(case_id=inbox.create_case(db, title)))


@when("the App turns Cases off")
def app_turns_cases_off(ctx):
    ctx.turn_off = lambda: inbox.set_module(inbox.connect(ctx.path), "cases", False)


@when("the Console applies the next Change set")
def console_applies(ctx):
    def open_case(app):
        app.main.go("cases:0")

    def change(app):
        ctx.turn_off()
        app.main.refresh_all()  # what the Console runs on a Change set (tui.apply_snapshot)

    def shown(app):  # toasts land on the next frame
        return type(app.main.current).__name__, [n.message for n in app._notifications]
    ctx.page, ctx.toasts = console(ctx.path, [open_case, change, shown])[2]


@then("the Console shows Today")
def console_today(ctx):
    assert ctx.page == "TodayPage"


@then(parsers.parse('the Console shows the toast "{text}"'))
def console_toast(ctx, text):
    assert text in ctx.toasts


# --- AS-MOD-6: undo into an off Module -----------------------------------------------------------------------

@given(parsers.parse('Jeremy set the Stage of "{name}" in the Revenue Pipeline to "{stage}"'))
def set_stage(ctx, name, stage):
    keep(ctx, inbox.ingest(ctx.db, email_in(ctx, f"buyer@{name.lower()}.example")))
    with ctx.db:
        ctx.db.execute("UPDATE organizations SET name = ? WHERE domain = ?", (name, f"{name.lower()}.example"))
    ctx.org = ctx.db.execute("SELECT id FROM organizations WHERE name = ?", (name,)).fetchone()[0]
    ctx.before_stage = pipeline.current(ctx.db, ctx.org).get("REVENUE", {}).get("stage")
    entry = pipeline.set_stage(ctx.db, ctx.org, "REVENUE", stage)
    gated = remote.gated(pipeline, "pipeline")  # a Client's undo goes through the Module gate (ADR 0017)
    ctx.undo = lambda: gated.undo(ctx.db, entry)


@when("Jeremy presses undo")
def press_undo(ctx):
    ctx.toast = None
    try:
        ctx.undo()
    except inbox.ModuleDisabled as e:  # the Clients say the first half and keep the undo
        ctx.toast = str(e).split(" on this Service")[0]


@then(parsers.parse('the toast "{text}" shows'))
def toast_shows(ctx, text):
    assert ctx.toast == text


@then(parsers.parse('"{name}" is at Stage "{stage}"'))
def at_stage(ctx, name, stage):
    assert pipeline.current(ctx.db, ctx.org)["REVENUE"]["stage"] == stage


@then(parsers.parse('"{name}" is at its Stage before "{stage}"'))
def at_stage_before(ctx, name, stage):
    assert pipeline.current(ctx.db, ctx.org).get("REVENUE", {}).get("stage") == ctx.before_stage


# --- AS-MOD-7: data kept --------------------------------------------------------------------------------------

def held(ctx):
    return ctx.db.execute("SELECT (SELECT count(*) FROM cases), (SELECT count(*) FROM threads"
                          " WHERE case_id IS NOT NULL AND merged_into IS NULL)").fetchone()[:]


@given(parsers.parse("{cases:d} Cases holding {threads:d} Threads"))
def cases_holding(ctx, cases, threads):
    ids = [inbox.create_case(ctx.db, f"Case {i}") for i in range(cases)]
    for i in range(threads):
        t = inbox.ingest(ctx.db, email_in(ctx, f"p{i}@acme.example"))
        inbox.act(ctx.db, t.id, inbox.SetCase(case_id=ids[i % cases]), now=tick(ctx))
    assert held(ctx) == (cases, threads)


@then(parsers.parse("there are {cases:d} Cases holding {threads:d} Threads"))
def still_holding(ctx, cases, threads):
    assert held(ctx) == (cases, threads)


# --- AS-MOD-8: a Context source page -------------------------------------------------------------------------

@given(parsers.parse('the Context source "{title}" fails with "{problem}"'))
def source_fails(ctx, title, problem):
    ctx.source = next(k for k, v in tui.CONTEXT_SOURCES.items() if v[0] == title)
    ctx.problem = problem


@when(parsers.parse('Jeremy opens "{title}" from the rail'))
def open_source(ctx, title):
    page = core.QUERIES["context.page"][0](None, ctx.db, ctx.source, ctx.problem, "2026-10-12T08:00:00+00:00")
    ctx.page_text = "\n".join("".join(seg[0] for seg in line) for line in page["lines"])


@then(parsers.parse('its Context source page shows "{problem}" and the time of its last check'))
def page_shows(ctx, problem):
    assert problem in ctx.page_text and "last check\nMon Oct 12" in ctx.page_text


@then(parsers.parse("it says Tana adds context to {scope}"))
def page_scope(ctx, scope):
    assert scope.replace('"', "") in ctx.page_text
