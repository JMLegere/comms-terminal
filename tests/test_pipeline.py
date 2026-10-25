"""Pipelines (.agents/prd/2026-10-23-pipeline.md): the log, undo, and the Bowtie math worked by hand.
Times are fixed: Friday 2026-10-23, 13:00 in Halifax; Revenue Target CAD 1M TTM by 2028-09-30 (343 days to
the 2027-10-01 Impact deadline). Math: WbD's Bowtie Standard v1.0."""

from datetime import date, datetime, timedelta, timezone

import pytest

from comms_terminal import inbox, pipeline as pl

NOW = datetime(2026, 10, 23, 16, 0, tzinfo=timezone.utc)
DAY = timedelta(days=1)
R = pl.REVENUE
WEEKS = 343 / 7


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(inbox, "_now", lambda: NOW)
    monkeypatch.setattr(pl, "_now", lambda: NOW)
    db = inbox.connect()
    inbox.migrate(db)
    return db


def org(db, domain, name=None):
    with db:
        return db.execute("INSERT INTO organizations (domain, name, created_at) VALUES (?, ?, ?)",
                          (domain, name or domain.split(".")[0].title(), NOW.isoformat())).lastrowid


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
    with pytest.raises(pl.Refused, match="revenue or fundraising"):
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
    s = m["summary"]
    assert (s["paying"], s["paying_count"], s["average"]) == (120_000, 1, 120_000)
    # Nobody paid a year ago: GRR and expansion are the ACV <= 150k benchmarks (97%, 25%)
    assert s["grr"] == (0.97, True) and s["expansion"] == (0.25, True)
    assert s["nrr"] == pytest.approx(0.97 * 1.25)
    assert s["kept"] == pytest.approx(120_000 * (0.97 * 1.25) ** (343 / 365))
    assert s["weighted"] == pytest.approx(60_000 * 0.5 + 30_000 / 3)  # C has no Value
    gap = 1_000_000 - s["kept"] - s["weighted"]
    assert (s["gap"], s["customers"], s["deadline"], s["weeks"]) == (
        pytest.approx(gap), -(-gap // 120_000), date(2027, 10, 1), pytest.approx(WEEKS))
    # The constraint: the measured rate furthest below its benchmark (Onboarding 100% vs 98% is the least
    # margin; Commit has no real benchmark)
    assert s["constraint"] == {"stage": "Onboarding", "next": "Impact", "rate": 1, "benchmark": 0.98}
    n = s["customers"]
    # need = customers / rates through the paying Stage / (weeks left - Time in the Stages ahead)
    assert rows["Onboarding"]["need"] == pytest.approx(n / 1 / (WEEKS - 20 / 7))
    assert rows["Commit"]["need"] == pytest.approx(n / 0.5 / (WEEKS - 27.5 / 7))
    assert rows["Awareness"]["need"] == pytest.approx(n / 0.2 / (WEEKS - 82.5 / 7))
    assert rows["Awareness"]["entries"] == pytest.approx(5 / (365 / 7))
    assert rows["Awareness"]["pace"] == pytest.approx(rows["Awareness"]["entries"] - rows["Awareness"]["need"])
    assert [(r["stage"], [o["domain"] for o in r["orgs"]]) for r in m["ending"]] == [
        ("Impact", ["d.com"]), ("Expansion", []), ("Lost", ["e.com"]), ("Churned", [])]
    assert [o["domain"] for o in rows["Awareness"]["orgs"]] == ["c.com"]


def test_retention_and_expansion_from_value_changes(db):
    """CR7 (GRR) and CR8 (expansion) over the last 12 months, for customers paying at its start."""
    p, q, r = org(db, "p.com"), org(db, "q.com"), org(db, "r.com")
    for o, value in ((p, 100_000), (q, 50_000), (r, 50_000)):
        pl.set_stage(db, o, R, "Impact", at=NOW - 400 * DAY)
        pl.set_value(db, o, R, value, at=NOW - 400 * DAY)
    pl.set_value(db, p, R, "120k", at=NOW - 100 * DAY)  # expanded
    pl.set_stage(db, q, R, "Churned", at=NOW - 50 * DAY)  # gone
    pl.set_value(db, r, R, "30k", at=NOW - 20 * DAY)  # downgraded
    s = pl.math(db, R)["summary"]
    assert s["grr"] == (pytest.approx(130 / 200), False)
    assert s["expansion"] == (pytest.approx(20 / 130), False)
    assert s["nrr"] == pytest.approx(130 / 200 * (1 + 20 / 130))  # = (retained + expansion) / base


@pytest.fixture
def thin(db):
    """One customer paying since before the 12-month window; one new Organization stuck in Education."""
    y, x = org(db, "y.com"), org(db, "x.com")
    walk(db, y, (400, "Impact"))
    pl.set_value(db, y, R, "100k", at=NOW - 400 * DAY)
    walk(db, x, (20, "Awareness"), (10, "Education"))
    pl.set_target(db, R, 1_000_000, date(2028, 9, 30))
    return db


def test_benchmarks_fill_missing_rates(thin):
    m = pl.math(thin, R)
    rows = {r["stage"]: r for r in m["stages"]}
    assert rows["Prioritization"]["rate"] is None and rows["Prioritization"]["used"] == 0.95  # ACV <= 150k
    assert rows["Prioritization"]["assumed"] and rows["Prioritization"]["to_paying"] is None  # never mixed
    assert m["summary"]["grr"] == (1.0, False) and m["summary"]["expansion"] == (0.0, False)
    assert (m["summary"]["kept"], m["summary"]["customers"]) == (pytest.approx(100_000), 9)
    assert rows["Prioritization"]["need"] == pytest.approx(9 / (0.95 * 0.30 * 1.0 * 0.98) / (WEEKS - 4 / 7))
    assert rows["Education"]["need"] is None  # measured 0%: nobody gets through
    assert m["summary"]["constraint"]["stage"] == "Education"
    out = pl.markdown(m)
    assert "| Prioritization | 0 | 95%b | 95% |" in out and "NRR 100%" in out


def test_late_and_no_target(thin):
    m = pl.math(thin, pl.FUNDRAISING)
    assert m["summary"]["target"] is None and m["summary"]["acv"] is None  # no benchmarks for Fundraising
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
    assert m["summary"]["constraint"]["stage"] == "Onboarding" and not m["stages"][5]["orgs"]
    assert pl.this_week(m) == [
        ("Find Organizations for Onboarding", "it is the constraint: 100% move on (benchmark 98%)"),
        (f"Add {-(-(top['need'] - top['entries']) // 1):.0f} Organizations to Awareness",
         f"need {pl.num(top['need'])} a week, getting {pl.num(top['entries'])}"),
        ("Set a Value for C", "the math leaves them out until then")]


def test_this_week_moves_the_accounts_sitting_in_the_constraint(db):
    a, b = org(db, "a.com", "Acme"), org(db, "b.com", "Bolt")
    walk(db, a, (40, "Selection"), (20, "Commit"), (10, "Onboarding"), value="10k")
    walk(db, b, (30, "Selection"), value="10k")
    m = pl.math(db, R)  # Onboarding: 0 of 1 reached Impact, against a 93% benchmark
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
    assert "- STMEC (stmec.example): CAD 45k" in out and "| Commit | 0 | 100%b | 100% |" in out
    with pytest.raises(SystemExit, match="Awareness, Education"):
        run("stage", "STMEC", "revenue", "Discovery")
    with pytest.raises(SystemExit, match="no Organization"):
        run("value", "nobody.example", "revenue", "1k")
