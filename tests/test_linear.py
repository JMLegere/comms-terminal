"""Read-only Linear Context: bounded matching, lifecycle, pagination, HTTP errors, and cache keys."""

import http.client
import io
import json
import urllib.error

import pytest

from universal_inbox import linear, tana

EMAIL, DOMAIN = "ana@acme.com", "acme.com"


def issue(id_, title="Customer setup", **values):
    return {"id": id_, "identifier": f"DEV-{id_}", "title": title, "description": None,
            "url": f"https://linear.app/example/issue/DEV-{id_}/setup", "priorityLabel": "High",
            "updatedAt": "2026-10-15T12:00:00Z", "state": {"name": "Backlog", "type": "backlog"},
            "assignee": None, "project": None, "completedAt": None, "canceledAt": None, "archivedAt": None,
            **values}


def need(id_, linked_issue, content="", **values):
    return {"id": id_, "issue": linked_issue, "content": content, "body": None, "customer": None,
            "archivedAt": None, **values}


class FakeLinear:
    def __init__(self, issues=(), needs=()):
        self.pages = {"issues": list(issues) or [[]], "customerNeeds": list(needs) or [[]]}
        self.calls = []

    def query(self, document, variables=None):
        self.calls.append((document, variables))
        assert document in (linear.ISSUES, linear.NEEDS)
        assert document.startswith("query ") and "includeArchived: false" in document
        field = "issues" if document == linear.ISSUES else "customerNeeds"
        after = variables["after"]
        index = int(after.split(":")[1]) if after else 0
        pages = self.pages[field]
        more = index + 1 < len(pages)
        return {field: {"nodes": pages[index], "pageInfo": {
            "hasNextPage": more, "endCursor": f"{field}:{index + 1}" if more else None}}}


def test_client_uses_raw_personal_key_and_read_only_graphql_post():
    calls = []

    def opener(request, timeout):
        calls.append((request, timeout))
        return io.BytesIO(b'{"data": {"ok": true}}')

    assert linear.Client("personal-test-key", opener).query("query Check { __typename }", {"x": 1}) == {"ok": True}
    [(request, timeout)] = calls
    assert request.full_url == linear.API and request.get_method() == "POST" and timeout == 30
    assert request.get_header("Authorization") == "personal-test-key"
    assert request.get_header("Content-type") == "application/json"
    assert json.loads(request.data) == {"query": "query Check { __typename }", "variables": {"x": 1}}
    for document in ("mutation { issueCreate { success } }", "subscription { __typename }",
                     "query Read { __typename } mutation Write { __typename }"):
        with pytest.raises(linear.LinearError, match="read-only"):
            linear.Client("personal-test-key", opener).query(document)
    assert len(calls) == 1


@pytest.mark.parametrize("failure, message", [
    (urllib.error.HTTPError(linear.API, 401, "private-test-key", {}, io.BytesIO(b"private response")), "HTTP 401"),
    (urllib.error.URLError("private-test-key in network error"), "network request failed"),
    (TimeoutError("private-test-key in timeout"), "network request failed"),
    (http.client.IncompleteRead(b"private-test-key"), "network request failed"),
])
def test_http_and_network_errors_are_sanitized(failure, message):
    def opener(request, timeout):
        raise failure

    with pytest.raises(linear.LinearError, match=message) as caught:
        linear.Client("private-test-key", opener).query(linear.ISSUES)
    assert "private" not in str(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("body, message", [
    (b"private-test-key is not json", "invalid response"),
    (b"\xff", "invalid response"),
    (b"[]", "invalid response"),
    (b'{"data": null}', "invalid response"),
    (b'{"data": []}', "invalid response"),
    (b'{"errors": [{"message": "private-test-key"}]}', "GraphQL query failed"),
    (b'{"data": {"issues": {}}, "errors": [{"message": "private-test-key"}]}', "GraphQL query failed"),
])
def test_invalid_json_shapes_and_partial_graphql_failures_are_sanitized(body, message):
    with pytest.raises(linear.LinearError, match=message) as caught:
        linear.Client("private-test-key", lambda request, timeout: io.BytesIO(body)).query(linear.ISSUES)
    assert "private" not in str(caught.value)


def test_non_200_response_is_not_treated_as_success():
    response = io.BytesIO(b"private-test-key response")
    response.status = 429
    with pytest.raises(linear.LinearError, match="Linear HTTP 429"):
        linear.Client("private-test-key", lambda request, timeout: response).query(linear.ISSUES)


def test_context_paginates_both_paths_deduplicates_and_accumulates_provenance():
    a = issue("a", f"Discuss {EMAIL}", description="Ana Reed at Acme Corp", assignee={"name": "Owner"},
              project={"name": "Onboarding"})
    b = issue("b", updatedAt="2026-10-15T13:00:00Z", state={"name": "Triage", "type": "triage"})
    c = issue("c", f"Near match: {EMAIL}.evil")
    related = need("related", b, "Customer wants setup", customer={"name": "Acme Corp", "domains": ["ACME.COM"]})
    mention = need("mention", a, f"Please ask {EMAIL}", customer={"name": "Other", "domains": ["other.com"]})
    fake = FakeLinear(issues=[[a, c], [a]], needs=[[related, mention], [related, mention,
        need("candidate", c, f"Contact {EMAIL}"), need("unrelated", a, "Unrelated request"),
        need("project-only", None, EMAIL, customer={"name": "Acme Corp", "domains": [DOMAIN]})]])
    records = linear.context(fake, [EMAIL], [DOMAIN], ["Ana Reed"], ["Acme Corp"])["issues"]
    assert [record["id"] for record in records] == ["b", "a", "c"]
    by_id = {record["id"]: record for record in records}
    assert by_id["b"]["state"] == "Triage"
    assert by_id["b"]["reasons"] == ["customer: acme corp", "customer: acme.com"]
    assert by_id["b"]["requests"] == [{"id": "related", "text": "Customer wants setup", "customer": "Acme Corp"}]
    assert {"email: ana@acme.com (title)", "domain: acme.com (title)", "person: ana reed (description)",
            "organization: acme corp (description)", "email: ana@acme.com (request)"} <= set(by_id["a"]["reasons"])
    assert len(by_id["a"]["reasons"]) == len(set(by_id["a"]["reasons"]))
    assert by_id["a"]["assignee"] == "Owner" and by_id["a"]["project"] == "Onboarding"
    assert [request["id"] for request in by_id["a"]["requests"]] == ["mention"]
    assert by_id["c"]["reasons"] == ["domain: acme.com (request)", "email: ana@acme.com (request)"]
    assert set(by_id["a"]) == {"id", "identifier", "title", "url", "state", "priority", "assignee", "project",
                                "updated", "reasons", "requests"}
    assert by_id["c"]["assignee"] == by_id["c"]["project"] == ""
    issue_calls = [args for document, args in fake.calls if document == linear.ISSUES]
    need_calls = [args for document, args in fake.calls if document == linear.NEEDS]
    assert [args["after"] for args in issue_calls] == [None, "issues:1"]
    assert [args["after"] for args in need_calls] == [None, "customerNeeds:1"]
    filters = need_calls[0]["filter"]["or"]
    assert {"customer": {"domains": {"some": {"eq": DOMAIN}}}} in filters
    assert {"and": [{"customer": {"name": {"containsIgnoreCase": word}}} for word in ("acme", "corp")]} in filters
    assert {"issue": {"id": {"in": ["a", "c"]}}} in filters
    assert all("content" not in json.dumps(args["filter"]) and "body" not in json.dumps(args["filter"])
               for _, args in fake.calls)


@pytest.mark.parametrize("kind, text, matched", [
    ("email", "Write ANA@ACME.COM.", True),
    ("email", "xana@acme.com", False),
    ("email", "x!ana@acme.com", False),
    ("email", "ana+extra@acme.com", False),
    ("email", "ana@acme.com.evil", False),
    ("email", "ana@acme.com+extra", False),
    ("domain", "support@ACME.COM.", True),
    ("domain", "https://acme.com/setup", True),
    ("domain", "evilacme.com", False),
    ("domain", "sub.acme.com", False),
    ("domain", "acme.com.evil", False),
    ("domain", "acme.com-extra", False),
    ("domain", "acme.com@other.com", False),
    ("person", "Ask ANA   REED.", True),
    ("person", "Ask Ana Reedley", False),
    ("person", "Ask XAna Reed", False),
    ("person", "Ask Ana Reed-Smith", False),
    ("person", "Ana Reed@example.com", False),
    ("organization", "Acme Corp's rollout", True),
    ("organization", "Acme Corps rollout", False),
])
def test_exact_email_domain_and_full_name_boundaries(kind, text, matched):
    inputs = {"emails": [], "domains": [], "person_names": [], "organization_names": []}
    key, value = {"email": ("emails", EMAIL), "domain": ("domains", DOMAIN),
                  "person": ("person_names", "Ana Reed"), "organization": ("organization_names", "Acme Corp")}[kind]
    inputs[key] = [value]
    fake = FakeLinear(issues=[[issue("a", text)]])
    assert bool(linear.context(fake, **inputs)["issues"]) is matched
    assert fake.calls[0][1]["filter"]["or"]  # bounded title/description candidates, even for names
    if kind in {"person", "organization"}:
        assert fake.calls[0][1]["filter"]["or"] == [
            {"and": [{field: {"containsIgnoreCase": word}} for word in value.casefold().split()]}
            for field in ("title", "description")]


@pytest.mark.parametrize("closed", [
    {"completedAt": "2026-10-14T12:00:00Z"}, {"canceledAt": "2026-10-14T12:00:00Z"},
    {"archivedAt": "2026-10-14T12:00:00Z"},
    {"state": {"name": "Custom closed", "type": "completed"}},
    {"state": {"name": "Custom rejected", "type": "canceled"}},
    {"state": {"name": "Custom archived", "type": "archived"}},
    {"state": {"name": " Done ", "type": "started"}},
    {"state": {"name": "Completed", "type": "started"}},
    {"state": {"name": "Canceled", "type": "started"}},
    {"state": {"name": "Cancelled", "type": "started"}},
    {"state": {"name": "Archived", "type": "started"}},
])
def test_terminal_and_archived_issues_excluded_on_both_paths(closed):
    backlog, triage = issue("backlog", EMAIL), issue("triage", EMAIL, state={"name": "Triage", "type": "triage"})
    fake = FakeLinear(issues=[[issue("closed-text", EMAIL, **closed), backlog, triage]], needs=[[
        need("closed-request", issue("closed-relation", **closed), "Request",
             customer={"name": "Acme", "domains": [DOMAIN]}),
        need("archived-request", issue("archived-request-issue"), EMAIL, archivedAt="2026-10-14T12:00:00Z",
             customer={"name": "Acme", "domains": [DOMAIN]})]])
    assert [record["id"] for record in linear.context(fake, [EMAIL], [DOMAIN])["issues"]] == ["backlog", "triage"]
    issue_filter = fake.calls[0][1]["filter"]
    assert all(issue_filter[key] == {"null": True} for key in ("completedAt", "canceledAt", "archivedAt"))


def test_customer_relations_require_exact_domain_or_full_organization_name():
    fake = FakeLinear(needs=[[
        need("domain", issue("domain"), customer={"name": "Unrelated title", "domains": ["ACME.COM"]}),
        need("name", issue("name"), customer={"name": " ACME   CORP ", "domains": []}),
        need("subdomain", issue("subdomain"), EMAIL, customer={"name": "Other", "domains": ["sub.acme.com"]}),
        need("suffix", issue("suffix"), EMAIL, customer={"name": "Acme Corps", "domains": ["acme.com.evil"]}),
    ]])
    records = linear.context(fake, [EMAIL], [DOMAIN], [], ["Acme Corp"])["issues"]
    assert [record["id"] for record in records] == ["domain", "name"]
    assert [record["reasons"] for record in records] == [["customer: acme.com"], ["customer: acme corp"]]


def test_customer_name_candidates_apply_all_word_predicates_before_normalized_exact_match():
    class NameFilteredLinear(FakeLinear):
        def query(self, document, variables=None):
            result = super().query(document, variables)
            if document == linear.NEEDS:
                def matches(node, predicate):
                    if "or" in predicate:
                        return any(matches(node, clause) for clause in predicate["or"])
                    if "and" in predicate:
                        return all(matches(node, clause) for clause in predicate["and"])
                    comparison = predicate["customer"]["name"]
                    name = node["customer"]["name"].casefold()  # Linear does not normalize whitespace.
                    if "eqIgnoreCase" in comparison:
                        return name == comparison["eqIgnoreCase"].casefold()
                    assert set(comparison) == {"containsIgnoreCase"}
                    return comparison["containsIgnoreCase"].casefold() in name

                result["customerNeeds"]["nodes"] = [
                    node for node in result["customerNeeds"]["nodes"] if matches(node, variables["filter"])]
                self.candidates = [node["id"] for node in result["customerNeeds"]["nodes"]]
            return result

    fake = NameFilteredLinear(needs=[[
        need(id_, issue(id_), customer={"name": name, "domains": []}) for id_, name in (
            ("space", " ACME   CORP "), ("tab", "Acme\tCorp"), ("suffix", "Acme Corps"),
            ("order", "Corp Acme"), ("first-only", "Acme Only"), ("last-only", "Only Corp"),
            ("unrelated", "Other Company"))]])
    records = linear.context(fake, [], [], [], ["Acme Corp"])["issues"]
    assert fake.candidates == ["space", "tab", "suffix", "order"]
    assert [record["id"] for record in records] == ["space", "tab"]
    assert all(record["reasons"] == ["customer: acme corp"] for record in records)


@pytest.mark.parametrize("source", ["description", "content", "body"])
@pytest.mark.parametrize("text, matched", [
    ("**ana@gmail.com**", True),
    ("_ana@gmail.com_", True),
    ("`ana@gmail.com`", True),
    ("*ana@gmail.com*", True),
    ("__ana@gmail.com__", True),
    ("***ana@gmail.com***", True),
    ("**_ana@gmail.com_**", True),
    ("~~ana@gmail.com~~", True),
    ("[**ana@gmail.com**](https://example.com/contact)", True),
    ("[Email Ana](mailto:ana@gmail.com)", True),
    ("<ana@gmail.com>", True),
    ("```text\nana@gmail.com\n```", True),
    ("**x!ana@gmail.com**", False),
    ("_xana@gmail.com_", False),
    ("`ana+extra@gmail.com`", False),
    ("**ana@gmail.com.evil**", False),
    ("_ana@gmail.com+extra_", False),
    ("`ana@gmail.com_extra`", False),
    ("**ana@gmail.com**suffix", False),
    ("**ana@gmail.com**+extra", False),
    ("_ana@gmail.com_.evil", False),
    ("x!**ana@gmail.com**", False),
    ("x!_ana@gmail.com_", False),
    ("x!`ana@gmail.com`", False),
    ("**x!****ana@gmail.com**", False),
    ("`*ana@gmail.com*`", False),
])
def test_markdown_email_matching_uses_exact_boundaries_on_all_text_sources(source, text, matched):
    email = "ana@gmail.com"
    # The longer address satisfies the remote substring query, but never matches locally.
    target = issue("a", f"Candidate: x{email}", description=text if source == "description" else None)
    request = need("formatted", target, text if source == "content" else "", body=text if source == "body" else None)
    fake = FakeLinear(issues=[[target]], needs=[[request]] if source != "description" else [])
    records = linear.context(fake, [email], [])["issues"]
    assert bool(records) is matched
    if records:
        assert records[0]["reasons"] == [f"email: {email} ({'description' if source == 'description' else 'request'})"]
        if source != "description":
            assert records[0]["requests"] == [{"id": "formatted", "text": " ".join(text.split()), "customer": ""}]
    assert target["description"] == (text if source == "description" else None)
    if source != "description":
        assert request[source] == text


@pytest.mark.parametrize("source", ["content", "body"])
def test_formatted_request_excerpt_keeps_source_markup_and_match_position(source):
    email = "ana@gmail.com"
    target = issue("a", f"Candidate: x{email}")
    text = "Unrelated prefix. " * 40 + f"Need **{email}** to approve rollout. " + "Other detail. " * 40
    request = need("long", target, **{source: text})
    fake = FakeLinear(issues=[[target]], needs=[[request]])
    [record] = linear.context(fake, [email], [])["issues"]
    excerpt = record["requests"][0]["text"]
    assert len(excerpt) <= 240 and f"**{email}**" in excerpt
    assert excerpt.startswith("…") and excerpt.endswith("…")
    assert record["reasons"] == [f"email: {email} (request)"]
    assert request[source] == text


def test_requests_use_effective_content_body_fallback_and_relevant_bounded_excerpts():
    target = issue("a", EMAIL)
    long_text = "Unrelated prefix. " * 40 + f"Need {EMAIL} to approve rollout. " + "Other detail. " * 40
    fake = FakeLinear(issues=[[target]], needs=[[
        need("long", target, long_text), need("body", target, None, body=f"Body for {EMAIL}"),
        need("blank", target, " \n ", body=f"Fallback for {EMAIL}"),
        need("effective", target, "Unrelated effective content", body=f"Superseded {EMAIL}"),
        need("irrelevant", target, "A different customer's request"),
    ]])
    [record] = linear.context(fake, [EMAIL], [])["issues"]
    requests = {request["id"]: request for request in record["requests"]}
    assert set(requests) == {"long", "body", "blank"}
    assert requests["body"]["text"] == f"Body for {EMAIL}"
    assert requests["blank"]["text"] == f"Fallback for {EMAIL}"
    assert len(requests["long"]["text"]) <= 240 and EMAIL in requests["long"]["text"]
    assert requests["long"]["text"].startswith("…") and requests["long"]["text"].endswith("…")
    assert all(request["customer"] == "" for request in requests.values())


def test_empty_inputs_do_not_query_and_no_unbounded_customer_request_scan():
    fake = FakeLinear()
    assert linear.context(fake, [], [], [], []) == {"issues": []}
    assert fake.calls == []
    assert linear.context(fake, [EMAIL], []) == {"issues": []}
    assert [document for document, _ in fake.calls] == [linear.ISSUES]


@pytest.mark.parametrize("field", ["issues", "customerNeeds"])
@pytest.mark.parametrize("cursors", [[None], [""], [123], ["same", "same"], ["a", "b", "a"]])
def test_pagination_rejects_missing_nonadvancing_and_cyclic_cursors(field, cursors):
    class Broken:
        def __init__(self):
            self.calls = 0

        def query(self, document, variables):
            current = "issues" if document == linear.ISSUES else "customerNeeds"
            if current != field:
                return {current: {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}}
            cursor = cursors[self.calls]
            self.calls += 1
            return {current: {"nodes": [], "pageInfo": {"hasNextPage": True, "endCursor": cursor}}}

    with pytest.raises(linear.LinearError, match="pagination did not advance"):
        linear.context(Broken(), [], [DOMAIN])


@pytest.mark.parametrize("connection", [{}, {"nodes": {}}, {"nodes": [], "pageInfo": {}},
                                         {"nodes": [None], "pageInfo": {"hasNextPage": False}}])
def test_malformed_connection_fails_visibly(connection):
    class Broken:
        def query(self, document, variables):
            return {"issues": connection}

    with pytest.raises(linear.LinearError, match="Linear returned"):
        linear.context(Broken(), [EMAIL], [])


@pytest.mark.parametrize("url", ["http://linear.app/x", "https://linear.app.evil/x", "https://linear.app@evil.com/x",
                                "https://evil.com@linear.app/x", "https://linear.app/x][link=https://evil.com]",
                                "https://linear.app/x\nnext", "https://linear.app/x\\y", "javascript:alert(1)", None])
def test_issue_links_reject_foreign_targets_and_markup_or_control_injection(url):
    fake = FakeLinear(issues=[[issue("a", EMAIL, url=url)]])
    assert linear.context(fake, [EMAIL], [])["issues"][0]["url"] == ""


def test_context_cache_normalizes_all_name_inputs_and_invalidates_renames_and_ambiguity(monkeypatch):
    calls, clock = [], [0.0]

    def fetch(client, *keys):
        calls.append(keys)
        return {"issues": []}

    monkeypatch.setattr(linear, "context", fetch)
    cached = linear.Context(object(), clock=lambda: clock[0])
    cached.get([EMAIL.upper(), EMAIL], [DOMAIN.upper()], [" Ana  Reed ", "ANA REED"], ["ACME CORP"])
    cached.get([EMAIL], [DOMAIN], ["ana reed"], ["acme corp"])
    assert len(calls) == 1
    assert calls[0] == ((EMAIL,), (DOMAIN,), ("ana reed",), ("acme corp",))
    cached.get([EMAIL], [DOMAIN], ["Ana Smith"], ["Acme Corp"])
    cached.get([EMAIL], [DOMAIN], [], ["Acme Corp"])  # name became ambiguous locally
    cached.get([EMAIL], [DOMAIN], ["Acme Corp"], [])  # separate Person/Organization key positions
    cached.get([EMAIL], [DOMAIN], [], ["Acme Renamed"])
    assert len(calls) == 5
    clock[0] = tana.TTL - 1
    cached.get([EMAIL], [DOMAIN], ["Ana Reed"], ["Acme Corp"])
    assert len(calls) == 5
    clock[0] = tana.TTL
    cached.get([EMAIL], [DOMAIN], ["Ana Reed"], ["Acme Corp"])
    assert len(calls) == 6


def test_cache_preserves_two_argument_providers_and_does_not_cache_failures():
    calls = []

    def fetch(client, emails, domains):
        calls.append((emails, domains))
        if len(calls) == 1:
            raise linear.LinearError("Linear GraphQL query failed")
        return {"ok": True}

    cached = tana.Context(object(), clock=lambda: 0, fetch=fetch)
    with pytest.raises(linear.LinearError):
        cached.get([EMAIL], [DOMAIN])
    assert cached.get([EMAIL.upper()], [DOMAIN.upper()]) == {"ok": True}
    assert cached.get([EMAIL], [DOMAIN]) == {"ok": True}
    assert calls == [((EMAIL,), (DOMAIN,)), ((EMAIL,), (DOMAIN,))]
