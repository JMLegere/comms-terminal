"""Local Drafts UI checks; no service, provider, or real-send path."""

import sqlite3

import pytest
from textual.widgets import Input, OptionList, TextArea

from test_console import run, seeded
from test_pane_arrows import check_editor_arrows
from universal_inbox import inbox
from universal_inbox.tui import (DRAFT_FIELDS, Console, DraftConnection, DraftsPage,
                                 Pick, ReplyBox, ThreadsPage, VIEW_NAMES)


async def choose(app, pilot, oid):
    menu = app.screen.query_one(OptionList)
    menu.highlighted = menu.get_option_index(oid)
    await pilot.press("enter")
    await pilot.pause()


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
        assert not hasattr(view, "thread_id") and not view.check_action("send", ())
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
        assert "2" not in [menu.get_option_at_index(i).id for i in range(menu.option_count)]
        await choose(app, pilot, "1")
        assert view.values()["sender"] == "jeremy@agentmaple.com"
        values = dict(connection_id=1, kind="email", sender="custom@inbox.example", to="  ANA@acme.com  ",
                      cc=" ops@acme.com ", subject="  Exact subject  ", body="\n Exact words\nwith trailing space \n")
        for name in ("sender", "to", "cc", "subject"):
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
        assert view.values()["sender"] == "custom@inbox.example"  # never overwrite an edited sender
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
