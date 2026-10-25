"""Platforms (GLOSSARY): the service a Connection runs through, one adapter module each; and the Message
formats they share.

Everything the service and the console need to know per Platform and per Message format lives here, so a new
Platform is one entry plus its adapter module (which provides `make_sender(connection, credential, shared)`).
Pure data and string building: importable by inbox and tui without importing the adapters.
"""

import re
from dataclasses import dataclass
from datetime import timedelta
from email.errors import HeaderParseError
from email.headerregistry import Address
from email.utils import getaddresses
from typing import Callable
from urllib.parse import quote

# A +E.164 number: 7 to 15 digits. E.164 allows at most 15; the shortest assigned numbers (Niue, Saint Helena:
# a 3-digit country code and 4 digits) have 7.
E164 = re.compile(r"\+[1-9]\d{6,14}")



def e164(number: str) -> str:
    """The +E.164 form Quo files messages under, so replies find the Thread.
    ponytail: bare numbers are taken as North American; type + for other countries."""
    digits = re.sub(r"\D", "", number)
    if number.strip().startswith("+"):
        return "+" + digits
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return number.strip()


def email_addresses(text: str) -> list[str]:
    """The usable email addresses in a To or Cc text; display names never count."""
    out = []
    for _, address in getaddresses([text]):
        try:
            parsed = Address(addr_spec=address)
        except (ValueError, HeaderParseError):
            continue
        if parsed.username and parsed.domain and address.isprintable():
            out.append(address)
    return out


EMAIL_ATTACHMENTS_LIMIT = 25 * 2**20  # D-A5: Resend takes 40 MB after Base64


def email_size_problem(files) -> str | None:
    """FR-A15: an email whose staged Attachments total more than 25 MB is refused before any send."""
    total = sum(f.size for f in files)
    if total <= EMAIL_ATTACHMENTS_LIMIT:
        return None
    mb = -(-total * 10 // 2**20) / 10  # rounded up to 0.1 MB, so a total just over 25 never reads as 25
    return f"Email Attachments total {mb:g} MB; the limit is 25 MB"


SLACK_FILE_LIMIT = 2**30  # https://slack.com/help/articles/201330736-Add-files-to-Slack: files up to 1 GB


def slack_size_problem(files) -> str | None:
    """A Slack message with any staged file over 1 GB is refused before any Slack call."""
    big = next((f for f in files if f.size > SLACK_FILE_LIMIT), None)
    if big is None:
        return None
    gb = -(-big.size * 10 // 2**30) / 10  # rounded up, so a file just over 1 GB never reads as 1 GB
    return f"Slack files can be at most 1 GB; {big.name} is {gb:g} GB"


@dataclass(frozen=True)
class Format:
    """A Message format's rules (GLOSSARY), shared by every Platform that uses it."""
    label: str                  # how a Draft names the format ("Switch to SMS")
    new_title: str              # a new Draft's title until it has a subject
    quiet: timedelta | None     # messages join one Thread until the conversation is quiet this long (FR-38);
                                # None: email threads by Message-ID and References
    cc_subject: bool            # a Draft has Cc and Subject
    can_attach: bool            # sends files (FR-A16)
    size_problem: Callable | None  # (staged files) -> why they cannot send, or None
    destinations: Callable      # (To or Cc text) -> the exact places it names: addresses, numbers, conversation ids
    addresses: Callable | None  # (text) -> the usable addresses in it; None where a person has no address (Slack)
    identity: str | None        # the Identity kind of those addresses: email or phone
    envelope: tuple             # the message data keys that hold addresses


FORMATS = {
    "email": Format("email", "New email", None, True, True, email_size_problem,
                    lambda text: [a for _, a in getaddresses([text]) if a], email_addresses, "email",
                    ("from", "to", "cc")),
    "sms": Format("SMS", "New SMS", timedelta(days=7), False, False, None,
                  lambda text: [e164(n) for n in re.split(r"[,;]+", text) if n.strip()],
                  lambda text: [text] if E164.fullmatch(text) else [], "phone", ("peer", "from", "to")),
    "slack": Format("Slack", "New Slack message", timedelta(hours=24), False, True, slack_size_problem,
                    lambda text: re.findall(r"<([A-Z0-9]+)>", text),  # "#eng <C0123>": the ID is where it posts
                    None, None, ()),
}


def destinations(text: str, kind: str) -> list[str]:
    """The exact places a Draft's To or Cc text names in its Message format."""
    return FORMATS[kind].destinations(text)


def _gmail(m, ch):
    native_id = m.get("wire_id", m["native_id"])
    return f"https://mail.google.com/mail/u/0/#search/rfc822msgid%3A{quote(native_id.strip('<>'))}" if native_id else None


def _fastmail(m, ch):
    d = m["data"]
    return f"https://app.fastmail.com/mail/Inbox/{d['fm_thread']}.{d['fm_id']}" if d.get("fm_id") else None


def _slack(m, ch):
    return f"slack://channel?team={ch['external_id']}&id={ch['native_id']}"


def _quo(m, ch):
    return m["data"].get("link")


def _gcal(m, ch):
    return (m["data"].get("meeting") or {}).get("join") or None


@dataclass(frozen=True)
class Platform:
    name: str          # connections.platform
    title: str         # the service's name ("open in Gmail")
    icon: str          # icons.ICON key
    format: str | None  # Message format: email, slack, or sms; None for a Platform that only reads (gcal)
    adapter: str       # module under comms_terminal
    by_address: bool   # a Connection is named by its address or number, else by title
    healthy: str       # Connection page text when healthy
    broken: str        # Connection page text with a Last Error, with recovery steps
    link: Callable     # (message, channel) -> URL that opens the message in the service, or None
    dm_where: str | None = None  # how a 1:1 Channel reads in the queue; None = "DM · name"
    can_attach: bool = True      # False where this Platform sends no files though its format can (FR-A16)
    hook_secret: str | None = None  # credential that signs its inbound webhook (serve.HOOKS); None = no webhook
    receipts: bool = False       # it sends through Resend: a sent message's Message-ID arrives later in a receipt,
    #                              and its mailbox Threads reconcile against those ids

    @property
    def takes_replies(self) -> bool:
        """A Platform with no Message format only reads (Google Calendar): its Threads take no replies."""
        return self.format is not None

    @property
    def sends_files(self) -> bool:
        """FR-A16: replies and Drafts through this Platform can carry Attachments."""
        return self.can_attach and self.format in FORMATS and FORMATS[self.format].can_attach


PLATFORMS = {p.name: p for p in (
    Platform("gmail", "Gmail", "google", "email", "email", True,
             "Receiving forwarded mail and sending replies.",
             "[#c14a4a]Mail may not be arriving or sending.[/] Check the forwarding rule and the Resend key; "
             "this clears on the next successful message.", _gmail, hook_secret="email-hook-secret", receipts=True),
    Platform("fastmail", "Fastmail", "fastmail", "email", "fastmail", True,
             "Receiving Fastmail's copies and sending replies through Fastmail.",
             "[#c14a4a]Fastmail may not be arriving or sending.[/] Check the Fastmail rule and re-run "
             "scripts/oauth_mcp.py if the sign-in expired; this clears on the next successful poll.", _fastmail,
             can_attach=False, hook_secret="email-hook-secret"),
    Platform("slack", "Slack", "slack", "slack", "slack", False,
             "Receiving Slack events and sending replies.",
             "[#c14a4a]Slack messages are not arriving.[/] Reconnect: run scripts/oauth_slack.py on Workbox, "
             "then restart comms-terminal serve.", _slack,
             hook_secret="slack-signing-secret"),
    Platform("quo", "Quo", "sms", "sms", "quo", True,
             "Receiving Texts from Quo and sending replies.",
             "[#c14a4a]Texts may not be sending.[/] Check the Quo API key and the webhook in Quo's settings; "
             "this clears on the next successful reply.", _quo, dm_where="SMS",
             hook_secret="quo-webhook-secret"),
    Platform("gcal", "Google Calendar", "calendar", None, "calendar", False,
             "Reading Google Calendar every 30 seconds; Meetings with someone outside Agent Maple become Threads.",
             "[#c14a4a]Google Calendar is not being read.[/] Check the google-calendar-ics credential; "
             "this clears on the next successful read.", _gcal),
)}

EMAIL = tuple(n for n, p in PLATFORMS.items() if p.format == "email")
