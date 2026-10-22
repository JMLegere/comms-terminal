"""y copies the shown Thread or Draft as Markdown for a terminal agent."""

from test_console import page, run, seeded
from comms_terminal import inbox
from comms_terminal.tui import Console, agent_text


def copied(app):
    out = []
    app.copy_to_clipboard = out.append
    return out


def test_agent_text_keeps_link_urls():
    assert agent_text("see [link='https://a.test/x'][underline]docs[/underline][/link] [#928374]↗ a.test[/]") == \
        "see docs <https://a.test/x> ↗ a.test"
    assert agent_text("[link='https://a.test/x'][underline]↗ a.test[/underline][/link]") == "https://a.test/x"


def test_y_copies_the_selected_thread_in_full(tmp_path):
    path, _ = seeded(tmp_path)
    app, seen = Console(path), {}

    async def go(app, pilot):
        seen["out"], seen["tid"] = copied(app), page(app).current_thread_id()
        await pilot.press("y")
        await pilot.pause()

    run(app, [go])
    v = inbox.thread_view(inbox.connect(path), seen["tid"])
    text = seen["out"][0]
    assert text.startswith(f"# {v['thread'].title}\n")
    assert f"- Thread: {seen['tid']} " in text
    assert text.count("\n### ") == len(v["messages"])


def test_y_copies_the_draft_as_edited(tmp_path):
    path, _ = seeded(tmp_path)
    inbox.save_draft(inbox.connect(path), dict(connection_id=None, kind="email", sender="me@example.test",
                                               to="ana@acme.com", cc="", subject="Pricing", body="Hi Ana"))
    app, seen = Console(path), {}

    async def go(app, pilot):
        seen["out"] = copied(app)
        await pilot.press("6")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()

    run(app, [go])
    text = seen["out"][0]
    assert text.startswith("# Draft: Pricing\n") and "- To: ana@acme.com" in text and "\nHi Ana\n" in text
    assert "## Captured history with these recipients" in text  # the seed has Threads with ana@acme.com


def test_agent_commands_read_threads_and_change_drafts_by_revision(tmp_path, capsys, monkeypatch):
    import io

    import pytest

    from comms_terminal.main import main
    path, ids = seeded(tmp_path)
    cli = lambda *args: main(["--db", path, "--socket", str(tmp_path / "none.sock"), *args])  # noqa: E731

    cli("threads")
    assert f"{ids['ana']}\t" in capsys.readouterr().out
    cli("thread", str(ids["ana"]))
    out = capsys.readouterr().out
    assert out.startswith("# ") and "service not reachable" in out  # the Context sources need the service

    db = inbox.connect(path)
    c = next(c for c in inbox.connections(db) if c["platform"] in ("gmail", "fastmail"))
    cli("draft", "new", "--connection", str(c["id"]), "--to", "ana@acme.com", "--subject", "Pricing")
    d = inbox.list_drafts(db)[0]
    assert (d["to"], d["subject"], d["sender"], d["revision"]) == ("ana@acme.com", "Pricing", c["self_id"], 1)
    assert "--revision 1" in capsys.readouterr().out

    with pytest.raises(SystemExit, match="--revision 1"):
        cli("draft", str(d["id"]), "--body", "Hi")
    monkeypatch.setattr("sys.stdin", io.StringIO("Hi Ana,\nTuesday works.\n"))
    cli("draft", str(d["id"]), "--revision", "1", "--body", "-")
    assert inbox.get_draft(db, d["id"])["body"] == "Hi Ana,\nTuesday works.\n"
    with pytest.raises(SystemExit, match="changed since"):  # a stale revision never overwrites newer words
        cli("draft", str(d["id"]), "--revision", "1", "--body", "stale")
    assert inbox.get_draft(db, d["id"])["revision"] == 2
