"""Health checks (decision #119): each adapter's probe, and probe_all recording results apart from Last Error."""

import io
import urllib.error
from datetime import datetime, timedelta, timezone

from universal_inbox import email, health, inbox, quo, serve


def creds(name):
    return b"key"


def no_creds(name):
    return None


def test_resend_probe():
    ok = lambda body, status=200: (lambda url, headers: (status, body))  # noqa: E731
    assert email.probe({}, creds, {}, get=ok({"data": []})) is None
    assert email.probe({}, creds, {}, get=ok({"name": "restricted_api_key"}, 401)) is None  # send-only key
    assert email.probe({}, creds, {}, get=ok({"name": "invalid_api_key", "message": "API key is invalid"}, 403)) \
        == "Resend refused the key: HTTP 403 API key is invalid"
    assert email.probe({}, no_creds, {}) == "Resend key is not installed"


def test_quo_probe():
    hook = {"url": "https://hooks.agentmaple.com/hooks/quo", "status": "enabled"}
    assert quo.probe({}, creds, {}, get=lambda url, key: {"data": [hook]}) is None
    assert quo.probe({}, creds, {}, get=lambda url, key: {"data": [hook | {"status": "disabled"}]}) \
        == "Quo has no enabled webhook to /hooks/quo"

    def refused(url, key):
        raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, io.BytesIO(b""))
    assert quo.probe({}, creds, {}, get=refused) == "Quo refused the key: HTTP 401"


def test_probe_all_records_results_apart_from_last_error(monkeypatch):
    db = inbox.connect()
    inbox.migrate(db)
    now = datetime.now(timezone.utc).isoformat()
    with db:
        for i, platform in enumerate(("gmail", "fastmail", "slack", "quo"), 1):
            db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at, last_error)"
                       " VALUES (?, ?, ?, ?, ?, ?, ?)", (i, platform, f"x{i}", f"x{i}", platform, now,
                                                         "send failed" if platform == "gmail" else None))
    monkeypatch.setattr(email, "probe", lambda c, cred, shared: None)
    monkeypatch.setattr(serve.slack, "probe", lambda c, cred, shared: "Slack refused the token: invalid_auth")
    monkeypatch.setattr(quo, "probe", lambda c, cred, shared: 1 / 0)
    serve.probe_all(db, {})
    rows = {r["platform"]: r for r in db.execute("SELECT * FROM connections")}
    assert rows["fastmail"]["probed_at"] is None  # its Sent poll is the check
    assert rows["gmail"]["probe_error"] is None and rows["gmail"]["last_error"] == "send failed"
    assert rows["slack"]["probe_error"] == "Slack refused the token: invalid_auth"
    assert rows["quo"]["probe_error"].startswith("health check crashed")
    assert [health.connection_health(rows[p]) for p in ("gmail", "fastmail", "slack", "quo")] == \
        ["BROKEN", "OK", "BROKEN", "BROKEN"]


def test_stale_check_is_broken():
    row = {"auth_ok": 1, "probe_error": None, "last_error": None,
           "probed_at": datetime(2026, 10, 13, 12, tzinfo=timezone.utc).isoformat()}
    at = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
    assert health.problem(row, at + timedelta(minutes=64)) is None
    assert health.problem(row, at + timedelta(minutes=66)).startswith("No health check for 66 minutes")
    assert health.problem(row | {"probed_at": None}, at) is None
