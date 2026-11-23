"""Tana as a read-only Context source (FR-13a): what the service asks Tana and what it answers."""

import io
import json
import threading
import time
import urllib.error
import urllib.request
import urllib.response
from datetime import datetime, timedelta, timezone
from email.message import Message

import pytest

from comms_terminal import clock, mcp, serve, tana

NOW = datetime(2026, 10, 13, 17, 0, tzinfo=timezone.utc)
ORG, PERSON = "tana:text:org", "tana:text:person"


class FakeTana:
    """Answers the MCP tools tana.context calls, from shapes seen on Jeremy's workspace (2026-10-13)."""

    def __init__(self):
        self.calls = []

    def tool(self, name, **args):
        self.calls.append((name, args))
        if name == "getShareLink":
            return {"links": [{"url": f"https://home.tana.inc/o/ws/l/{args['ids'][0]}"}]}
        if name == "listEdges":
            return {"edges": [{"fromNodeId": "tana:text:task-open"}, {"fromNodeId": "tana:text:task-done"},
                              {"fromNodeId": "tana:chat:x"}]}
        if name == "readEvent":
            return {"title": "NBIF Interview", "startTime": "2026-10-06T13:00:00-03:00", "summary": "Diligence next steps",
                    "summaryUri": "tana:text:sum",
                    "relatedDocs": [{"uri": "tana:text:sum"}, {"uri": "tana:text:obs"}, {"uri": "tana:chat:c"}]}
        assert name == "searchItems"
        if "itemIds" in args:
            known = {"tana:text:task-open": {"title": "Send deck", "state": "In Progress"},
                     "tana:text:task-done": {"title": "Old", "state": "Completed"},
                     "tana:text:obs": {"title": "Wants milestones", "type": "Observations"}}
            return {"items": [{"id": i, **known[i]} for i in args["itemIds"] if i in known]}
        target = args["targets"][0]
        if target["target"] == "event":
            if "eventStartTimeMax" not in target:
                return {"items": []}
            people = "Laïla <laila@nbif.ca> (organizer); attendees(2): jeremy@agentmaple.com"
            return {"items": [{"id": "tana:event:e1", "event": {"time": "past; 2026-10-06T16:00Z->17:30Z", "people": people}},
                              {"id": "tana:event:other", "event": {"time": "past; 2026-10-07T16:00Z->17:00Z",
                                                                  "people": "someone@else.ca"}}]}
        kind = target["type"]
        return {"items": {"People": [{"id": PERSON, "title": "Laïla Thériault", "type": "People"}],
                          "Organizations": [{"id": ORG, "title": "NBIF", "type": "Organizations"}],
                          "Deals": [{"id": "tana:text:deal", "title": "NBIF — investment", "type": "Deals"}],
                          "Threats": [], "Opportunities": []}[kind]}


def test_context_matches_records_tasks_and_meetings():
    client = FakeTana()
    ctx = tana.context(client, ["Laila@nbif.ca"], ["nbif.ca"], NOW)
    assert [r["title"] for r in ctx["people"]] == ["Laïla Thériault"]
    assert [r["title"] for r in ctx["deals"]] == ["NBIF — investment"]
    assert [r["title"] for r in ctx["tasks"]] == ["Send deck"]  # Completed tasks and chats are left out
    assert ctx["people"][0]["url"] == "https://home.tana.inc/o/ws/l/tana%3Atext%3Aperson"
    [m] = ctx["meetings"]  # the other event has no matching attendee
    assert (m["title"], m["upcoming"], m["summary"]) == ("NBIF Interview", False, "Diligence next steps")
    assert [(d["type"], d["title"]) for d in m["linked"]] == [("Observations", "Wants milestones")]
    assert m["summary_url"].endswith("tana%3Atext%3Asum")
    deal_filters = next(a["targets"][0]["fieldFilters"] for n, a in client.calls
                        if n == "searchItems" and a.get("targets", [{}])[0].get("type") == "Deals")
    assert deal_filters == [{"field": "Organization", "matchAny": [ORG]}]
    assert all(n in ("searchItems", "listEdges", "readEvent", "getShareLink") for n, _ in client.calls)  # read only


def test_context_is_cached():
    client = FakeTana()
    clock = [0.0]
    c = tana.Context(client, clock=lambda: clock[0])
    c.get(["laila@nbif.ca"], ["nbif.ca"])
    n = len(client.calls)
    c.get(["LAILA@nbif.ca"], ["nbif.ca"])
    assert len(client.calls) == n
    clock[0] = tana.TTL + 1
    c.get(["laila@nbif.ca"], ["nbif.ca"])
    assert len(client.calls) > n


def test_socket_route_reports_not_connected_and_errors():
    assert serve.handle_context(b'{"emails": []}', None) == (503, {"error": "Tana is not connected"})

    class Broken:
        def get(self, emails, domains):
            raise mcp.McpError("Tana sign-in expired or revoked (HTTP 400 invalid_grant)")

    status, out = serve.handle_context(b'{"emails": ["a@b.ca"], "domains": []}', Broken())
    assert status == 502 and "sign-in expired" in out["error"]
    assert serve.handle_context(b"not json", Broken())[0] == 400


def test_section_markup():
    from comms_terminal.tui import tana_markup
    text = tana_markup(tana.context(FakeTana(), ["laila@nbif.ca"], ["nbif.ca"], NOW))
    for part in ("Laïla Thériault", "NBIF — investment", "Send deck", "NBIF Interview", "Diligence next steps",
                 "Observations", "Wants milestones"):
        assert part in text
    empty = {k: [] for k in ("people", "organizations", "deals", "threats", "opportunities", "tasks", "meetings")}
    assert "nothing in Tana" in tana_markup(empty)
    assert "Tana unavailable: boom" in tana_markup(None, "boom")


def test_console_treats_a_service_without_tana_as_not_connected(tmp_path):
    import threading
    from comms_terminal.remote import socket_context
    sock = str(tmp_path / "s.sock")
    old = serve.UnixHTTPServer(sock, serve._handler({}))  # a service from before /tana: HTML 404
    threading.Thread(target=old.serve_forever, daemon=True).start()
    try:
        assert socket_context(sock, "tana")(["a@b.ca"], []) is None
    finally:
        old.shutdown()


def ready_prep_client(**kwargs):
    client = tana.Client({"client_id": "synthetic-client", "refresh_token": "synthetic-refresh"}, **kwargs)
    client.access = "synthetic-access"
    client.expires = clock.now() + timedelta(minutes=10)
    client.session = "synthetic-session"
    return client


@pytest.fixture
def live_prep_client(monkeypatch):
    monkeypatch.setattr(clock, "now", lambda: NOW)
    calls = []

    def client_for(respond):
        def http(url, body, headers, timeout=30, **_):
            calls.append((url, body, headers, timeout))
            return respond(json.loads(body))

        return ready_prep_client(http=http), calls

    return client_for


def prep_reply(message, result):
    return 200, {}, json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}).encode()


@pytest.mark.parametrize("session", ["synthetic-session", ""], ids=["stateful", "initialized-stateless"])
def test_brief_transport_uses_only_the_existing_oauth_and_mcp_session(live_prep_client, session):
    payload = {"pinnedItems": []}
    client, calls = live_prep_client(lambda msg: prep_reply(msg, {"structuredContent": payload}))
    client.session = session
    deadline = time.monotonic() + 5
    assert client.read_prep_tool("readEvent", deadline, eventUri="tana:event:site-visit") == payload
    [(url, raw, headers, timeout)] = calls
    message = json.loads(raw)
    assert url == tana.MCP and message["method"] == "tools/call"
    assert message["params"] == {"name": "readEvent", "arguments": {"eventUri": "tana:event:site-visit"}}
    assert headers["Authorization"] == "Bearer synthetic-access"
    assert headers.get("Mcp-Session-Id") == (session or None)
    assert 0 < timeout <= 5


@pytest.mark.parametrize("attr, value", [("access", None), ("expires", NOW), ("session", None)])
def test_brief_transport_never_refreshes_or_initializes_missing_live_state(live_prep_client, attr, value):
    client, calls = live_prep_client(lambda msg: pytest.fail(f"unexpected MCP request: {msg['method']}"))
    setattr(client, attr, value)
    with pytest.raises(mcp.McpError):
        client.read_prep_tool("readEvent", time.monotonic() + 5, eventUri="tana:event:site-visit")
    assert calls == []


@pytest.mark.parametrize("tool, args", [
    ("pinItem", {"eventUri": "tana:event:site-visit"}),
    ("readItems", {"ids": ["tana:text:brief"], "task": "Summarize"}),
])
def test_brief_transport_does_not_call_mutating_or_task_tools(live_prep_client, tool, args):
    client, calls = live_prep_client(lambda msg: pytest.fail(f"unexpected MCP request: {msg['method']}"))
    with pytest.raises(mcp.McpError):
        client.read_prep_tool(tool, time.monotonic() + 5, **args)
    assert calls == []


@pytest.mark.parametrize("status", [401, 403, 404, 200], ids=["token-expired", "denied", "session-expired", "tool-error"])
def test_brief_transport_denial_is_safe_and_not_retried(live_prep_client, caplog, status):
    secret = "private provider detail"

    def respond(message):
        if status == 200:
            return prep_reply(message, {"isError": True, "content": [{"type": "text", "text": secret}]})
        return status, {}, secret.encode()

    client, calls = live_prep_client(respond)
    with pytest.raises(mcp.McpError) as error:
        client.read_prep_tool("readEvent", time.monotonic() + 5, eventUri="tana:event:site-visit")
    [(url, raw, _, _)] = calls
    assert url == tana.MCP and json.loads(raw)["method"] == "tools/call"
    assert secret not in str(error.value) and secret not in caplog.text
    assert (client.access, client.session, client.expires) == (
        "synthetic-access", "synthetic-session", NOW + timedelta(minutes=10),
    )


def test_brief_transport_exhausted_deadline_does_not_send(live_prep_client):
    client, calls = live_prep_client(lambda msg: pytest.fail(f"unexpected MCP request: {msg['method']}"))
    with pytest.raises(TimeoutError):
        client.read_prep_tool("readEvent", time.monotonic() - 1, eventUri="tana:event:site-visit")
    assert calls == []


def test_brief_transport_preserves_network_timeout_without_retry(live_prep_client):
    def respond(_):
        raise urllib.error.URLError(TimeoutError("private provider detail"))

    client, calls = live_prep_client(respond)
    with pytest.raises(TimeoutError):
        client.read_prep_tool("readEvent", time.monotonic() + 5, eventUri="tana:event:site-visit")
    [(url, raw, _, _)] = calls
    assert url == tana.MCP and json.loads(raw)["method"] == "tools/call"


def test_brief_transport_never_waits_unbounded_for_shared_client_lock(live_prep_client):
    class BusyLock:
        def acquire(self, blocking=True, timeout=-1):
            assert not blocking or 0 <= timeout <= 5
            return False

    client, calls = live_prep_client(lambda msg: pytest.fail(f"unexpected MCP request: {msg['method']}"))
    client.lock = BusyLock()
    with pytest.raises((TimeoutError, mcp.McpError)):
        client.read_prep_tool("readEvent", time.monotonic() + 5, eventUri="tana:event:site-visit")
    assert calls == []


def test_brief_transport_discards_success_received_after_deadline(live_prep_client, monkeypatch):
    now = 100.0
    monkeypatch.setattr(time, "monotonic", lambda: now)

    def respond(message):
        nonlocal now
        now += 6
        return prep_reply(message, {"structuredContent": {"pinnedItems": []}})

    client, calls = live_prep_client(respond)
    with pytest.raises(TimeoutError):
        client.read_prep_tool("readEvent", 105.0, eventUri="tana:event:site-visit")
    assert len(calls) == 1


def test_brief_transport_does_not_forward_authenticated_request_on_redirect(monkeypatch):
    requests = []

    def redirect(_handler, _connection, request, **_):
        requests.append(request.full_url)
        headers = Message()
        headers["Location"] = "https://redirect.example.invalid/mcp"
        response = urllib.response.addinfourl(io.BytesIO(), headers, request.full_url, 302)
        response.msg = "Found"
        return response

    # Leave urllib's redirect handling real; replace only HTTPS I/O.
    monkeypatch.setattr(urllib.request.HTTPSHandler, "do_open", redirect)
    client = ready_prep_client()
    with pytest.raises(mcp.McpError):
        client.read_prep_tool("readEvent", time.monotonic() + 5, eventUri="tana:event:site-visit")
    assert requests == [tana.MCP]


def test_brief_transport_truncates_wire_read_not_supported_brief_text(monkeypatch):
    limit = 4 * 1024 * 1024

    class OversizeBody(io.BytesIO):
        def read(self, size=-1):
            assert 0 < size <= limit, "wire read must be bounded before decoding"
            data = super().read(size)
            assert self.tell() <= limit
            return data

        read1 = read

    def oversize(_handler, _connection, request, **_):
        msg = json.loads(request.data)
        _, _, body = prep_reply(msg, {"structuredContent": {"pinnedItems": []}})
        response = urllib.response.addinfourl(OversizeBody(body + b" " * limit), Message(), request.full_url, 200)
        response.msg = "OK"
        return response

    monkeypatch.setattr(urllib.request.HTTPSHandler, "do_open", oversize)
    client = ready_prep_client()
    with pytest.raises(mcp.McpError):
        client.read_prep_tool("readEvent", time.monotonic() + 5, eventUri="tana:event:site-visit")


@pytest.mark.parametrize("short", [False, True], ids=["leading-zero-length", "short_body"])
def test_brief_transport_validates_declared_length(monkeypatch, short):
    def respond(_handler, _connection, request, **_):
        msg = json.loads(request.data)
        _, _, body = prep_reply(msg, {"structuredContent": {"pinnedItems": []}})
        headers = Message()
        headers["Content-Length"] = f"0{len(body) + (100 if short else 0)}"
        response = urllib.response.addinfourl(io.BytesIO(body), headers, request.full_url, 200)
        response.msg = "OK"
        return response

    monkeypatch.setattr(urllib.request.HTTPSHandler, "do_open", respond)
    client = ready_prep_client()
    deadline = time.monotonic() + 5
    if short:
        with pytest.raises(mcp.McpError):
            client.read_prep_tool("readEvent", deadline, eventUri="tana:event:site-visit")
    else:
        assert client.read_prep_tool("readEvent", deadline, eventUri="tana:event:site-visit") == {"pinnedItems": []}


def test_brief_transport_does_not_hold_shared_lock_during_http(live_prep_client):
    def respond(message):
        assert client.lock.acquire(blocking=False), "Brief I/O blocks normal Context/refresh work"
        client.lock.release()
        return prep_reply(message, {"structuredContent": {"pinnedItems": []}})

    client, calls = live_prep_client(respond)
    assert client.read_prep_tool("readEvent", time.monotonic() + 5, eventUri="tana:event:site-visit") == {"pinnedItems": []}
    assert len(calls) == 1


def test_brief_transport_deadline_returns_while_blocked_io_keeps_only_one_slot(live_prep_client):
    entered, release, finished = (threading.Event() for _ in range(3))
    outcomes = []

    def respond(message):
        entered.set()
        if not release.wait(5):  # cleanup backstop; model a read blocked independently of the caller deadline
            raise AssertionError("test did not release HTTP")
        return prep_reply(message, {"structuredContent": {"pinnedItems": []}})

    client, calls = live_prep_client(respond)

    def first():
        try:
            outcomes.append(client.read_prep_tool("readEvent", time.monotonic() + 0.25, eventUri="tana:event:site-visit"))
        except Exception as e:
            outcomes.append(e)
        finally:
            finished.set()

    thread = threading.Thread(target=first, daemon=True)
    thread.start()
    try:
        assert entered.wait(2)
        assert finished.wait(2), "the caller did not return before I/O completed"
        assert len(outcomes) == 1 and isinstance(outcomes[0], TimeoutError)
        with pytest.raises((TimeoutError, mcp.McpError)):
            client.read_prep_tool("readEvent", time.monotonic() + 0.25, eventUri="tana:event:site-visit")
        assert len(calls) == 1
    finally:
        release.set()
        thread.join(2)
