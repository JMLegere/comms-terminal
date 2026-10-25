"""Comms Terminal's one clock. Every module reads the time here, so a test sets it once with
`monkeypatch.setattr(clock, "now", lambda: fixed)` and the domain, the console's countdowns, and the calendar
all agree."""

from datetime import datetime, time, timezone


def now() -> datetime:
    """This moment, aware, in UTC."""
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    """A moment as the database stores it: UTC, to the microsecond."""
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def local(value=None) -> datetime:
    """A date or datetime as an aware local datetime (naive and floating values are local time); now if None."""
    if value is None:
        return now().astimezone()
    return (value if isinstance(value, datetime) else datetime.combine(value, time())).astimezone()
