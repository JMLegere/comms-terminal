"""Typed reply and Draft text reaches the store shortly after typing stops, without Esc or leaving the page;
ctrl+s in a reply saves it rather than offering to send."""
import asyncio

import pytest
from textual.widgets import TextArea

from test_console import run, seeded
from comms_terminal import inbox, tui
from comms_terminal.tui import Console, Pick


def stored_replies(path):
    db = inbox.connect(path)
    try:
        return set(inbox.reply_drafts(db).values())
    finally:
        db.close()


def test_reply_text_reaches_the_store_while_the_box_stays_open(tmp_path, monkeypatch):
    monkeypatch.setattr(tui, "AUTOSAVE_GAP", 0.05)
    path, ids = seeded(tmp_path)
    seen = {}

    async def open_logan(app, pilot):
        app.main.open_thread(ids["logan"])

    async def wait(app, pilot):
        await asyncio.sleep(0.3)
        await pilot.pause()
        seen["stored"] = stored_replies(path)
        seen["open"] = app.main.thread_view.query_one(".reply").text

    run(Console(path), [open_logan, "r", *"deploying", wait])
    assert "deploying" in seen["stored"] and seen["open"] == "deploying"


def test_ctrl_s_in_a_reply_saves_it_and_does_not_offer_to_send(tmp_path, monkeypatch):
    monkeypatch.setattr(tui, "AUTOSAVE_GAP", 60)  # only ctrl+s can have saved it
    path, ids = seeded(tmp_path)
    seen = {}

    async def open_logan(app, pilot):
        app.main.open_thread(ids["logan"])

    async def inspect(app, pilot):
        seen["picker"] = isinstance(app.screen, Pick)
        seen["stored"] = stored_replies(path)

    run(Console(path), [open_logan, "r", *"hello", "ctrl+s", inspect])
    assert not seen["picker"] and "hello" in seen["stored"]


def test_draft_text_reaches_the_store_while_the_editor_stays_open(tmp_path, monkeypatch):
    monkeypatch.setattr(tui, "AUTOSAVE_GAP", 0.05)
    path, _ = seeded(tmp_path)
    db = inbox.connect(path)
    draft = inbox.save_draft(db, dict(connection_id=None, kind="email", sender="", to="", cc="", subject="", body=""))
    seen = {}

    async def scenario(app, pilot):
        app.main.go("drafts")
        await pilot.pause()
        view = app.main.draft_view
        view.query_one(TextArea).focus()
        await pilot.press(*"hi there")
        await asyncio.sleep(0.3)
        await pilot.pause()
        seen["body"] = inbox.get_draft(db, draft["id"])["body"]
        seen["dirty"] = view.dirty

    run(Console(path), [scenario])
    assert seen == {"body": "hi there", "dirty": False}
