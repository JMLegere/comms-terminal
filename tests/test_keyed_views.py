"""Views repaint only what changed (ADR 0012, Phase 6 of the 2026-10-29 sync PRD): a refresh with the same rows leaves
the lists and the Thread pane as painted, and a move to a neighbouring Thread paints from a view read ahead."""

import time

from textual.widgets import DataTable

from test_console import run, seeded
from comms_terminal.tui import Console


def test_a_refresh_with_the_same_rows_leaves_the_list_and_cursor_alone(tmp_path, monkeypatch):
    path, _ = seeded(tmp_path)
    seen, cleared = {}, []
    clear = DataTable.clear

    def counted(self, columns=False):
        cleared.append(self)
        return clear(self, columns)
    monkeypatch.setattr(DataTable, "clear", counted)

    async def scenario(app, pilot):
        await pilot.press("2")  # Todo
        await pilot.pause()
        table = app.main.current.query_one(DataTable)
        table.move_cursor(row=app.main.current.selectable_rows()[1])
        await pilot.pause()
        seen["before"] = (table.cursor_row, app.main.current.current_thread_id())
        cleared.clear()
        app.main.refresh_all()
        await pilot.pause()
        seen["after"] = (table.cursor_row, app.main.current.current_thread_id())
        seen["cleared"] = [t for t in cleared if t is table]

    run(Console(path), [scenario])
    assert seen["after"] == seen["before"] and seen["cleared"] == []


def test_a_move_paints_the_neighbour_from_the_view_read_ahead(tmp_path):
    path, _ = seeded(tmp_path)
    seen = {}

    async def scenario(app, pilot):
        await pilot.press("2")
        await pilot.pause()
        page = app.main.current
        await pilot.press("j")
        await pilot.pause()
        row = page.query_one(DataTable).cursor_row
        ahead = next(r["id"] for r in page.rows[row + 1:] if not r.get("header"))
        for _ in range(50):  # the reader thread
            if ahead in app.__dict__.get("_prefetched", {}):
                break
            time.sleep(0.02)
        seen["read ahead"] = ahead in app._prefetched
        page.query_one(DataTable).move_cursor(row=[r["id"] for r in page.rows].index(ahead))  # past any header
        await pilot.pause(0.3)  # past PREVIEW_GAP: the held-key debounce paints where the cursor stops
        seen["used"] = (app.main.thread_view.thread_id == ahead, ahead not in app._prefetched)

    run(Console(path), [scenario])
    assert seen == {"read ahead": True, "used": (True, True)}
