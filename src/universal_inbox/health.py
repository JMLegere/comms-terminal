"""Connection health (NFR-2): BROKEN when Slack revoked access or the last send/inbound request failed."""

from typing import Literal


def connection_health(connection) -> Literal["OK", "BROKEN"]:
    return "BROKEN" if not connection["auth_ok"] or connection["last_error"] else "OK"
