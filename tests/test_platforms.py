"""Message format rules live in platforms.py: one record per format, one +E.164 rule."""

from datetime import timedelta

from comms_terminal.platforms import CONTEXT, E164, FORMATS, PLATFORMS, kind_of


def test_e164_takes_7_to_15_digits_after_plus():
    for number in ("+15550001002", "+6831234", "+123456789012345"):
        assert E164.fullmatch(number), number
    for number in ("15550001002", "+05550001002", "+123456", "+1234567890123456", "+1 555 000 1002", "+1506"):
        assert not E164.fullmatch(number), number


def test_files_quiet_windows_and_draft_fields_per_format():
    assert {n: p.sends_files for n, p in PLATFORMS.items()} == \
        {"gmail": True, "fastmail": False, "slack": True, "quo": False, "gcal": False, "linear": False,
         "tana": False, "attio": False}
    assert {n: f.quiet for n, f in FORMATS.items()} == \
        {"email": None, "sms": timedelta(days=7), "slack": timedelta(hours=24)}
    assert [n for n, f in FORMATS.items() if f.cc_subject] == ["email"]
    assert all(p.format in FORMATS for n, p in PLATFORMS.items() if n not in ("gcal", *CONTEXT))
    assert PLATFORMS["gcal"].format is None  # Google Calendar only reads (ADR 0007): no message format


def test_connection_kinds():
    """ADR 0017: every Connection has a kind; one account can back two (Linear: Context and Issue)."""
    assert {n: p.kind for n, p in PLATFORMS.items()} == {
        "gmail": "email", "fastmail": "email", "slack": "slack", "quo": "sms", "gcal": "calendar",
        "linear": "context", "tana": "context", "attio": "context"}
    assert PLATFORMS["linear"].kinds == ("context", "issue")
    assert CONTEXT == ("linear", "tana", "attio")
    assert kind_of({"platform": "quo", "kind": None}) == "sms"
    assert kind_of({"platform": "linear", "kind": "issue"}) == "issue"
    assert not any(PLATFORMS[n].takes_replies for n in CONTEXT)
