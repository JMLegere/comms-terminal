"""Message format rules live in platforms.py: one record per format, one +E.164 rule."""

from datetime import timedelta

from comms_terminal.platforms import E164, FORMATS, PLATFORMS


def test_e164_takes_7_to_15_digits_after_plus():
    for number in ("+15550001002", "+6831234", "+123456789012345"):
        assert E164.fullmatch(number), number
    for number in ("15550001002", "+05550001002", "+123456", "+1234567890123456", "+1 555 000 1002", "+1506"):
        assert not E164.fullmatch(number), number


def test_files_quiet_windows_and_draft_fields_per_format():
    assert {n: p.sends_files for n, p in PLATFORMS.items()} == \
        {"gmail": True, "fastmail": False, "slack": True, "quo": False, "gcal": False}
    assert {n: f.quiet for n, f in FORMATS.items()} == \
        {"email": None, "sms": timedelta(days=7), "slack": timedelta(hours=24)}
    assert [n for n, f in FORMATS.items() if f.cc_subject] == ["email"]
    assert all(p.format in FORMATS for n, p in PLATFORMS.items() if n != "gcal")
    assert PLATFORMS["gcal"].format is None  # Google Calendar only reads (ADR 0007): no message format
