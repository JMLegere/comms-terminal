"""Pipeline pages (docs/design/console.md § Pipeline; .agents/prd/2026-10-23-pipeline.md) through Textual's
test driver. Screenshots for visual QA states 8-10 land in $PIPELINE_SHOTS when it is set."""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import seed_demo  # noqa: E402

from comms_terminal import inbox, pipeline as pl  # noqa: E402
from comms_terminal.tui import (Ask, Console, OrganizationPage, OrganizationsPage, Pick, PipelineOrgs,  # noqa: E402
                                PipelinePage, PipelineStagePage)

R = pl.REVENUE
DAY = timedelta(days=1)


def seeded(tmp_path):
    path = str(tmp_path / "inbox.db")
    seed_demo.seed(inbox.connect(path))
    db = inbox.connect(path)
    org = {r["domain"]: r["id"] for r in db.execute("SELECT id, domain FROM organizations")}
    now = datetime.now(timezone.utc)
    for domain, steps, value in (("acme.com", ((60, "Awareness"), (40, "Education"), (20, "Selection")), "90k"),
                                 ("northwind.io", ((80, "Education"), (50, "Selection"), (30, "Commit"),
                                                   (25, "Onboarding"), (10, "Impact")), "60k"),
                                 ("globex.com", ((30, "Awareness"),), None)):
        for days, stage in steps:
            pl.set_stage(db, org[domain], R, stage, at=now - days * DAY)
        if value:
            pl.set_value(db, org[domain], R, value)
    pl.set_target(db, R, "1m", "2028-09-30")
    return path, db, org


def run(app, steps, size=(160, 45)):
    async def go():
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            for step in steps:
                await (step(app, pilot) if callable(step) else pilot.press(step))
                await pilot.pause()
    asyncio.run(go())


def shot(app, name):
    if os.environ.get("PIPELINE_SHOTS"):
        app.save_screenshot(f"{name}.svg", os.environ["PIPELINE_SHOTS"])


def test_revenue_page_rail_table_pane_and_stage_change_with_undo(tmp_path):
    path, db, org = seeded(tmp_path)
    seen = {}

    async def open_revenue(app, pilot):
        app.main.go("pipeline:revenue")
        await pilot.pause(0.2)
        rail = app.main.rail
        seen["rail"] = [str(rail.get_option_at_index(i).prompt) for i in range(rail.option_count)]
        seen["page"] = type(app.main.current)
        page = app.main.current
        seen["summary"] = str(page.query_one("#plsummary").render())
        t = page.query_one("#table")
        seen["stages"] = [str(t.get_row_at(i)[0]) for i in range(t.row_count)]
        page.query_one("#table").move_cursor(row=3)  # Selection: Acme
        await pilot.pause()
        seen["pane"] = [o["domain"] for o in app.main.query_one(PipelineOrgs).orgs]
        shot(app, "8-revenue-wide")

    async def into_pane(app, pilot):
        seen["focus"] = type(app.focused)

    async def pick_commit(app, pilot):
        seen["picker"] = type(app.screen)
        app.screen.dismiss("Commit")
        await pilot.pause()
        seen["after"] = pl.current(db, org["acme.com"])[R]["stage"]

    async def undone(app, pilot):
        seen["undone"] = pl.current(db, org["acme.com"])[R]["stage"]

    run(Console(path), [open_revenue, "right", into_pane, "s", pick_commit, "z", undone])
    assert any("Revenue" in r and "%" in r for r in seen["rail"])
    assert seen["page"] is PipelinePage
    assert "CAD 1,000,000" in seen["summary"] and "constraint" in seen["summary"]
    assert "Selection" in seen["stages"][3] and "Impact" in seen["stages"][6]
    assert seen["pane"] == ["acme.com"]
    assert seen["focus"] is PipelineOrgs
    assert seen["picker"] is Pick and seen["after"] == "Commit"
    assert seen["undone"] == "Selection"
    assert "Selection" in pl.markdown(pl.math(db, R)) and "Onboarding" not in [
        h["new"] for h in pl.history(db, org["acme.com"])]


def test_narrow_stage_opens_as_a_page(tmp_path):
    path, _, _ = seeded(tmp_path)
    seen = {}

    async def open_revenue(app, pilot):
        app.main.go("pipeline:revenue")
        await pilot.pause(0.2)
        seen["pane"] = bool(app.main.query(PipelineOrgs))
        app.main.current.query_one("#table").move_cursor(row=3)
        shot(app, "8-revenue-narrow")

    async def opened(app, pilot):
        seen["page"] = type(app.main.current)
        seen["rows"] = [o["domain"] for o, _ in app.main.current.items]

    async def back(app, pilot):
        seen["back"] = type(app.main.current)

    run(Console(path), [open_revenue, "enter", opened, "escape", back], size=(100, 40))
    assert seen["pane"] is False
    assert seen["page"] is PipelineStagePage and seen["rows"] == ["acme.com"]
    assert seen["back"] is PipelinePage


def test_fundraising_without_target_or_organizations(tmp_path):
    path, _, _ = seeded(tmp_path)
    seen = {}

    async def open_fundraising(app, pilot):
        app.main.go("pipeline:fundraising")
        await pilot.pause(0.2)
        page = app.main.current
        seen["summary"] = str(page.query_one("#plsummary").render())
        seen["legend"] = str(page.query_one("#pllegend").render())
        rail = app.main.rail
        seen["rail"] = [str(rail.get_option_at_index(i).prompt) for i in range(rail.option_count)]
        shot(app, "9-fundraising-empty")

    run(Console(path), [open_fundraising])
    assert "No Target yet" in seen["summary"] and "pipeline target fundraising" in seen["summary"]
    assert "No Organizations in Fundraising yet" in seen["legend"]
    assert any("Fundraising" in r and "—" in r for r in seen["rail"])


def test_organization_page_pipeline_section_and_value(tmp_path):
    path, db, org = seeded(tmp_path)
    seen = {}

    async def open_org(app, pilot):
        app.main.open(lambda: OrganizationPage("acme.com"))
        await pilot.pause(0.2)
        seen["side"] = str(app.main.side_text.render())
        shot(app, "10-organization")

    async def typed(app, pilot):
        seen["ask"] = type(app.screen)
        app.screen.dismiss("120k")
        await pilot.pause()
        seen["value"] = pl.current(db, org["acme.com"])[R]["value"]

    async def stage_for_new(app, pilot):
        app.main.open(lambda: OrganizationPage("globex.com"))
        await pilot.pause(0.2)

    async def list_page(app, pilot):
        app.main.go("organizations")
        await pilot.pause(0.2)
        page = app.main.current
        seen["list"] = [o["domain"] for o, cells in page.items if "Selection" in str(cells[4])]

    run(Console(path), [open_org, "v", typed, list_page])
    assert "Pipeline" in seen["side"] and "Revenue  Selection" in seen["side"]
    assert "Education → Selection" in seen["side"] and "CAD 90,000" in seen["side"]
    assert seen["ask"] is Ask and seen["value"] == 120_000
    assert seen["list"] == ["acme.com"]


def test_stage_picker_asks_the_pipeline_for_an_organization_in_none(tmp_path):
    path, db, _ = seeded(tmp_path)
    with db:
        db.execute("INSERT INTO organizations (domain, name, created_at) VALUES ('initech.com', 'Initech', ?)",
                   (datetime.now(timezone.utc).isoformat(),))
    seen = {}

    async def open_org(app, pilot):
        app.main.open(lambda: OrganizationPage("initech.com"))
        await pilot.pause(0.2)

    async def pipeline_first(app, pilot):
        seen["first"] = [o for o, _ in app.screen.options]
        shot(app, "10-stage-picker-none")
        app.screen.dismiss("FUNDRAISING")
        await pilot.pause()
        seen["stages"] = [o for o, _ in app.screen.options]
        app.screen.dismiss("Research")
        await pilot.pause()

    run(Console(path), [open_org, "s", pipeline_first])
    assert seen["first"] == ["REVENUE", "FUNDRAISING"]
    assert seen["stages"][:2] == ["Research", "Outreach"] and seen["stages"][-1] == "pipeline:REVENUE"
    org = db.execute("SELECT id FROM organizations WHERE domain = 'initech.com'").fetchone()[0]
    assert pl.current(db, org)[pl.FUNDRAISING]["stage"] == "Research"
