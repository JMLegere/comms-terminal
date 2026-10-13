"""Synthetic reconciliation fixtures, driven through public Textual keys and controls."""

import asyncio
from datetime import datetime, timedelta, timezone

from textual.widgets import Input, OptionList, TextArea

from universal_inbox import inbox
from universal_inbox.tui import CaseCard, Console, Pick, ReplyBox


FIRST = "<first@example.test>"
SECOND = "<second@example.test>"
ME = "owner@example.test"


def seed_console(path, second_id=SECOND):
    """Two split email Threads plus an unrelated mailbox; safe for rendered fixtures."""
    db = inbox.connect(str(path))
    inbox.migrate(db)
    now = datetime.now(timezone.utc)
    with db:
        for cid, address in ((1, ME), (2, "other@example.test")):
            db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                       " VALUES (?, 'gmail', ?, ?, 'Gmail', ?)", (cid, address, address, now.isoformat()))
    ids = {}
    for name, cid, address, mid, hours, sender in (
        ("keep", 1, ME, FIRST, 3, "first@example.test"),
        ("old", 1, ME, second_id, 1, "second@example.test"),
        ("other", 2, "other@example.test", "<other@example.test>", 0.5, "third@example.test"),
    ):
        title = f"{name.title()} topic"
        thread = inbox.ingest(db, inbox.Incoming(
            cid, address, "mailbox", "MESSAGE", mid, inbox.Identity("email", sender, name.title()),
            now - timedelta(hours=hours), f"{name.title()} body\n\n> {name.title()} quote", title=title,
            data={"from": sender, "to": [address], "cc": ["observer@example.test"], "subject": title}))
        ids[name] = thread.id
    db.close()
    return ids


def bridge_console(path, second_id=SECOND):
    """Simulate service write; late historical bridge reorders the old Thread's message."""
    db = inbox.connect(str(path))
    try:
        return inbox.ingest(db, inbox.Incoming(
            1, ME, "mailbox", "MESSAGE", "<bridge@example.test>",
            inbox.Identity("email", "bridge@example.test", "Bridge"),
            datetime.now(timezone.utc) - timedelta(hours=2), "Bridge body", [FIRST, second_id],
            "Bridge topic", {"from": "bridge@example.test", "to": [ME]}))
    finally:
        db.close()


class FakeSender:
    def __init__(self, connection_id):
        self.connection_id, self.calls = connection_id, []

    def send(self, thread, text, to, cc, history):
        self.calls.append((thread.id, text, to, cc, [m["native_id"] for m in history]))
        return f"<fake-{self.connection_id}-{len(self.calls)}@example.test>"


def console(path, drafts=None):
    senders = {cid: FakeSender(cid) for cid in (1, 2)}

    def reply(tid, text, to, cc):
        db = inbox.connect(str(path))
        try:
            channel = inbox.thread_view(db, tid)["channel"]
            return inbox.reply(db, tid, text, senders[channel["connection_id"]], to, cc).__dict__
        finally:
            db.close()

    app = Console(str(path), reply=reply)
    app.drafts.update(drafts or {})
    return app, senders


def run(app, scenario):
    async def go():
        async with app.run_test(size=(150, 42)) as pilot:
            await pilot.pause()
            await scenario(app, pilot)
    asyncio.run(go())


async def open_old(app, pilot, old_id):
    app.action_open_thread(old_id)
    await pilot.pause()


def test_old_link_selects_survivor_and_reply_uses_its_connection(tmp_path):
    path = tmp_path / "inbox.db"
    ids = seed_console(path)
    bridge_console(path)
    app, senders = console(path)

    async def scenario(app, pilot):
        await open_old(app, pilot, ids["old"])
        assert app.main.page.current_thread_id() == ids["keep"]
        assert app.main.thread_view.thread_id == ids["keep"]
        await pilot.press("r", *"Correct account", "ctrl+enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

    run(app, scenario)
    assert senders[1].calls == [(ids["keep"], "Correct account", ["second@example.test"], [],
                                [FIRST, "<bridge@example.test>", SECOND])]
    assert senders[2].calls == []


def test_single_retired_draft_remains_editable_under_original_key(tmp_path):
    path = tmp_path / "inbox.db"
    ids = seed_console(path)
    bridge_console(path)
    app, _ = console(path, {ids["old"]: "Saved old text"})

    async def scenario(app, pilot):
        await open_old(app, pilot, ids["old"])
        await pilot.press("r")
        assert not isinstance(app.screen, Pick)
        assert app.main.thread_view.query_one(TextArea).text == "Saved old text"
        await pilot.press("ctrl+end", "!", "escape")
        assert app.drafts == {ids["old"]: "Saved old text!"}
        await pilot.press("r")
        assert app.main.thread_view.query_one(TextArea).text == "Saved old text!"
        await pilot.press("escape")

    run(app, scenario)


def test_multiple_drafts_use_existing_picker_and_send_only_chosen_text(tmp_path):
    path = tmp_path / "inbox.db"
    ids = seed_console(path)
    bridge_console(path)
    original = {ids["keep"]: "First saved text", ids["old"]: "Second saved text"}
    app, senders = console(path, original)

    async def scenario(app, pilot):
        await open_old(app, pilot, ids["old"])
        await pilot.press("r")
        assert isinstance(app.screen, Pick)
        assert app.screen.query_one(OptionList).option_count == 2
        await pilot.press("escape")
        assert app.drafts == original
        await pilot.press("r", "down", "enter")
        assert app.main.thread_view.query_one(TextArea).text == "Second saved text"
        await pilot.press("ctrl+end", "!", "escape")
        assert app.drafts == {ids["keep"]: "First saved text", ids["old"]: "Second saved text!"}
        await pilot.press("r", "down", "enter", "ctrl+enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.drafts == {ids["keep"]: "First saved text"}

    run(app, scenario)
    assert senders[1].calls[0][0:4] == (ids["keep"], "Second saved text!", ["second@example.test"], [])
    assert senders[2].calls == []


def test_identical_saved_drafts_keep_other_original_key_after_send(tmp_path):
    path = tmp_path / "inbox.db"
    ids = seed_console(path)
    bridge_console(path)
    app, senders = console(path, {ids["keep"]: "Same saved text", ids["old"]: "Same saved text"})

    async def scenario(app, pilot):
        await open_old(app, pilot, ids["old"])
        await pilot.press("r")
        assert not isinstance(app.screen, Pick)
        await pilot.press("ctrl+enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.drafts == {ids["old"]: "Same saved text"}

    run(app, scenario)
    assert senders[1].calls[0][1] == "Same saved text"


def test_remote_merge_preserves_live_composer_selection_and_invalidates_undo(tmp_path):
    path = tmp_path / "inbox.db"
    ids = seed_console(path)
    app, _ = console(path, {ids["keep"]: "Other saved draft"})

    async def scenario(app, pilot):
        await open_old(app, pilot, ids["old"])
        await pilot.press("r", *"Live text")
        box = app.main.thread_view.query_one(ReplyBox)
        box.query_one(".toinput", Input).value = "custom@example.test"
        app.offer_undo("Fixture change", lambda: None)
        bridge_console(path)
        app.check_changes()
        await pilot.pause()
        assert app.undo is None
        assert app.main.page.current_thread_id() == ids["keep"]
        assert app.main.thread_view.query_one(ReplyBox) is box
        assert box.has_class("open") and box.query_one(TextArea).text == "Live text"
        assert box.query_one(".toinput", Input).value == "custom@example.test"
        await open_old(app, pilot, ids["old"])
        assert app.main.thread_view.query_one(ReplyBox) is box
        await pilot.press("!", "escape")
        assert app.drafts == {ids["keep"]: "Other saved draft", ids["old"]: "Live text!"}

    run(app, scenario)


def test_alias_selection_follows_winning_status_without_replacing_live_composer(tmp_path):
    path = tmp_path / "inbox.db"
    ids = seed_console(path)
    app, _ = console(path)

    async def scenario(app, pilot):
        await open_old(app, pilot, ids["old"])
        await pilot.press("r", *"Unsent text")
        box = app.main.thread_view.query_one(ReplyBox)
        db = inbox.connect(str(path))
        try:
            inbox.act(db, ids["keep"], inbox.Done())
            inbox.ingest(db, inbox.Incoming(
                1, ME, "mailbox", "MESSAGE", FIRST, inbox.Identity("email", "first@example.test"),
                datetime.now(timezone.utc), "duplicate", [SECOND]))
        finally:
            db.close()
        app.check_changes()
        await pilot.pause()
        assert app.view == 2
        assert app.main.page.current_thread_id() == ids["keep"]
        assert app.main.thread_view.query_one(ReplyBox) is box
        assert box.has_class("open") and box.query_one(TextArea).text == "Unsent text"
        await pilot.press("!", "escape")
        assert app.drafts == {ids["old"]: "Unsent text!"}

    run(app, scenario)


def test_message_expansion_trim_and_recipients_follow_native_identity(tmp_path):
    path = tmp_path / "inbox.db"
    ids = seed_console(path)
    app, _ = console(path)

    async def scenario(app, pilot):
        await open_old(app, pilot, ids["old"])
        await pilot.click(app.main.thread_view.query_one(".mhead"), offset=(1, 1))
        await pilot.pause()
        await pilot.click(app.main.thread_view.query_one(".mtoggle"), offset=(2, 0))
        await pilot.pause()
        assert "Old quote" in str(app.main.thread_view.query_one(".mtrim").render())
        await pilot.click(app.main.thread_view.query_one(".mhead"), offset=(1, 0))
        await pilot.pause()
        bridge_console(path)
        app.check_changes()
        await pilot.pause()
        view = app.main.thread_view
        assert [m["native_id"] for m in view.view["messages"]] == [FIRST, "<bridge@example.test>", SECOND]
        assert len(view.query(".mhead")) == 2  # old message remains explicitly closed at its new index
        assert not view.query(".mtrim")
        await pilot.click(view.query_one(".mline"), offset=(1, 0))
        await pilot.pause()
        assert len(view.query(".mhead")) == 3
        assert "Old quote" in str(view.query_one(".mtrim").render())
        heads = list(view.query(".mhead"))
        assert "date" not in str(heads[0].render())
        assert "second@example.test" in str(heads[-1].render()) and "date" in str(heads[-1].render())

    run(app, scenario)


def test_case_merge_keeps_live_card_composer_and_original_draft_key(tmp_path):
    path = tmp_path / "inbox.db"
    ids = seed_console(path)
    db = inbox.connect(str(path))
    inbox.act(db, ids["keep"], inbox.SetCase(new_title="Synthetic case"))
    case_id = inbox.get_thread(db, ids["keep"]).case_id
    inbox.act(db, ids["old"], inbox.SetCase(case_id=case_id))
    db.close()
    app, _ = console(path)

    async def scenario(app, pilot):
        app.action_open_case(case_id)
        await pilot.pause()
        await pilot.press("j", "r", *"Live case text")
        box = app.main.page.query("CaseCard").last().query_one(ReplyBox)
        bridge_console(path)
        app.check_changes()
        # The merged-away card is still being removed: painting now must skip it, not paint or crash on it
        assert [c.thread_id for c in app.main.page.live_cards()] == [ids["keep"]]
        app.main.page.paint()
        await pilot.pause()
        cards = list(app.main.page.query(CaseCard))
        assert len(cards) == 1 and cards[0].thread_id == ids["keep"]
        assert cards[0].query_one(ReplyBox) is box
        assert box.query_one(TextArea).text == "Live case text"
        await pilot.press("!", "escape")
        assert app.drafts == {ids["old"]: "Live case text!"}

    run(app, scenario)


def test_stale_undo_keeps_reconciled_case_triage_and_live_draft(tmp_path):
    path = tmp_path / "inbox.db"
    ids = seed_console(path)
    app, _ = console(path, {ids["keep"]: "Other saved text", ids["old"]: "Old saved text"})

    async def scenario(app, pilot):
        await open_old(app, pilot, ids["old"])
        await pilot.press("e")
        assert app.undo is not None
        await open_old(app, pilot, ids["old"])
        await pilot.press("r", "ctrl+end", "!")
        box = app.main.thread_view.query_one(ReplyBox)
        inbox.act(app.db, ids["keep"], inbox.SetCase(new_title="Case kept after reconciliation"))
        case_id = inbox.get_thread(app.db, ids["keep"]).case_id
        inbox.ingest(app.db, inbox.Incoming(
            1, ME, "mailbox", "MESSAGE", FIRST, inbox.Identity("email", "first@example.test"),
            datetime.now(timezone.utc), "duplicate", [SECOND]))
        app.main.focus_list()
        await pilot.press("z")
        await pilot.pause()
        thread = inbox.get_thread(app.db, ids["keep"])
        assert app.undo is None
        assert thread.case_id == case_id and thread.status_detail == "DONE_MANUALLY_SET"
        assert app.main.thread_view.query_one(ReplyBox) is box
        assert box.has_class("open") and box.query_one(TextArea).text == "Old saved text!"
        box.query_one(TextArea).focus()
        await pilot.press("escape")
        assert app.drafts == {ids["keep"]: "Other saved text", ids["old"]: "Old saved text!"}

    run(app, scenario)


def test_domain_literal_message_controls_keep_exact_identity_through_merge(tmp_path):
    path = tmp_path / "inbox.db"
    native_id = "<second@[127.0.0.1]>"
    ids = seed_console(path, second_id=native_id)
    app, _ = console(path)

    async def scenario(app, pilot):
        await open_old(app, pilot, ids["old"])
        for selector, offset in ((".mhead", (1, 1)), (".mtoggle", (2, 0)), (".mhead", (1, 0))):
            await pilot.click(app.main.thread_view.query_one(selector), offset=offset)
            await pilot.pause()
        assert not app.main.thread_view.query(".mhead")
        bridge_console(path, second_id=native_id)
        app.check_changes()
        await pilot.pause()
        view = app.main.thread_view
        assert len(view.query(".mhead")) == 2
        await pilot.click(view.query_one(".mline"), offset=(1, 0))
        await pilot.pause()
        assert len(view.query(".mhead")) == 3
        assert "Old quote" in str(view.query_one(".mtrim").render())
        assert "date" in str(view.query(".mhead").last().render())

    run(app, scenario)


def test_selected_survivor_follows_merged_status_and_keeps_live_composer(tmp_path):
    path = tmp_path / "inbox.db"
    ids = seed_console(path)
    app, _ = console(path, {ids["old"]: "Other saved text"})

    async def scenario(app, pilot):
        await open_old(app, pilot, ids["keep"])
        await pilot.press("r", *"Live survivor")
        box = app.main.thread_view.query_one(ReplyBox)
        db = inbox.connect(str(path))
        try:
            inbox.act(db, ids["old"], inbox.Done())
            inbox.ingest(db, inbox.Incoming(
                1, ME, "mailbox", "MESSAGE", FIRST, inbox.Identity("email", "first@example.test"),
                datetime.now(timezone.utc), "duplicate", [SECOND]))
        finally:
            db.close()
        app.main.thread_view.refresh_data()  # message repaint must not acknowledge the queue's membership
        app.check_changes()
        await pilot.pause()
        assert app.view == 2 and app.main.page.current_thread_id() == ids["keep"]
        assert app.main.thread_view.query_one(ReplyBox) is box
        assert box.has_class("open") and box.query_one(TextArea).text == "Live survivor"
        await pilot.press("!", "escape")
        assert app.drafts == {ids["old"]: "Other saved text", ids["keep"]: "Live survivor!"}

    run(app, scenario)
