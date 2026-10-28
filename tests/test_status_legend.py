"""Status legends through native Textual widgets; synthetic data, no providers or sends."""

from datetime import datetime, timedelta, timezone

import pytest
from rich.text import Text
from textual.widgets import DataTable, Static

from test_console import run, seeded
from comms_terminal import inbox
from comms_terminal.icons import ICON
from comms_terminal.tui import Console, ThreadsPage


EXPECTED = {
    "Todo": (("CREATED", "Needs first response"), ("NEW_REPLY", "Needs next response"),
             ("IN_PROGRESS", "Investigating"), ("REPLY_DRAFT", "Reply Draft")),
    "Snoozed": (("WAITING_FOR_CUSTOMER", "Waiting for them"), ("WAITING_INDEFINITELY", "Paused for later")),
    "Done": (("DONE_MANUALLY_SET", "Done"),),
    "Ignored": (("IGNORED", "Ignored"),),
}
EXPECTED["All"] = tuple(entry for entries in EXPECTED.values() for entry in entries)


def check_legend(app, name):
    page = app.main.page
    assert isinstance(page, ThreadsPage)
    legend = page.query_one("#legend", Static)
    expected = " ".join(f"{ICON[detail]} {label.casefold()}" for detail, label in EXPECTED[name])
    content = Text.from_markup(str(legend.content)).plain
    assert " ".join(content.casefold().split()) == expected  # exact membership, order, and paused-entry dedupe
    assert legend.is_on_screen and legend.size.width > 0
    assert 0 <= legend.region.y < legend.region.bottom <= app.screen.size.height
    assert legend.region.right <= app.screen.size.width
    shown = " ".join(legend.render_line(y).text for y in range(legend.size.height))
    assert " ".join(shown.casefold().split()) == expected  # wrapping must not clip labels


@pytest.mark.parametrize("columns", [80, 187])
@pytest.mark.parametrize("populated", [False, True], ids=["empty", "seeded"])
def test_status_legend_follows_native_queue_navigation(tmp_path, columns, populated):
    path = seeded(tmp_path)[0] if populated else str(tmp_path / "inbox.db")
    sent = []
    app = Console(path, reply=lambda *args: sent.append(args))

    async def scenario(app, pilot):
        check_legend(app, "Todo")  # initial mount, before any navigation
        for key, name in (("3", "Snoozed"), ("4", "Done"), ("5", "Ignored"), ("2", "Todo")):
            await pilot.press(key)
            await pilot.pause()
            check_legend(app, name)
            assert app.view == int(key) - 2 and app.main.section == f"view:{int(key) - 2}"
            assert bool(app.main.page.rows) is populated
            assert app.focused is app.main.page.query_one(DataTable)
        await pilot.press("left")
        await pilot.pause()
        rail = app.main.rail
        assert app.focused is rail and rail.highlighted == rail.get_option_index("view:0")
        await pilot.press("down", "enter")
        await pilot.pause()
        assert app.view == 1 and app.main.section == "view:1"
        assert app.focused is app.main.page.query_one(DataTable)
        check_legend(app, "Snoozed")

    run(app, [scenario], size=(columns, 40))
    assert sent == []


def test_status_legend_refreshes_on_same_threads_page(tmp_path):
    path, ids = seeded(tmp_path)
    sent = []
    app = Console(path, reply=lambda *args: sent.append(args))

    async def scenario(app, pilot):
        app.main.open_thread(ids["ana"])
        await pilot.pause()
        page = app.main.page
        check_legend(app, "Todo")
        for action, name, view in ((inbox.Snooze(datetime.now(timezone.utc) + timedelta(days=1)), "Snoozed", 1), (inbox.Done(), "Done", 2),
                                   (inbox.Ignore(), "Ignored", 3), (inbox.Unignore(), "Todo", 0)):
            inbox.act(app.db, ids["ana"], action)
            app.main.open_thread(ids["ana"])  # existing pane takes refresh_data, not compose/remount
            await pilot.pause()
            assert app.main.page is page and app.view == view
            assert page.current_thread_id() == ids["ana"]
            assert app.focused is page.query_one(DataTable)
            check_legend(app, name)
        app.view = 4  # backend All remains valid; native key 5 still belongs to Drafts
        page.refresh_data()
        await pilot.pause()
        assert app.main.page is page
        check_legend(app, "All")

    run(app, [scenario], size=(187, 40))
    assert sent == []
