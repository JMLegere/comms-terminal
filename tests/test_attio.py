"""Attio, a read-only Context source (FR-13b): matching, fields, open tasks, and the section."""

from comms_terminal import attio, serve

ME = "m1"


def val(t, **kw):
    return {"attribute_type": t, **kw}


PERSON = {"id": {"record_id": "p1"}, "web_url": "https://app.attio.com/x/person/p1", "values": {
    "name": [val("personal-name", full_name="Laila Theriault")],
    "email_addresses": [val("email-address", email_address="laila@nbif.ca")],
    "company": [val("record-reference", target_object="companies", target_record_id="c1")],
    "job_title": [val("text", value="Investment Director")],
    "avatar_url": [val("text", value="https://img")],
    "last_interaction": [val("interaction", interaction_type="meeting", interacted_at="2026-10-07T16:00:00Z")],
    "strongest_connection_user": [val("actor-reference", referenced_actor_type="workspace-member", referenced_actor_id=ME)],
    "description": []}}
COMPANY = {"id": {"record_id": "c1"}, "web_url": "https://app.attio.com/x/company/c1", "values": {
    "name": [val("text", value="NBIF")], "domains": [val("domain", domain="nbif.ca")],
    "categories": [val("select", option={"title": "Venture Capital"}), val("select", option={"title": "B2B"})]}}


class FakeAttio:
    def __init__(self):
        self.calls = []

    def call(self, path, body=None):
        self.calls.append((path, body))
        if path == "/objects/people/records/query":
            return {"data": [PERSON] if body["filter"] == {"email_addresses": "laila@nbif.ca"} else []}
        if path == "/objects/companies/records/query":
            return {"data": [COMPANY] if body["filter"] == {"domains": "nbif.ca"} else []}
        if path.startswith("/objects/") and path.endswith("/entries"):
            return {"data": [{"list_api_slug": "customer_success_1", "entry_id": "e1"}] if "/c1/" in path else []}
        if path == "/lists/customer_success_1/entries/e1":
            return {"data": {"entry_values": {"stage": [val("status", status={"title": "Steady"})],
                                              "health": [val("select", option={"title": "Good"})]}}}
        if path.endswith("/attributes"):
            return {"data": [{"api_slug": "job_title", "title": "Job title"}, {"api_slug": "company", "title": "Company"},
                             {"api_slug": "stage", "title": "Stage"}, {"api_slug": "health", "title": "Health"},
                             {"api_slug": "categories", "title": "Categories"},
                             {"api_slug": "last_interaction", "title": "Last interaction"},
                             {"api_slug": "strongest_connection_user", "title": "Strongest connection"}]}
        if path == "/lists":
            return {"data": [{"api_slug": "customer_success_1", "name": "Paying Customers"}]}
        if path == "/workspace_members":
            return {"data": [{"id": {"workspace_member_id": ME}, "first_name": "Jeremy", "last_name": "Legere"}]}
        if path.startswith("/tasks?"):
            assert "is_completed=false" in path
            return {"data": [{"id": {"task_id": "t1"}, "content_plaintext": "Send the model", "deadline_at": "2026-10-20T00:00:00Z",
                              "assignees": [{"referenced_actor_type": "workspace-member", "referenced_actor_id": ME}]}]
                    if "linked_record_id=c1" in path else []}
        raise AssertionError(path)


def test_context_summarizes_people_companies_and_tasks():
    fake = FakeAttio()
    client = attio.Client("t")
    client.call = fake.call
    ctx = attio.context(client, ["laila@nbif.ca"], ["nbif.ca"])
    (p,), (c,) = ctx["people"], ctx["companies"]
    assert p["title"] == "Laila Theriault" and p["url"].endswith("/p1")
    assert p["fields"] == [["Company", "NBIF"], ["Job title", "Investment Director"],
                           ["Last interaction", "meeting · Oct 7, 2026"], ["Strongest connection", "Jeremy Legere"]]
    assert c["fields"] == [["Categories", "Venture Capital, B2B"], ["Paying Customers", "Stage: Steady · Health: Good"]]
    assert ctx["tasks"] == [{"id": "t1", "title": "Send the model", "due": "2026-10-20", "assignees": ["Jeremy Legere"],
                             "url": COMPANY["web_url"]}]
    assert all(body is None or "filter" in body for _, body in fake.calls)  # reads only: GETs and record queries


def test_section_and_service_route():
    from comms_terminal.tui import attio_markup
    fake = FakeAttio()
    client = attio.Client("t")
    client.call = fake.call
    text = attio_markup(attio.context(client, ["laila@nbif.ca"], ["nbif.ca"]))
    for part in ("Laila Theriault", "Investment Director", "paying customers",
                 "Send the model", "due 2026-10-20", "  [#928374]last contact[/]", "  meeting · Oct 7, 2026"):
        assert part in text
    assert "nothing in Attio" in attio_markup({"people": [], "companies": [], "tasks": []})
    assert serve.handle_context(b"{}", None, "Attio") == (503, {"error": "Attio is not connected"})


def test_section_survives_a_quote_in_a_field_url():
    from textual.content import Content
    from comms_terminal.tui import attio_markup
    url = "https://linkedin.com/company/st-mary's-entertainment-centre"
    ctx = {"people": [], "tasks": [], "companies": [{"title": "St Mary's", "url": None, "fields": [["LinkedIn", url]]}]}
    markup = attio_markup(ctx, width=80)
    Content.from_markup(markup)  # raised MarkupError when the quote ended the link value
    assert f"[link='{url.replace(chr(39), '%27')}']" in markup
