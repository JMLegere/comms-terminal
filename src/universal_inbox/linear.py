"""Linear issues and Customer Requests, queried only within a page's matching keys."""

import http.client
import json
import re
import urllib.error
import urllib.request
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
