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
from comms_terminal.tui import (Ask, BoardColumn, Console, OrganizationPage, Pick, PipelinePage, card)  # noqa: E402

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


def test_revenue_page_this_week_board_and_stage_change_with_undo(tmp_path):
    path, db, org = seeded(tmp_path)
    seen = {}

    async def open_revenue(app, pilot):
        app.main.go("pipeline:revenue")
        await pilot.pause(0.2)
        rail = app.main.rail
        seen["rail"] = [str(rail.get_option_at_index(i).prompt) for i in range(rail.option_count)]
        page = app.main.current
        seen["page"], seen["side"] = type(page), page.SIDE
        seen["week"] = str(page.query_one("#plweek").render())
        seen["progress"] = str(page.query_one("#plprogress").render())
        seen["heads"] = [str(h.render()) for h in page.query(".plcolhead")]
        cols = list(page.query(BoardColumn))
        seen["columns"] = [c.stage for c in cols]
        seen["cards"] = {c.stage: [o["domain"] for o in c.orgs] for c in cols if c.orgs}
        seen["focus"] = app.focused.stage
        shot(app, "8-revenue-wide")

    async def moved(app, pilot):
        seen["after right"] = app.focused.stage

    async def back_left(app, pilot):
        seen["after left"] = app.focused.stage

    async def pick_commit(app, pilot):
        seen["picker"] = type(app.screen)
        app.screen.dismiss("Commit")
        await pilot.pause()
        seen["after"] = pl.current(db, org["acme.com"])[R]["stage"]

    async def undone(app, pilot):
        seen["undone"] = pl.current(db, org["acme.com"])[R]["stage"]

    run(Console(path), [open_revenue, "right", moved, "left", back_left, "s", pick_commit, "z", undone], size=(236, 57))
    assert any("Revenue" in r and "behind" in r for r in seen["rail"])
    assert seen["page"] is PipelinePage and seen["side"] == ""
    assert seen["week"].startswith("1  ") and "Organizations to Awareness" in seen["week"]
    assert seen["progress"].startswith(("BEHIND PACE", "ON PACE")) and "Coverage" in seen["progress"]
    assert "CAD 1M" in seen["progress"] and "reach Impact by 2027-10-01" in seen["progress"]
    assert seen["heads"][0].startswith("Awareness\n1") and "keep" in seen["heads"][6]
    assert seen["columns"] == ["Awareness", "Education", "Prioritization", "Selection", "Commit", "Onboarding",
                               "Impact", "Expansion", "Lost", "Churned"]
    assert seen["cards"] == {"Selection": ["acme.com"], "Impact": ["northwind.io"], "Awareness": ["globex.com"]}
    assert seen["focus"] == "Awareness" and seen["after right"] == "Education" and seen["after left"] == "Awareness"
    assert seen["picker"] is Pick and seen["after"] is not None


def test_board_scrolls_sideways_when_narrow(tmp_path):
    path, _, _ = seeded(tmp_path)
    seen = {}

    async def open_revenue(app, pilot):
        app.main.go("pipeline:revenue")
        await pilot.pause(0.2)
        board = app.main.current.query_one("#plboard")
        seen["overflow"] = board.virtual_size.width > board.size.width
        shot(app, "8-revenue-narrow")

    run(Console(path), [open_revenue], size=(100, 40))
    assert seen["overflow"]


def test_fundraising_without_target_or_organizations(tmp_path):
    path, _, _ = seeded(tmp_path)
    seen = {}

    async def open_fundraising(app, pilot):
        app.main.go("pipeline:fundraising")
        await pilot.pause(0.2)
        page = app.main.current
        seen["week"] = str(page.query_one("#plweek").render())
        seen["progress"] = str(page.query_one("#plprogress").render())
        rail = app.main.rail
        seen["rail"] = [str(rail.get_option_at_index(i).prompt) for i in range(rail.option_count)]
        shot(app, "9-fundraising-empty")

    run(Console(path), [open_fundraising], size=(236, 57))
    assert "Set a Target" in seen["week"] and "pipeline target fundraising" in seen["week"]
    assert "No Target yet" in seen["progress"]
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


def test_unattended_shows_in_today_on_the_board_and_on_the_organization_page(tmp_path):
    """FR-OA-6, -7: Globex's Threads all Done, so it is Unattended; Acme still has Todo."""
    path, db, org = seeded(tmp_path)
    for t in inbox.organization_view(db, "globex.com")["threads"]:
        inbox.act(db, t.id, inbox.Done())
    seen = {}

    async def today(app, pilot):
        app.main.go("today")
        await pilot.pause(0.2)
        page = app.main.current
        seen["today"] = (page.count(page.orgs), str(page.orgs.parent.border_title),
                         [o["domain"] for o, _ in page.orgs.items])
        page.enter(page.orgs, 1)
        await pilot.pause()
        seen["side"] = str(app.main.side_text.render())
        shot(app, "11-today-unattended")

    async def opened(app, pilot):
        seen["opened"] = (type(app.main.current), getattr(app.main.current, "domain", None))

    async def board(app, pilot):
        app.main.go("pipeline:revenue")
        await pilot.pause(0.2)
        cards = {o["domain"]: o for c in app.main.current.query(BoardColumn) for o in c.orgs}
        seen["cards"] = {d: card(o, datetime.now(timezone.utc)) for d, o in cards.items()}
        shot(app, "12-board-rollup")

    run(Console(path), [today, "enter", opened, board], size=(236, 57))
    n, title, domains = seen["today"]
    assert n == 1 and "unattended · 1" in title and domains == ["globex.com"]
    assert "Now" in seen["side"] and "Unattended" in seen["side"]
    assert seen["opened"] == (OrganizationPage, "globex.com")
    assert "Unattended" in seen["cards"]["globex.com"] and "Unattended" not in seen["cards"]["acme.com"]
