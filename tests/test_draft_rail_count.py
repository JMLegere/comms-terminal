"""Drafts rail counts follow local draft rows, not edits or page visits."""

import pytest
from textual.widgets import DataTable, TextArea

from universal_inbox.tui import Console
from test_console import run, seeded


@pytest.mark.parametrize("width", [80, 187])
def test_drafts_rail_count_tracks_local_lifecycle(tmp_path, width):
    path, _ = seeded(tmp_path)
    reply_calls = []
    app = Console(path, reply=lambda *args: reply_calls.append(args))

    def check(expected, *ids):
        rail = app.main.rail
        lines = [rail.render_line(y).text.rstrip() for y in range(rail.content_size.height)]
        draft = next(line for line in lines if "Drafts" in line)
        todo = next(line for line in lines if "Todo" in line)
        assert draft.endswith(f"{expected:>3}")
        assert len(draft) == len(todo)  # Same right-aligned count column as other views.
        assert ("▌" in draft) == (app.main.section == "drafts")
        assert {row["id"] for row in app.db.execute("SELECT id FROM composition_drafts")} == set(ids)
        if app.main.section == "drafts":
            page = app.main.page
            assert {row["id"] for row in page.items} == set(ids)
            table = page.query_one(DataTable)
            if ids:
                assert {key.value for key in table.rows} == {str(draft_id) for draft_id in ids}
            else:
                assert table.row_count == 1  # Empty-list placeholder, not a persisted draft.

    async def lifecycle(app, pilot):
        check(0)
        await pilot.press("5")
        await pilot.pause()
        assert app.main.section == "drafts"
        check(0)  # Opening Drafts must not create a blank row.
        assert app.selected_draft is None

        await pilot.press("n")
        await pilot.pause()
        first = app.selected_draft
        assert first is not None
        check(1, first)

        editor = app.main.query_one("#thread .body-editor", TextArea)
        editor.focus()
        await pilot.press(*"Local edit")
        await pilot.pause()
        assert editor.text == "Local edit"
        check(1, first)
        assert app.db.execute("SELECT body FROM composition_drafts WHERE id = ?", (first,)).fetchone()[0] == ""
        await pilot.press("ctrl+s")
        await pilot.pause()
        check(1, first)
        assert app.db.execute("SELECT body FROM composition_drafts WHERE id = ?", (first,)).fetchone()[0] == "Local edit"

        app.main.page.query_one(DataTable).focus()
        await pilot.press("n")
        await pilot.pause()
        second = app.selected_draft
        assert second is not None and second != first
        check(2, first, second)

        app.main.page.query_one(DataTable).focus()
        await pilot.press("x")
        await pilot.pause()
        check(1, first)
        await pilot.press("z")
        await pilot.pause()
        check(2, first, second)

        await pilot.press("1")
        await pilot.pause()
        assert app.main.section == "view:0"
        check(2, first, second)
        await pilot.press("5")
        await pilot.pause()
        assert app.main.section == "drafts"
        check(2, first, second)
        assert reply_calls == []  # No reply/send shortcut or provider path is exercised.

    run(app, [lifecycle], size=(width, 42))
