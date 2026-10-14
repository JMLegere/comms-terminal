"""Attio, a read-only Context source (FR-13b, decision #123).

The service reads Attio's REST API with a read-only workspace access token (the `attio-token` credential)
and answers the console's POST /attio with what Attio knows about a page's email addresses and Organization
domains: the matching People and Companies with every filled field (less a short list of noise, `SKIP`),
the lists they are on with those entries' fields, their deals that are not Lost, and the open tasks on any of
them (most of Jeremy's tasks hang off a deal).
Nothing is written to Attio. Answers are cached for five minutes, like Tana's.
"""

import json
from datetime import datetime
import urllib.error
import urllib.request

from . import tana
from .mcp import USER_AGENT

API = "https://api.attio.com/v2"
# Fields that repeat what the page already shows, or say little: identifiers, images, audit stamps, the
# legacy strength score, social handles, and the first and per-channel interactions (last and next stay).
SKIP = {"record_id", "name", "email_addresses", "domains", "avatar_url", "logo_url", "created_at", "created_by",
        "associated_deals", "associated_people", "associated_company", "team", "strongest_connection_strength_legacy",
        "twitter_follower_count", "angellist", "facebook", "instagram", "twitter", "first_interaction",
        "first_email_interaction", "first_calendar_interaction", "last_email_interaction",
        "last_calendar_interaction", "next_calendar_interaction", "entry_id", "parent_record", "parent_object"}
CLOSED = {"Lost"}  # deal stages that are not open


class AttioError(Exception):
    pass


class Client:
    def __init__(self, token: str, opener=urllib.request.urlopen):
        self.token, self.opener, self.schema, self.members, self.list_names = token, opener, {}, None, None

    def call(self, path, body=None):
        req = urllib.request.Request(API + path, json.dumps(body).encode() if body is not None else None,
                                     {"Authorization": f"Bearer {self.token}", "User-Agent": USER_AGENT,
                                      "Content-Type": "application/json"}, method="POST" if body is not None else "GET")
        try:
            with self.opener(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            raise AttioError(f"Attio {e.code} on {path.split('?')[0]}") from e

    def titles(self, kind, slug) -> dict:
        """Attribute titles of an object or list (kind "objects" or "lists"), read once per process."""
        # ponytail: cached for the service's life; a renamed field shows after a restart
        if (kind, slug) not in self.schema:
            self.schema[kind, slug] = {a["api_slug"]: a["title"] for a in self.call(f"/{kind}/{slug}/attributes")["data"]}
        return self.schema[kind, slug]

    def member(self, member_id) -> str:
        if self.members is None:
            self.members = {m["id"]["workspace_member_id"]: f"{m['first_name']} {m['last_name']}".strip()
                            for m in self.call("/workspace_members")["data"]}
        return self.members.get(member_id, "someone")

    def list_name(self, slug) -> str:
        if self.list_names is None:
            self.list_names = {x["api_slug"]: x["name"] for x in self.call("/lists")["data"]}
        return self.list_names.get(slug, slug)


def _day(value) -> str:
    """"Oct 7, 2026" for a date or timestamp; the year alone for a January 1 (Attio's unknown month and day)."""
    try:
        d = datetime.fromisoformat(str(value)[:10])
    except ValueError:
        return str(value or "")
    return str(d.year) if (d.month, d.day) == (1, 1) else f"{d:%b} {d.day}, {d.year}"


def _one(client, v: dict, names: dict) -> str:
    t = v.get("attribute_type")
    if t == "personal-name":
        return v.get("full_name") or ""
    if t in ("select", "status"):
        return (v.get("option") or v.get("status") or {}).get("title", "")
    if t == "actor-reference":
        return client.member(v.get("referenced_actor_id")) if v.get("referenced_actor_type") == "workspace-member" else ""
    if t == "record-reference":
        return names.get(v.get("target_record_id"), "")
    if t == "currency":
        return f"{v.get('currency_code') or ''} {v.get('currency_value'):,.0f}".strip() if v.get("currency_value") is not None else ""
    if t == "location":
        return ", ".join(x for x in (v.get("locality"), v.get("region"), v.get("country_code")) if x)
    if t == "interaction":
        return " · ".join(x for x in (v.get("interaction_type"), _day(v.get("interacted_at"))) if x)
    if t == "email-address":
        return v.get("email_address", "")
    if t == "phone-number":
        return v.get("original_phone_number") or v.get("phone_number", "")
    if t == "domain":
        return v.get("domain", "")
    if t == "checkbox":
        return "yes" if v.get("value") else "no"
    if t in ("timestamp", "date"):
        return _day(v.get("value"))
    return str(v.get("value") if v.get("value") is not None else "")


def _fields(client, values: dict, titles: dict, names: dict) -> list[list[str]]:
    out = []
    for slug, vs in values.items():
        text = ", ".join(x for x in (_one(client, v, names) for v in vs) if x)
        if slug not in SKIP and text:
            out.append([titles.get(slug, slug), text])
    return out


def _title(values: dict) -> str:
    v = (values.get("name") or [{}])[0]
    return v.get("full_name") or v.get("value") or (values.get("domains") or [{}])[0].get("domain") or "untitled"


def _query(client, obj, filter_) -> list[dict]:
    return client.call(f"/objects/{obj}/records/query", {"filter": filter_, "limit": 25})["data"]


def _lists(client, obj, rid, names) -> list[list[str]]:
    """The lists a record is on, each as one field: "List: field value · field value"."""
    out = []
    for e in client.call(f"/objects/{obj}/records/{rid}/entries")["data"]:
        slug, eid = e["list_api_slug"], e["entry_id"]
        entry = client.call(f"/lists/{slug}/entries/{eid}")["data"]
        fields = _fields(client, entry.get("entry_values", {}), client.titles("lists", slug), names)
        out.append([client.list_name(slug), " · ".join(f"{k}: {v}" for k, v in fields) or "on the list"])
    return out


def context(client, emails, domains) -> dict:
    """Everything the Attio section shows, as plain JSON."""
    people = {p["id"]["record_id"]: p for e in emails for p in _query(client, "people", {"email_addresses": e})}
    companies = {c["id"]["record_id"]: c for d in domains for c in _query(client, "companies", {"domains": d})}
    names = {rid: _title(r["values"]) for rid, r in {**people, **companies}.items()}
    deals = {}
    for obj, ids, attr in (("companies", companies, "associated_company"), ("people", people, "associated_people")):
        for rid in ids:
            for d in _query(client, "deals", {attr: {"target_object": obj, "target_record_id": rid}}):
                deals[d["id"]["record_id"]] = d
    names |= {rid: _title(d["values"]) for rid, d in deals.items()}

    def record(obj, r):
        rid = r["id"]["record_id"]
        return {"id": rid, "title": _title(r["values"]), "url": r.get("web_url"),
                "fields": _fields(client, r["values"], client.titles("objects", obj), names) + _lists(client, obj, rid, names)}

    open_deals = []
    for rid, d in deals.items():
        stage = _one(client, (d["values"].get("stage") or [{}])[0], names)
        if stage in CLOSED:
            continue
        v = d["values"]
        open_deals.append({"id": rid, "title": _title(v), "url": d.get("web_url"), "stage": stage,
                           "value": _one(client, (v.get("value") or [{}])[0], names),
                           "owner": _one(client, (v.get("owner") or [{}])[0], names),
                           "lists": [x[0] for x in _lists(client, "deals", rid, names)]})
    tasks, open_ids = {}, {d["id"] for d in open_deals}
    for obj, ids in (("people", people), ("companies", companies), ("deals", {k: deals[k] for k in open_ids})):
        for rid in ids:
            for t in client.call(f"/tasks?linked_object={obj}&linked_record_id={rid}&is_completed=false&limit=50")["data"]:
                tasks[t["id"]["task_id"]] = {
                    "id": t["id"]["task_id"], "title": t.get("content_plaintext") or "untitled",
                    "due": (t.get("deadline_at") or "")[:10],
                    "assignees": [client.member(a.get("referenced_actor_id")) for a in t.get("assignees") or []
                                  if a.get("referenced_actor_type") == "workspace-member"],
                    "url": (people.get(rid) or companies.get(rid) or deals.get(rid) or {}).get("web_url")}
    return {"people": [record("people", p) for p in people.values()],
            "companies": [record("companies", c) for c in companies.values()],
            "deals": open_deals, "tasks": sorted(tasks.values(), key=lambda t: t["due"] or "9999")}


def Context(client, clock=None):  # noqa: N802  the service's cached answers, the same cache as Tana's
    return tana.Context(client, **({"clock": clock} if clock else {}), fetch=context)
