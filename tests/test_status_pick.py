"""Change status from the queue: Investigating comes first, and the cursor moves on to the next Thread."""

from test_console import run, seeded
from universal_inbox import inbox
from universal_inbox.tui import Console, Pick, ThreadsPage


def test_investigating_first_and_cursor_moves_on(tmp_path):
    path, _ = seeded(tmp_path)
    seen = {}

    async def scenario(app, pilot):
        page = app.main.page
        assert isinstance(page, ThreadsPage)
        threads = [r["id"] for r in page.rows if r["id"] is not None
                   and inbox.get_thread(app.db, r["id"]).status_detail != "IN_PROGRESS"]
        moved, after = threads[0], threads[1]
        app.main.pending_select = moved
        page.refresh_data()
        await pilot.pause()
        assert page.current_thread_id() == moved
        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen, Pick)
        await pilot.press("enter")  # the first option
        await pilot.pause()
        seen["status"] = inbox.get_thread(app.db, moved).status_detail
        seen["selected"], seen["after"] = page.current_thread_id(), after

    run(Console(path), [scenario], size=(187, 40))
    assert seen["status"] == "IN_PROGRESS"
    assert seen["selected"] == seen["after"]
