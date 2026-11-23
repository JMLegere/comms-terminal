"""Tana, a read-only Context source (FR-13a, decision #122).

The service signs in to Tana's MCP server the way it signs in to Fastmail's (OAuth with dynamic client
registration and PKCE: `scripts/oauth_mcp.py --resource tana`) and answers the console's POST /tana
with what Tana knows about a page's email addresses and Organization domains: matching People and
Organizations, the Organizations' Deals, unresolved Threats and Opportunities, open tasks that mention them,
and Meetings (the next upcoming and the last three) with their summaries and linked documents.
Nothing is written to Tana. Answers are cached for five minutes.
"""

import json
import threading
import time
import urllib.error
from concurrent.futures import Future, InvalidStateError
from datetime import datetime, timezone
from urllib.parse import quote

from .mcp import McpClient, McpError, _decode_reply

from . import clock

MCP = "https://home.tana.inc/mcp"
TOKEN = "https://login.tana.inc/oauth2/token"
TTL = 300  # seconds an answer is reused
PAST, UPCOMING = 3, 1
PREP_WIRE_BYTES = 1024 * 1024  # JSON escaping and MCP framing can exceed the Brief's plain-text byte limit


class Client(McpClient):
    mcp, token_url, name = MCP, TOKEN, "Tana"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._prep_io = threading.Lock()

    def _prep_http(self, msg, headers, remaining):
        """Stop waiting on deadline even when DNS or a trickling HTTP response ignores inactivity timeouts."""
        if not self._prep_io.acquire(blocking=False):
            raise McpError("Tana Prep read already in progress")
        reply = Future()

        def fetch():
            try:
                response = self.http(self.mcp, json.dumps(msg).encode(), headers,
                                     timeout=remaining(), max_bytes=PREP_WIRE_BYTES)
                remaining()
            except TimeoutError:
                response = TimeoutError("Tana Prep read timed out")
            except urllib.error.URLError as e:
                response = (TimeoutError("Tana Prep read timed out") if isinstance(e.reason, TimeoutError)
                            else McpError("Tana Prep read unavailable"))
            except Exception:
                response = McpError("Tana Prep read unavailable")
            finally:
                self._prep_io.release()
            try:
                reply.set_result(response)
            except InvalidStateError:
                pass  # The caller timed out and cancelled publication. Do not retain the late body.

        try:
            threading.Thread(target=fetch, daemon=True).start()
        except RuntimeError:
            self._prep_io.release()
            raise McpError("Tana Prep read unavailable") from None
        try:
            response = reply.result(timeout=remaining())
            remaining()
        finally:
            reply.cancel()
        if isinstance(response, Exception):
            raise response
        return response

    def read_prep_tool(self, name: str, deadline: float, **args) -> dict:
        """One bounded read on the existing session. Never refresh, initialize, retry or change session state."""
        expected = {"readEvent": {"eventUri"}, "readItems": {"ids"}}.get(name)
        if expected is None or args.keys() != expected:
            raise McpError("Unsupported Tana Prep read")

        def remaining():
            seconds = deadline - time.monotonic()
            if seconds <= 0:
                raise TimeoutError("Tana Prep read timed out")
            return seconds

        if not self.lock.acquire(timeout=remaining()):
            raise TimeoutError("Tana Prep read timed out")
        try:
            if not self.access or clock.now() >= self.expires or self.session is None:
                raise McpError("No current Tana session")
            msg, headers = self._request("tools/call", {"name": name, "arguments": args})
        finally:
            self.lock.release()

        try:
            status, _, body = self._prep_http(msg, headers, remaining)
            if status != 200 or len(body) > PREP_WIRE_BYTES:
                raise McpError("Tana Prep read refused")
            reply = _decode_reply(body)
            if (not isinstance(reply, dict) or reply.get("jsonrpc") != "2.0" or "error" in reply
                    or type(reply.get("id")) is not int or reply["id"] != msg["id"]):
                raise McpError("Invalid Tana Prep reply")
            result = reply["result"]
            if not isinstance(result, dict) or result.get("isError", False) is not False:
                raise McpError("Tana Prep read failed")
            if "structuredContent" in result:
                result = result["structuredContent"]
            else:
                [block] = result["content"]
                if block["type"] != "text":
                    raise McpError("Unsupported Tana Prep reply")
                result = json.loads(block["text"])
            if not isinstance(result, dict):
                raise McpError("Invalid Tana Prep payload")
            remaining()
            return result
        except (ValueError, TypeError, KeyError, RecursionError):
            raise McpError("Invalid Tana Prep response") from None


def _items(client, **args) -> list[dict]:
    return client.tool("searchItems", queries=args.pop("queries", ["*"]), **args).get("items", [])


def _typed(client, type_: str, filters: list[dict]) -> list[dict]:
    return _items(client, targets=[{"target": "text", "type": type_, "fieldFilters": filters}])


def _uniq(items: list[dict]) -> list[dict]:
    return list({i["id"]: i for i in items}.values())


def _start(item: dict) -> str:
    """'past; 2026-10-06T16:00Z->17:30Z' -> '2026-10-06T16:00Z' (sortable: always UTC here)."""
    return (item.get("event") or {}).get("time", "").split("; ", 1)[-1].split("->", 1)[0]


def meetings(client, emails: list[str], domains: list[str], now: datetime) -> list[dict]:
    """The next upcoming Meeting and the last three whose attendees include one of `emails` or an address at
    one of `domains`. Tana's search matches whole addresses, so domains alone rarely find anything: the
    console also sends the addresses of the Organization's people."""
    keys = emails + [f"@{d}" for d in domains]
    if not keys:
        return []
    at = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M")
    queries = (emails + domains)[:20]  # ponytail: first 20 addresses only; chunk if a Case ever has more

    def find(bound, direction):
        found = _items(client, queries=queries, limit=50, sortOptions=[{"field": "startTime", "direction": direction}],
                       targets=[{"target": "event", bound: at, "eventTimeZone": "UTC"}])
        found = [i for i in _uniq(found) if any(k in (i.get("event") or {}).get("people", "").lower() for k in keys)]
        return sorted(found, key=_start, reverse=direction == "desc")

    chosen = find("eventStartTimeMin", "asc")[:UPCOMING] + find("eventStartTimeMax", "desc")[:PAST]
    out = []
    for m in chosen:
        e = client.tool("readEvent", eventUri=m["id"])
        out.append({"id": m["id"], "title": e.get("title") or m.get("title") or "", "start": e.get("startTime", ""),
                    "upcoming": _start(m) >= at,
                    "summary": e.get("summary") or e.get("tagline") or "", "summary_id": e.get("summaryUri"),
                    "related": [d["uri"] for d in e.get("relatedDocs") or [] if d.get("uri", "").startswith("tana:text:")
                                and d["uri"] != e.get("summaryUri")]})
    return out


def context(client, emails, domains, now: datetime | None = None) -> dict:
    """Everything the Tana section shows, as plain JSON. Each record is {id, title, type, url}."""
    now = now or clock.now()
    emails, domains = sorted({e.lower() for e in emails}), sorted({d.lower() for d in domains})
    people = _uniq([i for e in emails for i in _typed(client, "People", [{"field": "Email", "equals": e}])])
    orgs = _uniq([i for d in domains for i in _typed(client, "Organizations", [{"field": "Domain", "equals": d}])])
    deals = threats = opportunities = []
    if orgs:
        ids = [o["id"] for o in orgs]
        deals = _typed(client, "Deals", [{"field": "Organization", "matchAny": ids}])
        threats = _typed(client, "Threats", [{"field": "Organizations", "matchAny": ids},
                                             {"field": "Status", "equals": "Active", "startsWith": "Mitigating"}])
        opportunities = _typed(client, "Opportunities", [{"field": "Organizations", "matchAny": ids},
                                                         {"field": "Status", "equals": "Exploring", "startsWith": "Validated"}])
    tasks = []
    if people or orgs:
        edges = client.tool("listEdges", nodeIds=[x["id"] for x in people + orgs], direction="incoming",
                            edgeTypes=["LINKS_TO"], limit=200).get("edges", [])
        mentions = list(dict.fromkeys(e["fromNodeId"] for e in edges if e.get("fromNodeId", "").startswith("tana:text:")))
        if mentions:  # a task is a document with a workflow state
            tasks = [i for i in _items(client, itemIds=mentions[:200]) if i.get("state") and i["state"] != "Completed"]
    found = meetings(client, emails, domains, now)
    related = list(dict.fromkeys(u for m in found for u in m["related"]))
    kinds = {i["id"]: i for i in _items(client, itemIds=related[:200])} if related else {}

    everything = people + orgs + deals + threats + opportunities + tasks + [{"id": m["id"]} for m in found]
    url = _linker(client, everything[0]["id"]) if everything else None
    rec = lambda i: {"id": i["id"], "title": i.get("title") or "", "type": i.get("type") or "",  # noqa: E731
                     "state": i.get("state") or "", "url": url(i["id"])}
    for m in found:
        linked = [kinds[u] for u in m.pop("related") if u in kinds]
        m["linked"] = [rec(i) | {"type": "Tasks" if i.get("state") else i.get("type")}
                       for i in linked if i.get("state") or i.get("type") not in (None, "", "(untyped)")]
        m["url"], m["summary_url"] = url(m["id"]), url(m["summary_id"]) if m["summary_id"] else None
    return {"people": [rec(i) for i in people], "organizations": [rec(i) for i in orgs],
            "deals": [rec(i) for i in deals], "threats": [rec(i) for i in threats],
            "opportunities": [rec(i) for i in opportunities], "tasks": [rec(i) for i in tasks], "meetings": found}


_prefix: dict = {}


def _linker(client, any_id: str):
    """Tana web links are https://home.tana.inc/o/<workspace>/l/<quoted URI>: ask once for the workspace part."""
    if "url" not in _prefix:
        link = client.tool("getShareLink", ids=[any_id])["links"][0]["url"]
        _prefix["url"] = link.rsplit("/l/", 1)[0] + "/l/"
    return lambda uri: _prefix["url"] + quote(uri, safe="")


class Context:
    """The service's answers from a Context source (Tana by default), cached by normalized match inputs for TTL."""

    def __init__(self, client, clock=time.monotonic, fetch=None):
        self.client, self.clock, self.cache, self.lock = client, clock, {}, threading.Lock()
        self.fetch = fetch or context

    def get(self, emails, domains, *matches) -> dict:
        key = (tuple(sorted({e.lower() for e in emails})), tuple(sorted({d.lower() for d in domains})))
        key += tuple(tuple(sorted({" ".join(value.casefold().split()) for value in values if value.strip()}))
                     for values in matches)
        with self.lock:
            hit = self.cache.get(key)
            if hit and self.clock() - hit[0] < TTL:
                return hit[1]
        out = self.fetch(self.client, *key)
        with self.lock:
            self.cache[key] = (self.clock(), out)
        return out
