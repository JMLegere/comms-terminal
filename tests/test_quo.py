"""Quo probe hook: Standard Webhooks signature check and the 0600 event log."""

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone

from universal_inbox import quo

KEY = b"whsec_" + base64.b64encode(b"secret-bytes")
NOW = datetime(2026, 10, 12, 20, 0, tzinfo=timezone.utc)


def signed(body: bytes, ts: int = int(NOW.timestamp()), wid: str = "msg_1") -> dict:
    sig = base64.b64encode(hmac.new(b"secret-bytes", f"{wid}.{ts}.".encode() + body, hashlib.sha256).digest())
    return {"webhook-id": wid, "webhook-timestamp": str(ts), "webhook-signature": "v1,bogus v1," + sig.decode()}


def test_verify():
    body = b'{"type":"message.received"}'
    assert quo.verify(signed(body), body, KEY, NOW)
    assert not quo.verify(signed(body), body + b" ", KEY, NOW)  # tampered body
    assert not quo.verify(signed(body, ts=int(NOW.timestamp()) - 301), body, KEY, NOW)  # stale
    assert not quo.verify({}, body, KEY, NOW)


def test_handle_hook_logs_only_verified(tmp_path):
    log, body = tmp_path / "quo-events.jsonl", b'{"type":"message.delivered","data":{}}'
    assert quo.handle_hook(log, signed(body), body, KEY, NOW) == 204
    assert quo.handle_hook(log, {}, body, KEY, NOW) == 401
    lines = log.read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0]) == {"webhook_id": "msg_1", "event": json.loads(body)}
    assert log.stat().st_mode & 0o777 == 0o600
