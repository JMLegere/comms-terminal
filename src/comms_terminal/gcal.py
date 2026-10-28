"""The Google Calendar Writer (FR-13e, ADR 0009): sets Jeremy's own response on his copy of an Event, and Google
tells the organizer. Reads stay on the secret iCal feed (calendar.py); this is the only write.

The sign-in is the `google-calendar-oauth` credential, {client_id, client_secret, refresh_token} from an internal
Workspace app (`scripts/oauth_google.py`). A refresh token Google rotates is kept in `state` (0600, beside the
database) and preferred from then on, as mcp.McpClient does. `respond` returns an outcome, never a token:
confirmed (Google took it), refused (nothing changed), unknown (the write may or may not have landed; never
retried), or unavailable (no sign-in, or Google could not be read, so nothing changed). An unavailable outcome
whose sign-in Google refused also carries "broken": True, for the writes health (FR-IR-10).
"""

import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import clock

TOKEN = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
STATUS = {"accept": "accepted", "maybe": "tentative", "decline": "declined"}  # Respond answer -> responseStatus
USER_AGENT = "comms-terminal/0.1 (+https://github.com/JMLegere/comms-terminal)"
WRITES_BROKEN = "Google Calendar writes broken: "  # the Calendar Connection's last_error after a refused sign-in


class GcalError(Exception):
    """Google could not be reached or read, or the sign-in is gone: nothing changed."""


class SignInError(GcalError):
    """Google refused the sign-in (expired or revoked): writes stay broken until Jeremy signs in again."""


class _Unknown(Exception):
    pass


def _http(method, url, body: bytes | None, headers: dict, timeout=20):
    req = urllib.request.Request(url, body, {"User-Agent": USER_AGENT, **headers}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()


def _message(out: dict) -> str:
    err = out.get("error", "")
    return err.get("message", "") if isinstance(err, dict) else str(err)


class Client:
    def __init__(self, cred: dict, state: Path | None = None, http=_http):
        self.cred, self.state, self.http = dict(cred), state, http
        self.access, self.expires = None, datetime.min.replace(tzinfo=timezone.utc)
        self.lock = threading.Lock()

    @classmethod
    def load(cls, cred_bytes: bytes, state: Path | None):
        cred = json.loads(cred_bytes)
        if state is not None and state.exists():
            cred |= json.loads(state.read_text())
        return cls(cred, state)

    def _refresh(self):
        body = urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": self.cred["refresh_token"],
                                       "client_id": self.cred["client_id"],
                                       "client_secret": self.cred["client_secret"]}).encode()
        try:
            st, _, raw = self.http("POST", TOKEN, body, {"Content-Type": "application/x-www-form-urlencoded",
                                                        "Accept": "application/json"})
        except OSError as e:
            raise GcalError(f"Google sign-in could not be checked ({type(e).__name__})") from None
        try:
            tok = json.loads(raw or b"{}")
        except ValueError:
            tok = {}
        if st != 200 or "access_token" not in tok:  # error code only: Google's description can quote the token
            raise SignInError(f"Google Calendar sign-in expired or revoked (HTTP {st} {tok.get('error', '')})".strip())
        self.access = tok["access_token"]
        self.expires = clock.now() + timedelta(seconds=int(tok.get("expires_in", 3600)) - 120)
        new = tok.get("refresh_token")
        if new and new != self.cred["refresh_token"]:
            self.cred["refresh_token"] = new
            if self.state is not None:
                fd = os.open(self.state.with_suffix(".tmp"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as f:
                    json.dump({"refresh_token": new}, f)
                os.replace(self.state.with_suffix(".tmp"), self.state)

    def _call(self, method, url, body: dict | None = None, headers: dict | None = None):
        """(status, parsed body). A rejected access token is refreshed once. A read that fails raises GcalError;
        a write that is lost or fails on Google's side raises _Unknown."""
        data = json.dumps(body).encode() if body is not None else None
        for attempt in (0, 1):
            if self.access is None or clock.now() >= self.expires:
                self._refresh()
            h = {"Authorization": f"Bearer {self.access}", "Accept": "application/json", **(headers or {})}
            if data is not None:
                h["Content-Type"] = "application/json"
            try:
                st, _, raw = self.http(method, url, data, h)
            except OSError as e:  # TimeoutError and URLError are OSErrors
                if method == "GET":
                    raise GcalError(f"Google Calendar did not answer ({type(e).__name__})") from None
                raise _Unknown() from None
            if st == 401 and attempt == 0:
                self.access = None
                continue
            if st == 401:
                raise SignInError("Google Calendar sign-in expired or revoked (HTTP 401)")
            if st >= 500:
                if method == "GET":
                    raise GcalError(f"Google Calendar failed (HTTP {st})")
                raise _Unknown()
            try:
                return st, json.loads(raw or b"{}")
            except ValueError:
                return st, {}
        raise AssertionError("unreachable")

    def respond(self, uid: str, recurrence_id: str | None, answer: str) -> dict:
        """Set Jeremy's response on the Event with iCalendar `uid`: the whole Event or series when
        `recurrence_id` is None, else that one occurrence (`recurrence_id` as Google's instance suffix: UTC
        `YYYYMMDDTHHMMSSZ`, or `YYYYMMDD` for an all-day Event). Sends the full attendee list with only
        Jeremy's entry changed, guarded by the Event's etag, and asks Google to notify every guest."""
        if answer not in STATUS:
            raise ValueError(f"unknown answer {answer!r}")
        with self.lock:
            try:
                return self._respond(uid, recurrence_id, STATUS[answer])
            except GcalError as e:
                out = {"outcome": "unavailable", "error": str(e)}
                return out | {"broken": True} if isinstance(e, SignInError) else out
            except _Unknown:
                return {"outcome": "unknown", "error": "Google Calendar did not confirm the response."}

    def _respond(self, uid, recurrence_id, status) -> dict:
        st, found = self._call("GET", f"{API}?" + urllib.parse.urlencode({"iCalUID": uid}))
        if st != 200:
            raise GcalError(f"Google Calendar refused the lookup (HTTP {st})")
        series = [e for e in found.get("items", []) if not e.get("recurringEventId")]
        if not series:
            return _refused("Google Calendar has no Event with that UID.")
        ev = series[0]
        if recurrence_id:
            st, ev = self._call("GET", f"{API}/{urllib.parse.quote(ev['id'] + '_' + recurrence_id)}")
            if st == 404:
                return _refused("Google Calendar has no such occurrence.")
            if st != 200:
                raise GcalError(f"Google Calendar refused the lookup (HTTP {st})")
        if ev.get("status") == "cancelled":
            return _refused("This Event was cancelled.")
        attendees = ev.get("attendees", [])
        mine = next((a for a in attendees if a.get("self")), None)
        if (ev.get("organizer") or {}).get("self") or (mine or {}).get("organizer"):
            return _refused("You organized this Event.")
        if mine is None:
            return _refused("You are not a guest on this Event.")
        changed = [{**a, "responseStatus": status} if a is mine else a for a in attendees]
        st, out = self._call("PATCH", f"{API}/{urllib.parse.quote(ev['id'])}?sendUpdates=all",
                             {"attendees": changed}, {"If-Match": ev.get("etag", "*")})
        if st == 200:
            return {"outcome": "confirmed", "status": status}
        if st == 412:
            return _refused("The Event changed in Google Calendar; try again.")
        return _refused(f"Google Calendar refused the response (HTTP {st} {_message(out)})".strip())


def _refused(reason: str) -> dict:
    return {"outcome": "refused", "error": reason}
