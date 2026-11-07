"""Modules in the Console (PRD 2026-11-07-modules, Phase 3): the rail, keys, pages, Settings and undo."""

from comms_terminal import inbox, remote, tui
from comms_terminal.tui import CasesPage, Console, SettingsPage, TodayPage
from test_console import run, seeded


def rail_text(app):
    rail = app.main.rail
    return "\n".join(rail.render_line(y).text for y in range(rail.content_size.height))


def toasts(app):
    return [n.message for n in app._notifications]


def off(path, *modules):
    db = inbox.connect(path)
    for m in modules:
        inbox.set_module(db, m, False)
    return db


def test_with_every_module_off_the_rail_shows_only_core_and_settings(tmp_path):  # AS-MOD-1, FR-MOD-9
    path, _ = seeded(tmp_path)
    off(path, "pipelines", "sequences", "cases")
    app = Console(path)

    async def check(app, pilot):
        text = rail_text(app)
        for core in ("Today", "Calendar", "Todo", "Drafts", "People", "Organizations", "Channels", "Settings"):
            assert core in text, core
        for gone in ("cases", "Tasks", "Sequences", "Revenue", "Fundraising", "Talent"):
            assert gone not in text, gone

    async def not_cases(app, pilot):
        assert not isinstance(app.main.current, CasesPage)  # 9 opens Cases only while it is on
    run(app, [check, "9", not_cases])


def test_with_cases_off_the_thread_keys_lose_case(tmp_path):  # AS-MOD-2, FR-MOD-9
    path, _ = seeded(tmp_path)
    app = Console(path)
    seen = {}

    async def check(app, pilot):
        seen["on"] = app.main.page.check_action("case", ())
        off(path, "cases")
        app.main.refresh_all()
        seen["off"] = app.main.page.check_action("case", ())
    run(app, [check])
    assert seen == {"on": True, "off": False}


def test_a_page_of_a_module_turned_off_elsewhere_goes_back_to_today(tmp_path):  # AS-MOD-5, FR-MOD-10
    path, _ = seeded(tmp_path)
    app = Console(path)

    async def turn_off(app, pilot):
        assert isinstance(app.main.current, CasesPage)
        off(path, "cases")  # another Client's change, arriving with the next refresh
        app.main.refresh_all()

    async def today(app, pilot):
        assert isinstance(app.main.current, TodayPage)
        assert "Cases turned off" in toasts(app)
    run(app, ["9", turn_off, today])


def test_settings_turns_a_module_off_and_z_turns_it_back_on(tmp_path):  # FR-MOD-14, FR-MOD-13
    path, _ = seeded(tmp_path)
    app = Console(path)

    async def opened(app, pilot):
        assert isinstance(app.main.current, SettingsPage)
        assert [m for m, _ in app.main.current.items] == ["pipelines", "sequences", "cases"]
    run(app, ["ctrl+comma", opened, "down", "down", "space",
              assert_off, "z"])
    assert "cases" in inbox.modules_on(inbox.connect(path))


async def assert_off(app, pilot):
    assert "cases" not in inbox.modules_on(app.db)
    assert "cases" not in rail_text(app)
    assert any(t.startswith("Cases turned off.") for t in toasts(app))


def test_undo_into_an_off_module_is_refused_and_kept(tmp_path, monkeypatch):  # AS-MOD-6, FR-MOD-11, R4
    path, _ = seeded(tmp_path)
    monkeypatch.setattr(tui, "inbox", remote.gated(inbox, "inbox"))  # as main.py gates a local console
    app = Console(path)

    async def offer(app, pilot):
        app.offer_undo("Removed the Case.", lambda: tui.inbox.create_case(app.db, "Logan onboarding"))
        off(path, "cases")
        app.refresh_modules()

    async def back_on(app, pilot):
        assert app.undo is not None and "Cases is disabled" in toasts(app)
        inbox.set_module(app.db, "cases", True)
        app.refresh_modules()
    run(app, [offer, "z", back_on, "z"])
    assert inbox.connect(path).execute("SELECT count(*) FROM cases WHERE title = 'Logan onboarding'").fetchone()[0] == 1
