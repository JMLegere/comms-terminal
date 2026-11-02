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

NO_STACK = "No page stack: the rail and keys 1–0 reach every page, and Escape leaves a text field"
SCROLLS = "Panes scroll with the wheel or trackpad"

APP = {
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
    ("TablePage", "down"): "people.next", ("TablePage", "up"): "people.prev",
    ("PeoplePage", "merge"): "people.merge", ("PeoplePage", "enroll"): "person.enroll",
    ("CasesPage", "new_case"): "cases.new",
    ("OrganizationPage", "stage"): "organization.stage", ("OrganizationPage", "value"): "organization.value",
    ("OrganizationPage", "people"): "organization.people",
    ("BoardColumn", "cursor_down"): "pipeline.next", ("BoardColumn", "cursor_up"): "pipeline.prev",
    ("BoardColumn", "stage"): "organization.stage", ("BoardColumn", "value"): "organization.value",
    ("PipelinePage", "copy"): "pipeline.copy",
    ("PersonPage", "down"): "person.next", ("PersonPage", "up"): "person.prev", ("PersonPage", "block"): "person.block",
    ("PersonPage", "rename"): "person.rename", ("PersonPage", "add"): "person.add", ("PersonPage", "remove"): "person.remove",
    ("PersonPage", "enroll"): "person.enroll", ("PersonPage", "resume"): "person.resume",
    ("PersonPage", "finish"): "person.finish", ("PersonPage", "organizations"): "person.organizations",
    ("PersonPage", "pick(1)"): "person.pick_first", ("PersonPage", "pick(0)"): "person.pick_second",
    ("ChannelsPage", "add"): "connection.add", ("ChannelsPage", "delete"): "connection.delete",
    ("CasePage", "down"): "case.next", ("CasePage", "up"): "case.prev", ("CasePage", "toggle"): "case.open",
    ("CasePage", "describe"): "case.describe",
    ("TasksPage", "down"): "tasks.next", ("TasksPage", "up"): "tasks.prev", ("TasksPage", "done"): "tasks.done",
    ("TasksPage", "skip"): "tasks.skip", ("TasksPage", "new_thread"): "tasks.new_thread",
    ("TasksPage", "filter"): "tasks.filter", ("TasksPage", "person"): "tasks.person", ("TasksPage", "copy"): "tasks.copy",
    ("TasksPage", "pick(1)"): "tasks.pick_first", ("TasksPage", "pick(0)"): "tasks.pick_second",
    ("SequencesTable", "new_sequence"): "sequences.new", ("SequencesTable", "archive"): "sequences.archive",
    ("SequencesTable", "delete_sequence"): "sequences.delete", ("SequencesTable", "rename"): "sequences.rename",
    ("SequencesTable", "describe"): "sequences.describe", ("SequencesTable", "sends_from"): "sequences.sends_from",
    ("StepsTable", "edit"): "sequence.edit", ("StepsTable", "new_step"): "sequence.new_step",
    ("StepsTable", "delete_step"): "sequence.delete_step", ("StepsTable", "move(1)"): "sequence.move_down",
    ("StepsTable", "move(-1)"): "sequence.move_up",
    ("SequencesPage", "down"): "sequences.next", ("SequencesPage", "up"): "sequences.prev",
    ("MonthGrid", "move(-1)"): "calendar.prev_day", ("MonthGrid", "move(1)"): "calendar.next_day",
    ("MonthGrid", "move(7)"): "calendar.next_week", ("MonthGrid", "move(-7)"): "calendar.prev_week",
    ("MonthGrid", "month(-1)"): "calendar.prev_month", ("MonthGrid", "month(1)"): "calendar.next_month",
    ("MonthGrid", "today"): "calendar.today",
    ("DayList", "cursor_down"): "calendar.next", ("DayList", "cursor_up"): "calendar.prev", ("DayList", "join"): "event.join",
    ("EventPage", "join"): "event.join", ("EventPage", "respond"): "event.respond",
    ("MainScreen", "app.undo"): "thread.undo", ("MainScreen", "app.help"): "overlay.help",
    ("MainScreen", "go('today')"): "nav.today", ("MainScreen", "go('drafts')"): "nav.drafts",
    ("MainScreen", "go('people')"): "nav.people", ("MainScreen", "go('organizations')"): "nav.organizations",
    ("MainScreen", "go('cases:0')"): "nav.cases", ("MainScreen", "go('channels')"): "nav.channels",
}

TYPED = {  # (view, keystroke as the view's script compares it)
    ("Pick", "dismiss(None)"): ("overlay", "escape"), ("Ask", "dismiss(None)"): ("overlay", "escape"),
    ("Help", "dismiss(None)"): ("overlay", "escape"), ("PickFile", "dismiss(None)"): ("overlay", "escape"),
    ("ReplyBox", "send"): ("thread", "ctrl-enter"), ("ReplyBox", "save"): ("thread", "ctrl-s"),
    ("ReplyBox", "close"): ("thread", "escape"),
    ("DraftView", "send"): ("draft", "ctrl-enter"), ("DraftView", "save"): ("draft", "ctrl-s"),
    ("DraftView", "close"): ("draft", "escape"),
    ("DraftsPage", "send"): ("draft", "ctrl-enter"), ("DraftsPage", "save"): ("draft", "ctrl-s"),
    ("DescriptionBox", "cancel"): ("overlay", "escape"),
    ("StepEditor", "save"): ("sequence", "ctrl-s"), ("StepEditor", "close"): ("sequence", "escape"),
}

WHY = {
    (THREAD, "refresh_reactions"): "v reads Jeremy's Slack reactions fresh each time it opens",
    ("ThreadsPage", "scroll_thread(1)"): SCROLLS, ("ThreadsPage", "scroll_thread(-1)"): SCROLLS,
    ("AddressInput", "accept_completion"): "To and Cc take typed addresses; completion is not built in the App yet",
    ("ReplyBox", "attach"): "Files attach by dropping them on the window (FR-10)",
    ("DraftView", "attach"): "Files attach by dropping them on the window (FR-10)",
    ("DraftConnection", "choose"): "A new Draft picks its Connection when n starts it",
    ("TablePage", "back"): NO_STACK, ("PersonPage", "back"): NO_STACK, ("ConnectionPage", "back"): NO_STACK,
    ("CasePage", "back"): NO_STACK, ("MonthGrid", "back"): NO_STACK, ("EventPage", "back"): NO_STACK,
    ("StepsTable", "sequences"): NO_STACK, ("MainScreen", "back"): NO_STACK,
    ("PeoplePage", "collapse_all"): "The App's People list is one flat, filterable list",
    ("PipelinePage", "scroll(1)"): SCROLLS, ("PipelinePage", "scroll(-1)"): SCROLLS,
    ("CasePage", "scroll(1)"): SCROLLS, ("CasePage", "scroll(-1)"): SCROLLS,
    ("EventPage", "scroll(1)"): SCROLLS, ("EventPage", "scroll(-1)"): SCROLLS,
    ("DescriptionBox", "save"): "The description prompt saves on Enter",
    ("SequencesTable", "steps"): "A Sequence's Steps show beside the list as soon as it is selected",
    ("PausedPage", "resume"): "Paused Enrollments show on Today; their Person page resumes them (r)",
    ("PausedPage", "finish"): "Paused Enrollments show on Today; their Person page finishes them (f)",
    ("TodayPage", "next_list"): "j/k run on from one Today list into the next",
    ("MonthGrid", "day_pane"): "The chosen day's Events show under the month; n/p pick one",
    ("DayList", "grid"): "The month and the day's Events share one pane",
    ("MainScreen", "app.quit"): "Close the window; the Local core keeps running (FR-2)",
    ("MainScreen", "focus_pane(-1)"): "No pane focus: each page's keys reach its list and its detail",
    ("MainScreen", "focus_pane(1)"): "No pane focus: each page's keys reach its list and its detail",
    ("MainScreen", "app.expand_all(True)"): "The App shows every message expanded",
    ("MainScreen", "app.expand_all(False)"): "The App shows every message expanded",
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
    return (DEFAULTS / "views" / view / "main.js").read_text()


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
