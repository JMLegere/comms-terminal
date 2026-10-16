"""Local Drafts UI checks; no service, provider, or real-send path."""

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from threading import Event
from types import SimpleNamespace

import pytest
from textual.widgets import DataTable, Input, OptionList, TextArea
from textual.worker import get_current_worker

from test_console import run, seeded
from test_pane_arrows import check_editor_arrows
from test_reconcile_console import bridge_console, seed_console
from universal_inbox import files, inbox, serve
from universal_inbox.tui import (DRAFT_FIELDS, Console, DraftConnection, DraftSendError, DraftsPage,
                                 Pick, ReplyBox, ReplyError, ThreadsPage, VIEW_NAMES)


async def choose(app, pilot, oid):
    menu = app.screen.query_one(OptionList)
    menu.highlighted = menu.get_option_index(oid)
    await pilot.press("enter")
    await pilot.pause()


async def type_address(field, pilot, value):
    field.focus()
    await pilot.pause()
    await pilot.press("ctrl+shift+a", "backspace", *value)
    await pilot.pause()


def completion_seeded(tmp_path):
    path, ids = seeded(tmp_path)
    db = inbox.connect(path)
    at = datetime(2026, 10, 14, tzinfo=timezone.utc)
    with db:
        db.executemany(
            "INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [(3, "quo", "+12025550100", "+12025550100", "Work", at.isoformat()),
             (4, "fastmail", "alias@example.test", "alias@example.test", "Alias", at.isoformat())])
        db.execute("INSERT INTO identities (kind, value) VALUES ('phone', '+12025550124')")
        db.execute(
            "INSERT INTO activities (thread_id, kind, occurred_at, native_id, content, data)"
            " VALUES (?, 'MESSAGE', ?, 'completion-outgoing', 'body-only@example.test', ?)",
            (ids["ana"], at.isoformat(), json.dumps({
                "to": ["Captured Person <captured-to@example.test>"],
                "cc": ["captured-cc@example.test"], "subject": "subject-only@example.test"})))
    inbox.save_draft(db, dict(connection_id=None, kind="email", sender="saved-sender@example.test",
                             to="Saved Recipient <saved-to@example.test>", cc="saved-cc@example.test",
                             subject="subject-only@example.test", body="body-only@example.test"))
    inbox.save_draft(db, dict(connection_id=3, kind="sms", sender="+12025550888",
                             to="+12025550777", cc="", subject=None, body="unsent SMS"))
    inbox.ingest(db, inbox.Incoming(3, "+12025550999", "im", "MESSAGE", "completion-sms",
                                   None, at, "captured SMS", data={"peer": "+12025550999"}))
    assert db.execute("SELECT 1 FROM identities WHERE value IN"
                      " ('captured-to@example.test', 'captured-cc@example.test', '+12025550999')").fetchone() is None
    db.close()
    return path, ids


@pytest.mark.parametrize("columns", [187, 80])
def test_drafts_entry_creation_native_focus_and_status_navigation(tmp_path, columns):
    path, _ = seeded(tmp_path)
    sent = []
    app = Console(path, reply=lambda *args: sent.append(args))

    async def scenario(app, pilot):
        counts = tuple(app.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                       for table in ("threads", "activities"))
        all_threads = inbox.queue(app.db, "All")
        await pilot.press("5")
        await pilot.pause()
        page = app.main.page
        assert isinstance(page, DraftsPage) and app.main.section == "drafts"
        rail_ids = [app.main.rail.get_option_at_index(i).id for i in range(app.main.rail.option_count)]
        assert rail_ids.count("drafts") == 1 and "view:4" not in rail_ids
        assert app.main.rail.region.width == 30 and VIEW_NAMES == list(inbox.VIEWS)
        assert inbox.list_drafts(app.db) == [] and app.main.thread_view is None
        await pilot.press("5")
        await pilot.pause()
        assert app.main.page is page and inbox.list_drafts(app.db) == []
        await pilot.press("n")
        await pilot.pause()
        view = app.main.draft_view
        assert view is not None and len(inbox.list_drafts(app.db)) == 1
        assert view.values() == dict(connection_id=None, kind="email", sender="", to="", cc="", subject="", body="")
        assert not app.main.query(ReplyBox) and app.main.thread_view is None
        assert not hasattr(view, "thread_id")
        assert view.query_one(TextArea).size.height == 8
        assert all(row.size.height == 1 for row in view.query(".field"))
        table, timeline, rail = page.query_one("#table"), view.query_one(".timeline"), app.main.rail
        page.focus_first()
        await pilot.pause()
        for key, focus in (("left", rail), ("right", table), ("right", timeline), ("left", table)):
            await pilot.press(key)
            await pilot.pause()
            assert app.focused is focus and app.main.draft_view is view
        for field in (view.query_one(".toinput", Input), view.query_one(TextArea)):
            await check_editor_arrows(app, pilot, field)
            assert app.main.draft_view is view
        area = view.query_one(TextArea)
        area.text = "unsent words"
        area.focus()
        await pilot.pause()
        await pilot.press("ctrl+enter")
        await pilot.pause()
        assert sent == [] and area.text == "unsent words"
        assert "Sending unavailable" in str(view.query_one(".draftnotice").render())
        app.main.go("drafts")
        await pilot.pause()
        assert app.main.page is page and app.main.draft_view is view and view.dirty
        did = view.draft_id
        for index in range(4):
            await pilot.press(str(index + 1))
            await pilot.pause()
            assert isinstance(app.main.page, ThreadsPage) and app.view == index
        await pilot.press("5")
        await pilot.pause()
        assert app.main.draft_view.draft_id == did
        assert app.main.draft_view.query_one(TextArea).text == "unsent words"
        assert inbox.get_draft(app.db, did)["body"] == "unsent words"
        assert inbox.queue(app.db, "All") == all_threads and sent == []
        assert counts == tuple(app.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                               for table in ("threads", "activities"))

    run(app, [scenario], size=(columns, 40))


def test_full_envelope_save_restart_and_explicit_format_switch(tmp_path):
    path, ids = seeded(tmp_path)
    db = inbox.connect(path)
    with db:
        db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                   " VALUES (3, 'quo', '+12025550100', '+12025550100', 'Work', '2026-10-14T00:00:00+00:00')")
    saved = {}

    async def edit(app, pilot):
        await pilot.press("5", "n")
        await pilot.pause()
        view = app.main.draft_view
        view.query_one(DraftConnection).focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        menu = app.screen.query_one(OptionList)
        assert "2" in [menu.get_option_at_index(i).id for i in range(menu.option_count)]  # Slack Drafts
        await choose(app, pilot, "1")
        assert view.values()["sender"] == "jeremy@agentmaple.com"
        values = dict(connection_id=1, kind="email", sender="jeremy@agentmaple.com", to="  ANA@acme.com  ",
                      cc=" ops@acme.com ", subject="  Exact subject  ", body="\n Exact words\nwith trailing space \n")
        for name in ("to", "cc", "subject"):
            view.query_one(f".{name}input", Input).value = values[name]
        view.query_one(TextArea).text = values["body"]
        view.query_one(TextArea).focus()
        await pilot.pause()
        assert ids["ana"] in [v["thread"].id for v in view.history_views]
        await pilot.press("ctrl+s")
        await pilot.pause()
        saved.update(id=view.draft_id, values=values)
        assert {k: inbox.get_draft(app.db, view.draft_id)[k] for k in DRAFT_FIELDS} == values
        assert not view.dirty

    run(Console(path), [edit])

    async def resume(app, pilot):
        await pilot.press("5")
        await pilot.pause()
        view = app.main.draft_view
        assert view.draft_id == saved["id"] and view.values() == saved["values"]
        view.query_one(DraftConnection).focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await choose(app, pilot, "3")
        assert isinstance(app.screen, Pick) and view.kind == "email"
        await choose(app, pilot, "cancel")
        assert view.values() == saved["values"]
        view.query_one(DraftConnection).focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await choose(app, pilot, "3")
        await choose(app, pilot, "clear")
        assert view.kind == "sms" and view.values()["subject"] is None and view.values()["cc"] == ""
        assert view.values()["sender"] == "+12025550100"  # the Connection's own number
        assert not view.query_one(".field.subject").display and not view.query_one(".field.cc").display
        view.query_one(".toinput", Input).value = "+12025550124"
        view.query_one(TextArea).focus()
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()
        row = inbox.get_draft(app.db, view.draft_id)
        assert row["kind"] == "sms" and row["connection_id"] == 3 and row["subject"] is None and row["cc"] == ""

    run(Console(path), [resume])

    async def resume_sms(app, pilot):
        await pilot.press("5")
        await pilot.pause()
        view = app.main.draft_view
        assert view.draft_id == saved["id"] and view.kind == "sms"
        assert view.values()["to"] == "+12025550124" and view.values()["body"] == saved["values"]["body"]
        assert view.values()["subject"] is None and not view.query_one(".field.subject").display

    run(Console(path), [resume_sms])


def test_failed_stale_save_and_history_failure_preserve_editor_and_selection(tmp_path, monkeypatch):
    path, _ = seeded(tmp_path)
    db = inbox.connect(path)
    values = dict(connection_id=None, kind="email", sender="", to="new@inbox.example", cc="", subject="", body="")
    inbox.save_draft(db, values)
    latest = inbox.save_draft(db, values)

    async def scenario(app, pilot):
        await pilot.press("5")
        await pilot.pause()
        page, view = app.main.page, app.main.draft_view
        assert view.draft_id == latest["id"]
        view.query_one(".subjectinput", Input).value = "selection save"
        await pilot.press("j")
        await pilot.pause()
        assert app.main.draft_view.draft_id != latest["id"]
        await pilot.press("k")
        await pilot.pause()
        view = app.main.draft_view
        assert view.draft_id == latest["id"]
        assert inbox.get_draft(app.db, view.draft_id)["subject"] == "selection save"
        assert "No captured match" in str(view.query_one(".historynotice").render())
        area = view.query_one(TextArea)
        area.text = "newest unsaved words"
        area.focus()
        await pilot.pause()
        original = view.values()
        with monkeypatch.context() as patch:
            def fail(*args, **kwargs):
                raise RuntimeError("injected local storage failure")
            patch.setattr(inbox, "save_draft", fail)
            await pilot.press("ctrl+s", "escape")
            app.main.go("view:1")
            await app.action_quit()
            page.action_new()
            assert len(inbox.list_drafts(app.db)) == 2
            page.query_one("#table").move_cursor(row=1)
            await pilot.pause()
            assert app.main.page is page and app.main.draft_view is view and not app._exit
            assert view.values() == original and view.save_error
            assert page.selected()["id"] == view.draft_id
            assert inbox.get_draft(app.db, view.draft_id)["body"] == ""
            assert "Save failed" in str(view.query_one(".savenotice").render())
        with monkeypatch.context() as patch:
            def history_fail(*args):
                raise RuntimeError("injected history failure")
            patch.setattr(inbox, "recipient_threads", history_fail)
            view.refresh_history(force=True)
            await pilot.pause()
            assert "Captured history unavailable" in str(view.query_one(".historynotice").render())
            assert "External history unavailable" in str(view.query_one(".historynotice").render())
            assert view.values() == original
        external = inbox.connect(path)
        newer = inbox.save_draft(external, dict(values, body="newer stored words"), draft_id=view.draft_id,
                                  expected_revision=view.draft["revision"])
        app.check_changes()
        await pilot.pause()
        assert view.values() == original
        area.focus()
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert "Save conflict" in str(view.query_one(".savenotice").render())
        assert view.values() == original and inbox.get_draft(app.db, view.draft_id) == newer
        app.main.go("drafts")
        await pilot.pause()
        assert app.main.page is page and app.main.draft_view is view and view.values() == original
        await pilot.press("ctrl+r")
        await pilot.pause()
        await choose(app, pilot, "keep")
        assert view.values() == original and view.save_error
        await pilot.press("ctrl+r")
        await pilot.pause()
        await choose(app, pilot, "reload")
        assert not view.dirty and not view.save_error
        assert view.values() == {k: newer[k] for k in DRAFT_FIELDS}
        assert inbox.get_draft(app.db, view.draft_id) == newer

    run(Console(path), [scenario])


@pytest.mark.parametrize("leave", ["navigate", "quit"])
@pytest.mark.parametrize("failure", ["list", "reload"])
def test_failed_creation_refresh_still_saves_visible_editor(tmp_path, monkeypatch, leave, failure):
    path, _ = seeded(tmp_path)
    db = inbox.connect(path)
    values = dict(connection_id=None, kind="email", sender="", to="", cc="", subject="", body="")
    original = inbox.save_draft(db, values)
    db.close()

    async def scenario(app, pilot):
        await pilot.press("5")
        await pilot.pause()
        view = app.main.draft_view
        with monkeypatch.context() as patch:
            get_draft = inbox.get_draft
            def fail(db, *args):
                if failure == "list":
                    raise RuntimeError("injected list failure")
                if not db.in_transaction and args[0] != original["id"]:
                    raise sqlite3.OperationalError("injected reload failure")
                return get_draft(db, *args)
            patch.setattr(inbox, "list_drafts" if failure == "list" else "get_draft", fail)
            app.main.page.action_new()
            await pilot.pause()
            assert app.main.draft_view is view
            assert app.main.page.selected()["id"] == original["id"]
            view.query_one(TextArea).text = "Keep visible unsent words"
            await pilot.pause()
            if leave == "quit":
                await app.action_quit()
            else:
                app.main.go("view:0")
                await pilot.pause()
            assert inbox.get_draft(app.db, original["id"])["body"] == "Keep visible unsent words"

    run(Console(path), [scenario])


def test_untouched_mixed_newline_body_survives_field_save_and_navigation(tmp_path):
    path, _ = seeded(tmp_path)
    db = inbox.connect(path)
    body = "first\r\nsecond\nthird\rfourth\r\n"
    saved = inbox.save_draft(db, dict(connection_id=None, kind="email", sender="", to="", cc="",
                                     subject="", body=body))
    db.close()

    async def scenario(app, pilot):
        await pilot.press("5")
        await pilot.pause()
        view = app.main.draft_view
        assert view.values()["body"] == body and not view.dirty
        view.query_one(".subjectinput", Input).value = "Only subject edited"
        await pilot.pause()
        assert view.save()
        app.main.go("view:0")
        await pilot.pause()
        assert inbox.get_draft(app.db, saved["id"])["body"] == body
        await pilot.press("5")
        await pilot.pause()
        assert app.main.draft_view.values()["body"] == body and not app.main.draft_view.dirty

    run(Console(path), [scenario])


def test_draft_address_ghosts_complete_exact_suffix_and_save_only_on_request(tmp_path):
    path, ids = completion_seeded(tmp_path)
    sent = []

    async def scenario(app, pilot):
        await pilot.press("5", "n")
        await pilot.pause()
        view = app.main.draft_view
        before = inbox.get_draft(app.db, view.draft_id)
        counts = tuple(app.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                       for table in ("threads", "activities", "identities", "persons"))
        cases = [
            ("to", "  X@Y.IO  ,  ANA@", "  X@Y.IO  ,  ANA@acme.com"),
            ("cc", '"X, Y" <x@y.io>,  OPS@', '"X, Y" <x@y.io>,  OPS@acme.com'),
            ("to", "SAVED-TO@", "SAVED-TO@example.test"),
            ("cc", "SAVED-CC@", "SAVED-CC@example.test"),
            ("to", "CAPTURED-TO@", "CAPTURED-TO@example.test"),
            ("cc", "CAPTURED-CC@", "CAPTURED-CC@example.test"),
        ]
        for name, prefix, completed in cases:
            field = view.query_one(f".{name}input", Input)
            await type_address(field, pilot, prefix)
            assert field.value == prefix and view.dirty
            assert completed[len(prefix):] in field.render_line(0).text
            assert inbox.get_draft(app.db, view.draft_id) == before and sent == []
            await pilot.press("tab")
            await pilot.pause()
            assert app.focused is field and field.value == completed
            assert field.cursor_position == len(completed) and field.selection.is_empty
            assert view.connection_id is None and inbox.get_draft(app.db, view.draft_id) == before
        assert [v["thread"].id for v in view.history_views] == [ids["ana"]]
        values = view.values()
        await pilot.press("ctrl+s")
        await pilot.pause()
        saved = inbox.get_draft(app.db, view.draft_id)
        assert {k: saved[k] for k in DRAFT_FIELDS} == values and not view.dirty
        assert saved["revision"] == before["revision"] + 1
        assert counts == tuple(app.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                               for table in ("threads", "activities", "identities", "persons"))
        # Inject after history renders; malformed envelope shapes exercise completion only.
        with app.db:
            app.db.execute(
                "INSERT INTO activities (thread_id, kind, occurred_at, native_id, data)"
                " VALUES (?, 'MESSAGE', ?, 'completion-malformed', ?)",
                (ids["ana"], saved["updated_at"], json.dumps({
                    "to": [None, 42, {"email": "metadata-only@example.test"}, "broken@@example.test"],
                    "cc": {"address": "metadata-only@example.test"}})))
        counts = tuple(app.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                       for table in ("threads", "activities", "identities", "persons"))
        for name, prefix in (("to", "+120255509"),
                             ("to", "body-only@"), ("to", "subject-only@"),
                             ("to", "metadata-only@"), ("to", "broken@"),
                             ("to", "Saved Recipient"), ("subject", "ANA@")):
            field = view.query_one(f".{name}input", Input)
            await type_address(field, pilot, prefix)
            assert field.render_line(0).text.rstrip() == prefix
            await pilot.press("tab")
            await pilot.pause()
            target = view.query_one(TextArea) if name == "subject" else view.query_one(".ccinput", Input)
            assert app.focused is target and field.value == prefix
        assert inbox.get_draft(app.db, view.draft_id) == saved and sent == []
        assert view.connection_id is None and view.query_one(TextArea).text == ""
        assert counts == tuple(app.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                               for table in ("threads", "activities", "identities", "persons"))

    run(Console(path, reply=lambda *args: sent.append(args)), [scenario], size=(187, 40))


def test_connection_switch_recomputes_sms_destination_suggestions(tmp_path):
    path, _ = completion_seeded(tmp_path)
    sent = []

    async def scenario(app, pilot):
        await pilot.press("5", "n")
        await pilot.pause()
        view = app.main.draft_view
        before = inbox.get_draft(app.db, view.draft_id)
        to = view.query_one(".toinput", Input)
        await type_address(to, pilot, "ANA@")
        assert "acme.com" in to.render_line(0).text
        view.query_one(DraftConnection).focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await choose(app, pilot, "3")
        assert view.kind == "sms" and view.connection_id == 3 and to.value == "ANA@"
        assert not view.query_one(".field.cc").display and not view.query_one(".field.subject").display
        to.focus()
        await pilot.pause()
        await pilot.press("end")
        await pilot.pause()
        assert to.render_line(0).text.rstrip() == "ANA@"
        await pilot.press("tab")
        await pilot.pause()
        assert app.focused is view.query_one(TextArea) and to.value == "ANA@"
        for name, prefix, completed in (
                ("to", "+1202555012", "+12025550124"),
                ("to", "+120255507", "+12025550777"),
                ("to", "+120255509", "+12025550999")):
            field = view.query_one(f".{name}input", Input)
            await type_address(field, pilot, prefix)
            assert field.value == prefix and completed in field.render_line(0).text
            await pilot.press("tab")
            await pilot.pause()
            assert app.focused is field and field.value == completed
        assert inbox.get_draft(app.db, view.draft_id) == before and sent == []

    run(Console(path, reply=lambda *args: sent.append(args)), [scenario])


@pytest.mark.parametrize("name,prefix,completed", [
    ("to", "  X@Y.IO  ,  ANA@", "  X@Y.IO  ,  ANA@acme.com"),
    ("cc", '"X, Y" <x@y.io>,  OPS@', '"X, Y" <x@y.io>,  OPS@acme.com'),
])
def test_existing_email_reply_recipients_accept_ghost_without_sending(tmp_path, name, prefix, completed):
    path, ids = seeded(tmp_path)
    sent = []

    async def scenario(app, pilot):
        app.main.open_thread(ids["ana"])
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        box = app.main.thread_view.query_one(ReplyBox)
        area = box.query_one(TextArea)
        area.text = "unsent reply"
        field = box.query_one(f".{name}input", Input)
        counts = tuple(app.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                       for table in ("threads", "activities"))
        await type_address(field, pilot, prefix)
        assert field.value == prefix and completed[len(prefix):] in field.render_line(0).text
        await pilot.press("tab")
        await pilot.pause()
        assert app.focused is field and field.value == completed and area.text == "unsent reply"
        await pilot.press("escape")
        await pilot.pause()
        assert not box.has_class("open") and app.drafts[ids["ana"]] == "unsent reply"
        assert inbox.list_drafts(app.db) == [] and sent == []
        assert counts == tuple(app.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                               for table in ("threads", "activities"))

    run(Console(path, reply=lambda *args: sent.append(args)), [scenario], size=(187, 40))


@pytest.mark.parametrize("state", ["missing", "mid-cursor", "selection", "backward"])
def test_address_tab_keeps_native_traversal_when_completion_is_ineligible(tmp_path, state):
    path, _ = seeded(tmp_path)

    async def scenario(app, pilot):
        await pilot.press("5", "n")
        await pilot.pause()
        view = app.main.draft_view
        field = view.query_one(".toinput", Input)
        prefix = "no-such-address" if state == "missing" else "ANA@"
        await type_address(field, pilot, prefix)
        if state != "missing":
            assert "acme.com" in field.render_line(0).text
        if state == "mid-cursor":
            await pilot.press("left")
            assert field.cursor_position == len(prefix) - 1
        elif state == "selection":
            await pilot.press("home", "shift+end")
            assert not field.selection.is_empty and field.cursor_position == len(prefix)
        await pilot.press("shift+tab" if state == "backward" else "tab")
        await pilot.pause()
        target = view.query_one(DraftConnection) if state == "backward" else view.query_one(".ccinput", Input)
        assert app.focused is target and field.value == prefix
        if state == "missing":
            await pilot.press("shift+tab")
            await pilot.pause()
            assert app.focused is field and field.value == prefix

    run(Console(path), [scenario])


def test_address_storage_failure_clears_ghost_and_preserves_tab_focus(tmp_path):
    path, _ = seeded(tmp_path)

    async def scenario(app, pilot):
        await pilot.press("5", "n")
        await pilot.pause()
        view = app.main.draft_view
        field = view.query_one(".toinput", Input)
        before = inbox.get_draft(app.db, view.draft_id)
        await type_address(field, pilot, "ANA")
        assert field.render_line(0).text.rstrip() != "ANA"
        app.db.set_authorizer(lambda action, table, *args: sqlite3.SQLITE_DENY
                              if action == sqlite3.SQLITE_READ and table == "identities" else sqlite3.SQLITE_OK)
        try:
            await pilot.press("@")
            await pilot.pause()
            assert field.value == "ANA@" and field.render_line(0).text.rstrip() == "ANA@"
            await pilot.press("tab")
            await pilot.pause()
            assert app.focused is view.query_one(".ccinput", Input) and field.value == "ANA@"
            await pilot.press("shift+tab")
            await pilot.pause()
            assert app.focused is field and field.value == "ANA@"
        finally:
            app.db.set_authorizer(None)
        assert inbox.get_draft(app.db, view.draft_id) == before and view.dirty
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert inbox.get_draft(app.db, view.draft_id)["to"] == "ANA@" and not view.dirty

    run(Console(path), [scenario])


def test_delete_draft_saves_edits_selects_neighbor_and_undo_restores(tmp_path):
    path, _ = seeded(tmp_path)
    db = inbox.connect(path)
    first, second = (inbox.save_draft(db, dict(connection_id=None, kind="email", sender="", to="", cc="",
                                               subject=s, body="")) for s in ("first", "second"))
    db.close()
    app = Console(path)

    async def scenario(app, pilot):
        await pilot.press("5")
        await pilot.pause()
        page = app.main.page
        page.focus_first()
        await pilot.pause()
        assert app.main.draft_view.draft_id == second["id"]
        app.main.draft_view.query_one(TextArea).text = "edited before delete"
        await pilot.press("x")
        await pilot.pause()
        assert [d["id"] for d in inbox.list_drafts(app.db)] == [first["id"]]
        assert app.main.draft_view.draft_id == first["id"]
        await pilot.press("z")
        await pilot.pause()
        restored = inbox.get_draft(app.db, second["id"])
        assert restored["body"] == "edited before delete" and len(page.items) == 2
        await pilot.press("x", "x")
        await pilot.pause()
        assert inbox.list_drafts(app.db) == [] and app.main.draft_view is None

    run(app, [scenario])


def test_drafts_list_keys_work_from_open_draft_but_text_fields_still_type(tmp_path):
    path, _ = seeded(tmp_path)
    app = Console(path)

    async def scenario(app, pilot):
        await pilot.press("5", "n")
        await pilot.pause()
        app.main.draft_view.query_one(DraftConnection).focus()
        await pilot.pause()
        assert len(inbox.list_drafts(app.db)) == 1
        await pilot.press("n")
        await pilot.pause()
        assert len(inbox.list_drafts(app.db)) == 2
        newest = app.main.draft_view.draft_id
        await pilot.press("j")
        await pilot.pause()
        assert app.main.draft_view.draft_id != newest
        await pilot.press("k")
        await pilot.pause()
        assert app.main.draft_view.draft_id == newest
        area = app.main.draft_view.query_one(TextArea)
        area.focus()
        await pilot.press("n", "x", "j")
        await pilot.pause()
        assert area.text == "nxj" and len(inbox.list_drafts(app.db)) == 2
        app.main.draft_view.query_one(DraftConnection).focus()
        await pilot.press("x")
        await pilot.pause()
        assert [d["id"] for d in inbox.list_drafts(app.db)] != [] and newest not in [
            d["id"] for d in inbox.list_drafts(app.db)]

    run(app, [scenario])


def test_switching_drafts_repaints_the_saved_row(tmp_path):
    path, _ = seeded(tmp_path)
    app = Console(path)

    async def scenario(app, pilot):
        await pilot.press("5", "n")
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        view, page = app.main.draft_view, app.main.page
        view.query_one(".toinput", Input).value = "kory@example.test"
        view.query_one(".subjectinput", Input).value = "Checklist follow up"
        page.focus_first()
        await pilot.press("j")
        await pilot.pause()
        row = page.query_one(DataTable).get_row(str(view.draft_id))
        assert "kory@example.test" in row[1] and row[2].startswith("Ch")

    run(app, [scenario])


def test_send_confirms_envelope_then_removes_draft_or_locks_on_unknown(tmp_path):
    path, _ = seeded(tmp_path)
    db = inbox.connect(path)
    first, second = (inbox.save_draft(db, dict(connection_id=1, kind="email", sender="jeremy@agentmaple.com",
                                               to="kory@example.test", cc="", subject=s, body="Hi Kory"))
                     for s in ("first", "second"))
    db.close()
    outcomes = iter([None, inbox.SendUnknown("lost"), None])

    def send(draft_id, revision, again=False):  # the service's side, on its own connection
        error, service = next(outcomes), inbox.connect(path)
        try:
            if error:
                with service:
                    service.execute("UPDATE composition_drafts SET sending_at = 'now' WHERE id = ?", (draft_id,))
                raise DraftSendError(str(error), 504)
            inbox.delete_draft(service, draft_id)
        finally:
            service.close()
        return {"thread_id": 1}

    app = Console(path, send_draft=send)

    async def scenario(app, pilot):
        await pilot.press("5")
        await pilot.pause()
        view = app.main.draft_view
        assert view.draft_id == second["id"] and "Sending unavailable" not in str([n.render() for n in view.query(".draftnotice")])
        view.query_one(TextArea).focus()
        await pilot.press("ctrl+enter")
        await pilot.pause()
        assert isinstance(app.screen, Pick) and "kory@example.test" in str(app.screen.body)
        await choose(app, pilot, "send")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert [d["id"] for d in inbox.list_drafts(app.db)] == [first["id"]]
        view = app.main.draft_view
        assert view.draft_id == first["id"]
        view.query_one(TextArea).focus()
        await pilot.press("ctrl+enter")
        await pilot.pause()
        await choose(app, pilot, "send")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert view.draft["sending_at"] and view.query_one(TextArea).disabled
        assert "Send outcome unknown" in str(view.query_one(".savenotice").render())
        await pilot.press("ctrl+enter")
        await pilot.pause()
        await choose(app, pilot, "again")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert inbox.list_drafts(app.db) == [] and app.main.draft_view is None

    run(app, [scenario], size=(187, 40))


SLACK_CONVERSATIONS = [{"id": "C0EXTACME", "kind": "channel", "label": "#ext-acme"},
                       {"id": "D0LOGAN", "kind": "im", "label": "Logan Carr"},
                       {"id": "C0RANDOM", "kind": "channel", "label": "#random"}]


@pytest.mark.parametrize("columns", [187, 80])
def test_slack_draft_picks_a_conversation_by_tab_and_saves_it(tmp_path, columns):
    path, ids = seeded(tmp_path)
    asked = []

    def conversations(connection_id):
        asked.append(connection_id)
        return SLACK_CONVERSATIONS

    async def scenario(app, pilot):
        await pilot.press("5", "n")
        await pilot.pause()
        view = app.main.draft_view
        view.query_one(".subjectinput", Input).value = "Old subject"
        view.query_one(DraftConnection).focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await choose(app, pilot, "2")
        assert isinstance(app.screen, Pick)  # email fields would be lost
        await choose(app, pilot, "clear")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert view.kind == "slack" and asked == [2] and view.conversations == SLACK_CONVERSATIONS
        assert not view.query_one(".field.cc").display and not view.query_one(".field.subject").display
        to = view.query_one(".toinput", Input)
        await type_address(to, pilot, "ext")
        assert to._suggestion == "ext-acme <C0EXTACME>"
        await pilot.press("tab")
        await pilot.pause()
        assert to.value == "#ext-acme <C0EXTACME>"
        await type_address(to, pilot, "carr")
        await pilot.press("tab")
        await pilot.pause()
        assert to.value == "Logan Carr <D0LOGAN>"
        await type_address(to, pilot, "#ext")
        await pilot.press("tab")
        await pilot.pause()
        view.query_one(TextArea).text = "Spec is in"
        view.query_one(TextArea).focus()
        await pilot.pause()
        mia = app.db.execute("SELECT t.id FROM threads t JOIN channels ch ON ch.id = t.channel_id"
                             " WHERE ch.native_id = 'C0EXTACME'").fetchone()[0]
        assert mia in [v["thread"].id for v in view.history_views]  # the channel's stored Threads
        await pilot.press("ctrl+s")
        await pilot.pause()
        row = inbox.get_draft(app.db, view.draft_id)
        assert (row["kind"], row["connection_id"], row["to"], row["cc"], row["subject"]) == (
            "slack", 2, "#ext-acme <C0EXTACME>", "", None)
        assert inbox.draft_problem(row) is None
        assert "Slack" in view.query_one(".detailtext").render().plain

    run(Console(path, slack_conversations=conversations), [scenario], size=(columns, 40))


def test_slack_conversation_fetch_failure_keeps_the_draft_editable(tmp_path):
    path, _ = seeded(tmp_path)

    def broken(connection_id):
        raise ReplyError("Slack is not connected")

    async def scenario(app, pilot):
        await pilot.press("5", "n")
        await pilot.pause()
        view = app.main.draft_view
        view.query_one(DraftConnection).focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await choose(app, pilot, "2")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert view.kind == "slack" and view.conversations is None
        to = view.query_one(".toinput", Input)
        await type_address(to, pilot, "ext")
        assert to.value == "ext" and not to.disabled

    run(Console(path, slack_conversations=broken), [scenario])


@pytest.mark.parametrize("outcome,status", [
    ("pending", 400), ("pending-before-callback", 400), ("unknown", 504), ("refused", 502),
    ("cached-unknown", 504), ("cached-refused", 502),
])
def test_selected_alias_reply_errors_keep_text_and_each_owners_files(tmp_path, outcome, status):
    path = str(tmp_path / "inbox.db")
    ids = seed_console(path)
    bridge_console(path)
    db = inbox.connect(path)
    cached = outcome in ("cached-unknown", "cached-refused")
    if outcome in ("pending", "pending-before-callback") or cached:
        inbox.reply(db, ids["old"], "Previous send",
                    SimpleNamespace(send=lambda *a, **k: "<00000000-0000-4000-8000-000000000001@resend>"))
    for owner, name in ((ids["keep"], "unchosen.txt"), (ids["old"], "selected.txt")):
        inbox.stage(db, sha=files.store_file(db, name.encode()), name=name, type="text/plain",
                    size=len(name), thread_id=owner)
    requests, dispatches = [], []

    def send(thread, text, to, cc, history, files=()):
        dispatches.append([f.name for f in files])
        raise (inbox.SendUnknown("email send outcome unknown") if outcome in ("unknown", "cached-unknown")
               else inbox.SendFailed("HTTP 422 refused"))

    senders = serve.Senders(made={1: SimpleNamespace(send=send)})

    def reply(tid, text, to, cc):
        service = inbox.connect(path)
        try:
            if cached:
                inbox.record_resend_receipt(service, 1, "00000000-0000-4000-8000-000000000001",
                                            "<confirmed@email.amazonses.test>")
                inbox.reconcile_resend_receipts(service)
            code, body = serve.handle_reply(service, json.dumps(
                dict(thread_id=tid, text=text, to=to, cc=cc)).encode(), senders)
            requests.append((tid, code))
            if outcome == "pending-before-callback":
                inbox.record_resend_receipt(service, 1, "00000000-0000-4000-8000-000000000001",
                                            "<confirmed@email.amazonses.test>")
                inbox.reconcile_resend_receipts(service)
                app.call_from_thread(refresh)
            if code != 200:
                raise ReplyError(body["error"])
            return body
        finally:
            service.close()

    app = Console(path, reply=reply)
    app.drafts.update({ids["keep"]: "Unchosen text", ids["old"]: "Selected text"})
    refresh = app.check_changes
    if cached:
        app.check_changes = lambda: None  # hold the old view until after the provider outcome is displayed

    async def scenario(app, pilot):
        app.action_open_thread(ids["old"])
        await pilot.pause()
        await pilot.press("r")
        assert isinstance(app.screen, Pick)
        await choose(app, pilot, str(ids["old"]))
        box = app.main.thread_view.query_one(ReplyBox)
        area = box.query_one(TextArea)
        box.query_one(".toinput", Input).value = "chosen@example.test"
        box.query_one(".ccinput", Input).value = "copy@example.test"
        area.text = "Selected revised text"
        area.focus()
        await pilot.press("ctrl+enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        hint = str(box.query_one(".replyhint").render())
        if outcome == "pending-before-callback":
            assert box.parent_confirmed() and not box.pending_confirmation
            assert "ctrl+enter send" in hint and not box.query_one(".replyhint").has_class("error")
        else:
            assert "Send error" in hint and "Not sent" not in hint
        assert box.has_class("open") and area.text == "Selected revised text"
        assert (box.query_one(".toinput", Input).value, box.query_one(".ccinput", Input).value) == (
            "chosen@example.test", "copy@example.test")
        assert box.draft_key == ids["old"] and box.attach_owner() == ("thread", ids["old"])
        assert [r["name"] for r in inbox.staged(db, thread_id=ids["old"])] == ["selected.txt"]
        assert [r["name"] for r in inbox.staged(db, thread_id=ids["keep"])] == ["unchosen.txt"]
        if outcome == "pending":
            assert inbox.EMAIL_CONFIRMATION_PENDING in hint and box.pending_confirmation
            assert box.view["messages"][-1]["wire_id"] is None
            staged = {owner: inbox.staged(db, thread_id=owner) for owner in (ids["old"], ids["keep"])}
            drafts = dict(app.drafts)
            inbox.record_resend_receipt(db, 1, "00000000-0000-4000-8000-000000000001",
                                        "<confirmed@email.amazonses.test>")
            inbox.reconcile_resend_receipts(db)
            app.check_changes()
            await pilot.pause()
            assert app.main.thread_view.query_one(ReplyBox) is box
            ready = box.query_one(".replyhint")
            assert "ctrl+enter send" in str(ready.render()) and "confirmation pending" not in str(ready.render())
            assert not ready.has_class("error")
            assert not box.pending_confirmation
            assert box.has_class("open") and area.text == "Selected revised text"
            assert (box.query_one(".toinput", Input).value, box.query_one(".ccinput", Input).value) == (
                "chosen@example.test", "copy@example.test")
            assert box.draft_key == ids["old"] and box.attach_owner() == ("thread", ids["old"])
            assert app.drafts == drafts
            assert {owner: inbox.staged(db, thread_id=owner) for owner in staged} == staged
            assert requests == [(ids["old"], status)] and dispatches == []
        if cached:
            assert box.view["messages"][-1]["wire_id"] is None
            assert not box.pending_confirmation
            staged = {owner: inbox.staged(db, thread_id=owner) for owner in (ids["old"], ids["keep"])}
            drafts = dict(app.drafts)
            refresh()
            await pilot.pause()
            assert app.main.thread_view.query_one(ReplyBox) is box
            assert box.view["messages"][-1]["wire_id"] == "<confirmed@email.amazonses.test>"
            retained = box.query_one(".replyhint")
            assert str(retained.render()) == hint and retained.has_class("error")
            assert not box.pending_confirmation
            assert box.has_class("open") and area.text == "Selected revised text"
            assert (box.query_one(".toinput", Input).value, box.query_one(".ccinput", Input).value) == (
                "chosen@example.test", "copy@example.test")
            assert box.draft_key == ids["old"] and box.attach_owner() == ("thread", ids["old"])
            assert app.drafts == drafts
            assert {owner: inbox.staged(db, thread_id=owner) for owner in staged} == staged
            assert requests == [(ids["old"], status)] and dispatches == [["selected.txt"]]
        await pilot.press("escape")
        assert app.drafts == {ids["keep"]: "Unchosen text", ids["old"]: "Selected revised text"}

    run(app, [scenario])
    assert requests == [(ids["old"], status)]
    assert dispatches == ([] if outcome in ("pending", "pending-before-callback") else [["selected.txt"]])


@pytest.mark.parametrize("older,newer", [
    (inbox.EMAIL_CONFIRMATION_PENDING, "email send outcome unknown"),
    ("email send outcome unknown", "HTTP 422 refused"),
])
def test_cancelled_reply_callbacks_preserve_material_warning_and_draft(tmp_path, older, newer):
    path = str(tmp_path / "inbox.db")
    ids = seed_console(path)
    bridge_console(path)
    db = inbox.connect(path)
    if older != inbox.EMAIL_CONFIRMATION_PENDING:
        inbox.reply(db, ids["old"], "Previous send",
                    SimpleNamespace(send=lambda *a, **k: "<00000000-0000-4000-8000-000000000001@resend>"))
    for owner, name in ((ids["keep"], "unchosen.txt"), (ids["old"], "selected.txt")):
        inbox.stage(db, sha=files.store_file(db, name.encode()), name=name, type="text/plain",
                    size=len(name), thread_id=owner)
    started, release = Event(), Event()
    requests, workers = [], []

    def reply(tid, text, to, cc):
        requests.append((tid, text, to, cc))
        workers.append(get_current_worker())
        if len(workers) == 1:
            started.set()
            assert release.wait(5), "older fixture was not released"
            raise ReplyError(older)
        raise ReplyError(newer)

    app = Console(path, reply=reply)
    app.drafts.update({ids["keep"]: "Unchosen text", ids["old"]: "Selected text"})

    async def scenario(app, pilot):
        app.action_open_thread(ids["old"])
        await pilot.pause()
        await pilot.press("r")
        await choose(app, pilot, str(ids["old"]))
        box = app.main.thread_view.query_one(ReplyBox)
        box.query_one(".toinput", Input).value = "chosen@example.test"
        box.query_one(".ccinput", Input).value = "copy@example.test"
        box.query_one(TextArea).text = "Selected revised text"
        box.query_one(TextArea).focus()
        staged = {owner: inbox.staged(db, thread_id=owner) for owner in (ids["old"], ids["keep"])}
        old_done, new_done = asyncio.Event(), asyncio.Event()
        show_result = box.send_result

        def observe_result(worker, error):
            show_result(worker, error)
            (old_done if worker is workers[0] else new_done).set()

        box.send_result = observe_result
        try:
            await pilot.press("ctrl+enter")
            assert await asyncio.to_thread(started.wait, 5)
            await pilot.press("ctrl+enter")
            await asyncio.wait_for(new_done.wait(), 5)
            assert workers[0].is_cancelled and not workers[1].is_cancelled
            hint = str(box.query_one(".replyhint").render())
            assert newer in hint and box.query_one(".replyhint").has_class("error")
            if older == inbox.EMAIL_CONFIRMATION_PENDING:
                box.hint(f"Send error: {inbox.EMAIL_CONFIRMATION_PENDING}", True, True)
                assert str(box.query_one(".replyhint").render()) == hint
            release.set()
            await asyncio.wait_for(old_done.wait(), 5)
            await pilot.pause()
            retained = str(box.query_one(".replyhint").render())
            if older == inbox.EMAIL_CONFIRMATION_PENDING:
                assert retained == hint
            else:
                assert older in retained and newer not in retained
                assert box.view["messages"][-1]["wire_id"] is None
                inbox.record_resend_receipt(db, 1, "00000000-0000-4000-8000-000000000001",
                                            "<confirmed@email.amazonses.test>")
                inbox.reconcile_resend_receipts(db)
                app.check_changes()
                await pilot.pause()
                assert box.view["messages"][-1]["wire_id"] == "<confirmed@email.amazonses.test>"
                assert str(box.query_one(".replyhint").render()) == retained
            assert box.query_one(".replyhint").has_class("error") and not box.pending_confirmation
            assert box.has_class("open") and box.query_one(TextArea).text == "Selected revised text"
            assert (box.query_one(".toinput", Input).value, box.query_one(".ccinput", Input).value) == (
                "chosen@example.test", "copy@example.test")
            assert box.draft_key == ids["old"] and box.attach_owner() == ("thread", ids["old"])
            assert app.drafts == {ids["keep"]: "Unchosen text", ids["old"]: "Selected text"}
            assert {owner: inbox.staged(db, thread_id=owner) for owner in staged} == staged
            assert len(requests) == 2  # the two explicit keypresses; the late callback never retries
        finally:
            release.set()

    run(app, [scenario])
