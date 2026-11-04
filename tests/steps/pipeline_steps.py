"""Step definitions for features/pipeline.feature: the Revenue Pipeline holds Organizations, Fundraising and Talent
hold People (ADR 0016)."""

from pytest_bdd import given, parsers, then, when

from comms_terminal import inbox, pipeline

from .inbox_steps import email_in


@given(parsers.parse('a Person "{name}" with address "{addr}"'))
def named_person(ctx, name, addr):
    inbox.ingest(ctx.db, email_in(ctx, addr))
    pid = pipeline.find_person(ctx.db, addr)["id"]
    with ctx.db:
        ctx.db.execute("UPDATE persons SET display_name = ? WHERE id = ?", (name, pid))


@when(parsers.parse('Jeremy sets the Fundraising Stage of "{addr}" to "{stage}" and the Value to "{value}"'))
def set_person(ctx, addr, stage, value):
    pid = pipeline.find_person(ctx.db, addr)["id"]
    pipeline.set_stage(ctx.db, pid, pipeline.FUNDRAISING, stage)
    pipeline.set_value(ctx.db, pid, pipeline.FUNDRAISING, value)


@when(parsers.parse('Jeremy sets the Revenue Stage of the Organization "{domain}" to "{stage}"'))
def set_org(ctx, domain, stage):
    pipeline.set_stage(ctx.db, pipeline.find_org(ctx.db, domain)["id"], pipeline.REVENUE, stage)


def _cards(ctx, p):
    m = pipeline.math(ctx.db, p)
    return {o["name"]: (r["stage"], o) for r in m["stages"] + m["ending"] for o in r["orgs"]}


@then(parsers.parse('the Fundraising Pipeline shows "{name}" at "{stage}" with Value {value:d} and Organizations "{orgs}"'))
def shows_person(ctx, name, stage, value, orgs):
    at, o = _cards(ctx, pipeline.FUNDRAISING)[name]
    assert (at, o["value"], o["person"], o["organizations"]) == (stage, value, True, orgs.split(", "))


@then(parsers.parse('the Organization "{domain}" holds no Fundraising Stage'))
def org_none(ctx, domain):
    assert pipeline.FUNDRAISING not in pipeline.current(ctx.db, pipeline.find_org(ctx.db, domain)["id"])


@then(parsers.parse('the Revenue Pipeline shows "{domain}" at "{stage}"'))
def shows_org(ctx, domain, stage):
    at, o = _cards(ctx, pipeline.REVENUE)[pipeline.find_org(ctx.db, domain)["name"]]
    assert (at, o["person"], o["domain"]) == (stage, False, domain)
