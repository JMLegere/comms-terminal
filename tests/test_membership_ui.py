"""Membership on the Person page (o) and the Organization page (p), with z undo (FR-OA-2). Screenshots for
visual QA land in $MEMBERSHIP_SHOTS when it is set."""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import seed_demo  # noqa: E402

from comms_terminal import inbox  # noqa: E402
from comms_terminal.tui import Ask, Console, OrganizationPage, PersonPage, Pick  # noqa: E402


def seeded(tmp_path):
    path = str(tmp_path / "inbox.db")
    seed_demo.seed(inbox.connect(path))
    db = inbox.connect(path)
    pid = db.execute("SELECT person_id FROM identities WHERE kind = 'email' AND value LIKE '%@acme.com'"
                     " ORDER BY id").fetchone()[0]
    return path, db, pid


def run(app, steps, size=(160, 45)):
    async def go():
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            for step in steps:
                await (step(app, pilot) if callable(step) else pilot.press(step))
                await pilot.pause()
    asyncio.run(go())


def shot(app, name):
    if os.environ.get("MEMBERSHIP_SHOTS"):
        app.save_screenshot(f"{name}.svg", os.environ["MEMBERSHIP_SHOTS"])


def test_person_page_adds_and_removes_organizations_with_undo(tmp_path):
    path, db, pid = seeded(tmp_path)
    seen = {}

    async def open_person(app, pilot):
        app.main.open(lambda: PersonPage(pid))
        await pilot.pause(0.2)

    async def menu(app, pilot):
        seen["pick"] = type(app.screen), [o for o, _ in app.screen.options]
        shot(app, "membership-person-menu")
        app.screen.dismiss("add")
        await pilot.pause()
        seen["ask"] = type(app.screen)
        app.screen.dismiss("lclexcavation.com")
        await pilot.pause()
        seen["added"] = inbox.person_organizations(db, pid)
        seen["side"] = str(app.main.current.query_one("#prest").render())

    async def undone(app, pilot):
        seen["undone"] = inbox.person_organizations(db, pid)

    async def remove(app, pilot):
        app.screen.dismiss("acme.com")
        await pilot.pause()
        seen["removed"] = inbox.person_organizations(db, pid)

    run(Console(path), [open_person, "o", menu, "z", undone, "o", remove])
    assert seen["pick"] == (Pick, ["add", "acme.com"]) and seen["ask"] is Ask
    assert seen["added"] == ["acme.com", "lclexcavation.com"] and "lclexcavation.com" in seen["side"]
    assert seen["undone"] == ["acme.com"]
    assert seen["removed"] == [] and inbox.membership(db, pid, "acme.com") is False


def test_organization_page_adds_a_person_by_address(tmp_path):
    path, db, pid = seeded(tmp_path)
    phone_only = db.execute("INSERT INTO persons (display_name, created_at) VALUES ('JP', '2026-10-01T00:00:00+00:00')"
                            ).lastrowid
    db.commit()
    inbox.add_identity(db, phone_only, "phone", "+15065550100")
    seen = {}

    async def open_org(app, pilot):
        app.main.open(lambda: OrganizationPage("acme.com"))
        await pilot.pause(0.2)

    async def add(app, pilot):
        seen["options"] = [o for o, _ in app.screen.options]
        app.screen.dismiss("add")
        await pilot.pause()
        app.screen.dismiss("+15065550100")
        await pilot.pause()
        shot(app, "membership-organization")
        seen["people"] = [p["id"] for p in inbox.organization_view(db, "acme.com")["people"]]

    run(Console(path), [open_org, "p", add])
    assert seen["options"][0] == "add" and str(pid) in seen["options"]
    assert phone_only in seen["people"] and pid in seen["people"]
