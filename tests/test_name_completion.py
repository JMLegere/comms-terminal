"""Synthetic local names through native address inputs; no provider or send path."""

import pytest
from textual.widgets import Input, TextArea

from test_console import run, seeded
from test_drafts_ui import choose, type_address
from comms_terminal import inbox, tui
from comms_terminal.tui import Console, DraftConnection, ReplyBox


@pytest.fixture(autouse=True)
def no_autosave(monkeypatch):
    """These tests check that completion keys neither save nor send; autosave timing is tested in test_autosave."""
    monkeypatch.setattr(tui, "AUTOSAVE_GAP", 3600)


EMAIL, PHONE = "lcarr@example.test", "+12025550123"
OWN_EMAIL, OWN_PHONE = "owner@example.test", "+12025550100"


def named_seeded(tmp_path):
    path, ids = seeded(tmp_path)
    db = inbox.connect(path)
    at = "2026-10-14T00:00:00+00:00"
    with db:
        db.executemany(
            "INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [(3, "quo", OWN_PHONE, OWN_PHONE, "Phone Desk", at),
             (4, "fastmail", OWN_EMAIL, OWN_EMAIL, "Owner Mail", at)])
        ids["logan_person"] = db.execute(
            "SELECT person_id FROM identities WHERE kind = 'slack_user' AND value = 'U0LOGAN'"
        ).fetchone()[0]
        db.executemany("INSERT INTO identities (person_id, kind, value) VALUES (?, ?, ?)",
                       [(ids["logan_person"], "email", EMAIL),
                        (ids["logan_person"], "phone", PHONE)])
        for name, emails, phones in (
                ("Morgan Vale", ["m-one@example.test", "m-two@example.test"],
                 ["+12025550910", "+12025550920"]),
                ("Email Only", ["email-only@example.test"], []),
                ("Phone Only", [], ["+12025550124"]),
                ("Invalid Name", ["not-an-address"], ["not-a-number"]),
                ("Unsafe\nName", ["untrusted-person@example.test"], ["+12025550125"])):
            person = db.execute("INSERT INTO persons (display_name, created_at) VALUES (?, ?)",
                                (name, at)).lastrowid
            db.executemany("INSERT INTO identities (person_id, kind, value) VALUES (?, ?, ?)",
                           [(person, "email", value) for value in emails] +
                           [(person, "phone", value) for value in phones])
    db.close()
    return path, ids


async def new_draft(app, pilot, kind):
    await pilot.press("6", "n")
    await pilot.pause()
    view = app.main.draft_view
    if kind == "sms":
        view.query_one(DraftConnection).focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await choose(app, pilot, "3")
    assert view.kind == kind
    return view


@pytest.mark.parametrize("key", ["tab", "right"])
@pytest.mark.parametrize("mode,kind", [("draft", "sms"), ("draft", "email"), ("reply", "email")])
def test_named_recipients_show_destination_then_accept_bare_token(tmp_path, key, mode, kind):
    path, ids = named_seeded(tmp_path)
    sent = []

    async def scenario(app, pilot):
        if mode == "draft":
            view = await new_draft(app, pilot, kind)
            before = inbox.get_draft(app.db, view.draft_id)
        else:
            app.main.open_thread(ids["ana"])
            await pilot.pause()
            await pilot.press("r")
            await pilot.pause()
            view = app.main.thread_view.query_one(ReplyBox)
            before = inbox.list_drafts(app.db)
        area = view.query_one(TextArea)
        area.text = "unsent body"
        counts = tuple(app.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                       for table in ("threads", "activities", "identities", "persons"))
        destination = PHONE if kind == "sms" else EMAIL
        cases = [("to", "log", destination), ("to", "  LoGaN cA", "  " + destination),
                 ("to", "  cAr", "  " + destination)]
        if kind == "email":
            cases += [("to", "  X@Y.IO  ,  LOG", "  X@Y.IO  ,  " + destination),
                      ("cc", '"X, Y" <x@y.io>,  cAr', '"X, Y" <x@y.io>,  ' + destination)]
        for name, prefix, completed in cases:
            field = view.query_one(f".{name}input", Input)
            await type_address(field, pilot, prefix)
            assert field.value == prefix and destination in field.render_line(0).text
            await pilot.press(key)
            await pilot.pause()
            assert app.focused is field and field.value == completed
            assert field.cursor_position == len(completed) and field.selection.is_empty
            assert area.text == "unsent body" and sent == []
            saved = inbox.get_draft(app.db, view.draft_id) if mode == "draft" else inbox.list_drafts(app.db)
            assert saved == before
        assert counts == tuple(app.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                               for table in ("threads", "activities", "identities", "persons"))

    run(Console(path, reply=lambda *args: sent.append(args)), [scenario], size=(187, 40))


@pytest.mark.parametrize("kind", ["email", "sms"])
def test_unknown_ambiguous_and_wrong_kind_names_keep_native_focus(tmp_path, kind):
    path, _ = named_seeded(tmp_path)

    async def scenario(app, pilot):
        view = await new_draft(app, pilot, kind)
        before = inbox.get_draft(app.db, view.draft_id)
        cases = [("to", "Morgan"), ("to", "Nobody Here"),
                 ("to", "Email Only" if kind == "sms" else "Phone Only"),
                 ("to", "Invalid"), ("to", "Unsafe")]
        for name, prefix in cases:
            field = view.query_one(f".{name}input", Input)
            await type_address(field, pilot, prefix)
            assert field.render_line(0).text.rstrip() == prefix
            await pilot.press("tab")
            await pilot.pause()
            target = view.query_one(".ccinput", Input) if kind == "email" else view.query_one(TextArea)
            assert app.focused is target and field.value == prefix
        field = view.query_one(".toinput", Input)
        prefix, completed = ("m-one@", "m-one@example.test") if kind == "email" else (
            "+1202555091", "+12025550910")
        await type_address(field, pilot, prefix)
        assert completed in field.render_line(0).text
        await pilot.press("tab")
        await pilot.pause()
        assert app.focused is field and field.value == completed
        assert inbox.get_draft(app.db, view.draft_id) == before

    run(Console(path), [scenario], size=(187, 40))


@pytest.mark.parametrize("key", ["tab", "right"])
def test_repeated_own_connection_address_resolves_once(tmp_path, key):
    path, _ = named_seeded(tmp_path)

    async def scenario(app, pilot):
        view = await new_draft(app, pilot, "email")
        before = inbox.get_draft(app.db, view.draft_id)
        for name, prefix, destination in (("to", "log", EMAIL),):
            field = view.query_one(f".{name}input", Input)
            await type_address(field, pilot, prefix)
            assert destination in field.render_line(0).text and field.value == prefix
            await pilot.press(key)
            await pilot.pause()
            assert app.focused is field and field.value == destination
        assert inbox.get_draft(app.db, view.draft_id) == before

    run(Console(path), [scenario], size=(187, 40))


@pytest.mark.parametrize("key", ["tab", "right"])
def test_stale_person_mapping_cannot_accept_hidden_destination(tmp_path, key):
    path, ids = named_seeded(tmp_path)

    async def scenario(app, pilot):
        view = await new_draft(app, pilot, "email")
        field = view.query_one(".toinput", Input)
        before = inbox.get_draft(app.db, view.draft_id)
        await type_address(field, pilot, "log")
        assert EMAIL in field.render_line(0).text
        with app.db:
            app.db.execute("UPDATE identities SET person_id = NULL WHERE person_id = ? AND kind = 'email'",
                           (ids["logan_person"],))
        await pilot.press(key)
        await pilot.pause()
        assert field.value == "log" and EMAIL not in field.render_line(0).text
        assert app.focused is (view.query_one(".ccinput", Input) if key == "tab" else field)
        field.focus()
        await pilot.pause()
        assert field.render_line(0).text.rstrip() == "log"
        assert inbox.get_draft(app.db, view.draft_id) == before

    run(Console(path), [scenario], size=(187, 40))


def test_live_connection_kind_replaces_named_ghost_without_changing_typed_value(tmp_path):
    path, _ = named_seeded(tmp_path)

    async def scenario(app, pilot):
        view = await new_draft(app, pilot, "email")
        field = view.query_one(".toinput", Input)
        before = inbox.get_draft(app.db, view.draft_id)
        await type_address(field, pilot, "log")
        assert EMAIL in field.render_line(0).text
        view.query_one(DraftConnection).focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await choose(app, pilot, "3")
        field.focus()
        await pilot.pause()
        await pilot.press("end")
        await pilot.pause()
        assert view.kind == "sms" and field.value == "log"
        rendered = field.render_line(0).text
        assert PHONE in rendered and EMAIL not in rendered
        await pilot.press("tab")
        await pilot.pause()
        assert app.focused is field and field.value == PHONE
        assert inbox.get_draft(app.db, view.draft_id) == before

    run(Console(path), [scenario], size=(187, 40))


@pytest.mark.parametrize("state", ["mid-cursor", "selection", "backward"])
def test_named_ghost_does_not_override_native_selection_or_backward_focus(tmp_path, state):
    path, _ = named_seeded(tmp_path)

    async def scenario(app, pilot):
        view = await new_draft(app, pilot, "email")
        field = view.query_one(".toinput", Input)
        await type_address(field, pilot, "log")
        assert EMAIL in field.render_line(0).text
        if state == "mid-cursor":
            await pilot.press("left")
            assert field.cursor_position == 2
        elif state == "selection":
            await pilot.press("home", "shift+end")
            assert not field.selection.is_empty
        await pilot.press("shift+tab" if state == "backward" else "tab")
        await pilot.pause()
        target = view.query_one(DraftConnection) if state == "backward" else view.query_one(".ccinput", Input)
        assert app.focused is target and field.value == "log"

    run(Console(path), [scenario], size=(187, 40))
