"""Platforms (GLOSSARY): the service a Connection runs through, one adapter module each.

Everything the service and the console need to know per Platform lives here, so a new Platform is one entry
plus its adapter module (which provides `make_sender(connection, credential, shared)`). Pure data and string
building: importable by inbox and tui without importing the adapters.
"""

from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote


def _gmail(m, ch):
    return f"https://mail.google.com/mail/u/0/#search/rfc822msgid%3A{quote(m['native_id'].strip('<>'))}"


def _fastmail(m, ch):
    d = m["data"]
    return f"https://app.fastmail.com/mail/Inbox/{d['fm_thread']}.{d['fm_id']}" if d.get("fm_id") else None


def _slack(m, ch):
    return f"slack://channel?team={ch['external_id']}&id={ch['native_id']}"


def _quo(m, ch):
    return m["data"].get("link")


@dataclass(frozen=True)
class Platform:
    name: str          # connections.platform
    title: str         # the service's name ("open in Gmail")
    icon: str          # icons.ICON key
    format: str        # Message format: email, slack, or sms
    adapter: str       # module under universal_inbox
    by_address: bool   # a Connection is named by its address or number, else by title
    healthy: str       # Connection page text when healthy
    broken: str        # Connection page text with a Last Error, with recovery steps
    link: Callable     # (message, channel) -> URL that opens the message in the service, or None
    dm_where: str | None = None  # how a 1:1 Channel reads in the queue; None = "DM · name"


PLATFORMS = {p.name: p for p in (
    Platform("gmail", "Gmail", "google", "email", "email", True,
             "Receiving forwarded mail and sending replies.",
             "[#c14a4a]Mail may not be arriving or sending.[/] Check the forwarding rule and the Resend key; "
             "this clears on the next successful message.", _gmail),
    Platform("fastmail", "Fastmail", "fastmail", "email", "fastmail", True,
             "Receiving Fastmail's copies and sending replies through Fastmail.",
             "[#c14a4a]Fastmail may not be arriving or sending.[/] Check the Fastmail rule and re-run "
             "scripts/oauth_mcp.py if the sign-in expired; this clears on the next successful poll.", _fastmail),
    Platform("slack", "Slack", "slack", "slack", "slack", False,
             "Receiving Slack events and sending replies.",
             "[#c14a4a]Slack messages are not arriving.[/] Reconnect: run scripts/oauth_slack.py on Workbox, "
             "then restart universal-inbox serve.", _slack),
    Platform("quo", "Quo", "sms", "sms", "quo", True,
             "Receiving Texts from Quo and sending replies.",
             "[#c14a4a]Texts may not be sending.[/] Check the Quo API key and the webhook in Quo's settings; "
             "this clears on the next successful reply.", _quo, dm_where="SMS"),
)}

EMAIL = tuple(n for n, p in PLATFORMS.items() if p.format == "email")
