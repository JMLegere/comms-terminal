"""Horizontal arrows move pane focus, never selection or text-editor focus."""

from textual.widgets import Input, TextArea

from test_console import page, run, seeded
from universal_inbox import inbox
from universal_inbox.tui import Ask, CaseCard, Console, ReplyBox, ThreadsPage


async def check_editor_arrows(app, pilot, field):
    field.focus()
    if isinstance(field, Input):
        field.value = "abcd"
    else:
        field.text = "abcd"
    await pilot.pause()
    for start, key, end in ((2, "left", 1), (1, "right", 2),
                            (0, "left", 0), (4, "right", 4)):
        if isinstance(field, Input):
            field.cursor_position = start
        else:
            field.move_cursor((0, start))
        await pilot.pause()
        await pilot.press(key)
        await pilot.pause()
        assert app.focused is field, key
        cursor = field.cursor_position if isinstance(field, Input) else field.cursor_location
        assert cursor == (end if isinstance(field, Input) else (0, end)), key
    await pilot.press("left", "x")
    await pilot.pause()
    assert app.focused is field
    assert (field.value if isinstance(field, Input) else field.text) == "abcxd"


def test_arrows_move_between_panes_without_activating_or_changing_threads(tmp_path):
    path, _ = seeded(tmp_path)
    app = Console(path)

    async def navigate(app, pilot):
        await pilot.press("j", "enter")  # Enter opens the highlighted Thread beside the list
        await pilot.pause()
        current, rail, thread = page(app), app.main.rail, app.main.thread_view
        table, timeline = current.query_one("#table"), thread.query_one(".timeline")
        box = thread.query_one(ReplyBox)
        row, tid, view, stack = table.cursor_row, current.current_thread_id(), app.view, tuple(app.main.stack)
        assert row > 0 and tid is not None

        def unchanged():
            assert page(app) is current and app.main.thread_view is thread
            assert (table.cursor_row, current.current_thread_id(), app.view, tuple(app.main.stack)) == (row, tid, view, stack)
            assert not box.has_class("open")

        for key, focus, option in (("left", rail, "view:0"), ("left", rail, "view:0"),
                                   ("down", rail, "view:1"), ("up", rail, "view:0"),
                                   ("down", rail, "view:1"), ("right", table, None),
                                   ("right", timeline, None), ("right", timeline, None),
                                   ("left", table, None)):
            await pilot.press(key)
            await pilot.pause()
            assert app.focused is focus, key
            if option is not None:
                assert rail.get_option_at_index(rail.highlighted).id == option
            unchanged()

        app.main.query_one("#thread").display = False
        await pilot.pause()
        await pilot.press("right")
        await pilot.pause()
        assert app.focused is table
        unchanged()

        await pilot.press("left")
        await pilot.pause()
        assert app.focused is rail
        await pilot.press("down")
        await pilot.pause()
        assert rail.get_option_at_index(rail.highlighted).id == "view:1"
        unchanged()
        await pilot.press("enter")
        await pilot.pause()
        assert app.view == 1 and isinstance(page(app), ThreadsPage)
        assert page(app) is not current and app.focused is page(app).query_one("#table")

    run(app, [navigate])


def test_empty_views_and_browse_side_info_have_no_right_focus_target(tmp_path):
    app = Console(str(tmp_path / "empty.db"))

    async def navigate(app, pilot):
        for target in (*[f"view:{i}" for i in range(5)], "people", "organizations", "cases", "channels"):
            app.main.go(target)
            await pilot.pause()
            current, rail, stack = page(app), app.main.rail, tuple(app.main.stack)
            table = current.query_one("#table")
            row, view = table.cursor_row, app.view
            assert app.main.thread_view is None
            for key, focus in (("right", table), ("right", table), ("left", rail),
                               ("left", rail), ("right", table), ("right", table)):
                await pilot.press(key)
                await pilot.pause()
                assert app.focused is focus, target
                assert page(app) is current and app.main.thread_view is None
                assert (table.cursor_row, app.view, tuple(app.main.stack)) == (row, view, stack)

    run(app, [navigate])


def test_thread_reply_recipients_and_text_keep_native_horizontal_arrows(tmp_path):
    path, ids = seeded(tmp_path)
    app = Console(path)

    async def edit(app, pilot):
        app.main.open_thread(ids["ana"])
        await pilot.pause()
        current, thread = page(app), app.main.thread_view
        table, stack = current.query_one("#table"), tuple(app.main.stack)
        row, view = table.cursor_row, app.view
        await pilot.press("r")
        await pilot.pause()
        box = thread.query_one(ReplyBox)
        assert box.has_class("open")
        for selector in (".toinput", ".ccinput", ".reply"):
            await check_editor_arrows(app, pilot, box.query_one(selector))
            assert page(app) is current and app.main.thread_view is thread
            assert (table.cursor_row, app.view, tuple(app.main.stack)) == (row, view, stack)
            assert current.current_thread_id() == ids["ana"] and box.has_class("open")

    run(app, [edit])


def test_person_page_returns_from_rail_and_ask_modal_keeps_native_arrows(tmp_path):
    path, _ = seeded(tmp_path)
    app = Console(path)

    async def edit(app, pilot):
        app.main.go("people")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        current, rail, stack = page(app), app.main.rail, tuple(app.main.stack)
        table = current.query_one("#ids")
        row = table.cursor_row
        assert len(stack) == 2
        for key, focus in (("left", rail), ("down", rail), ("right", table), ("right", table)):
            await pilot.press(key)
            await pilot.pause()
            assert app.focused is focus
            assert page(app) is current and tuple(app.main.stack) == stack and table.cursor_row == row
        await pilot.press("a")
        await pilot.pause()
        modal = app.screen
        assert isinstance(modal, Ask)
        await check_editor_arrows(app, pilot, modal.query_one(Input))
        assert app.screen is modal and page(app) is current and tuple(app.main.stack) == stack
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is app.main and app.focused is table
        assert table.cursor_row == row and tuple(app.main.stack) == stack

    run(app, [edit])


def test_case_pane_focus_and_editors_preserve_selected_card_and_stack(tmp_path):
    path, ids = seeded(tmp_path)
    case_id = inbox.get_thread(inbox.connect(path), ids["ana"]).case_id
    app = Console(path)

    async def edit(app, pilot):
        app.action_open_case(case_id)
        await pilot.pause()
        await pilot.press("j")
        await pilot.pause()
        current, rail, stack = page(app), app.main.rail, tuple(app.main.stack)
        tid, view = current.current_thread_id(), app.view
        assert len(stack) == 2
        assert tid == list(current.query(CaseCard))[1].thread_id
        for key, focus in (("right", current), ("left", rail), ("down", rail),
                           ("up", rail), ("right", current), ("right", current)):
            await pilot.press(key)
            await pilot.pause()
            assert app.focused is focus
            assert page(app) is current
            assert (current.current_thread_id(), app.view, tuple(app.main.stack)) == (tid, view, stack)
        await pilot.press("r")
        await pilot.pause()
        card = next(c for c in current.query(CaseCard) if c.thread_id == tid)
        for selector in (".toinput", ".ccinput", ".reply"):
            await check_editor_arrows(app, pilot, card.query_one(selector))
            assert current.current_thread_id() == tid and page(app) is current
        await pilot.press("escape", "d")
        await pilot.pause()
        await check_editor_arrows(app, pilot, current.query_one(".desctext", TextArea))
        assert (current.current_thread_id(), app.view, tuple(app.main.stack)) == (tid, view, stack)

    run(app, [edit])


def test_enter_on_a_thread_row_moves_into_the_thread_and_x_ignores(tmp_path):
    path, _ = seeded(tmp_path)
    seen = {}

    async def scenario(app, pilot):
        await pilot.press("j")
        await pilot.pause()
        current = page(app)
        tid = current.current_thread_id()
        await pilot.press("enter")  # opens it; focus stays in the list
        await pilot.pause()
        seen["stayed"] = app.focused is current.query_one("#table")
        await pilot.press("enter")  # Enter on the open Thread moves into it
        await pilot.pause()
        seen["focus"] = app.focused is app.main.thread_view.query_one(".timeline")
        await pilot.press("left")
        await pilot.pause()
        await pilot.press("x")
        await pilot.pause()
        seen["ignored"] = inbox.get_thread(app.db, tid).status_detail
        await pilot.press("z")
        await pilot.pause()
        seen["restored"] = inbox.get_thread(app.db, tid).status_detail

    run(Console(path), [scenario])
    assert seen["stayed"] is True and seen["focus"] is True
    assert seen["ignored"] == "IGNORED"
    assert seen["restored"] != "IGNORED"
