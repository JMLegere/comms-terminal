"""Tana as a read-only Context source (FR-13a): what the service asks Tana and what it answers."""

from datetime import datetime, timezone

from universal_inbox import mcp, serve, tana

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
    assert serve.handle_tana(b'{"emails": []}', None) == (503, {"error": "Tana is not connected"})

    class Broken:
        def get(self, emails, domains):
            raise mcp.McpError("Tana sign-in expired or revoked (HTTP 400 invalid_grant)")

    status, out = serve.handle_tana(b'{"emails": ["a@b.ca"], "domains": []}', Broken())
    assert status == 502 and "sign-in expired" in out["error"]
    assert serve.handle_tana(b"not json", Broken())[0] == 400


def test_section_markup():
    from universal_inbox.tui import tana_markup
    text = tana_markup(tana.context(FakeTana(), ["laila@nbif.ca"], ["nbif.ca"], NOW))
    for part in ("Laïla Thériault", "NBIF — investment", "Send deck", "NBIF Interview", "Diligence next steps",
                 "Observations", "Wants milestones"):
        assert part in text
    empty = {k: [] for k in ("people", "organizations", "deals", "threats", "opportunities", "tasks", "meetings")}
    assert "nothing in Tana" in tana_markup(empty)
    assert "Tana unavailable: boom" in tana_markup(None, "boom")


def test_console_treats_a_service_without_tana_as_not_connected(tmp_path):
    import threading
    from universal_inbox.tui import socket_tana
    sock = str(tmp_path / "s.sock")
    old = serve.UnixHTTPServer(sock, serve._handler({}))  # a service from before /tana: HTML 404
    threading.Thread(target=old.serve_forever, daemon=True).start()
    try:
        assert socket_tana(sock)(["a@b.ca"], []) is None
    finally:
        old.shutdown()
