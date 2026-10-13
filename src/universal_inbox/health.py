"""Connection health (NFR-2): BROKEN when Slack revoked access, the last health check failed, the last
send/inbound request failed, or no health check has run for over an hour (the service is down)."""

from datetime import datetime, timedelta, timezone
from typing import Literal

STALE = timedelta(minutes=65)  # two missed 30-minute checks (decision #119)


def problem(connection, now: datetime | None = None) -> str | None:
    """Why a Connection is BROKEN, or None when it is OK."""
    if not connection["auth_ok"]:
        return "Slack revoked access (token revoked or app uninstalled)"
    if connection["probe_error"]:
        return f"Health check failed: {connection['probe_error']}"
    if connection["last_error"]:
        return connection["last_error"]
    if at := connection["probed_at"]:  # never checked (a database without the service) makes no claim
        age = (now or datetime.now(timezone.utc)) - datetime.fromisoformat(at)
        if age > STALE:
            return f"No health check for {int(age.total_seconds() // 60)} minutes: is universal-inbox serve running?"
    return None


def connection_health(connection, now: datetime | None = None) -> Literal["OK", "BROKEN"]:
    return "BROKEN" if problem(connection, now) else "OK"
