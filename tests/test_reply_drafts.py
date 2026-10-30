"""Reply Drafts (.agents/prd/2026-10-16-sequencing.md P1, FR-SEQ-11): a reply box's text is its Thread's Draft."""

import sqlite3

import pytest

from test_console import seed_demo  # puts scripts/ on the path
from comms_terminal import inbox
from test_attachment_send import FakeSender


@pytest.fixture
def seeded(tmp_path):
    path = str(tmp_path / "inbox.db")
    return path, seed_demo.seed(inbox.connect(path))


def test_reply_draft_saves_replaces_survives_a_restart_and_empty_text_deletes_it(seeded):
    path, ids = seeded
    db = inbox.connect(path)
    first = inbox.save_reply_draft(db, ids["ana"], "Hi Ana,")
    assert (first["thread_id"], first["kind"], first["subject"], first["revision"]) == (
        ids["ana"], "email", "Re: Pricing for 40 seats?", 1)
    assert "ana@acme.com" in first["to"] and "ops@acme.com" in first["cc"] and first["connection_id"] == "1"
    again = inbox.save_reply_draft(db, ids["ana"], "Hi Ana, yes.")
    assert (again["id"], again["revision"], again["body"]) == (first["id"], 2, "Hi Ana, yes.")
    db.close()
    db = inbox.connect(path)  # a console restart reads the same text
    assert inbox.reply_drafts(db) == {ids["ana"]: "Hi Ana, yes."}
    assert [d["id"] for d in inbox.list_drafts(db)] == [first["id"]]
    assert inbox.save_reply_draft(db, ids["ana"], "  \n") is None
    assert inbox.reply_drafts(db) == {} and inbox.list_drafts(db) == []


def test_slack_reply_draft_has_no_subject_and_names_the_conversation(seeded):
    path, ids = seeded
    db = inbox.connect(path)
    d = inbox.save_reply_draft(db, ids["logan"], "no blockers")
    assert (d["kind"], d["subject"], d["cc"], d["connection_id"]) == ("slack", None, "", "2")
    assert d["to"] == inbox.thread_view(db, ids["logan"])["where"]


def test_one_reply_draft_per_thread(seeded):
    path, ids = seeded
    db = inbox.connect(path)
    d = inbox.save_reply_draft(db, ids["ana"], "one")
    values = {k: d[k] for k in ("connection_id", "kind", "sender", "to", "cc", "subject", "body")}
    with pytest.raises(sqlite3.IntegrityError):
        inbox.save_draft(db, values, thread_id=ids["ana"])


def test_a_reply_draft_sends_from_its_thread_not_as_a_new_thread(seeded):
    path, ids = seeded
    db = inbox.connect(path)
    d = inbox.save_reply_draft(db, ids["ana"], "Hi Ana,")
    with pytest.raises(ValueError, match="reply box"):
        inbox.send_draft(db, d["id"], d["revision"], FakeSender())
    assert inbox.reply_drafts(db) == {ids["ana"]: "Hi Ana,"}


def test_reply_removes_its_draft_only_when_the_send_goes_through(seeded):
    path, ids = seeded
    db = inbox.connect(path)
    inbox.save_reply_draft(db, ids["ana"], "Hi Ana,")
    inbox.save_reply_draft(db, ids["spec"], "other Thread")
    with pytest.raises(inbox.SendFailed):
        inbox.reply(db, ids["ana"], "Hi Ana,", FakeSender(inbox.SendFailed("HTTP 422")), ["ana@acme.com"])
    assert ids["ana"] in inbox.reply_drafts(db)
    inbox.reply(db, ids["ana"], "Hi Ana,", FakeSender("<r1@example.test>"), ["ana@acme.com"])
    assert inbox.reply_drafts(db) == {ids["spec"]: "other Thread"}


def test_deleted_reply_draft_restores_under_its_thread(seeded):
    path, ids = seeded
    db = inbox.connect(path)
    d = inbox.save_reply_draft(db, ids["ana"], "Hi Ana,")
    inbox.restore_draft(db, inbox.delete_draft(db, d["id"]))
    assert inbox.reply_drafts(db) == {ids["ana"]: "Hi Ana,"}


def test_reply_text_survives_a_console_restart_and_shows_on_the_drafts_page(seeded):
    from textual.widgets import DataTable, TextArea
    from test_console import run
    from comms_terminal.tui import Console, DraftsPage, ReplyBox
    path, ids = seeded
    seen = {}

    async def write(app, pilot):
        app.main.open_thread(ids["ana"])
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        app.main.thread_view.query_one(ReplyBox).query_one(TextArea).text = "Hi Ana, Tuesday works."
        await pilot.press("escape")
        await pilot.pause()

    run(Console(path), [write], size=(187, 40))

    async def reopen(app, pilot):  # a new console process: the text comes back from the database
        app.main.go("drafts")
        await pilot.pause()
        page = app.main.current
        assert isinstance(page, DraftsPage) and [d["thread_id"] for d in page.items] == [ids["ana"]]
        row = "".join(str(c) for c in page.query_one(DataTable).get_row_at(0))
        seen["row"] = row
        seen["pane"] = str(app.main.query_one("#thread .threadempty").content)
        await pilot.press("enter")
        for _ in range(4):
            await pilot.pause()
        box = app.main.thread_view.query_one(ReplyBox)
        seen["open"] = (box.has_class("open"), box.draft_key, box.query_one(TextArea).text)

    run(Console(path), [reopen], size=(187, 40))
    assert "Reply Draft · [/]Re: Pr" in seen["row"]
    assert "Hi Ana, Tuesday works." in seen["pane"] and "enter opens the Thread" in seen["pane"]
    assert seen["open"] == (True, ids["ana"], "Hi Ana, Tuesday works.")


def test_agent_drafts_list_labels_reply_drafts(seeded, capsys):
    from types import SimpleNamespace
    from comms_terminal import agent
    path, ids = seeded
    db = inbox.connect(path)
    d = inbox.save_reply_draft(db, ids["ana"], "Hi Ana,")
    agent.run(SimpleNamespace(cmd="drafts"), db)
    assert f"{d['id']}\temail Reply Draft · Thread {ids['ana']}\t" in capsys.readouterr().out


def test_a_file_staged_on_a_reply_draft_goes_out_with_the_reply(seeded):
    from test_attachment_send import PDF, pdf
    path, ids = seeded
    db = inbox.connect(path)
    d = inbox.save_reply_draft(db, ids["ana"], "Hi Ana, attached.")
    inbox.stage(db, sha=pdf(db), name="pkg.pdf", type="application/pdf", size=len(PDF), draft_id=d["id"])
    assert [s["name"] for s in inbox.staged(db, thread_id=ids["ana"])] == ["pkg.pdf"]  # the reply box lists it
    assert [s["name"] for s in inbox.staged(db, draft_id=d["id"])] == ["pkg.pdf"]
    sender = FakeSender()
    inbox.reply(db, ids["ana"], d["body"], sender, to=d["to"].split(", "), cc=[c for c in d["cc"].split(", ") if c])
    assert [f.name for f in sender.files] == ["pkg.pdf"]
    assert inbox.list_drafts(db) == [] and inbox.staged(db, thread_id=ids["ana"]) == []


def test_version_18_moves_files_staged_on_a_reply_draft_to_its_thread(seeded):
    from test_attachment_send import PDF, pdf
    path, ids = seeded
    db = inbox.connect(path)
    reply = inbox.save_reply_draft(db, ids["ana"], "Hi Ana,")
    new = inbox.save_draft(db, {"connection_id": "1", "kind": "email", "sender": "me@x.io", "to": "a@b.io",
                                "cc": "", "subject": "New", "body": "x"})
    sha = pdf(db)
    with db:  # how a schema-17 inbox.stage stored them
        for draft_id in (reply["id"], new["id"]):
            db.execute("INSERT INTO staged_attachments (draft_id, sha, name, type, size, added_at)"
                       " VALUES (?, ?, 'pkg.pdf', 'application/pdf', ?, '2026-10-16T21:10:06+00:00')",
                       (draft_id, sha, len(PDF)))
    from schema_history import downgrade, renumbered
    (ana,), (new_id,) = renumbered(db, "threads", ids["ana"]), renumbered(db, "composition_drafts", new["id"])
    downgrade(db, 17)
    inbox.migrate(db)
    rows = db.execute("SELECT draft_id, thread_id FROM staged_attachments ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [(None, ana), (new_id, None)]


def test_console_says_to_restart_once_the_database_outgrows_its_code(seeded):
    from test_console import run
    from comms_terminal.tui import Console
    path, _ = seeded
    seen = {}

    async def upgrade(app, pilot):
        other = sqlite3.connect(path)
        other.execute(f"PRAGMA user_version = {inbox.SCHEMA_VERSION + 1}")
        other.commit()
        other.close()
        for _ in range(2):
            app.check_changes()
            await pilot.pause()
        seen["toasts"] = [n.message for n in app._notifications if "upgraded" in n.message]

    run(Console(path), [upgrade])
    assert seen["toasts"] == ["The inbox was upgraded while this console ran · quit (q) and start it again."]


def test_agent_draft_new_with_a_thread_makes_its_reply_draft(seeded, capsys):
    from comms_terminal import agent
    path, ids = seeded
    db = inbox.connect(path)
    out = agent.draft(db, "new", {"body": "Hi Ana, Tuesday works."}, None, None, ids["ana"])
    (d,) = inbox.list_drafts(db)
    assert (d["thread_id"], d["subject"], d["body"]) == (ids["ana"], "Re: Pricing for 40 seats?", "Hi Ana, Tuesday works.")
    assert f"- Reply to: Thread {ids['ana']}" in out
    assert inbox.reply_drafts(db) == {ids["ana"]: "Hi Ana, Tuesday works."}  # the console's reply box opens on it
    with pytest.raises(ValueError, match=f"already has reply Draft {d['id']}"):
        agent.draft(db, "new", {"body": "again"}, None, None, ids["ana"])
    with pytest.raises(ValueError, match="needs --body"):
        agent.draft(db, "new", {}, None, None, ids["logan"])
    assert "Reply to" not in agent.draft(db, "new", {}, None, "1")  # no Thread: a new Thread's Draft
