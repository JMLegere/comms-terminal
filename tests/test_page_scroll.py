"""Page keys retain one rendered row of context; synthetic Console data only."""

from datetime import datetime, timezone

import pytest
from textual.widgets import TextArea

from test_console import run, seeded
from universal_inbox import inbox
from universal_inbox.tui import Console


@pytest.mark.parametrize("wrapped", [False, True], ids=["lines", "wrapped-lines"])
def test_draft_body_page_keys_overlap_without_editing(tmp_path, wrapped):
    path, _ = seeded(tmp_path)
    body = "\n".join(f"{i:02d} " + ("word " * 90 if wrapped else "body text stays unchanged")
                     for i in range(8 if wrapped else 50))

    async def scenario(app, pilot):
        await pilot.press("6", "n")
        await pilot.pause()
        view = app.main.draft_view
        area = view.query_one(TextArea)
        area.text = body
        area.focus()
        area.move_cursor((0, 3))
        area.move_cursor((0, 7), select=True)
        await pilot.pause()
        height = area.scrollable_content_region.height
        stride = height - 1
        assert height > 1 and area.max_scroll_y > 2 * stride
        assert area.scroll_y == 0 and area.selected_text == body[3:7]
        last_visible = area.render_line(height - 1).text

        def cursor():
            return area.wrapped_document.location_to_offset(area.cursor_location)

        await pilot.press("pagedown")
        await pilot.pause()
        assert app.focused is area and area.scroll_y == stride
        assert cursor() == (7, stride) and area.selected_text == ""
        assert area.render_line(0).text == last_visible
        if wrapped:
            assert area.cursor_location[0] < stride  # visual rows, not logical lines
        else:
            assert area.cursor_location == (stride, 7)

        first_visible = area.render_line(0).text
        await pilot.press("pageup")
        await pilot.pause()
        assert area.scroll_y == 0 and area.cursor_location == (0, 7)
        assert area.render_line(height - 1).text == first_visible
        for key, expected in (("pageup", (7, 0)), ("down", (7, 1)), ("up", (7, 0))):
            await pilot.press(key)
            await pilot.pause()
            assert cursor() == expected and area.scroll_y == 0

        end = (area.document.line_count - 1, len(area.document[-1]))
        area.move_cursor(end)
        await pilot.pause()
        await pilot.press("pagedown", "pagedown")
        await pilot.pause()
        assert area.cursor_location == end and area.scroll_y == area.max_scroll_y
        assert area.text == body and view.values()["body"] == body

        area.styles.height = 1
        await pilot.pause()
        assert area.scrollable_content_region.height == 1
        area.move_cursor((0, 7))
        area.scroll_to(y=0, animate=False, immediate=True)
        await pilot.pause()
        for key, expected in (("pagedown", 1), ("pageup", 0)):
            await pilot.press(key)
            await pilot.pause()
            assert area.scroll_y == expected and cursor() == (7, expected)
        area.show_cursor = False
        for key, expected in (("pagedown", 1), ("pageup", 0)):
            await pilot.press(key)
            await pilot.wait_for_scheduled_animations()
            assert area.scroll_y == expected and area.cursor_location == (0, 7)
        assert area.text == body and app.focused is area

    run(Console(path), [scenario])


def test_thread_timeline_page_keys_dispatch_and_limits(tmp_path):
    path, ids = seeded(tmp_path)
    db = inbox.connect(path)
    body = "\n".join(f"Visible row {i:03d}: unchanged long text." for i in range(100))
    reference = db.execute(
        "SELECT native_id FROM activities WHERE thread_id = ? AND kind = 'MESSAGE' ORDER BY id LIMIT 1",
        (ids["ana"],)).fetchone()[0]
    thread = inbox.ingest(db, inbox.Incoming(
        1, "jeremy@agentmaple.com", "mailbox", "MESSAGE", "<page-scroll@example.test>",
        inbox.Identity("email", "ana@acme.com", "Ana Ruiz"), datetime.now(timezone.utc), body, [reference],
        data={"from": "ana@acme.com", "to": ["jeremy@agentmaple.com"]}))
    assert thread.id == ids["ana"]
    db.close()

    async def scenario(app, pilot):
        app.main.open_thread(ids["ana"])
        await pilot.pause()
        page = app.main.page
        pane = app.main.thread_view.query_one(".timeline")
        pane.scroll_to(y=0, animate=False, immediate=True)
        await pilot.pause()
        stride = pane.scrollable_content_region.height - 1
        assert stride > 0 and pane.max_scroll_y > 2 * stride

        pane.focus()
        await pilot.pause()
        for key, expected in (("pagedown", stride), ("pageup", 0), ("down", 1), ("up", 0)):
            await pilot.press(key)
            await pilot.wait_for_scheduled_animations()
            assert pane.scroll_y == expected and app.focused is pane
        table = page.query_one("#table")
        table.focus()
        await pilot.pause()
        selected = table.cursor_row
        await pilot.press("space")
        await pilot.wait_for_scheduled_animations()
        assert pane.scroll_y == stride and table.cursor_row == selected and app.focused is table

        for start, action, expected in ((pane.max_scroll_y - 1, pane.scroll_page_down, pane.max_scroll_y),
                                        (1, pane.scroll_page_up, 0)):
            pane.scroll_to(y=start, animate=False, immediate=True)
            for _ in range(2):
                action(animate=False)
                await pilot.pause()
                assert pane.scroll_y == expected

        pane.styles.height = 1
        pane.styles.padding = 0
        await pilot.pause()
        assert pane.scrollable_content_region.height == 1
        pane.scroll_page_down(animate=False)
        await pilot.pause()
        assert pane.scroll_y == 1
        pane.scroll_page_up(animate=False)
        await pilot.pause()
        assert pane.scroll_y == 0

    run(Console(path), [scenario])
