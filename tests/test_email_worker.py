"""The Cloudflare Email Worker (deploy/email-worker/worker.js) signs requests the way /hooks/email verifies.

Runs the Worker under Node against the real hooks server. Skipped where Node is not installed.
"""

import os
import shutil
import subprocess
import threading
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from universal_inbox import inbox, serve, slack

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


@pytest.fixture
def hook(tmp_path, monkeypatch):
    creds = tmp_path / "creds"
    creds.mkdir()
    secret = "a" * 64
    for name, value in {"email-hook-secret": secret, "slack-signing-secret": "s", "slack-user-token": "xoxp-x"}.items():
        (creds / name).write_text(value)
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(creds))
    db_path = str(tmp_path / "inbox.db")
    db = inbox.connect(db_path)
    inbox.migrate(db)
    with db:
        db.execute("INSERT INTO connections (platform, external_id, self_id, label, connected_at) VALUES"
                   " ('gmail', 'jeremy@agentmaple.com', 'jeremy@agentmaple.com', 'Gmail', ?)",
                   (datetime.now(timezone.utc).isoformat(),))
    server = serve.hooks_server(db_path, "127.0.0.1", 0, client=slack.SlackClient("x", get=lambda *a: {}))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/hooks/email", secret, db
    server.shutdown()


def run_worker(url, secret, tmp_path, to="jeremy@inbox.agentmaple.com"):
    eml = tmp_path / "m.eml"
    eml.write_bytes(b"From: Ana Ruiz <ana@acme.com>\r\nTo: jeremy@agentmaple.com\r\nSubject: Worker test\r\n"
                    b"Message-ID: <w1@acme.com>\r\nDate: Mon, 12 Oct 2026 10:00:00 +0000\r\n"
                    b"Content-Type: text/plain; charset=utf-8\r\n\r\nHello from the Worker\r\n")
    out = subprocess.run(["node", str(ROOT / "tests/email_worker_harness.mjs"), url, secret, str(eml), to],
                         capture_output=True, text=True, timeout=30, cwd=ROOT)
    return out.stdout.strip() or out.stderr.strip()


def test_worker_request_is_accepted_and_ingested(hook, tmp_path):
    url, secret, db = hook
    assert run_worker(url, secret, tmp_path) == "accepted"
    assert db.execute("SELECT title FROM threads").fetchone()["title"] == "Worker test"


def test_worker_with_the_wrong_secret_is_rejected(hook, tmp_path):
    url, _, db = hook
    assert run_worker(url, "b" * 64, tmp_path) == "rejected: universal-inbox hook returned HTTP 401"
    assert db.execute("SELECT count(*) FROM threads").fetchone()[0] == 0


def test_the_envelope_recipient_picks_the_fastmail_connection(hook, tmp_path):
    """Fastmail's rule copies to fastmail@inbox.agentmaple.com; that copy is filed under Fastmail (#113)."""
    url, secret, db = hook
    with db:
        fm = db.execute("INSERT INTO connections (platform, external_id, self_id, label, connected_at) VALUES"
                        " ('fastmail', 'legere@fastmail.com', 'legere@fastmail.com', 'Fastmail', ?)",
                        (datetime.now(timezone.utc).isoformat(),)).lastrowid
    assert run_worker(url, secret, tmp_path, "fastmail@inbox.agentmaple.com") == "accepted"
    row = db.execute("SELECT ch.connection_id FROM threads t JOIN channels ch ON ch.id = t.channel_id").fetchone()
    assert row["connection_id"] == fm


def test_dual_delivery_availability_and_signed_http_boundaries():
    out = subprocess.run(["node", str(ROOT / "tests/email_worker_harness.mjs"), "--check"],
                         capture_output=True, text=True, timeout=30, cwd=ROOT)
    assert out.returncode == 0, out.stdout + out.stderr
    assert out.stdout.strip() == "checked"


def test_worker_routes_preserve_legacy_hooks_and_add_resend():
    config = tomllib.loads((ROOT / "deploy/email-worker/wrangler.toml").read_text())
    assert config["name"] == "universal-inbox-email"
    assert config["routes"] == [
        {"pattern": f"https://hooks.agentmaple.com/hooks/{name}*", "zone_name": "agentmaple.com"}
        for name in ("email", "slack", "quo", "resend")
    ]
    assert config["vars"] == {
        "HOOK_URL": "https://hooks.agentmaple.com/hooks/email",
        "MIRROR_HOOK_URL": "https://hooks-inbox.jmlegere.com/hooks/email",
    }
