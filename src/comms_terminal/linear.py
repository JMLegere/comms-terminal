"""Linear issues and Customer Requests, queried only within a page's matching keys."""

import hashlib
import hmac
import http.client
import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from . import tana
from .mcp import USER_AGENT

API = "https://api.linear.app/graphql"
TERMINAL = {"done", "completed", "canceled", "cancelled", "archived"}
ISSUE_FIELDS = """
    id identifier title description url priorityLabel updatedAt completedAt canceledAt archivedAt
    state { name type } assignee { name } project { name }
"""
ISSUES = """query ContextIssues($filter: IssueFilter!, $after: String) {
  issues(filter: $filter, first: 100, after: $after, includeArchived: false) {
    nodes { """ + ISSUE_FIELDS + """ }
    pageInfo { hasNextPage endCursor }
  }
}"""
NEEDS = """query ContextCustomerNeeds($filter: CustomerNeedFilter!, $after: String) {
  customerNeeds(filter: $filter, first: 100, after: $after, includeArchived: false) {
    nodes { id content body archivedAt customer { name domains } issue { """ + ISSUE_FIELDS + """ } }
    pageInfo { hasNextPage endCursor }
  }
}"""


class LinearError(Exception):
    pass


class Client:
    def __init__(self, token: str, opener=urllib.request.urlopen):
        self.token, self.opener = token, opener

    def query(self, document: str, variables: dict | None = None) -> dict:
        if not re.match(r"\s*query\b", document) or re.search(r"\b(mutation|subscription)\b", document):
            raise LinearError("Linear accepts read-only queries only")
        try:
            request = urllib.request.Request(API, json.dumps({"query": document, "variables": variables or {}}).encode(),
                                             {"Authorization": self.token, "Content-Type": "application/json",
                                              "User-Agent": USER_AGENT}, method="POST")
            with self.opener(request, timeout=30) as response:
                status = getattr(response, "status", 200)
                if status != 200:
                    raise LinearError(f"Linear HTTP {status}" if isinstance(status, int) and 100 <= status <= 599
                                      else "Linear HTTP error")
                result = json.load(response)
        except urllib.error.HTTPError as error:
            code = error.code
            raise LinearError(f"Linear HTTP {code}" if isinstance(code, int) and 100 <= code <= 599
                              else "Linear HTTP error") from None
        except (urllib.error.URLError, OSError, http.client.HTTPException):
            raise LinearError("Linear network request failed") from None
        except (ValueError, UnicodeError):
            raise LinearError("Linear returned an invalid response") from None
        if not isinstance(result, dict):
            raise LinearError("Linear returned an invalid response")
        if result.get("errors"):
            raise LinearError("Linear GraphQL query failed")
        if not isinstance(result.get("data"), dict):
            raise LinearError("Linear returned an invalid response")
        return result["data"]

    def write(self, name: str, variables: dict) -> dict:
        """One of WRITES, for the Action Queue only (ADR 0019); its outcome, never an exception."""
        return mutate(self.token, name, variables, self.opener)


WRITES = {  # the only mutations Comms Terminal sends (ADR 0019): Jeremy's Issue changes, never a delete
    "issueUpdate": """mutation IssueUpdate($id: String!, $input: IssueUpdateInput!) {
  issueUpdate(id: $id, input: $input) { success issue { id identifier url createdAt updatedAt } }
}""",
    "issueCreate": """mutation IssueCreate($input: IssueCreateInput!) {
  issueCreate(input: $input) { success issue { id identifier url createdAt updatedAt } }
}""",
}
ISSUE_BY_ID = """query IssueById($id: String!) { issue(id: $id) { id identifier url createdAt updatedAt } }"""


def _said(error) -> str:
    return " ".join(str(error.get("message") or "Linear refused it").split())[:200] if isinstance(error, dict) \
        else "Linear refused it"


def mutate(client_token: str, name: str, variables: dict, opener=urllib.request.urlopen) -> dict:
    """One write (WRITES[name]) and its outcome for the Action Queue (ADR 0019): {"outcome": "confirmed", "issue"},
    {"outcome": "refused", "error"} (Linear said no), {"outcome": "broken", "error"} (the key was refused), or
    {"outcome": "unknown", "error"} (no answer: it may or may not have happened)."""
    try:
        request = urllib.request.Request(API, json.dumps({"query": WRITES[name], "variables": variables}).encode(),
                                         {"Authorization": client_token, "Content-Type": "application/json",
                                          "User-Agent": USER_AGENT}, method="POST")
        with opener(request, timeout=30) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            return {"outcome": "broken", "error": f"Linear HTTP {error.code}"}
        if 400 <= error.code < 500 and error.code != 429:
            try:
                return {"outcome": "refused", "error": _said((json.load(error).get("errors") or [None])[0])}
            except (ValueError, AttributeError, UnicodeError):
                return {"outcome": "refused", "error": f"Linear HTTP {error.code}"}
        return {"outcome": "unknown", "error": f"Linear HTTP {error.code}"}
    except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError, UnicodeError):
        return {"outcome": "unknown", "error": "Linear did not answer"}
    errors = result.get("errors") if isinstance(result, dict) else None
    if errors:
        first = errors[0] if isinstance(errors[0], dict) else {}
        code = str((first.get("extensions") or {}).get("type") or (first.get("extensions") or {}).get("code") or "")
        if "authentication" in code.casefold() or "forbidden" in code.casefold():
            return {"outcome": "broken", "error": _said(first)}
        return {"outcome": "refused", "error": _said(first)}
    out = ((result.get("data") or {}).get(name) or {}) if isinstance(result, dict) else {}
    if not out.get("success") or not isinstance(out.get("issue"), dict):
        return {"outcome": "refused", "error": "Linear did not save it"}
    return {"outcome": "confirmed", "issue": out["issue"]}


def _pages(client, document, field, filter_):
    after, seen = None, set()
    while True:
        result = client.query(document, {"filter": filter_, "after": after}).get(field)
        if not isinstance(result, dict) or not isinstance(result.get("nodes"), list):
            raise LinearError("Linear returned an invalid connection")
        page = result.get("pageInfo")
        if not isinstance(page, dict) or not isinstance(page.get("hasNextPage"), bool):
            raise LinearError("Linear returned invalid pagination")
        for node in result["nodes"]:
            if not isinstance(node, dict):
                raise LinearError("Linear returned an invalid record")
            yield node
        if not page["hasNextPage"]:
            return
        cursor = page.get("endCursor")
        if not isinstance(cursor, str) or not cursor or cursor in seen:
            raise LinearError("Linear pagination did not advance")
        seen.add(cursor)
        after = cursor


def _open(issue):
    state = issue.get("state") or {}
    return not (any(issue.get(key) for key in ("completedAt", "canceledAt", "archivedAt"))
                or any(str(state.get(key) or "").strip().casefold() in TERMINAL for key in ("name", "type")))


def _normal(values):
    return sorted({" ".join(value.casefold().split()) for value in values if value.strip()})


def _targets(emails, domains, person_names, organization_names):
    out = []
    for kind, values in (("email", emails), ("domain", domains), ("person", person_names),
                         ("organization", organization_names)):
        for value in values:
            # A final period may be punctuation; a dot followed by a label extends the token.
            before = r"[\w.!#$%&'*+/=?^`{|}~@-]" if kind == "email" else r"[\w.@-]"
            if kind == "domain":
                before = r"[\w.-]"  # a domain may occur after the @ in an email address
            phrase = r"\s+".join(re.escape(word) for word in value.split())
            pattern = re.compile(r"(?<!" + before + r")" + phrase + r"(?![\w@+-]|\.[\w-])", re.IGNORECASE)
            out.append((kind, value, pattern))
    return out


def _matches(text, targets):
    # ponytail: paired Markdown delimiters, not a full parser. Keep raw excerpt offsets and code literals.
    offsets, literal = list(range(len(text))), set()
    paired = (r"(?<![\w.!#$%&'*+/=?^`{|}~@\\-])(?P<mark>\*{1,3}|_{1,3}|`+|~~)"
              r"(?=\S)(?P<body>.+?)(?<=\S)(?P=mark)(?![\w@+-]|\.[\w-])")
    while pairs := [match for match in re.finditer(paired, text, re.DOTALL)
                    if offsets[match.start()] not in literal and offsets[match.end() - 1] not in literal]:
        for match in reversed(pairs):
            start, end = match.span()
            first, last = match.span("body")
            if match["mark"].startswith("`"):
                literal.update(offsets[first:last])
            text = text[:start] + text[first:last] + text[end:]
            offsets[start:end] = offsets[first:last]
    return [(kind, value, offsets[match.start()]) for kind, value, pattern in targets
            if (match := pattern.search(text))]


def _reasons(text, targets, source):
    return [f"{kind}: {value} ({source})" for kind, value, _ in _matches(text, targets)]


def _url(value):
    if not isinstance(value, str) or any(c.isspace() or ord(c) < 32 or ord(c) == 127 or c in "[]\\" for c in value):
        return ""
    try:
        parts = urlsplit(value)
        return value if parts.scheme == "https" and parts.netloc.casefold() == "linear.app" else ""
    except ValueError:
        return ""


def _record(issue):
    return {"id": issue["id"], "identifier": issue.get("identifier") or "", "title": issue.get("title") or "",
            "url": _url(issue.get("url")), "state": (issue.get("state") or {}).get("name") or "",
            "priority": issue.get("priorityLabel") or "", "assignee": (issue.get("assignee") or {}).get("name") or "",
            "project": (issue.get("project") or {}).get("name") or "", "updated": issue.get("updatedAt") or "",
            "reasons": [], "requests": []}


def _excerpt(text, targets):
    text = " ".join(text.split())
    hits = [start for _, _, start in _matches(text, targets)]
    start = max(0, min(hits, default=0) - 80)
    excerpt = text[start:start + 238]
    return ("…" if start else "") + excerpt + ("…" if start + len(excerpt) < len(text) else "")


def context(client, emails, domains, person_names=(), organization_names=()) -> dict:
    emails, domains, person_names, organization_names = map(_normal, (emails, domains, person_names, organization_names))
    targets = _targets(emails, domains, person_names, organization_names)
    if not targets:
        return {"issues": []}
    candidates, found, request_ids = set(), {}, set()
    clauses = [{field: {"containsIgnoreCase": value}} for value in sorted(set(emails + domains))
               for field in ("title", "description")]
    # All name words bound candidates without assuming their exact whitespace; verify the full phrase locally.
    clauses += [{"and": [{field: {"containsIgnoreCase": word}} for word in name.split()]}
                for name in sorted(set(person_names + organization_names)) for field in ("title", "description")]
    issue_filter = {"completedAt": {"null": True}, "canceledAt": {"null": True}, "archivedAt": {"null": True},
                    "state": {"type": {"nin": sorted(TERMINAL)}}, "or": clauses}
    for issue in _pages(client, ISSUES, "issues", issue_filter):
        if not _open(issue):
            continue
        candidates.add(issue["id"])
        reasons = _reasons(issue.get("title") or "", targets, "title")
        reasons += _reasons(issue.get("description") or "", targets, "description")
        if reasons:
            record = found.setdefault(issue["id"], _record(issue))
            record["reasons"].extend(reasons)

    relations = [{"customer": {"domains": {"some": {"eq": domain}}}} for domain in domains]
    relations += [{"and": [{"customer": {"name": {"containsIgnoreCase": word}}} for word in name.split()]}
                  for name in organization_names]
    if candidates:
        relations.append({"issue": {"id": {"in": sorted(candidates)}}})
    if relations:
        for need in _pages(client, NEEDS, "customerNeeds", {"or": relations}):
            issue = need.get("issue")
            if need.get("archivedAt") or not issue or not _open(issue):
                continue
            customer = need.get("customer") or {}
            customer_domains = set(_normal(customer.get("domains") or []))
            reasons = [f"customer: {domain}" for domain in domains if domain in customer_domains]
            name = " ".join((customer.get("name") or "").casefold().split())
            if name in organization_names:
                reasons.append(f"customer: {name}")
            if not reasons and issue["id"] not in candidates:
                continue
            content = need.get("content") or ""
            text = content if content.strip() else need.get("body") or ""
            text_reasons = _reasons(text, targets, "request")
            if not reasons and not text_reasons:
                continue
            record = found.setdefault(issue["id"], _record(issue))
            record["reasons"].extend(reasons + text_reasons + _reasons(issue.get("title") or "", targets, "title")
                                     + _reasons(issue.get("description") or "", targets, "description"))
            request_key = (issue["id"], need["id"])
            if request_key not in request_ids:
                record["requests"].append({"id": need["id"], "text": _excerpt(text, targets),
                                           "customer": customer.get("name") or ""})
                request_ids.add(request_key)
    for record in found.values():
        record["reasons"] = sorted(set(record["reasons"]))
        record["requests"].sort(key=lambda request: request["id"])
    ordered = sorted(found.values(), key=lambda record: record["id"])
    return {"issues": sorted(ordered, key=lambda record: record["updated"], reverse=True)}


def Context(client, clock=None):  # noqa: N802  share the existing five-minute Context cache
    return tana.Context(client, **({"clock": clock} if clock is not None else {}), fetch=context)


# --- Issue connection (ADR 0018): the copy of Jeremy's assigned Issues ------------------------------------------

CLOSED_KEPT_DAYS = 30  # closed Issues stay in the copy this long, for the Issues page's completed window
VIEWER = "query Viewer { viewer { id } }"
ASSIGNED = """query AssignedIssues($filter: IssueFilter!, $after: String) {
  issues(filter: $filter, first: 100, after: $after, includeArchived: false) {
    nodes {
      id identifier title description url priority estimate dueDate createdAt updatedAt completedAt canceledAt
      state { id } team { id key name } project { name } cycle { number } parent { identifier }
      labels { nodes { name } }
    }
    pageInfo { hasNextPage endCursor }
  }
}"""
TEAMS = """query TeamStatuses($after: String) {
  teams(first: 50, after: $after) {
    nodes { id states { nodes { id name type position } } }
    pageInfo { hasNextPage endCursor }
  }
}"""


def _label_names(labels) -> list[str]:
    """Labels arrive as a GraphQL connection ({"nodes": [...]}) from a read, and as a list from a webhook."""
    items = labels.get("nodes") if isinstance(labels, dict) else labels
    return sorted(str(x["name"]) for x in items or () if isinstance(x, dict) and x.get("name"))


def normal(node: dict) -> dict:
    """One Linear Issue, from a read or a webhook's `data`, as issues.upsert takes it."""
    state, team = node.get("state") or {}, node.get("team") or {}
    data = {"team_key": team.get("key"), "team": team.get("name"), "project": (node.get("project") or {}).get("name"),
            "cycle": (node.get("cycle") or {}).get("number"), "parent": (node.get("parent") or {}).get("identifier"),
            "labels": _label_names(node.get("labels"))}
    return {"id": node["id"], "identifier": node["identifier"], "title": node.get("title") or "",
            "description": node.get("description"), "url": node.get("url"),
            "team_id": team.get("id") or node.get("teamId"), "state_id": state.get("id") or node.get("stateId"),
            "priority": int(node.get("priority") or 0), "estimate": node.get("estimate"),
            "due_date": node.get("dueDate"), "created_at": node["createdAt"], "updated_at": node["updatedAt"],
            "completed_at": node.get("completedAt"), "canceled_at": node.get("canceledAt"),
            "data": {k: v for k, v in data.items() if v not in (None, [])}}


def full_read(client, now: datetime) -> tuple[str, list[dict], list[dict]]:
    """Jeremy's Linear user id, every Issue assigned to him that is open or closed within CLOSED_KEPT_DAYS, and
    every team status (FR-IS-1, FR-IS-3)."""
    since = (now - timedelta(days=CLOSED_KEPT_DAYS)).isoformat()
    me = {"assignee": {"isMe": {"eq": True}}}
    # Three plain filters: Linear answered every assigned Issue (812 for 57 open) to `or` beside `assignee`, and
    # more than that with `assignee` inside each branch (live check, 2026-11-07)
    filters = ({**me, "completedAt": {"null": True}, "canceledAt": {"null": True}},
               {**me, "completedAt": {"gt": since}}, {**me, "canceledAt": {"gt": since}})
    viewer = client.query(VIEWER).get("viewer") or {}
    if not viewer.get("id"):
        raise LinearError("Linear returned no viewer")
    found = {node["id"]: node for f in filters for node in _pages(client, ASSIGNED, "issues", f)}
    issues = [normal(node) for node in found.values()]
    statuses, after, seen = [], None, set()
    while True:  # teams page like _pages, but take no filter
        result = client.query(TEAMS, {"after": after}).get("teams")
        if not isinstance(result, dict) or not isinstance(result.get("nodes"), list):
            raise LinearError("Linear returned an invalid connection")
        for team in result["nodes"]:
            statuses += [{"id": s["id"], "team_id": team["id"], "name": s["name"], "type": s["type"],
                          "position": s.get("position") or 0} for s in (team.get("states") or {}).get("nodes", ())]
        page = result.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        after = page.get("endCursor")
        if not after or after in seen:
            raise LinearError("Linear pagination did not advance")
        seen.add(after)
    return viewer["id"], issues, statuses


def verify(headers, body: bytes, secret: bytes, now: datetime) -> str | None:
    """None when Linear signed this body (hex HMAC-SHA256 of the raw body in Linear-Signature) within the last
    minute (webhookTimestamp, ms), else why not (linear.app/developers/webhooks, "Securing Webhooks")."""
    signature = headers.get("Linear-Signature") or ""
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature.strip().lower(), expected):
        return "bad signature"
    try:
        sent = json.loads(body)["webhookTimestamp"] / 1000
    except (ValueError, KeyError, TypeError):
        return "no webhookTimestamp"
    if abs(now.timestamp() - sent) > 60:
        return "stale webhookTimestamp"
    return None


def handle_hook(db, connection, headers, body: bytes, secret: bytes, now: datetime) -> int | tuple[int, str]:
    """POST /hooks/linear (through serve.intake): an Issue event updates the copy (FR-IS-2). Linear retries any
    non-200, so everything verified answers 200, including events about other people's Issues."""
    from . import issues
    if (why := verify(headers, body, secret, now)) is not None:
        return 401, why
    event = json.loads(body)
    data = event.get("data")
    if event.get("type") != "Issue" or not isinstance(data, dict) or not data.get("id"):
        return 200
    mine = connection["self_id"] and data.get("assigneeId") == connection["self_id"]
    if event.get("action") == "remove" or data.get("archivedAt") or (connection["self_id"] and not mine):
        issues.remove(db, data["id"])
    elif mine:
        issues.upsert(db, connection["id"], normal(data), now)
    # before the first full read Jeremy's Linear id is unknown: that read copies the Issue instead
    return 200
