"""Quo (formerly OpenPhone) SMS Connection, probe stage (.agents/discovery/2026-10-12-sms.md).

/hooks/quo verifies each delivery and appends it to quo-events.jsonl beside the database, so live payloads
can settle V1 (phone-app sends fire message.delivered) and V2 (media, group participants) before the adapter
is built. Deliveries use Standard Webhooks signing (Quo-Api-Version 2026-03-30).
"""

import base64
import hashlib
import hmac
import json
import os
from datetime import datetime
from pathlib import Path

SIGNATURE_WINDOW = 300  # seconds


def verify(headers, body: bytes, key: bytes, now: datetime) -> bool:
    """Standard Webhooks: base64 HMAC-SHA256 over "<webhook-id>.<webhook-timestamp>.<body>" with the
    base64-decoded `whsec_` key; webhook-signature holds space-separated "v1,<sig>" entries."""
    wid, ts, sigs = (headers.get(h, "") for h in ("webhook-id", "webhook-timestamp", "webhook-signature"))
    if not wid or not ts.isdigit() or abs(now.timestamp() - int(ts)) > SIGNATURE_WINDOW:
        return False
    secret = base64.b64decode(key.removeprefix(b"whsec_"))
    want = base64.b64encode(hmac.new(secret, f"{wid}.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
    return any(hmac.compare_digest(want, s.partition(",")[2]) for s in sigs.split() if s.startswith("v1,"))


def handle_hook(log: Path, headers, body: bytes, key: bytes, now: datetime) -> int:
    """Probe: record each verified delivery (raw event plus its webhook-id) in a 0600 JSON-lines file."""
    if not verify(headers, body, key, now):
        return 401
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a") as f:
        f.write(json.dumps({"webhook_id": headers.get("webhook-id"), "event": json.loads(body)}) + "\n")
    return 204
