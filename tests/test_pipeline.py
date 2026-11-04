"""Pipelines (.agents/prd/2026-10-23-pipeline.md): the log, undo, and the Bowtie math worked by hand.
Times are fixed: Friday 2026-10-23, 13:00 in Halifax; Revenue Target CAD 1M TTM by 2028-09-30 (343 days to
the 2027-10-01 Impact deadline). Math: WbD's Bowtie Standard v1.0."""

from datetime import date, datetime, timedelta, timezone

import pytest

from comms_terminal import clock, inbox, pipeline as pl

NOW = datetime(2026, 10, 23, 16, 0, tzinfo=timezone.utc)
DAY = timedelta(days=1)
R = pl.REVENUE
WEEKS = 343 / 7


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(clock, "now", lambda: NOW)
    db = inbox.connect()
    inbox.migrate(db)
    return db


def org(db, domain, name=None):
    with db:
        return db.execute("INSERT INTO organizations (domain, name, created_at) VALUES (?, ?, ?) RETURNING id",
                          (domain, name or domain.split(".")[0].title(), NOW.isoformat())).fetchone()[0]


def walk(db, o, *steps, value=None):
    for days_ago, stage in steps:
        pl.set_stage(db, o, R, stage, at=NOW - days_ago * DAY)
    if value is not None:
        pl.set_value(db, o, R, value, at=NOW - 1 * DAY)


@pytest.fixture
def five(db):
    """A skips Prioritization; D skips Awareness and pays; E is Lost; C has no Value."""
    a, b, c, d, e = (org(db, f"{x}.com") for x in "abcde")
    walk(db, a, (60, "Awareness"), (50, "Education"), (30, "Selection"), (10, "Commit"), value="60k")
    walk(db, b, (80, "Awareness"), (70, "Education"), value=30_000)
    walk(db, c, (40, "Awareness"))
    walk(db, d, (85, "Education"), (75, "Prioritization"), (60, "Selection"), (45, "Commit"), (40, "Onboarding"),
         (20, "Impact"), value="120k")
    walk(db, e, (88, "Awareness"), (70, "Lost"))
    pl.set_target(db, R, "1m", "2028-09-30")
    return db


def test_seeding_history_and_current_stage(db):
    o = org(db, "ironmaple.example", "Iron Maple")
    pl.set_stage(db, o, R, "education", at="2026-08-03")
    pl.set_stage(db, o, R, "Selection", at="2026-09-12")
    pl.set_value(db, o, R, "90k")
    now = pl.current(db, o)[R]
    assert (now["stage"], now["value"]) == ("Selection", 90_000)
    assert (NOW - now["since"]).days == 41
    assert [(h["kind"], h["old"], h["new"]) for h in pl.history(db, o)] == [
        ("VALUE", None, 90_000), ("STAGE", "Education", "Selection"), ("STAGE", None, "Education")]
    assert pl.find_org(db, "iron maple")["id"] == o


def test_refusals(db):
    o = org(db, "acme.com")
    with pytest.raises(pl.Refused, match="Awareness, Education"):
        pl.set_stage(db, o, R, "Discovery")
    with pytest.raises(pl.Refused, match="future"):
        pl.set_stage(db, o, R, "Awareness", at=NOW + DAY)
    with pytest.raises(pl.Refused, match="revenue, fundraising, or talent"):
        pl.parse_pipeline("partners")
    with pytest.raises(pl.Refused):
        pl.find_org(db, "nobody.com")
    assert [pl.parse_amount(x) for x in ("90000", "90,000", "90k", "1.2m", "CAD 45k")] == \
        [90_000, 90_000, 90_000, 1_200_000, 45_000]


def test_log_is_append_only(db):
    o = org(db, "acme.com")
    e = pl.set_stage(db, o, R, "Awareness")
    with pytest.raises(Exception, match="append-only"):
        db.execute("UPDATE pipeline_log SET stage = 'Commit' WHERE id = ?", (e,))
    with pytest.raises(Exception, match="append-only"):
        db.execute("DELETE FROM pipeline_log WHERE id = ?", (e,))


def test_undo_hides_the_change_from_history_and_math(five):
    a = pl.find_org(five, "a.com")["id"]
    before = pl.math(five, R)
    e = pl.set_stage(five, a, R, "Onboarding")
    pl.undo(five, e)
    assert pl.current(five, a)[R]["stage"] == "Commit"
    assert "Onboarding" not in [h["new"] for h in pl.history(five, a)]
    assert pl.math(five, R)["stages"] == before["stages"]
    with pytest.raises(pl.Refused, match="already undone"):
        pl.undo(five, e)


def test_math_by_hand(five):
    m = pl.math(five, R)
    rows = {r["stage"]: r for r in m["stages"]}
    # Conversion rate: reached the next Stage / reached this one (a skip counts as passing)
    assert {s: r["rate"] for s, r in rows.items()} == pytest.approx(
        {"Awareness": 3 / 5, "Education": 2 / 3, "Prioritization": 1, "Selection": 1, "Commit": 1 / 2,
         "Onboarding": 1})
    # Time in Stage: median days; a skipped Stage is 0, a still-open one runs to now, Lost ends it
    assert {s: r["time"] for s, r in rows.items()} == pytest.approx(
        {"Awareness": 10, "Education": 20, "Prioritization": 7.5, "Selection": 17.5, "Commit": 7.5,
         "Onboarding": 20})
    assert rows["Awareness"]["to_paying"] == pytest.approx(0.2)
    # Every move is inside 90 days, so each 2-year Benchmark equals its rate
    assert all(r["benchmark"] == r["rate"] for r in rows.values())
    s = m["summary"]
    assert (s["paying"], s["paying_count"], s["average"]) == (120_000, 1, 120_000)
    # Nobody paid 90 days or 2 years ago: no GRR, expansion, or NRR, so paying Value is kept as is
    assert s["grr"] == (None, False) and s["expansion"] == (None, False) and s["nrr"] is None
    assert s["kept"] == 120_000
    assert s["weighted"] == pytest.approx(60_000 * 0.5 + 30_000 / 3)  # C has no Value
    gap = 1_000_000 - s["kept"] - s["weighted"]
    assert (s["gap"], s["customers"], s["deadline"], s["weeks"]) == (
        pytest.approx(gap), -(-gap // 120_000), date(2027, 10, 1), pytest.approx(WEEKS))
    # No 90-day rate sits below its 2-year Benchmark, so the lever is new Awareness Organizations
    top = rows["Awareness"]
    assert s["constraint"] == {"kind": "volume", "stage": "Awareness", "next": "Education",
                               "getting": top["entries"], "need": top["need"], "lift": top["need"] / top["entries"]}
    n = s["customers"]
    # need = customers / rates through the paying Stage / (weeks left - Time in the Stages ahead)
    assert rows["Onboarding"]["need"] == pytest.approx(n / 1 / (WEEKS - 20 / 7))
    assert rows["Commit"]["need"] == pytest.approx(n / 0.5 / (WEEKS - 27.5 / 7))
    assert rows["Awareness"]["need"] == pytest.approx(n / 0.2 / (WEEKS - 82.5 / 7))
    assert rows["Awareness"]["entries"] == pytest.approx(5 / (90 / 7))
    assert rows["Awareness"]["pace"] == pytest.approx(rows["Awareness"]["entries"] - rows["Awareness"]["need"])
    assert [(r["stage"], [o["domain"] for o in r["orgs"]]) for r in m["ending"]] == [
        ("Impact", ["d.com"]), ("Expansion", []), ("Lost", ["e.com"]), ("Churned", [])]
    assert [o["domain"] for o in rows["Awareness"]["orgs"]] == ["c.com"]


def test_retention_and_expansion_from_value_changes(db):
    """CR7 (GRR) and CR8 (expansion) for customers paying at the window's start, annualized: the last 90 days,
    against the last 2 years as the Benchmark."""
    p, q, r = org(db, "p.com"), org(db, "q.com"), org(db, "r.com")
    for o, value in ((p, 100_000), (q, 50_000), (r, 50_000)):
        pl.set_stage(db, o, R, "Impact", at=NOW - 800 * DAY)
        pl.set_value(db, o, R, value, at=NOW - 800 * DAY)
    pl.set_value(db, p, R, "120k", at=NOW - 100 * DAY)  # expanded, before the 90 days
    pl.set_stage(db, q, R, "Churned", at=NOW - 50 * DAY)  # gone
    pl.set_value(db, r, R, "30k", at=NOW - 20 * DAY)  # downgraded
    m = pl.math(db, R)
    s, impact, expansion = m["summary"], m["ending"][0], m["ending"][1]
    assert s["grr"] == (pytest.approx((150 / 220) ** (365 / 90)), False)  # 120k + 0 + 30k of 220k
    assert s["expansion"] == (pytest.approx(0), False)
    assert s["nrr"] == pytest.approx((150 / 220) ** (365 / 90))
    assert impact["benchmark"] == pytest.approx((130 / 200) ** 0.5)  # 100k + 0 + 30k of 200k, over 2 years
    assert expansion["benchmark"] == pytest.approx((1 + 20 / 130) ** 0.5 - 1)
    # GRR back to 2 years lifts kept Value ×3.8; expansion back lifts it only ×1.07
    assert s["constraint"]["stage"] == "GRR" and s["constraint"]["lift"] == pytest.approx(
        (130 / 200) ** 0.5 / (150 / 220) ** (365 / 90))


@pytest.fixture
def thin(db):
    """One customer paying since before the 2-year window; one new Organization stuck in Education."""
    y, x = org(db, "y.com"), org(db, "x.com")
    walk(db, y, (400, "Impact"))
    pl.set_value(db, y, R, "100k", at=NOW - 400 * DAY)
    walk(db, x, (20, "Awareness"), (10, "Education"))
    pl.set_target(db, R, 1_000_000, date(2028, 9, 30))
    return db


def test_two_year_rates_fill_missing_rates(thin):
    m = pl.math(thin, R)
    rows = {r["stage"]: r for r in m["stages"]}
    # Only X moved in 90 days; Y's jump to Impact 400 days ago gives the 2-year rates
    assert rows["Prioritization"]["rate"] is None and rows["Prioritization"]["used"] == 1
    assert rows["Prioritization"]["assumed"] and rows["Prioritization"]["to_paying"] is None  # never mixed
    assert m["summary"]["grr"] == (1.0, False) and m["summary"]["expansion"] == (0.0, False)
    assert (m["summary"]["kept"], m["summary"]["customers"]) == (pytest.approx(100_000), 9)
    assert rows["Prioritization"]["need"] == pytest.approx(9 / 1 / (WEEKS - 4 / 7))
    assert rows["Education"]["need"] is None  # measured 0%: nobody gets through
    assert m["summary"]["constraint"] == {"kind": "rate", "stage": "Education", "next": "Prioritization", "rate": 0,
                                          "benchmark": 0.5, "lift": float("inf")}  # 0% offers any lift
    out = pl.markdown(m)
    assert "| Prioritization | 0 | 100%b | 100% |" in out and "NRR 100%" in out


def test_late_and_no_target(thin):
    m = pl.math(thin, pl.FUNDRAISING)
    assert m["summary"]["target"] is None
    assert "No Target yet: comms-terminal pipeline target fundraising" in pl.markdown(m)
    pl.set_target(thin, R, "1m", "2027-10-25")  # Impact deadline 2026-10-26: three days left
    rows = {r["stage"]: r for r in pl.math(thin, R)["stages"]}
    assert rows["Onboarding"]["need"] != "late" and rows["Prioritization"]["need"] == "late"


def test_on_track_and_coverage(five):
    m = pl.math(five, R)
    s, top = m["summary"], m["stages"][0]
    assert s["coverage"] == pytest.approx(s["weighted"] / (1_000_000 - s["kept"]))
    assert pl.on_track(m) == ("Behind pace", f"need {pl.num(top['need'])} new Awareness Organizations a week,"
                                             f" getting {pl.num(top['entries'])}")
    pl.set_target(five, R, "100k", "2028-09-30")  # customers and open accounts already cover it
    m = pl.math(five, R)
    assert m["summary"]["coverage"] is None and pl.on_track(m)[0] == "On track"
    pl.set_target(five, R, "1m", "2027-10-25")  # too close for new Awareness accounts
    assert pl.on_track(pl.math(five, R))[0] == "Off track"


def test_this_week_bullets(five):
    m = pl.math(five, R)
    top = m["stages"][0]
    assert m["summary"]["constraint"]["kind"] == "volume"
    assert pl.constraint_text(m["summary"]["constraint"]) == \
        f"New Awareness Organizations: {pl.num(top['entries'])} a week, need {pl.num(top['need'])}"
    assert pl.this_week(m) == [
        (f"Add {-(-(top['need'] - top['entries']) // 1):.0f} Organizations to Awareness",
         f"need {pl.num(top['need'])} a week, getting {pl.num(top['entries'])}, the constraint"),
        ("Set a Value for C", "the math leaves them out until then")]


def test_this_week_moves_the_accounts_sitting_in_the_constraint(db):
    a, b, c = org(db, "a.com", "Acme"), org(db, "b.com", "Bolt"), org(db, "c.com", "Cove")
    walk(db, a, (40, "Selection"), (20, "Commit"), (10, "Onboarding"), value="10k")
    walk(db, b, (30, "Selection"), value="10k")
    walk(db, c, (300, "Onboarding"), (200, "Impact"))
    m = pl.math(db, R)  # Onboarding: 0 of 1 reached Impact in 90 days, against 1 of 2 over 2 years
    assert m["summary"]["constraint"] == {"kind": "rate", "stage": "Onboarding", "next": "Impact", "rate": 0,
                                          "benchmark": 0.5, "lift": float("inf")}  # no Target: no volume lever
    assert pl.constraint_text(m["summary"]["constraint"]) == "Onboarding → Impact: 0% vs 50% benchmark"
    assert pl.this_week(m)[:2] == [("Set a Target", "comms-terminal pipeline target revenue AMOUNT --by DATE"),
                                   ("Move Acme", "Onboarding → Impact, the constraint")]


def test_cli(tmp_path, capsys):
    from comms_terminal import main
    path = str(tmp_path / "inbox.db")
    run = lambda *argv: main.main(["--db", path, "pipeline", *argv])  # noqa: E731
    run("revenue")
    assert "No Target yet: comms-terminal pipeline target revenue" in capsys.readouterr().out
    db = inbox.connect(path)
    with db:
        db.execute("INSERT INTO organizations (domain, name, created_at) VALUES ('stmec.example', 'STMEC', ?)",
                   (NOW.isoformat(),))
    run("target", "revenue", "1m", "--by", "2028-09-30")
    assert "CAD 1,000,000 by 2028-09-30" in capsys.readouterr().out
    run("stage", "STMEC", "revenue", "selection", "--at", "2026-09-01")
    run("value", "stmec.example", "revenue", "45k")
    assert capsys.readouterr().out.splitlines()[-1] == "STMEC (stmec.example) Revenue: Selection, CAD 45,000"
    run("revenue")
    out = capsys.readouterr().out
    assert "- STMEC (stmec.example): CAD 45k" in out and "| Selection | 1 | 0% | 0% |" in out
    with pytest.raises(SystemExit, match="Awareness, Education"):
        run("stage", "STMEC", "revenue", "Discovery")
    with pytest.raises(SystemExit, match="no Organization"):
        run("value", "nobody.example", "revenue", "1k")



def _v35_with_log(rows):
    """A schema-35 database (Pipeline log on Organizations) holding `rows`: (key, domain or "SOLE" for Ana's Sole
    proprietorship, pipeline, kind, stage, value, key of the entry it undoes). Returns the database, {key: id} and
    Ana's Person id."""
    from schema_history import downgrade
    db = inbox.connect()
    inbox.migrate(db)
    with db:
        ana = db.execute("INSERT INTO persons (display_name, created_at) VALUES ('Ana', 'x') RETURNING id").fetchone()[0]
        for domain, name in (("acme.com", "Acme"), ("nbif.ca", "NBIF"), (inbox.sole_key(ana), "Ana")):
            db.execute("INSERT INTO organizations (domain, name, created_at) VALUES (?, ?, 'x')", (domain, name))
    downgrade(db, 35)
    orgs = dict(db.execute("SELECT domain, id FROM organizations").fetchall())
    orgs["SOLE"] = orgs[inbox.sole_key(ana)]
    ids = {}
    with db:
        for n, (key, domain, p, kind, stage, value, undoes) in enumerate(rows):
            at = f"2026-10-{n + 1:02d}T00:00:00+00:00"
            ids[key] = db.execute(
                "INSERT INTO pipeline_log (organization_id, pipeline, kind, stage, value, undoes, effective_at,"
                " recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
                (orgs[domain], p, kind, stage, value, ids.get(undoes), at, at)).fetchone()[0]
    return db, ids, ana


def test_migration_36_moves_people_and_drops_undone_firm_entries():
    """ADR 0016, shaped like the live log: Revenue stays, a Sole proprietorship's Fundraising and Talent entries
    move to its Person, and NBIF's undone firm entries go with their undos."""
    db, ids, ana = _v35_with_log([
        ("r", "acme.com", "REVENUE", "STAGE", "Commit", None, None),
        ("f", "SOLE", "FUNDRAISING", "STAGE", "Meeting", None, None),
        ("fv", "SOLE", "FUNDRAISING", "VALUE", None, 10000, None),
        ("t", "SOLE", "TALENT", "STAGE", "Explore", None, None),
        ("tu", "SOLE", "TALENT", "UNDO", None, None, "t"),
        ("n", "nbif.ca", "FUNDRAISING", "STAGE", "Soft commit", None, None),
        ("nv", "nbif.ca", "FUNDRAISING", "VALUE", None, 100000, None),
        ("nu", "nbif.ca", "FUNDRAISING", "UNDO", None, None, "n"),
        ("nvu", "nbif.ca", "FUNDRAISING", "UNDO", None, None, "nv"),
    ])
    inbox.migrate(db)
    rows = {r["id"]: r for r in db.execute("SELECT * FROM pipeline_log")}
    assert set(rows) == {ids[k] for k in ("r", "f", "fv", "t", "tu")}
    assert rows[ids["r"]]["organization_id"] and rows[ids["r"]]["person_id"] is None
    assert {rows[ids[k]]["person_id"] for k in ("f", "fv", "t", "tu")} == {ana}
    assert all(rows[ids[k]]["organization_id"] is None for k in ("f", "fv", "t", "tu"))
    held = pl.current(db, person_id=ana)
    assert list(held) == ["FUNDRAISING"] and (held["FUNDRAISING"]["stage"], held["FUNDRAISING"]["value"]) == ("Meeting", 10000)
    nbif = pl.find_org(db, "nbif.ca")["id"]
    assert pl.current(db, nbif) == {}
    with pytest.raises(Exception, match="append-only"):
        db.execute("DELETE FROM pipeline_log")


def test_migration_36_stops_on_a_live_firm_entry():
    """A firm's Fundraising entry that is still live has no Person to move to: the upgrade stops, losing nothing."""
    db, ids, _ = _v35_with_log([("n", "nbif.ca", "FUNDRAISING", "STAGE", "Soft commit", None, None)])
    with pytest.raises(Exception, match="on a firm"):
        inbox.migrate(db)
    assert db.execute("PRAGMA user_version").fetchone()[0] == 35
    assert db.execute("SELECT count(*) FROM pipeline_log").fetchone()[0] == 1


def test_merging_people_keeps_their_pipeline_place(db):
    """ADR 0016: a Person merge moves the dropped Person's Fundraising Stage to the kept one."""
    with db:
        keep, drop = (db.execute("INSERT INTO persons (display_name, created_at) VALUES (?, 'x') RETURNING id",
                                 (n,)).fetchone()[0] for n in ("Ana Ruiz", "ana"))
    pl.set_stage(db, drop, pl.FUNDRAISING, "Diligence")
    inbox.merge_persons(db, keep, drop)
    assert pl.current(db, person_id=keep)[pl.FUNDRAISING]["stage"] == "Diligence"
