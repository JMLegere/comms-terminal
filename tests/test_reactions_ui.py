"""Synthetic native widgets/Pilot coverage. No provider, credentials, network, or durable reaction store.

Capture seam: make_reaction_app(tmp_path), then inside app.run_test(size=(187, 50))
await prepare_reaction_state(app, pilot, "ready" | "checking" | "reacted" |
"unavailable" | "refused" | "unknown" | "count_failure"); app.export_screenshot().
Use (80, 58) for narrow. Checking intentionally parks a fake read in app.pending_reaction_reads.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from rich.text import Text
from textual import events
from textual.widgets import TextArea

from test_console import run
from comms_terminal import inbox, tui
from comms_terminal.tui import CasePage, Console, OrganizationPage, PipelinePage, ReactionLine


class FakeReactions:
    def __init__(self, revisions, mode="ready"):
        self.revisions, self.mode, self.calls = revisions, mode, []
        self.groups = {mid: [] for mid in revisions}

    @property
    def writes(self):
        return [call for call in self.calls if call["op"] != "read"]

    def __call__(self, payload):
        self.calls.append(dict(payload))
        target = {k: payload[k] for k in ("thread_id", "message_id", "connection_id")}
        mid = payload["message_id"]
        if payload["op"] == "read":
            if self.mode == "unavailable" or (self.mode == "count_failure" and self.writes):
                return dict(target, outcome="unavailable", error="Reaction read permission unavailable.")
            return dict(target, outcome="ready", revision=self.revisions[mid],
                        groups=[dict(group) for group in self.groups[mid]])
        if self.mode == "refused":
            return dict(target, outcome="refused", error="Reaction write permission unavailable.")
        name, mine = payload["name"], payload["op"] == "add"
        group = next((g for g in self.groups[mid] if g["name"] == name), None)
        was_mine = group["mine"] if group else False
        if group is None:
            group = dict(name=name, count=0, mine=False)
            self.groups[mid].append(group)
        group.update(count=group["count"] + (int(mine) - int(was_mine)), mine=mine)
        self.groups[mid] = [g for g in self.groups[mid] if g["count"]]
        if self.mode == "unknown":
            return dict(target, outcome="unknown", error="Response lost after dispatch.")
        return dict(target, outcome="confirmed", op=payload["op"], name=name,
                    revision=payload["expected_revision"], mine=mine, changed=was_mine != mine)


def make_reaction_app(tmp_path, *, mode="ready", message_count=3):
    """Small reusable native app fixture; caller owns run_test/export_screenshot, never real Slack."""
    path = str(tmp_path / "reactions.db")
    db = inbox.connect(path)
    inbox.migrate(db)
    now = datetime.now(timezone.utc)
    with db:
        db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at, probed_at)"
                   " VALUES (1, 'slack', 'T_SYNTHETIC', 'U_SELF', 'Slack', ?, ?)",
                   (now.isoformat(), now.isoformat()))
    native, thread = [], None
    for i in range(message_count):
        at = now - timedelta(minutes=message_count + 2 - i)
        ts = f"{at.timestamp():.6f}"
        native.append(f"C_REVIEW:{ts}")
        name = ("Alex Rivera", "Maya Chen", "Ravi Shah")[i % 3]
        body = ("Gate plan ready.", "Review ready.\nGate plan unchanged.", "I will check.")[i % 3]
        thread = inbox.ingest(db, inbox.Incoming(
            1, "C_REVIEW", "channel", "MESSAGE", native[-1], inbox.Identity("slack_user", f"U_{i}", name),
            at, body, native[:1] if i else [], "Gate review", {"channel": "C_REVIEW", "ts": ts}, "review-room"))
    other = inbox.ingest(db, inbox.Incoming(
        1, "D_OTHER", "im", "MESSAGE", "D_OTHER:999.000001", inbox.Identity("slack_user", "U_OTHER", "Other Person"),
        now, "Another card.", data={"channel": "D_OTHER", "ts": "999.000001"}))
    inbox.act(db, thread.id, inbox.Investigate())
    inbox.act(db, other.id, inbox.Investigate())
    view = inbox.thread_view(db, thread.id)
    messages = view["messages"]
    revisions = {m["id"]: m["revision"] for m in messages}
    revisions.update({m["id"]: m["revision"] for m in inbox.thread_view(db, other.id)["messages"]})
    fake = FakeReactions(revisions, mode)
    if len(messages) > 1:
        fake.groups[messages[1]["id"]] = [dict(name="+1", count=3, mine=False), dict(name="pray", count=2, mine=False)]
    ids = dict(thread=thread.id, other=other.id, messages=[m["id"] for m in messages], native=native,
               keys=[(thread.id, m["id"], 1) for m in messages])
    db.close()
    app = Console(path, reactions=fake)
    app.home, app.reaction_fixture = "view:0", (ids, fake)
    return app, ids, fake


async def settle(app, pilot):
    await pilot.pause(tui.PREVIEW_GAP * 2 + 0.02)
    for _ in range(30):
        await pilot.pause(0.02)
        if app._reaction_read_active is None and not app._reaction_reads:
            await app.workers.wait_for_complete()
            await pilot.pause()
            return
    raise AssertionError("Synthetic reaction reads did not settle")


def read_result(job, groups=()):
    tid, mid, cid = job["key"]
    return dict(outcome="ready", thread_id=tid, message_id=mid, connection_id=cid,
                revision=job["revision"], groups=[dict(g) for g in groups])


def message_row(app, key):
    return next(w for w in app.reaction_scope()[1].query(".mrow.open") if w.reaction_key == key)


def visible_text(root):
    return "\n".join(Text.from_markup(str(w.content)).plain for w in root.query(tui.MessageText))


async def prepare_reaction_state(app, pilot, state="ready"):
    """Exercise implemented actions for parent screenshot capture; no separate mock renderer."""
    ids, fake = app.reaction_fixture
    await settle(app, pilot)
    target = ids["keys"][min(1, len(ids["keys"]) - 1)]
    app.last_opened_message[ids["thread"]] = target[1]
    if state == "checking":
        app.pending_reaction_reads = []
        app._reaction_fetch = app.pending_reaction_reads.append
    elif state in ("unavailable", "refused", "unknown", "count_failure"):
        fake.mode = state
    await pilot.press("v")
    await pilot.pause()
    for _ in range(len(ids["keys"])):
        if app.react_target == target:
            break
        if app.react_target is None:
            await pilot.press("k")
        else:
            await pilot.press("k" if app.react_target[1] > target[1] else "j")
        await pilot.pause()
    assert app.react_target == target
    if state != "checking":
        await settle(app, pilot)
    if state in ("reacted", "refused", "unknown", "count_failure"):
        await pilot.press("1")
        await settle(app, pilot)
    return target


def test_entry_early_digits_cancel_and_one_ready_write_keep_target_and_viewport(tmp_path):
    app, ids, fake = make_reaction_app(tmp_path)

    async def scenario(app, pilot):
        await settle(app, pilot)
        alex = ids["keys"][0]
        app.action_msg_toggle(ids["thread"], ids["native"][0])
        app.action_msg_toggle(ids["thread"], ids["native"][0])
        await pilot.pause()
        pane = app.main.thread_view.query_one(".timeline")
        pane.scroll_home(animate=False, immediate=True)
        await pilot.pause()
        y, page = pane.scroll_y, app.main.page
        pending = []
        fetch = app._reaction_fetch
        app._reaction_fetch = pending.append
        await pilot.press("v", "1", "2", "3")
        await pilot.pause()
        assert app.react_target == alex and app.react_mode
        assert app.main.page is page and pane.scroll_y == y and not fake.writes
        assert "Checking reactions" in visible_text(message_row(app, alex))
        assert len(pending) == 1
        await pilot.press("escape")
        assert not app.react_mode and not fake.writes
        app._reaction_read_done(pending.pop(), read_result(app._reaction_read_active))
        app._reaction_fetch = fetch
        await pilot.press("v")
        await settle(app, pilot)
        assert app.react_target == alex, (app.react_target, app.reaction_states[alex])
        assert app.reaction_states[alex]["ready"], (app.reaction_states[alex], fake.calls)
        await pilot.press("1")
        await settle(app, pilot)
        assert not app.react_mode and len(fake.writes) == 1
        assert fake.writes[0] == dict(op="add", name="+1", expected_revision=alex[1],
                                      thread_id=alex[0], message_id=alex[1], connection_id=alex[2])
        assert pane.scroll_y == y and app.main.page is page
        assert app.reaction_states[alex]["write_outcome"] == "confirmed"

    run(app, [scenario], size=(187, 50))


def test_checked_choice_removes_only_own_membership_and_returns_to_reading(tmp_path):
    app, ids, fake = make_reaction_app(tmp_path)
    maya = ids["keys"][1]
    fake.groups[maya[1]][0].update(count=7, mine=True)

    async def scenario(app, pilot):
        await prepare_reaction_state(app, pilot)
        row = message_row(app, maya)
        text = visible_text(row)
        assert "7 ✓" in text and "1 👍 ✓ Like" in text
        await pilot.press("1")
        await settle(app, pilot)
        assert fake.writes[-1]["op"] == "remove" and not app.react_mode
        assert fake.groups[maya[1]][0] == dict(name="+1", count=6, mine=False)
        assert fake.groups[maya[1]][1] == dict(name="pray", count=2, mine=False)
        assert "Removed 👍" in visible_text(message_row(app, maya))

    run(app, [scenario], size=(187, 50))


def test_navigation_stops_skips_collapsed_and_explicitly_reveals_offscreen_open_messages(tmp_path):
    app, ids, fake = make_reaction_app(tmp_path, message_count=14)

    async def scenario(app, pilot):
        await settle(app, pilot)
        app.action_msg_toggle(ids["thread"], ids["native"][12])
        await pilot.pause()
        pane = app.main.thread_view.query_one(".timeline")
        before = pane.scroll_y
        await pilot.press("v")
        await settle(app, pilot)
        assert app.react_target == ids["keys"][-1] and pane.scroll_y == before
        await pilot.press("j", "down")
        assert app.react_target == ids["keys"][-1]
        await pilot.press("k")
        await settle(app, pilot)
        assert app.react_target == ids["keys"][11] and pane.scroll_y < before
        for _ in range(20):
            await pilot.press("up")
        await settle(app, pilot)
        assert app.react_target == ids["keys"][0]
        assert not fake.writes
        await pilot.press("escape")
        app.action_expand_all(False)
        await pilot.pause()
        await pilot.press("v", "1")
        assert app.react_target is None and not fake.writes

    run(app, [scenario], size=(120, 24))


def test_editor_keys_and_organization_pipeline_v_keep_existing_owners(tmp_path, monkeypatch):
    app, ids, fake = make_reaction_app(tmp_path)
    values = []
    monkeypatch.setattr(tui, "set_org_value", lambda app, org: values.append(org["id"]))

    async def scenario(app, pilot):
        await settle(app, pilot)
        await pilot.press("r")
        await pilot.pause()
        body = app.main.thread_view.query_one(".reply", TextArea)
        body.focus()
        await pilot.press("v", "1", "2", "3", "left")
        assert body.text == "v123" and app.focused is body and not app.react_mode
        await pilot.press("escape")
        with app.db:
            app.db.execute("INSERT INTO organizations (domain, name, created_at) VALUES ('acorn.test', 'Acorn Lab', ?)",
                           (datetime.now(timezone.utc).isoformat(),))
        app.main.open(lambda: OrganizationPage("acorn.test"))
        await pilot.pause()
        await pilot.press("v")
        assert values and not app.react_mode
        assert any(b.key == "v" and b.action == "value" for b in tui.BoardColumn.BINDINGS)
        assert not any(b.key == "v" for b in PipelinePage.BINDINGS)
        assert not fake.writes

    run(app, [scenario], size=(187, 50))


def test_unknown_maya_never_resolved_by_default_alex_or_passive_refresh(tmp_path):
    app, ids, fake = make_reaction_app(tmp_path, mode="unknown")
    alex, maya = ids["keys"][:2]

    async def scenario(app, pilot):
        await settle(app, pilot)
        app.last_opened_message[ids["thread"]] = alex[1]
        await pilot.press("v")
        await settle(app, pilot)
        assert app.react_target == alex
        await pilot.press("j")
        await settle(app, pilot)
        await pilot.press("1")
        await settle(app, pilot)
        assert app.reaction_states[maya]["write_outcome"] == "unknown"
        await pilot.press("v")
        await settle(app, pilot)
        assert app.react_target == alex and app.reaction_states[maya]["write_outcome"] == "unknown"
        await pilot.press("escape", "f5")
        await settle(app, pilot)
        assert app.reaction_states[maya]["write_outcome"] == "unknown" and len(fake.writes) == 1
        await pilot.press("v")
        await settle(app, pilot)
        await pilot.press("j")
        await settle(app, pilot)
        assert app.react_target == maya and app.reaction_states[maya]["write_outcome"] is None
        assert app.reaction_states[maya]["own"]["+1"] and len(fake.writes) == 1
        assert "Current reactions checked" in visible_text(message_row(app, maya))

    run(app, [scenario], size=(187, 50))


@pytest.mark.parametrize("loss", ["deleted", "identity"])
def test_unknown_original_target_loss_fails_closed(tmp_path, loss):
    app, ids, fake = make_reaction_app(tmp_path, mode="unknown")
    maya = ids["keys"][1]

    async def scenario(app, pilot):
        await prepare_reaction_state(app, pilot, "unknown")
        if loss == "deleted":
            inbox.ingest(app.db, inbox.Incoming(1, "C_REVIEW", "channel", "DELETE", ids["native"][1], None,
                                                datetime.now(timezone.utc), None))
        else:
            with app.db:
                app.db.execute("UPDATE connections SET self_id = 'U_DIFFERENT' WHERE id = 1")
        app.repaint_messages()
        await pilot.pause()
        app.enter_react()
        app._reaction_select(maya, recovery=True)
        await pilot.press("c", "1", "2", "3")
        await pilot.pause()
        assert app.reaction_states[maya]["write_outcome"] == "unknown"
        assert not app.reaction_states[maya]["ready"] and len(fake.writes) == 1

    run(app, [scenario], size=(187, 50))


def test_late_and_wrong_echo_reads_cannot_replace_newer_check_or_selected_message(tmp_path):
    app, ids, fake = make_reaction_app(tmp_path)
    alex, maya = ids["keys"][:2]

    async def scenario(app, pilot):
        await settle(app, pilot)
        pending = []
        app._reaction_fetch = pending.append
        app.last_opened_message[ids["thread"]] = alex[1]
        await pilot.press("v")
        await pilot.pause()
        old = pending.pop(0)
        app._reaction_select(maya, recovery=True)
        app._reaction_read_done(old, dict(read_result(old), message_id=maya[1]))
        latest = pending.pop(0)
        app._reaction_read_done(latest, read_result(latest, [dict(name="eyes", count=9, mine=True)]))
        assert app.react_target == maya and app.reaction_states[maya]["ready"]
        app._reaction_select(alex, recovery=True)
        newer = pending.pop(0)
        app._reaction_read_done(old, read_result(old, [dict(name="+1", count=999, mine=True)]))
        assert not app.reaction_states[alex]["ready"]
        app._reaction_read_done(newer, read_result(newer))
        assert app.reaction_states[alex]["groups"] == [] and app.reaction_states[alex]["ready"]
        assert app.reaction_states[maya]["groups"][0]["count"] == 9
        assert not fake.writes

    run(app, [scenario], size=(187, 50))


def test_pending_mutation_returns_to_reading_and_blocks_only_exact_pair(tmp_path):
    app, ids, fake = make_reaction_app(tmp_path)
    maya = ids["keys"][1]

    async def scenario(app, pilot):
        await prepare_reaction_state(app, pilot)
        changes = []
        app._reaction_change = changes.append
        await pilot.press("1")
        await pilot.pause()
        assert not app.react_mode and len(changes) == 1
        await pilot.press("v", "1", "2", "3", "escape")
        assert len(changes) == 1 and app.reaction_states[maya]["pending"]
        await pilot.press("j")
        await pilot.pause(tui.PREVIEW_GAP * 2)
        assert app.main.page.current_thread_id() == ids["other"]
        foreign = app.main.thread_view.query_one(".timeline")
        pin, y = foreign.pinned, foreign.scroll_y
        job = changes[0]
        payload = dict(op=job["op"], name=job["name"], expected_revision=job["revision"],
                       thread_id=maya[0], message_id=maya[1], connection_id=maya[2])
        app._reaction_change_done(job, fake(payload))
        await settle(app, pilot)
        assert app.reaction_states[maya]["write_outcome"] == "confirmed"
        assert app.main.thread_view.thread_id == ids["other"] and not app.main.thread_view.query(".react-selected")
        assert foreign.pinned == pin and foreign.scroll_y == y

    run(app, [scenario], size=(187, 50))


@pytest.mark.parametrize("size", [(187, 50), (80, 58)])
def test_confirmed_write_failed_count_refresh_keeps_own_result_and_native_controls(tmp_path, size):
    app, ids, fake = make_reaction_app(tmp_path)
    maya = ids["keys"][1]

    async def scenario(app, pilot):
        before = app.db.total_changes
        await prepare_reaction_state(app, pilot, "count_failure")
        state = app.reaction_states[maya]
        assert state["write_outcome"] == "confirmed" and state["own"]["+1"]
        assert state["totals"] == "unavailable" and len(fake.writes) == 1
        row = message_row(app, maya)
        text = visible_text(row)
        assert "Added 👍" in text and "Reaction totals unavailable" in text and "👍 ✓" in text
        assert "Outcome unknown" not in text and "👍 0" not in text and "👍 3" not in text
        assert app.db.total_changes == before and not app.react_mode
        assert not row.query(".reaction-choices")
        await pilot.press("v")
        await settle(app, pilot)
        choices = message_row(app, maya).query_one(".reaction-choices", ReactionLine)
        plain = Text.from_markup(str(choices.content)).plain
        assert all(label in plain for label in ("Like", "Thanks", "Looking"))
        assert all("@click" not in str(w.content) for w in row.query(".reaction-chips"))

    run(app, [scenario], size=size)


def test_snapshot_triggers_exclude_repaint_resize_clock_and_replica_change(tmp_path):
    app, ids, fake = make_reaction_app(tmp_path)

    async def scenario(app, pilot):
        await settle(app, pilot)
        assert len(fake.calls) == 3
        app.repaint_messages()
        app.main.refresh_all()
        app.minute()
        await pilot.resize_terminal(170, 48)
        db = inbox.connect(app.db_path)
        with db:
            db.execute("UPDATE persons SET display_name = display_name || ' updated' WHERE id = (SELECT min(id) FROM persons)")
        db.close()
        app.check_changes()
        await settle(app, pilot)
        assert len(fake.calls) == 3
        await pilot.press("f5")
        await settle(app, pilot)
        assert len(fake.calls) == 6
        app.main.open_thread(ids["thread"])
        await settle(app, pilot)
        assert len(fake.calls) == 9
        await pilot.press("j")
        await settle(app, pilot)
        assert fake.calls[-1]["thread_id"] == ids["other"] and len(fake.calls) == 10

    run(app, [scenario], size=(187, 50))


def test_case_only_selected_card_reads_and_react_navigation_never_crosses_cards(tmp_path):
    app, ids, fake = make_reaction_app(tmp_path)
    db = inbox.connect(app.db_path)
    inbox.act(db, ids["thread"], inbox.SetCase(new_title="Synthetic gate review"))
    case_id = inbox.get_thread(db, ids["thread"]).case_id
    inbox.act(db, ids["other"], inbox.SetCase(case_id=case_id))
    db.close()

    async def scenario(app, pilot):
        await settle(app, pilot)
        fake.calls.clear()
        app.main.open(lambda: CasePage(case_id))
        await settle(app, pilot)
        page = app.main.page
        first = page.current_thread_id()
        assert {c["thread_id"] for c in fake.calls} == {first}
        page.paint()
        app.main.refresh_all()
        await settle(app, pilot)
        count = len(fake.calls)
        assert {c["thread_id"] for c in fake.calls} == {first}
        await pilot.press("v")
        await settle(app, pilot)
        for _ in range(5):
            await pilot.press("j")
        await settle(app, pilot)
        assert app.react_target[0] == first and page.current_thread_id() == first
        assert len(page.query(".reaction-choices")) == 1 and not fake.writes
        await pilot.press("escape", "down")
        await settle(app, pilot)
        assert page.current_thread_id() != first and fake.calls[-1]["thread_id"] != first
        assert len(fake.calls) > count

    run(app, [scenario], size=(187, 50))


def test_read_batch_is_serial_coalesced_and_stops_obsolete_work(tmp_path):
    app, ids, fake = make_reaction_app(tmp_path, message_count=20)
    pending = []
    app._reaction_fetch = pending.append

    async def scenario(app, pilot):
        await pilot.pause(tui.PREVIEW_GAP * 2 + 0.05)
        assert len(pending) == 1 and len(app._reaction_reads) == 19
        app.schedule_reaction_snapshot(ids["thread"], force=True)
        await pilot.pause(tui.PREVIEW_GAP * 2)
        assert len(pending) == 1 and len(app._reaction_reads) == 19
        old = pending[0]
        await pilot.press("j")
        await pilot.pause(tui.PREVIEW_GAP * 3)
        assert all(job["key"][0] == ids["other"] for job in app._reaction_reads)
        app._reaction_read_done(old, read_result(old))
        assert len(pending) == 2 and pending[-1]["key"][0] == ids["other"]
        app._reaction_read_done(pending[-1], read_result(pending[-1]))
        assert not app._reaction_reads and not fake.calls

    run(app, [scenario], size=(187, 50))


@pytest.mark.parametrize("mode", ["unavailable", "refused", "unknown"])
def test_failure_states_are_message_local_and_never_resend(tmp_path, mode):
    app, ids, fake = make_reaction_app(tmp_path)

    async def scenario(app, pilot):
        target = await prepare_reaction_state(app, pilot, mode)
        text = visible_text(message_row(app, target))
        expected = {"unavailable": "Couldn’t check reactions", "refused": "Not added", "unknown": "Outcome unknown"}[mode]
        assert expected in text
        assert len(fake.writes) == (0 if mode == "unavailable" else 1)
        if mode == "unavailable":
            assert "c Check" in text
            await pilot.press("1", "2", "3")
            assert not fake.writes
        app.repaint_messages()
        await pilot.pause()
        assert len(fake.writes) == (0 if mode == "unavailable" else 1)

    run(app, [scenario], size=(187, 50))


def test_offline_and_build_gates_prevent_source_dispatch_without_remote_queue(tmp_path):
    app, ids, fake = make_reaction_app(tmp_path)

    async def scenario(app, pilot):
        await prepare_reaction_state(app, pilot)
        calls = len(fake.calls)
        app.remote = SimpleNamespace(online=False, problem="Offline", queue=lambda *a: pytest.fail("reaction queued"))
        await pilot.press("1")
        assert len(fake.calls) == calls and not fake.writes
        app.remote.online = True
        app.outdated = True
        app.reaction_states[ids["keys"][1]]["ready"] = True
        await pilot.press("1")
        assert len(fake.calls) == calls and not fake.writes
        app.remote = None

    run(app, [scenario], size=(187, 50))


def test_remote_writer_proxies_keep_reaction_reads_adds_and_removes_on_private_transport(tmp_path, monkeypatch):
    from comms_terminal import remote

    app, ids, fake = make_reaction_app(tmp_path)
    route, seen = ["ssh", "synthetic", "relay"], []

    def forbidden(*args, **kwargs):
        pytest.fail("reaction entered the remote ledger RPC or optimistic queue")

    def post(command, path, payload):
        seen.append((command, path, dict(payload)))
        return 200, fake(payload)

    async def scenario(app, pilot):
        await settle(app, pilot)
        before = tuple(app.db.iterdump())
        with monkeypatch.context() as patch:
            for name in ("inbox", "sequences", "pipeline", "files"):
                patch.setattr(tui, name, getattr(tui, name))
            patch.setattr(remote.files, "file_url", remote.files.file_url)
            patch.setattr(tui, "_post", post)
            rem = SimpleNamespace(online=True, problem="", call=forbidden, queue=forbidden)
            remote.install(tui, rem, {})
            app.remote, app.reactions = rem, tui.socket_slack_reactions(route)
            try:
                target = await prepare_reaction_state(app, pilot)
                await pilot.press("1")
                await settle(app, pilot)
                await pilot.press("v")
                await settle(app, pilot)
                await pilot.press("1")
                await settle(app, pilot)
                assert [call["op"] for call in fake.writes] == ["add", "remove"]
                assert all((call["thread_id"], call["message_id"], call["connection_id"]) == target
                           for call in fake.writes)
                assert {body["op"] for _, _, body in seen} == {"read", "add", "remove"}
                assert all(command == route and path == "/slack-reactions" for command, path, _ in seen)
                assert tuple(app.db.iterdump()) == before
                assert not any("reaction" in name for name in remote.CALLS)
            finally:
                app.remote, app.reactions = None, fake

    run(app, [scenario], size=(187, 50))


@pytest.mark.parametrize("transport", ["/tmp/fake.sock", ["ssh", "synthetic", "relay"]])
def test_socket_reactions_uses_existing_transport_and_preserves_typed_outcomes(monkeypatch, transport):
    payload = dict(op="add", thread_id=1, message_id=2, connection_id=3, name="+1", expected_revision=2)
    seen = []
    monkeypatch.setattr(tui, "_post", lambda socket, path, body: seen.append((socket, path, body)) or
                        (504, dict(outcome="unknown", error="Response lost")))
    assert tui.socket_slack_reactions(transport)(payload)["outcome"] == "unknown"
    assert seen == [(transport, "/slack-reactions", payload)]
    monkeypatch.setattr(tui, "_post", lambda *a: (200, {"mine": True}))
    assert tui.socket_slack_reactions(transport)(payload)["outcome"] == "unknown"
    monkeypatch.setattr(tui, "_post", lambda *a: (404, {"error": "Missing route"}))
    assert tui.socket_slack_reactions(transport)(payload)["outcome"] == "unavailable"


def test_passive_custom_chip_escapes_markup_and_own_edges_have_no_blue_fill(tmp_path):
    app, ids, fake = make_reaction_app(tmp_path)
    fake.groups[ids["messages"][1]] = [dict(name="[red]custom[/red]", count=7, mine=True)]

    async def scenario(app, pilot):
        await settle(app, pilot)
        chip = message_row(app, ids["keys"][1]).query_one(".reaction-chips")
        text = Text.from_markup(str(chip.content))
        assert ":[red]custom[/red]: 7 ✓" in text.plain
        assert not any(str(span.style) == "red" for span in text.spans)
        assert "on #45707a" not in str(chip.content) and "[#45707a]" in str(chip.content)
        assert "@click" not in str(chip.content)

    run(app, [scenario], size=(187, 50))


@pytest.mark.parametrize("close", ["colon", "mouse"])
def test_closing_selected_message_cannot_write_a_hidden_target(tmp_path, close):
    app, ids, fake = make_reaction_app(tmp_path)

    async def scenario(app, pilot):
        target = await prepare_reaction_state(app, pilot)
        if close == "colon":
            await pilot.press("colon")
        else:
            app.action_msg_toggle(ids["thread"], ids["native"][1])
        await pilot.pause()
        assert not app.react_mode and not app.main.query(".reaction-choices")
        # Shared dispatch guard also fails closed if called after disposable widgets have changed.
        app.react_target = target
        app._reaction_choose(0)
        assert not fake.writes

    run(app, [scenario], size=(187, 50))


def test_known_edit_requires_review_and_source_check_does_not_bypass_revision(tmp_path):
    app, ids, fake = make_reaction_app(tmp_path)
    maya = ids["keys"][1]

    async def scenario(app, pilot):
        await prepare_reaction_state(app, pilot)
        inbox.ingest(app.db, inbox.Incoming(
            1, "C_REVIEW", "channel", "EDIT", ids["native"][1] + ":edited", None,
            datetime.now(timezone.utc), "Changed review instructions."))
        app.repaint_messages()
        await pilot.pause()
        assert "Message changed — review" in visible_text(message_row(app, maya))
        await pilot.press("1")
        assert not fake.writes
        # c is a new explicit check of the now displayed revision, never replay of the dropped key.
        fake.revisions[maya[1]] = inbox.thread_view(app.db, ids["thread"])["messages"][1]["revision"]
        await pilot.press("c")
        await settle(app, pilot)
        assert app.reaction_states[maya]["ready"] and not fake.writes

    run(app, [scenario], size=(187, 50))


def test_passive_snapshots_keep_native_latest_message_pin(tmp_path):
    app, ids, fake = make_reaction_app(tmp_path, message_count=10)

    async def scenario(app, pilot):
        await settle(app, pilot)
        pane = app.main.thread_view.query_one(".timeline")
        assert pane.pinned and pane.scroll_y == pane.max_scroll_y
        await pilot.press("f5")
        await settle(app, pilot)
        assert pane.pinned and pane.scroll_y == pane.max_scroll_y
        assert not fake.writes

    run(app, [scenario], size=(140, 22))


def test_raw_key_burst_and_pre_ready_forwarded_digit_are_consumed_not_replayed(tmp_path):
    app, ids, fake = make_reaction_app(tmp_path)

    async def scenario(app, pilot):
        await settle(app, pilot)
        page, pending = app.main.page, []
        fetch = app._reaction_fetch
        app._reaction_fetch = pending.append
        for key in ("v", "1", "2", "3"):
            app.post_message(events.Key(key, key))
        await pilot.pause()
        assert app.react_mode and app.main.page is page and len(pending) == 1 and not fake.writes
        early = events.Key("1", "1")
        early._forwarded = True  # Already in a widget/parent queue when the read completion wins.
        job = pending.pop()
        app._reaction_read_done(job, read_result(job))
        assert app.reaction_states[job["key"]]["ready"]
        app.post_message(early)
        await pilot.pause()
        assert app.react_mode and app.main.page is page and not fake.writes
        app._reaction_fetch = fetch
        await pilot.press("1")
        await settle(app, pilot)
        assert len(fake.writes) == 1 and not app.react_mode

    run(app, [scenario], size=(187, 50))


def test_today_explicit_redisplay_of_same_thread_refreshes_without_extra_enter(tmp_path):
    app, ids, fake = make_reaction_app(tmp_path)
    db = inbox.connect(app.db_path)
    inbox.save_draft(db, dict(connection_id=None, kind="email", sender="", to="", cc="", subject="", body="Synthetic draft"))
    db.close()
    app.home = "today"

    async def scenario(app, pilot):
        await settle(app, pilot)
        before = len(fake.calls)
        today = app.main.page
        today.enter(today.drafts, 0)
        await settle(app, pilot)
        assert len(fake.calls) == before
        today.enter(today.threads, 0)
        await settle(app, pilot)
        assert len(fake.calls) == before + 3 and app.main.thread_view.thread_id == ids["thread"]

    run(app, [scenario], size=(187, 50))


def test_selected_source_fallback_uses_original_message_not_newest_thread_link(tmp_path, monkeypatch):
    from dataclasses import replace

    # Native Slack links currently identify a conversation. Distinct injected links prove message routing
    # without inventing an unsupported Slack URI parameter or fetching a provider permalink.
    monkeypatch.setitem(tui.PLATFORMS, "slack", replace(
        tui.PLATFORMS["slack"], link=lambda m, ch: f"https://synthetic.invalid/messages/{m['id']}"))
    app, ids, fake = make_reaction_app(tmp_path)

    async def scenario(app, pilot):
        target = await prepare_reaction_state(app, pilot, "unavailable")
        expected = app._reaction_target(target)[0]["link"]
        assert expected != app.main.thread_view.view["link"]
        notices = []
        app.notify = lambda message, **kwargs: notices.append(message)
        await pilot.press("o")
        assert expected in notices[-1] and not fake.writes

    run(app, [scenario], size=(187, 50))


def test_server_newer_revision_requires_review_without_a_write(tmp_path):
    app, ids, fake = make_reaction_app(tmp_path)

    async def scenario(app, pilot):
        await settle(app, pilot)
        pending = []
        app._reaction_fetch = pending.append
        await pilot.press("v")
        await pilot.pause()
        job = pending.pop()
        app._reaction_read_done(job, dict(read_result(job), revision=job["revision"] + 100))
        await pilot.pause()
        assert "Message changed — review" in visible_text(message_row(app, job["key"]))
        await pilot.press("1")
        assert not fake.writes and not app.reaction_states[job["key"]]["ready"]

    run(app, [scenario], size=(187, 50))


def test_deferred_scroll_restoration_cannot_retarget_reused_thread_timeline(tmp_path):
    app, ids, fake = make_reaction_app(tmp_path)

    async def scenario(app, pilot):
        await prepare_reaction_state(app, pilot)
        app.leave_react()
        await pilot.pause()
        callbacks = []
        after = app.call_after_refresh
        app.call_after_refresh = callbacks.append
        app._reaction_repaint()
        app.call_after_refresh = after
        assert callbacks
        await pilot.press("j")
        await settle(app, pilot)
        foreign = app.main.thread_view.query_one(".timeline")
        assert app.main.thread_view.thread_id == ids["other"]
        y, pinned = foreign.scroll_y, foreign.pinned
        for callback in callbacks:
            callback()
        await pilot.pause()
        assert foreign.scroll_y == y and foreign.pinned == pinned and not fake.writes

    run(app, [scenario], size=(187, 50))


@pytest.mark.parametrize("size", [(187, 50), (80, 58)])
def test_reading_footer_keeps_keyboard_react_hint_and_refresh_when_space_fits(tmp_path, size):
    app, ids, fake = make_reaction_app(tmp_path)

    async def scenario(app, pilot):
        await settle(app, pilot)
        footer = app.main.query_one(tui.StatusLine)
        text = Text.from_markup(str(footer.content))
        assert "v react" in text.plain and text.cell_len <= size[0]
        if size[0] == 187:
            assert "f5 refresh" in text.plain
        hints = [item for group in footer.keys_markup() for item in group
                 if Text.from_markup(item).plain in ("v react", "f5 refresh")]
        assert len(hints) == 2 and all("@click" not in item for item in hints)
        assert not fake.writes

    run(app, [scenario], size=size)
