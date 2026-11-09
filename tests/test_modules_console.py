"""Modules in the Console (PRD 2026-11-07-modules, Phase 3): the rail, keys, pages, Settings and undo."""

from comms_terminal import commands, inbox, remote, tui
from comms_terminal.tui import CasesPage, Console, SettingsPage, TodayPage
from test_console import run, seeded


def rail_text(app):
    """Every rail row's words, including rows scrolled out of view (History and Settings sit at the bottom)."""
    rail = app.main.rail
    return "\n".join(str(rail.get_option_at_index(i).prompt) for i in range(rail.option_count))


def toasts(app):
    return [n.message for n in app._notifications]


def off(path, *modules):
    """Turn Modules off elsewhere: another Client's (here the Service's) Command, not the Console's."""
    db = inbox.connect(path)
    with commands.source("service"):
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
        assert [m for m, _ in app.main.current.items] == ["pipelines", "sequences", "cases", "issues", "undo_window"]
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

    case = inbox.connect(path).execute("SELECT id FROM cases LIMIT 1").fetchone()[0]

    async def offer(app, pilot):
        tui.inbox.set_case_description(app.db, case, "Roll out to 40 seats")  # a Console Command
        off(path, "cases")
        app.refresh_modules()

    async def back_on(app, pilot):
        assert commands.newest(app.db)["writer"] == "inbox.set_case_description" and "Cases is disabled" in toasts(app)
        with commands.source("service"):  # on again elsewhere; turning it on here would be the newest change itself
            inbox.set_module(app.db, "cases", True)
        app.refresh_modules()
    run(app, [offer, "z", back_on, "z"])
    assert inbox.connect(path).execute("SELECT description FROM cases WHERE id = ?", (case,)).fetchone()[0] != \
        "Roll out to 40 seats"


def app_module_commands() -> list[tuple[str, str]]:
    """app/src/window.rs MODULE_COMMANDS: (command or view prefix, Module)."""
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app" / "src" / "window.rs").read_text()
    block = src[src.index("const MODULE_COMMANDS"):src.index("];", src.index("const MODULE_COMMANDS"))]
    return re.findall(r'\("([\w.]+)", "(\w+)"\)', block)


def app_module(command: str) -> str | None:
    return next((m for c, m in app_module_commands() if (command.startswith(c) if c.endswith(".") else command == c)), None)


def test_console_and_app_tag_the_same_keys_with_the_same_module():  # NFR-MOD-1
    from test_app_parity import APP
    for (cls, action), command in APP.items():
        page = getattr(tui, cls, None)
        console = tui.target_module(action[4:-2]) if action.startswith("go(") else tui.MODULE_ACTIONS.get(action)
        if console is None and getattr(page, "MODULE", None):  # a key of a Module's page: the App may route it to a
            assert app_module(command) in (page.MODULE, None), (cls, action, command)  # Core command (nav.back)
            continue
        assert console == app_module(command), f"{cls}.{action} is {console} in the Console, {command} is {app_module(command)} in the App"


def test_a_context_source_page_shows_its_problem_last_check_and_scope(tmp_path):  # AS-MOD-8, FR-MOD-16
    path, _ = seeded(tmp_path)
    app = Console(path)
    shown = {}

    async def open_tana(app, pilot):
        app.source_state["tana"] = "401 Unauthorized"
        app.source_checked["tana"] = "2026-11-07T14:05:00+00:00"
        app.main.go("context:tana")
        await pilot.pause()
        shown["text"] = str(app.main.current.query_one("#context").render())
        shown["page"] = type(app.main.current).__name__
    run(app, [open_tana])
    assert shown["page"] == "ContextSourcePage"
    assert "401 Unauthorized" in shown["text"] and "Threads, People, Organizations and Cases" in shown["text"]
    assert "Sat Nov 7" in shown["text"]


def test_the_local_core_draws_the_same_context_source_page(tmp_path):  # FR-MOD-16, NFR-MOD-5
    from comms_terminal import core
    db = inbox.connect(str(tmp_path / "x.db"))
    inbox.migrate(db)
    inbox.set_module(db, "cases", False)
    fn = core.QUERIES["context.page"][0]
    lines = fn(None, db, "tana", "401 Unauthorized", None)["lines"]
    text = "\n".join("".join(seg[0] for seg in line) for line in lines)
    assert "401 Unauthorized" in text and "Threads, People and Organizations" in text and "Never." in text
