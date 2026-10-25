"""Slack reactions: synthetic source transports, exact captured targets, and zero ledger side effects."""

import http.client
import json
import urllib.error
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from comms_terminal import inbox, serve, slack

AT = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)


class SlackAPI:
    """The existing get/post transport seams; never reaches Slack."""

    def __init__(self, channel="C1", ts="100.2"):
        self.calls = []
        self.auth = {"ok": True, "team_id": "T1", "user_id": "UME"}
        self.snapshot = {"ok": True, "type": "message", "channel": channel,
                         "message": {"type": "message", "ts": ts, "reactions": []}}
        self.change = {"ok": True}
        self.on_auth = self.on_snapshot = None

    def get(self, url, token, params):
        self.calls.append((url.rsplit("/", 1)[-1], token, params))
        auth = url.endswith("auth.test")
        callback = self.on_auth if auth else self.on_snapshot
        if callback:
            callback()
        result = self.auth if auth else self.snapshot
        if isinstance(result, Exception):
            raise result
        return result

    def post(self, url, token, payload):
        self.calls.append((url.rsplit("/", 1)[-1], token, payload))
        if isinstance(self.change, Exception):
            raise self.change
        return self.change

    @property
    def writes(self):
        return [call for call in self.calls if call[0] in ("reactions.add", "reactions.remove")]


def arranged(channel="C1", kind="channel", data=None):
    db = inbox.connect()
    inbox.migrate(db)
    with db:
        db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                   " VALUES (1, 'slack', 'T1', 'UME', 'Slack', ?)", (AT.isoformat(),))
    data = {"channel": channel, "ts": "100.2", "thread_ts": "100.1", "kind": kind} if data is None else data
    t = inbox.ingest(db, inbox.Incoming(1, channel, kind, "MESSAGE", f"{channel}:100.2",
                                       inbox.Identity("slack_user", "UOTHER"), AT, "Review ready", data=data))
    message = inbox.thread_view(db, t.id)["messages"][0]
    api = SlackAPI(channel)
    sender = slack.SlackSender("fake-user-token", get=api.get, post=api.post,
                               client=slack.SlackClient("fake-user-token", get=api.get))
    return SimpleNamespace(db=db, thread=t, message=message, api=api, sender=sender,
                           senders=serve.Senders(made={1: sender}),
                           request={"op": "read", "thread_id": t.id, "message_id": message["id"], "connection_id": 1})


@pytest.fixture
def reaction():
    ctx = arranged()
    yield ctx
    ctx.db.close()


def invoke(ctx, request=None, **changes):
    before = tuple(ctx.db.iterdump())
    status, out = serve.handle_slack_reactions(ctx.db, json.dumps(request or (ctx.request | changes)).encode(), ctx.senders)
    assert tuple(ctx.db.iterdump()) == before  # includes Activities, Status, Cases, tasks, and Connection health
    return status, out


def change_request(ctx, op="add", name="eyes"):
    return ctx.request | {"op": op, "name": name, "expected_revision": ctx.message["revision"]}


def affect(ctx, kind="EDIT"):
    inc = inbox.Incoming(1, "C1", "channel", kind,
                         "C1:100.2:101.0" if kind == "EDIT" else "C1:100.2",
                         inbox.Identity("slack_user", "UOTHER"), AT + timedelta(minutes=1), "Changed",
                         data={"channel": "C1", "ts": "100.2", "kind": "channel"})
    inbox.ingest(ctx.db, inc)


def test_partial_reactors_totals_aliases_and_custom_names_are_independent(reaction):
    reaction.api.snapshot["message"]["reactions"] = [
        {"name": "thumbsup", "count": 7, "users": ["UOTHER", "UME"]},
        {"name": "pray", "count": 2, "users": ["UOTHER"]},
        {"name": "[bold]custom[/bold]", "count": 3, "users": []},
    ]
    status, out = invoke(reaction)
    target = {k: reaction.request[k] for k in ("thread_id", "message_id", "connection_id")}
    assert status == 200 and out == {"outcome": "ready", **target, "revision": reaction.message["id"],
                                    "groups": [{"name": "+1", "count": 7, "mine": True},
                                               {"name": "pray", "count": 2, "mine": False},
                                               {"name": "[bold]custom[/bold]", "count": 3, "mine": False}]}
    assert not reaction.api.writes
    assert reaction.api.calls[-1][2] == {"channel": "C1", "timestamp": "100.2"}


@pytest.mark.parametrize("omit", [False, True])
def test_valid_empty_is_distinct_from_unavailable(reaction, omit):
    if omit:
        del reaction.api.snapshot["message"]["reactions"]
    assert invoke(reaction)[1]["groups"] == []
    reaction.api.snapshot = {"ok": False, "error": "missing_scope"}
    status, out = invoke(reaction)
    assert status == 503 and out["outcome"] == "unavailable" and "groups" not in out
    assert "approval" in out["error"]


@pytest.mark.parametrize("bad", [None, [], {}, {"ok": True}, {"ok": 1},
    {"ok": True, "type": "file", "file": {}},
    {"ok": True, "type": "message", "channel": "C2", "message": {"type": "message", "ts": "100.2"}},
    {"ok": True, "type": "message", "channel": "C1", "message": {"type": "message", "ts": "100.1"}},
])
def test_missing_or_mismatched_success_is_not_empty(reaction, bad):
    reaction.api.snapshot = bad
    status, out = invoke(reaction)
    assert (status, out["outcome"]) == (503, "unavailable") and "groups" not in out


@pytest.mark.parametrize("groups", [None, {}, [None], [{"name": "eyes", "count": 2}],
    [{"name": "eyes", "count": True, "users": []}], [{"name": "eyes", "count": -1, "users": []}],
    [{"name": "eyes", "count": 0, "users": []}], [{"name": "eyes", "count": 1, "users": None}],
    [{"name": "eyes", "count": 1, "users": [None]}], [{"name": "eyes", "count": 1, "users": ["UME", "UOTHER"]}],
    [{"name": "eyes", "count": 2, "users": ["UME", "UME"]}], [{"name": "bad\nname", "count": 1, "users": []}],
    [{"name": "+1", "count": 1, "users": []}, {"name": "thumbsup", "count": 1, "users": []}],
])
def test_malformed_groups_are_unavailable_not_zero(reaction, groups):
    reaction.api.snapshot["message"]["reactions"] = groups
    assert invoke(reaction)[1]["outcome"] == "unavailable"


@pytest.mark.parametrize("field", ["thread_id", "message_id", "connection_id", "expected_revision"])
@pytest.mark.parametrize("value", [None, "", "1", False, True, 0, -1, 1.0, [], {}, 2**63])
def test_ids_and_revision_are_positive_explicit_non_bool_integers(reaction, field, value):
    req = change_request(reaction) | {field: value}
    assert invoke(reaction, req)[0] == 400
    assert reaction.api.calls == []


@pytest.mark.parametrize("field", ["op", "thread_id", "message_id", "connection_id", "name", "expected_revision"])
def test_omitted_change_fields_have_no_defaults(reaction, field):
    req = change_request(reaction)
    del req[field]
    assert invoke(reaction, req)[0] == 400 and reaction.api.calls == []


@pytest.mark.parametrize("extra", [{"channel": "C2"}, {"ts": "100.1"}, {"timestamp": "100.1"}, {"user": "UOTHER"},
                                   {"name": None}, {"expected_revision": None}])
def test_read_rejects_arbitrary_targets_and_change_fields_even_null(reaction, extra):
    assert invoke(reaction, reaction.request | extra)[0] == 400 and reaction.api.calls == []


@pytest.mark.parametrize("changes", [{"op": "toggle"}, {"op": None}, {"op": []}, {"name": None}, {"name": ":eyes:"},
                                     {"name": "thumbsup"}, {"name": []}, {"name": "custom"}, {"bulk": True}])
def test_change_rejects_unsupported_operations_names_and_extra_fields(reaction, changes):
    assert invoke(reaction, change_request(reaction) | changes)[0] == 400 and reaction.api.calls == []


@pytest.mark.parametrize("raw", [b"null", b"[]", b"bad json", b"\xff", b'{"op":"read","op":"add"}'])
def test_invalid_json_object_and_duplicate_fields_fail_closed(reaction, raw):
    before = tuple(reaction.db.iterdump())
    assert serve.handle_slack_reactions(reaction.db, raw, reaction.senders)[0] == 400
    assert not reaction.api.calls and tuple(reaction.db.iterdump()) == before


@pytest.mark.parametrize("field", ["thread_id", "message_id", "connection_id"])
def test_target_ownership_never_falls_back(reaction, field):
    assert invoke(reaction, change_request(reaction) | {field: 999})[0] == 400
    assert reaction.api.calls == []


def test_other_existing_thread_message_and_connection_are_not_substituted(reaction):
    with reaction.db:
        reaction.db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                            " VALUES (2, 'slack', 'T2', 'UME', 'Other', ?)", (AT.isoformat(),))
    other = inbox.ingest(reaction.db, inbox.Incoming(2, "C2", "channel", "MESSAGE", "C2:200.1",
                        inbox.Identity("slack_user", "UOTHER"), AT, "Other account",
                        data={"channel": "C2", "ts": "200.1", "kind": "channel"}))
    other_message = inbox.thread_view(reaction.db, other.id)["messages"][0]
    for changes in ({"thread_id": other.id}, {"message_id": other_message["id"]}, {"connection_id": 2}):
        assert invoke(reaction, change_request(reaction) | changes)[0] == 400
    assert reaction.api.calls == []


def test_non_slack_platform_is_refused_without_provider_calls(reaction):
    with reaction.db:
        reaction.db.execute("UPDATE connections SET platform = 'gmail' WHERE id = 1")
    assert invoke(reaction, change_request(reaction))[0] == 400 and reaction.api.calls == []


def test_missing_sender_is_unavailable_and_clientless_sender_uses_injected_get(reaction):
    reaction.senders.made[1] = None
    assert invoke(reaction, change_request(reaction))[1]["outcome"] == "unavailable" and reaction.api.calls == []
    reaction.senders.made[1] = reaction.sender
    reaction.sender.client = None
    assert invoke(reaction)[1]["groups"] == []


@pytest.mark.parametrize("result", [
    {"ok": False, "error": "ratelimited"}, {"ok": False, "error": "private-secret-details"},
    urllib.error.HTTPError("https://example.invalid/private-secret-details", 429, "private-secret-details",
                           {"Retry-After": "31"}, None),
    urllib.error.HTTPError("https://example.invalid/private-secret-details", 503, "private-secret-details", {}, None),
    http.client.RemoteDisconnected("private-secret-details"),
])
def test_read_transport_or_rate_limit_failure_is_unavailable_without_leaking_source_details(reaction, result):
    reaction.api.snapshot = result
    status, out = invoke(reaction)
    assert (status, out["outcome"]) == (503, "unavailable") and "groups" not in out
    assert "private-secret-details" not in json.dumps(out) and not reaction.api.writes
    if isinstance(result, urllib.error.HTTPError) and result.code == 429:
        assert "wait 31 seconds" in out["error"]


@pytest.mark.parametrize("data", [{}, {"channel": "C1"}, {"channel": "C2", "ts": "100.2"},
                                  {"channel": "C1", "ts": "100.1"}, {"channel": "C1", "ts": 100.2},
                                  {"channel": "C1", "ts": "100.2", "kind": "im"}])
def test_missing_or_inconsistent_captured_identifiers_are_not_guessed(data):
    ctx = arranged(data=data)
    try:
        assert invoke(ctx, change_request(ctx))[0] == 400 and ctx.api.calls == []
    finally:
        ctx.db.close()


@pytest.mark.parametrize("auth", [{"ok": True, "team_id": "T2", "user_id": "UME"},
                                  {"ok": True, "team_id": "T1", "user_id": "UOTHER"}])
@pytest.mark.parametrize("op", ["read", "add", "remove"])
def test_global_token_must_match_intended_workspace_and_user(reaction, auth, op):
    reaction.api.auth = auth
    request = reaction.request if op == "read" else change_request(reaction, op)
    assert invoke(reaction, request)[1]["outcome"] == "refused"
    assert [c[0] for c in reaction.api.calls] == ["auth.test"]


@pytest.mark.parametrize("auth", [None, {"ok": True}, {"ok": True, "team_id": None, "user_id": "UME"},
    {"ok": True, "team_id": "T1", "user_id": "UME", "bot_id": "B1"}, {"ok": False, "error": "invalid_auth"},
    TimeoutError("private transport detail")])
def test_unverified_identity_disables_all_operations(reaction, auth):
    reaction.api.auth = auth
    status, out = invoke(reaction, change_request(reaction))
    assert (status, out["outcome"]) == (503, "unavailable") and not reaction.api.writes
    assert "private" not in out["error"]


def test_read_and_write_must_share_same_authenticated_token(reaction):
    reaction.sender.token = "another-fake-token"
    assert invoke(reaction, change_request(reaction))[1]["outcome"] == "refused" and reaction.api.calls == []


@pytest.mark.parametrize("channel,kind", [("C1", "channel"), ("C2", "private"), ("D1", "im"), ("G1", "mpim")])
@pytest.mark.parametrize("op", ["add", "remove"])
def test_exact_captured_reply_timestamp_and_explicit_own_change(channel, kind, op):
    ctx = arranged(channel, kind)
    try:
        status, out = invoke(ctx, change_request(ctx, op, "pray"))
        assert status == 200 and out == {"outcome": "confirmed", "op": op, "name": "pray",
                                        "thread_id": ctx.thread.id, "message_id": ctx.message["id"], "connection_id": 1,
                                        "revision": ctx.message["revision"], "mine": op == "add", "changed": True}
        assert ctx.api.writes == [(f"reactions.{op}", "fake-user-token",
                                   {"channel": channel, "timestamp": "100.2", "name": "pray"})]
    finally:
        ctx.db.close()


@pytest.mark.parametrize("op,error", [("add", "already_reacted"), ("remove", "no_reaction")])
def test_already_desired_state_confirms_without_claiming_new_change(reaction, op, error):
    reaction.api.change = {"ok": False, "error": error}
    status, out = invoke(reaction, change_request(reaction, op))
    assert status == 200 and out["outcome"] == "confirmed" and out["mine"] == (op == "add") and not out["changed"]
    assert len(reaction.api.writes) == 1


@pytest.mark.parametrize("response", [None, [], {}, {"ok": 1}, {"ok": False}, {"ok": False, "error": []},
    {"ok": False, "error": "internal_error"}, {"ok": False, "error": "fatal_error"},
    {"ok": False, "error": "service_unavailable"}, {"ok": False, "error": "request_timeout"},
    {"ok": False, "error": "new_provider_error"}, {"ok": False, "error": "private-secret-details"},
    TimeoutError("private-secret-details"), urllib.error.URLError("private-secret-details"),
    http.client.RemoteDisconnected("private-secret-details"), ValueError("private-secret-details"),
    urllib.error.HTTPError("https://example.invalid/private-secret-details", 503, "private-secret-details", {}, None),
    urllib.error.HTTPError("https://example.invalid/private-secret-details", 408, "private-secret-details", {}, None),
])
def test_ambiguous_post_dispatch_result_is_unknown_never_retried(reaction, response):
    reaction.api.change = response
    status, out = invoke(reaction, change_request(reaction))
    assert (status, out["outcome"]) == (504, "unknown") and len(reaction.api.writes) == 1
    assert "private-secret-details" not in json.dumps(out)


@pytest.mark.parametrize("response", [{"ok": False, "error": "missing_scope"}, {"ok": False, "error": "thread_locked"},
    {"ok": False, "error": "message_not_found"}, {"ok": False, "error": "ratelimited"},
    urllib.error.HTTPError("https://example.invalid/private-secret-details", 403, "private-secret-details", {}, None),
    urllib.error.HTTPError("https://example.invalid/private-secret-details", 429, "private-secret-details", {"Retry-After": "31"}, None),
])
def test_definitive_rejection_is_refused_not_unknown(reaction, response):
    reaction.api.change = response
    status, out = invoke(reaction, change_request(reaction))
    assert (status, out["outcome"]) == (502, "refused") and len(reaction.api.writes) == 1
    assert "private-secret-details" not in json.dumps(out)
    if isinstance(response, urllib.error.HTTPError) and response.code == 429:
        assert "wait 31 seconds" in out["error"]


def test_edit_and_delete_revision_guard_uses_original_message_not_edit_id(reaction):
    affect(reaction)
    message = inbox.thread_view(reaction.db, reaction.thread.id)["messages"][0]
    assert message["id"] == reaction.message["id"] and message["revision"] > message["id"] and message["edited"]
    status, out = invoke(reaction, change_request(reaction))
    assert (status, out["outcome"], out["revision"]) == (409, "review_required", message["revision"])
    assert not reaction.api.calls
    assert invoke(reaction, change_request(reaction) | {"message_id": message["revision"]})[0] == 400
    affect(reaction, "DELETE")
    deleted = inbox.thread_view(reaction.db, reaction.thread.id)["messages"][0]
    assert deleted["revision"] > message["revision"] and deleted["deleted"] and deleted["content"] is None
    assert invoke(reaction, change_request(reaction) | {"expected_revision": deleted["revision"]})[1]["outcome"] == "refused"
    assert not reaction.api.writes


@pytest.mark.parametrize("change", ["edit", "delete", "connection", "token", "arrival"])
def test_recheck_after_auth_latency_keeps_source_target_and_identity(reaction, change):
    expected = []

    def race():
        if change in ("edit", "delete"):
            affect(reaction, "EDIT" if change == "edit" else "DELETE")
        elif change == "connection":
            with reaction.db:
                reaction.db.execute("UPDATE connections SET self_id = 'UOTHER' WHERE id = 1")
        elif change == "token":
            reaction.sender.token = "new-fake-token"
        else:
            inbox.ingest(reaction.db, inbox.Incoming(1, "C1", "channel", "MESSAGE", "C1:200.1",
                         inbox.Identity("slack_user", "UOTHER"), AT + timedelta(minutes=2), "New arrival",
                         data={"channel": "C1", "ts": "200.1", "kind": "channel"}))
        expected.append(tuple(reaction.db.iterdump()))

    reaction.api.on_auth = race
    status, out = serve.handle_slack_reactions(reaction.db, json.dumps(change_request(reaction)).encode(), reaction.senders)
    assert tuple(reaction.db.iterdump()) == expected[0]
    if change == "arrival":
        assert status == 200 and reaction.api.writes[0][2]["timestamp"] == "100.2"
    else:
        assert out["outcome"] in ("refused", "review_required") and not reaction.api.writes


def test_read_completion_after_known_edit_requires_review(reaction):
    reaction.api.on_snapshot = lambda: affect(reaction)
    status, out = serve.handle_slack_reactions(reaction.db, json.dumps(reaction.request).encode(), reaction.senders)
    assert (status, out["outcome"]) == (409, "review_required") and not reaction.api.writes


def test_confirmed_write_stays_confirmed_when_later_count_read_fails(reaction):
    status, confirmed = invoke(reaction, change_request(reaction))
    reaction.api.snapshot = TimeoutError("private source detail")
    assert invoke(reaction)[1]["outcome"] == "unavailable"
    assert status == 200 and confirmed["outcome"] == "confirmed" and confirmed["mine"] and len(reaction.api.writes) == 1


def test_identity_is_not_reused_after_connection_changes(reaction):
    assert invoke(reaction)[0] == 200
    with reaction.db:
        reaction.db.execute("UPDATE connections SET self_id = 'UOTHER' WHERE id = 1")
    assert invoke(reaction, change_request(reaction))[1]["outcome"] == "refused"
    assert [c[0] for c in reaction.api.calls].count("auth.test") == 2 and not reaction.api.writes


@pytest.mark.parametrize("response,outcome", [({"ok": True}, "confirmed"),
    ({"ok": False, "error": "missing_scope"}, "refused"), (TimeoutError("lost response"), "unknown")])
def test_overlapping_private_client_cannot_change_same_source_pair_and_guard_releases(reaction, response, outcome):
    overlapping = []
    writes = []
    original = reaction.sender.post
    before = tuple(reaction.db.iterdump())

    def held_post(url, token, payload):
        writes.append((url, payload))
        # A second private request arrives while the first source call is still active.
        overlapping.append(invoke(reaction, change_request(reaction, "remove")))
        if isinstance(response, Exception):
            raise response
        return response

    reaction.sender.post = held_post
    status, out = invoke(reaction, change_request(reaction))
    assert out["outcome"] == outcome and len(writes) == 1
    assert overlapping[0][0] == 503 and overlapping[0][1]["outcome"] == "unavailable"
    assert "already in progress" in overlapping[0][1]["error"]
    assert tuple(reaction.db.iterdump()) == before
    reaction.sender.post = original
    # A later fresh explicit request is possible; the earlier intent is never queued or replayed.
    assert invoke(reaction, change_request(reaction, "remove"))[0] == 200
    assert len(reaction.api.writes) == 1


def test_two_threads_atomically_claim_the_same_source_pair(reaction, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import closing
    from threading import Barrier, BrokenBarrierError, Event, get_ident

    path = str(tmp_path / "reactions.db")
    with closing(inbox.connect(path)) as db:
        reaction.db.backup(db)
    claim_seen, writing, release = Event(), Event(), Event()
    claims = Barrier(2)

    class ConcurrentClaims(set):
        def __contains__(self, key):
            present = super().__contains__(key)
            claim_seen.set()
            # Without the mutex, both clients can observe absence before either adds.
            try:
                claims.wait(timeout=1)
            except BrokenBarrierError:
                pass  # The real mutex serializes membership checks.
            return present

    reaction.senders._reaction_pending = ConcurrentClaims()
    original = reaction.sender.post

    def held_post(url, token, payload):
        writing.set()
        assert release.wait(10), "test did not release the synthetic source write"
        return original(url, token, payload)

    def client(op):
        with closing(inbox.connect(path)) as db:
            ctx = SimpleNamespace(db=db, senders=reaction.senders)
            return get_ident(), invoke(ctx, change_request(reaction, op))

    reaction.sender.post = held_post
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(client, "add")
        try:
            assert claim_seen.wait(5), "first client did not reach the source-pair claim"
            second = pool.submit(client, "remove")
            assert writing.wait(5), "first client did not reach the synthetic source write"
            second_thread, (status, out) = second.result(timeout=5)
            assert status == 503 and out["outcome"] == "unavailable"
            assert "already in progress" in out["error"]
            assert reaction.api.writes == []
        finally:
            release.set()
        first_thread, (status, out) = first.result(timeout=5)
        assert first_thread != second_thread and status == 200 and out["outcome"] == "confirmed"
        assert len(reaction.api.writes) == 1 and not reaction.senders._reaction_pending
        # The rejected overlap was not queued; a later explicit request can proceed.
        assert pool.submit(client, "remove").result(timeout=5)[1][0] == 200
        assert len(reaction.api.writes) == 2 and not reaction.senders._reaction_pending
