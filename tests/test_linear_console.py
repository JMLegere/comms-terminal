"""Linear Context uses the existing source socket, page sections, health, and clipboard paths."""

import threading

import pytest
from rich.text import Text

from test_console import page, run, seeded
from comms_terminal import inbox, linear, serve, tui
from comms_terminal.icons import ICON
from comms_terminal.tui import (CONTEXT_SOURCES, CasePage, Console, ContextSection, OrganizationPage,
                                 PersonPage, ReplyError, ThreadsPage, agent_text, linear_markup, socket_context)


URL = "https://linear.app/example/issue/DEV-123/pricing"


def answer(**changes):
    issue = {"id": "issue-1", "identifier": "DEV-123", "title": "Pricing for 40 seats", "url": URL,
             "state": "Backlog", "priority": "High", "assignee": "Jeremy Legere", "project": "Acme rollout",
             "updated": "2026-10-12T10:00:00+00:00", "reasons": ["email: ana@acme.com (description)",
                                                                          "customer: acme.com"],
             "requests": [{"id": "request-1", "text": "Need 40 seats before the November rollout.", "customer": "Acme"}]}
    issue.update(changes)
    return {"issues": [issue]}


def section(root):
    return next(s for s in root.query(ContextSection) if s.source == "linear")


def plain(markup):
    return Text.from_markup(markup).plain


async def settle(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


def test_linear_registration_reuses_task_glyph_and_existing_source_order():
    assert list(CONTEXT_SOURCES) == ["attio", "tana", "linear"]
    assert CONTEXT_SOURCES["linear"] == ("Linear", "task", linear_markup)
    assert ContextSection("linear").border_title == f"{ICON['task']} linear"


def test_agent_thread_command_keeps_linear_in_fresh_markdown(tmp_path, monkeypatch, capsys):
    from comms_terminal.main import main

    path, ids = seeded(tmp_path)
    calls = []

    def source(socket, name):
        def fetch(emails, domains):
            calls.append((name, emails, domains))
            return answer() if name == "linear" else None
        return fetch

    monkeypatch.setattr("comms_terminal.agent.socket_context", source)
    main(["--db", path, "--socket", str(tmp_path / "context.sock"), "thread", str(ids["ana"])])
    text = capsys.readouterr().out
    assert [name for name, _, _ in calls] == ["attio", "tana", "linear"]
    assert "ana@acme.com" in calls[-1][1] and "acme.com" in calls[-1][2]
    assert "## Linear\n" in text and '"identifier": "DEV-123"' in text
    assert "Need 40 seats before the November rollout." in text and URL in text


@pytest.mark.parametrize("width", [18, 26, 42])
def test_linear_markup_wraps_escaped_metadata_match_reasons_and_requests(monkeypatch, width):
    monkeypatch.setattr(tui, "when", lambda value: "2d")
    ctx = answer(title="[red]price[/red]", state="[b]Triage[/b]", assignee="[b]Jeremy[/b]",
                 project="[b]Acme[/b]", reasons=["email: ana@acme.com [b]match[/b]"],
                 requests=[{"id": "request-1", "text": "Need [b]40 seats[/b] before November.",
                            "customer": "[b]Acme[/b]"}])
    markup = linear_markup(ctx, width=width)
    rendered = Text.from_markup(markup)
    compact = "".join(rendered.plain.split())
    for value in ("DEV-123", "[red]price[/red]", "state · [b]Triage[/b]", "priority · High",
                  "assignee · [b]Jeremy[/b]", "project · [b]Acme[/b]", "updated · 2d", "WHY MATCHED",
                  "email: ana@acme.com [b]match[/b]", "CUSTOMER REQUESTS", "customer · [b]Acme[/b]",
                  "Need [b]40 seats[/b] before November.", "Open in Linear"):
        assert "".join(value.split()) in compact
    assert all(Text.from_markup(row).cell_len <= width for row in markup.splitlines())
    assert not any(span.style == "red" for span in rendered.spans)
    assert f"Open in Linear <{URL}>" in agent_text(markup)


@pytest.mark.parametrize("url", ["http://linear.app/example/issue/DEV-123", "https://evil.example/issue/DEV-123",
                                  "https://linear.app.evil.example/issue/DEV-123",
                                  "https://linear.app@evil.example/issue/DEV-123",
                                  "https://linear.app:443/example/issue/DEV-123",
                                  "https://linear.app/x'][link='https://evil.example']",
                                  "https://linear.app/x[bold]", "https://linear.app/x\\y",
                                  "https://linear.app/x\nnext", "https://linear.app/x\x1bnext", ""])
def test_linear_markup_never_links_unsafe_targets(url):
    markup = linear_markup(answer(url=url))
    assert "DEV-123" in plain(markup)
    assert "[link=" not in markup and "Open in Linear" not in plain(markup)


def test_linear_markup_keeps_all_issues_and_handles_empty_display_values():
    ctx = answer(updated="[red]bad date[/red]", state="", priority="", assignee="", project="", reasons=[], requests=[])
    ctx["issues"].append({**ctx["issues"][0], "id": "issue-2", "identifier": "DEV-124", "title": "Second issue"})
    text = " ".join(plain(linear_markup(ctx, width=60)).split())
    assert "DEV-123" in text and "DEV-124 · Second issue" in text
    assert "updated · [red]bad date[/red]" in text
    assert "WHY MATCHED" not in text and "CUSTOMER REQUESTS" not in text and "assignee" not in text


def test_linear_markup_has_distinct_loading_empty_and_escaped_failure_states():
    assert plain(linear_markup(None)) == "loading…"
    assert plain(linear_markup({"issues": []})) == "no matching open issues in Linear"
    error = "[red]provider unavailable[/red] retry later"
    markup = linear_markup(None, error, width=26)
    assert "Linear unavailable: [red]provider unavailable[/red] retry later" in " ".join(plain(markup).split())
    assert all(Text.from_markup(row).cell_len <= 26 for row in markup.splitlines())


@pytest.mark.parametrize("status", [200, 404, 503, 502])
def test_linear_socket_fetch_preserves_emails_domains_contract(monkeypatch, status):
    calls = []
    body = answer() if status == 200 else {"error": "Linear unavailable"}

    def post(socket_path, path, payload, timeout):
        calls.append((socket_path, path, payload, timeout))
        return status, body

    monkeypatch.setattr(tui, "_post", post)
    fetch = socket_context("/synthetic/inbox.sock", "linear")
    if status == 502:
        with pytest.raises(ReplyError, match="Linear unavailable"):
            fetch(["ana@acme.com"], ["acme.com"])
    else:
        assert fetch(["ana@acme.com"], ["acme.com"]) == (body if status == 200 else None)
    assert calls == [("/synthetic/inbox.sock", "/linear", {"emails": ["ana@acme.com"], "domains": ["acme.com"]}, 120)]


@pytest.mark.parametrize("surface", ["thread", "person", "organization", "case"])
def test_linear_context_and_match_evidence_on_all_four_surfaces(tmp_path, surface):
    path, ids = seeded(tmp_path)
    calls, replies, copied = [], [], []
    ctx = answer()

    def fetch(emails, domains):
        calls.append((emails, domains))
        return ctx

    app = Console(path, reply=lambda *args: replies.append(args), sources={"linear": fetch})
    app.copy_to_clipboard = copied.append

    async def scenario(app, pilot):
        await settle(app, pilot)
        before = inbox.get_thread(app.db, ids["ana"])
        activities = app.db.execute("SELECT count(*) FROM activities").fetchone()[0]
        app.main.open_thread(ids["ana"])
        await settle(app, pilot)
        person_id = app.db.execute("SELECT person_id FROM identities WHERE value = 'ana@acme.com'").fetchone()[0]
        emails, domains = ["ana.ruiz@gmail.com", "ana@acme.com", "ops@acme.com"], ["acme.com"]
        if surface == "thread":
            root = app.main.thread_view
            assert isinstance(page(app), ThreadsPage)
        else:
            calls.clear()
            if surface == "person":
                app.main.open_person(person_id)
                emails = ["ana.ruiz@gmail.com", "ana@acme.com"]
            elif surface == "organization":
                app.action_open_org("acme.com")
                emails = ["ana.ruiz@gmail.com", "ana@acme.com"]
            else:
                app.action_open_case(before.case_id)
                emails = sorted([*emails, "dana@northwind.io"])
                domains = ["acme.com", "northwind.io"]
            await settle(app, pilot)
            root = app.main.query_one("#thread") if surface == "organization" else page(app)
            assert isinstance(page(app), {"person": PersonPage, "organization": OrganizationPage, "case": CasePage}[surface])
        current = section(root)
        assert (emails, domains) in calls
        assert [s.source for s in root.query(ContextSection)] == ["attio", "tana", "linear"]
        assert current.display and current.answer == (ctx, None)
        text = " ".join(str(current.render()).split())
        for content in ("DEV-123", "WHY MATCHED", "ana@acme.com", "CUSTOMER REQUESTS", "Need 40 seats", "Open in Linear"):
            assert content in text
        assert current.styles.text_wrap == "wrap"
        assert app.source_state["linear"] == "ok"
        if surface in ("thread", "case"):
            page(app).action_copy()
            assert "## Linear\n" in copied[-1] and '"identifier": "DEV-123"' in copied[-1]
            assert "Need 40 seats before the November rollout." in copied[-1] and URL in copied[-1]
            assert "Fresh copy: `comms-terminal thread " in copied[-1]
        if surface == "person":
            await pilot.press("escape")
            await settle(app, pilot)
            assert isinstance(page(app), ThreadsPage) and app.main.thread_view.thread_id == ids["ana"]
        assert inbox.get_thread(app.db, ids["ana"]) == before
        assert app.db.execute("SELECT count(*) FROM activities").fetchone()[0] == activities
        assert replies == []

    run(app, [scenario], size=(190, 52))


@pytest.mark.parametrize("outcome", ["ok", "error", "disconnected"])
def test_linear_health_uses_generic_rail_footer_and_unavailable_section(tmp_path, outcome):
    path, ids = seeded(tmp_path)

    def fetch(emails, domains):
        if outcome == "error":
            raise ReplyError("Linear request failed")
        return {"issues": []} if outcome == "ok" else None

    app = Console(path, sources={"linear": fetch})

    async def scenario(app, pilot):
        app.main.open_thread(ids["ana"])
        await settle(app, pilot)
        current = section(app.main.thread_view)
        rail = app.main.rail
        option_ids = [rail.get_option_at_index(i).id for i in range(rail.option_count)]
        footer = str(app.main.query_one("#statusline").render())
        if outcome == "disconnected":
            assert app.source_state["linear"] is None and not current.display
            assert "context:linear" not in option_ids and "Linear broken" not in footer
        else:
            assert current.display and "context:linear" in option_ids
            prompt = str(rail.get_option("context:linear").prompt)
            assert "Linear" in prompt and ICON["task"] in prompt
            if outcome == "error":
                assert app.source_state["linear"] == "Linear request failed"
                assert ICON["broken"] in prompt and "Linear broken" in footer
                assert "Linear unavailable:" in " ".join(str(current.render()).split())
            else:
                assert app.source_state["linear"] == "ok" and ICON["ok"] in prompt
                assert ICON["task"] in footer and "Linear broken" not in footer
                assert "no matching open issues" in str(current.render())

    run(app, [scenario], size=(190, 52))


@pytest.mark.parametrize("change", ["rename", "local-ambiguity", "external-ambiguity"])
def test_linear_person_context_refreshes_local_names_over_unix_socket(tmp_path, change):
    path, _ = seeded(tmp_path)
    calls, provider_calls = [], []
    identifiers = {"ana ruiz": "OLD-1", "ana pereira": "NEW-1"}

    def matching_names(client, emails, domains, person_names, organization_names):
        provider_calls.append((emails, domains, person_names, organization_names))
        return {"issues": [answer(identifier=identifiers[name], title=f"Issue for {name}",
                                 reasons=[f"person: {name} (title)"], requests=[])["issues"][0]
                           for name in person_names if name in identifiers]}

    context = linear.Context(None, clock=lambda: 0.0)
    context.fetch = matching_names  # Synthetic provider; real socket, local-name resolver, and names-aware cache.
    sock = str(tmp_path / "inbox.sock")
    server = serve.reply_server(path, sock, serve.Senders(), {"linear": context})
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    socket_fetch = socket_context(sock, "linear")

    def fetch(emails, domains):
        calls.append((emails, domains))
        return socket_fetch(emails, domains)

    app = Console(path, sources={"linear": fetch, "attio": lambda *args: None, "tana": lambda *args: None})

    async def scenario(app, pilot):
        await settle(app, pilot)
        pid = app.db.execute("SELECT person_id FROM identities WHERE value = 'ana@acme.com'").fetchone()[0]
        app.main.open_person(pid)
        await settle(app, pilot)
        current = section(page(app))
        old_key, old_answer = current.key, current.answer
        legacy_keys = {s.source: s.key for s in page(app).query(ContextSection) if s.source != "linear"}
        assert all(len(key) == 2 for key in legacy_keys.values())
        assert old_key == (("ana.ruiz@gmail.com", "ana@acme.com"), ("acme.com",),
                           (app.db.total_changes, app.db.execute("PRAGMA data_version").fetchone()[0]))
        assert old_answer[0]["issues"][0]["identifier"] == "OLD-1"
        assert provider_calls[-1][2] == ("ana ruiz",)
        call_count, provider_count = len(calls), len(provider_calls)
        page(app).refresh_data()
        await settle(app, pilot)
        assert len(calls) == call_count  # No writes: same keys still avoid even the local socket.

        if change == "rename":
            await pilot.press("n")
            await pilot.pause()
            await pilot.press(*"Ana Pereira", "enter")
            assert inbox.person_view(app.db, pid)["name"] == "Ana Pereira"
        else:
            db = inbox.connect(path) if change == "external-ambiguity" else app.db
            try:
                with db:
                    db.execute("INSERT INTO persons (display_name, created_at) VALUES (' ANA  RUIZ ', 'now')")
            finally:
                if db is not app.db:
                    db.close()
            if change == "external-ambiguity":
                app.check_changes()
            else:
                page(app).refresh_data()
        await settle(app, pilot)
        latest = current.answer
        assert len(calls) == call_count + 1 and len(provider_calls) == provider_count + 1
        assert calls[-1] == (list(old_key[0]), list(old_key[1]))
        assert current.key[:2] == old_key[:2] and current.key != old_key
        if change == "external-ambiguity":
            assert current.key[2][0] == old_key[2][0] and current.key[2][1] != old_key[2][1]
        else:
            assert current.key[2][0] > old_key[2][0] and current.key[2][1] == old_key[2][1]
        assert {s.source: s.key for s in page(app).query(ContextSection) if s.source != "linear"} == legacy_keys
        if change == "rename":
            assert provider_calls[-1][2] == ("ana pereira",)
            assert latest[0]["issues"][0]["identifier"] == "NEW-1" and latest[1] is None
            assert "NEW-1" in str(current.render()) and "OLD-1" not in str(current.render())
        else:
            assert provider_calls[-1][2] == ()
            assert latest == ({"issues": []}, None)
            assert "no matching open issues" in str(current.render())
        current.show(old_key, old_answer[0], None)
        current.show(old_key, None, "stale name failure")
        assert current.answer == latest and app.source_state["linear"] == "ok"

        # Conservative local invalidation still reuses the service's unchanged names-aware cache.
        before = current.key
        call_count, provider_count = len(calls), len(provider_calls)
        with app.db:
            app.db.execute("UPDATE persons SET display_name = display_name WHERE id = ?", (pid,))
        page(app).refresh_data()
        await settle(app, pilot)
        assert current.key[:2] == before[:2] and current.key != before
        assert len(calls) == call_count + 1 and len(provider_calls) == provider_count
        assert current.answer == latest

    try:
        run(app, [scenario], size=(190, 52))
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_linear_context_ignores_late_answers_after_page_addresses_move(tmp_path):
    path, ids = seeded(tmp_path)
    app = Console(path, sources={"linear": lambda emails, domains: {"issues": []}})

    async def scenario(app, pilot):
        app.main.open_thread(ids["ana"])
        await settle(app, pilot)
        current, pending = section(app.main.thread_view), []
        current.fetch = pending.append  # Deterministic slow source; no provider, sleeps, or worker races.
        current.load(["OLD@acme.com", "old@acme.com"], ["ACME.COM"])
        old = current.key
        assert old == (("old@acme.com",), ("acme.com",),
                       (app.db.total_changes, app.db.execute("PRAGMA data_version").fetchone()[0]))
        assert current.answer == (None, None) and "loading…" in str(current.render())
        current.load(["new@northwind.io"], ["northwind.io"])
        new = current.key
        assert pending == [old, new]
        current.show(old, answer(identifier="OLD-1"), None)
        current.show(old, None, "stale failure")
        assert current.answer == (None, None) and app.source_state["linear"] == "ok"
        latest = answer(identifier="NEW-1")
        current.show(new, latest, None)
        await pilot.pause()
        assert current.answer == (latest, None) and "NEW-1" in str(current.render())
        current.show(old, answer(identifier="OLD-1"), None)
        assert current.answer == (latest, None) and app.source_state["linear"] == "ok"
        current.load([], [])
        assert not current.display and pending == [old, new]

    run(app, [scenario], size=(190, 52))
