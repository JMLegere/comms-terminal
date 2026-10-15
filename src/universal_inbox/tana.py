"""Tana, a read-only Context source (FR-13a, decision #122).

The service signs in to Tana's MCP server the way it signs in to Fastmail's (OAuth with dynamic client
registration and PKCE: `scripts/oauth_mcp.py --resource tana`) and answers the console's POST /tana
with what Tana knows about a page's email addresses and Organization domains: matching People and
Organizations, the Organizations' Deals, unresolved Threats and Opportunities, open tasks that mention them,
and Meetings (the next upcoming and the last three) with their summaries and linked documents.
Nothing is written to Tana. Answers are cached for five minutes.
"""

import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote

from .mcp import McpClient

MCP = "https://home.tana.inc/mcp"
TOKEN = "https://login.tana.inc/oauth2/token"
TTL = 300  # seconds an answer is reused
PAST, UPCOMING = 3, 1


class Client(McpClient):
    mcp, token_url, name = MCP, TOKEN, "Tana"


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
    now = now or datetime.now(timezone.utc)
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
