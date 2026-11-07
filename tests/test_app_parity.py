"""FR-6, AS-11: every key binding written in the Console (`tui.py`) reaches the desktop App, or says why not.

Each literal `Binding(key, action)` in `tui.py`, keyed by the class that declares it, is in exactly one table:
- APP: the App command it maps to; the shipped `keys.json` binds that command, and the view handles it;
- TYPED: a key the App handles inside a text field or the overlay, by its keystroke in that view's script;
- WHY: the reason the App has no key for it.
A new Console key fails this test until it is placed.
"""

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = ROOT / "app" / "defaults"
THREAD = "<module>"  # THREAD_KEYS: the module-level Thread keys every Thread page shares

SCROLLS = "Panes scroll with the wheel or trackpad"  # the Thread takes space/pgdn/pgup (D55); other pages are Phase 5 and 6

APP = {
    ("MainScreen", "go('settings')"): "settings.open",
    (THREAD, "reply('sender')"): "thread.reply_sender", (THREAD, "reply('all')"): "thread.reply_all",
    (THREAD, "done"): "thread.done", (THREAD, "pause"): "thread.snooze", (THREAD, "block_sender"): "thread.block",
    (THREAD, "ignore"): "thread.ignore", (THREAD, "case"): "thread.case", (THREAD, "status"): "thread.status",
    (THREAD, "person"): "thread.person", (THREAD, "link"): "thread.link", (THREAD, "copy"): "thread.copy",
    (THREAD, "forward"): "thread.forward", (THREAD, "react"): "thread.react", (THREAD, "respond"): "thread.respond",
    ("ThreadsPage", "down"): "threads.next", ("ThreadsPage", "up"): "threads.prev",
    ("StagedList", "unstage"): "thread.unstage",
    ("DraftView", "page('down')"): "drafts.next", ("DraftView", "page('up')"): "drafts.prev",
    ("DraftView", "page('new')"): "drafts.new", ("DraftView", "page('delete')"): "drafts.delete",
    ("DraftView", "page('copy')"): "drafts.copy",
    ("DraftsPage", "down"): "drafts.next", ("DraftsPage", "up"): "drafts.prev", ("DraftsPage", "new"): "drafts.new",
    ("DraftsPage", "delete"): "drafts.delete", ("DraftsPage", "copy"): "drafts.copy",
    ("DraftsPage", "send"): "draft.send", ("DraftsPage", "save"): "draft.save",  # from the list too (D78)
    ("PausedPage", "resume"): "today.r", ("PausedPage", "finish"): "today.f", ("TodayPage", "next_list"): "today.tab",
    ("TablePage", "down"): "people.next", ("TablePage", "up"): "people.prev",
    ("PeoplePage", "merge"): "people.merge", ("PeoplePage", "enroll"): "person.enroll",
    ("CasesPage", "new_case"): "cases.new", ("PeoplePage", "collapse_all"): "people.collapse_all",
    ("OrganizationPage", "stage"): "organization.stage", ("OrganizationPage", "value"): "organization.value",
    ("OrganizationPage", "people"): "organization.people",
    ("BoardColumn", "cursor_down"): "pipeline.next", ("BoardColumn", "cursor_up"): "pipeline.prev",
    ("BoardColumn", "stage"): "pipeline.stage", ("BoardColumn", "value"): "pipeline.value",
    ("PipelinePage", "copy"): "pipeline.copy",
    ("PersonPage", "down"): "person.next", ("PersonPage", "up"): "person.prev", ("PersonPage", "block"): "person.block",
    ("PersonPage", "rename"): "person.rename", ("PersonPage", "add"): "person.add", ("PersonPage", "remove"): "person.remove",
    ("PersonPage", "enroll"): "person.enroll", ("PersonPage", "resume"): "person.resume",
    ("PersonPage", "finish"): "person.finish", ("PersonPage", "organizations"): "person.organizations",
    ("PersonPage", "stage"): "person.stage", ("PersonPage", "value"): "person.value",
    ("PersonPage", "pick(1)"): "person.pick_first", ("PersonPage", "pick(0)"): "person.pick_second",
    ("ChannelsPage", "add"): "connection.add", ("ChannelsPage", "delete"): "connection.delete",
    ("CasePage", "down"): "case.next", ("CasePage", "up"): "case.prev", ("CasePage", "toggle"): "case.toggle",
    ("CasePage", "describe"): "case.describe",
    ("TasksPage", "down"): "tasks.next", ("TasksPage", "up"): "tasks.prev", ("TasksPage", "done"): "tasks.done",
    ("TasksPage", "skip"): "tasks.skip", ("TasksPage", "new_thread"): "tasks.new_thread",
    ("TasksPage", "person"): "tasks.person", ("TasksPage", "copy"): "tasks.copy",
    ("TasksPage", "pick(1)"): "tasks.pick_first", ("TasksPage", "pick(0)"): "tasks.pick_second",
    ("SequencesTable", "new_sequence"): "sequences.new", ("SequencesTable", "archive"): "sequences.archive",
    ("SequencesTable", "delete_sequence"): "sequences.delete", ("SequencesTable", "rename"): "sequences.rename",
    ("SequencesTable", "describe"): "sequences.describe", ("SequencesTable", "sends_from"): "sequences.sends_from",
    ("StepsTable", "edit"): "sequences.enter", ("StepsTable", "new_step"): "sequences.new",
    ("StepsTable", "delete_step"): "sequences.delete", ("StepsTable", "move(1)"): "sequences.move_down",
    ("StepsTable", "move(-1)"): "sequences.move_up", ("StepsTable", "sequences"): "sequences.escape",
    ("SequencesTable", "steps"): "sequences.enter",
    ("SequencesPage", "down"): "sequences.next", ("SequencesPage", "up"): "sequences.prev",
    ("MonthGrid", "move(-1)"): "calendar.prev_day", ("MonthGrid", "move(1)"): "calendar.next_day",
    ("MonthGrid", "move(7)"): "calendar.next_week", ("MonthGrid", "move(-7)"): "calendar.prev_week",
    ("MonthGrid", "month(-1)"): "calendar.prev_month", ("MonthGrid", "month(1)"): "calendar.next_month",
    ("MonthGrid", "today"): "calendar.today",
    ("DayList", "cursor_down"): "calendar.next_week", ("DayList", "cursor_up"): "calendar.prev_week",  # j/k in the day pane
    ("DayList", "join"): "day.join", ("MonthGrid", "day_pane"): "calendar.tab", ("DayList", "grid"): "calendar.tab",
    ("EventPage", "scroll(1)"): "event.scroll_down", ("EventPage", "scroll(-1)"): "event.scroll_up",
    ("EventPage", "join"): "event.join", ("EventPage", "respond"): "event.respond",
    ("MainScreen", "app.undo"): "nav.undo", ("MainScreen", "app.help"): "overlay.help",
    ("ThreadsPage", "scroll_thread(1)"): "thread.scroll_down", ("ThreadsPage", "scroll_thread(-1)"): "thread.scroll_up",
    ("MainScreen", "app.expand_all(True)"): "thread.expand_all", ("MainScreen", "app.expand_all(False)"): "thread.collapse_all",
    ("MainScreen", "go('today')"): "nav.today", ("MainScreen", "go('drafts')"): "nav.drafts",
    ("MainScreen", "go('people')"): "nav.people", ("MainScreen", "go('organizations')"): "nav.organizations",
    ("MainScreen", "go('cases:0')"): "nav.cases", ("MainScreen", "go('channels')"): "nav.channels",
    ("TablePage", "back"): "nav.back", ("PersonPage", "back"): "nav.back", ("ConnectionPage", "back"): "nav.back", ("ContextSourcePage", "back"): "nav.back",
    ("CasePage", "back"): "nav.back", ("MonthGrid", "back"): "calendar.escape", ("EventPage", "back"): "nav.back",
    ("MainScreen", "back"): "nav.back",
    ("MainScreen", "focus_pane(-1)"): "nav.focus_left", ("MainScreen", "focus_pane(1)"): "nav.focus_right",
}

TYPED = {  # (view, keystroke as the view's script compares it)
    ("Pick", "dismiss(None)"): ("overlay", "escape"), ("Ask", "dismiss(None)"): ("overlay", "escape"),
    ("Help", "dismiss(None)"): ("overlay", "escape"), ("PickFile", "dismiss(None)"): ("overlay", "escape"),
    ("ReplyBox", "send"): ("thread", "ctrl-enter"), ("ReplyBox", "save"): ("thread", "ctrl-s"),
    ("ReplyBox", "close"): ("thread", "escape"),
    ("DraftView", "send"): ("draft", "ctrl-enter"), ("DraftView", "save"): ("draft", "ctrl-s"),
    ("DraftView", "close"): ("draft", "escape"),
    ("DescriptionBox", "cancel"): ("overlay", "escape"),
    ("StepEditor", "save"): ("sequence", "ctrl-s"), ("StepEditor", "close"): ("sequence", "escape"),
}

WHY = {
    (THREAD, "refresh_reactions"): "v reads Jeremy's Slack reactions fresh each time it opens",
    ("AddressInput", "accept_completion"): "To and Cc take typed addresses; completion is not built in the App yet",
    ("ReplyBox", "attach"): "Files attach by dropping them on the window (FR-10)",
    ("DraftView", "attach"): "Files attach by dropping them on the window (FR-10)",
    ("DraftConnection", "choose"): "A click on the editor's Conn. row picks the Connection; the row takes no keyboard focus",
    ("PipelinePage", "scroll(1)"): SCROLLS, ("PipelinePage", "scroll(-1)"): SCROLLS,
    ("CasePage", "scroll(1)"): SCROLLS, ("CasePage", "scroll(-1)"): SCROLLS,
    ("DescriptionBox", "save"): "The description prompt saves on Enter",
    ("MainScreen", "app.quit"): "Close the window; the Local core keeps running (FR-2)",
    ("TasksPage", "filter"): "The Palette (/) replaced the Tasks filter (FR-BAR-4)",
    ("SettingsPage", "toggle"): "App Settings › Modules switches are clicked, as every App Settings control is (FR-SET-1)",
}


def console_bindings() -> set[tuple[str, str]]:
    """(declaring class, action) for every literal Binding in tui.py."""
    found = set()

    def walk(node, cls):
        for child in ast.iter_child_nodes(node):
            inner = child.name if isinstance(child, ast.ClassDef) else cls
            if (isinstance(child, ast.Call) and getattr(child.func, "id", None) == "Binding" and len(child.args) >= 2
                    and all(isinstance(a, ast.Constant) for a in child.args[:2])):
                found.add((cls or THREAD, child.args[1].value))
            walk(child, inner)

    walk(ast.parse((ROOT / "src" / "comms_terminal" / "tui.py").read_text()), None)
    return found


def view_source(view: str) -> str:
    """A view's main.js, with the shared files it imports (workspace.rs ships each beside it, as the Composer)."""
    src = (DEFAULTS / "views" / view / "main.js").read_text()
    shared = [f for f in (DEFAULTS / "views").glob("*.js") if f'from "./{f.name}"' in src]
    return src + "".join(f.read_text() for f in shared)


def test_every_console_binding_reaches_the_app_or_says_why():
    tables = (APP, TYPED, WHY)
    unplaced = sorted(b for b in console_bindings() if not any(b in t for t in tables))
    assert not unplaced, f"place these Console keys in APP, TYPED or WHY: {unplaced}"
    twice = sorted(b for b in console_bindings() if sum(b in t for t in tables) > 1)
    assert not twice, twice
    stale = sorted((set(APP) | set(TYPED) | set(WHY)) - console_bindings())
    assert not stale, f"entries for keys the Console no longer has: {stale}"


def test_each_mapped_command_is_bound_and_handled():
    bound = {action for section in json.loads((DEFAULTS / "keys.json").read_text()).values() for action in section.values()}
    for console, command in APP.items():
        assert command in bound, f"{console} maps to {command}, which keys.json does not bind"
        view, name = command.split(".", 1)
        assert f'"{name}"' in view_source(view), f"views/{view} does not handle {name!r} ({console})"


def test_each_typed_key_is_in_its_view():
    for console, (view, keystroke) in TYPED.items():
        assert f'"{keystroke}"' in view_source(view), f"views/{view} does not read {keystroke!r} ({console})"


def test_rejected_app_differences_are_removed():
    """PT-CONF-1: the dark theme, list filter fields, header folding and delete confirmations are gone (FR-CONF-5)."""
    views = ROOT / "app" / "defaults" / "views"
    assert not (ROOT / "app" / "defaults" / "themes" / "gruvbox.json").exists()
    assert "gruvbox" not in (ROOT / "app" / "defaults" / "AGENTS.md").read_text()
    for name in ("people", "organizations"):
        assert "InputState" not in (views / name / "main.js").read_text(), name
    assert "folded" not in (views / "threads" / "main.js").read_text()
    for name in ("drafts", "sequences"):
        assert "delete-picked" not in (views / name / "main.js").read_text(), name
