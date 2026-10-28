"""Google Calendar Writer (gcal.Client, FR-13e, ADR 0009): Jeremy's own response, through a fake Google."""

import json
import os
import stat
import urllib.parse

import pytest

from comms_terminal import gcal

CRED = {"client_id": "cid.apps.example", "client_secret": "s3cret", "refresh_token": "rt-1"}
ME = {"email": "jeremy@agentmaple.example", "self": True, "responseStatus": "needsAction"}
BRENDAN = {"email": "brendan@snaptrade.example", "organizer": True, "responseStatus": "accepted"}
LOGAN = {"email": "logan@agentmaple.example", "responseStatus": "tentative", "comment": "maybe late"}


def event(**kw):
    return {"id": "ev1", "etag": '"e1"', "status": "confirmed", "iCalUID": "uid-1@google.com",
            "organizer": {"email": BRENDAN["email"]}, "attendees": [BRENDAN, ME, LOGAN], **kw}


class Google:
    """Answers each request from `routes` (method, path) -> (status, body) or an exception; records requests."""

    def __init__(self, routes=None, token=(200, {"access_token": "at-1", "expires_in": 3600})):
        self.routes, self.token, self.requests = dict(routes or {}), token, []

    def __call__(self, method, url, body, headers, timeout=20):
        u = urllib.parse.urlsplit(url)
        self.requests.append({"method": method, "path": u.path, "query": urllib.parse.parse_qs(u.query),
                              "headers": headers, "body": body})
        if u.netloc == "oauth2.googleapis.com":
            answer = self.token
        else:
            answer = self.routes[(method, u.path.removeprefix("/calendar/v3"))]
        if isinstance(answer, Exception):
            raise answer
        status, payload = answer
        return status, {}, json.dumps(payload).encode()

    def sent(self, method):
        return [r for r in self.requests if r["method"] == method]


LIST = ("GET", "/calendars/primary/events")


def client(google, tmp_path):
    return gcal.Client(dict(CRED), tmp_path / "google-calendar-oauth.json", http=google)


def test_accept_sends_the_full_list_with_only_jeremys_entry_changed(tmp_path):
    google = Google({LIST: (200, {"items": [event()]}),
                     ("PATCH", "/calendars/primary/events/ev1"): (200, event())})
    out = client(google, tmp_path).respond("uid-1@google.com", None, "accept")
    assert out == {"outcome": "confirmed", "status": "accepted"}
    get = google.sent("GET")[0]
    assert get["query"]["iCalUID"] == ["uid-1@google.com"]
    assert get["headers"]["Authorization"] == "Bearer at-1"
    (patch,) = google.sent("PATCH")
    assert patch["query"]["sendUpdates"] == ["all"]
    assert patch["headers"]["If-Match"] == '"e1"'
    assert json.loads(patch["body"]) == {"attendees": [BRENDAN, {**ME, "responseStatus": "accepted"}, LOGAN]}


@pytest.mark.parametrize("answer,status", [("accept", "accepted"), ("maybe", "tentative"), ("decline", "declined")])
def test_each_answer_maps_to_googles_response_status(tmp_path, answer, status):
    google = Google({LIST: (200, {"items": [event()]}),
                     ("PATCH", "/calendars/primary/events/ev1"): (200, event())})
    assert client(google, tmp_path).respond("uid-1@google.com", None, answer)["status"] == status


def test_an_unknown_answer_is_a_programming_error(tmp_path):
    with pytest.raises(ValueError):
        client(Google(), tmp_path).respond("uid-1@google.com", None, "yes please")


def test_a_series_response_patches_the_series_not_a_moved_occurrence(tmp_path):
    moved = event(id="ev1_20261105T190000Z", recurringEventId="ev1", etag='"m1"')
    google = Google({LIST: (200, {"items": [moved, event(recurrence=["RRULE:FREQ=WEEKLY"])]}),
                     ("PATCH", "/calendars/primary/events/ev1"): (200, event())})
    assert client(google, tmp_path).respond("uid-1@google.com", None, "maybe")["outcome"] == "confirmed"
    assert [r["path"] for r in google.sent("PATCH")] == ["/calendar/v3/calendars/primary/events/ev1"]


def test_an_occurrence_response_patches_that_instance_only(tmp_path):
    inst = "ev1_20261029T190000Z"
    google = Google({LIST: (200, {"items": [event(recurrence=["RRULE:FREQ=WEEKLY"])]}),
                     ("GET", f"/calendars/primary/events/{inst}"): (200, event(id=inst, etag='"i1"')),
                     ("PATCH", f"/calendars/primary/events/{inst}"): (200, event(id=inst))})
    out = client(google, tmp_path).respond("uid-1@google.com", "20261029T190000Z", "decline")
    assert out == {"outcome": "confirmed", "status": "declined"}
    (patch,) = google.sent("PATCH")
    assert patch["path"].endswith(inst) and patch["headers"]["If-Match"] == '"i1"'


@pytest.mark.parametrize("items,reason", [
    ([], "Google Calendar has no Event with that UID."),
    ([event(status="cancelled")], "This Event was cancelled."),
    ([event(attendees=[BRENDAN, LOGAN])], "You are not a guest on this Event."),
    ([event(attendees=[{**ME, "organizer": True}, LOGAN])], "You organized this Event."),
    ([event(organizer={"email": ME["email"], "self": True})], "You organized this Event."),
])
def test_refusals_send_no_change(tmp_path, items, reason):
    google = Google({LIST: (200, {"items": items})})
    assert client(google, tmp_path).respond("uid-1@google.com", None, "accept") == {"outcome": "refused", "error": reason}
    assert google.sent("PATCH") == []


def test_an_occurrence_google_no_longer_has_is_refused(tmp_path):
    inst = "ev1_20261029T190000Z"
    google = Google({LIST: (200, {"items": [event()]}),
                     ("GET", f"/calendars/primary/events/{inst}"): (404, {"error": {"message": "Not Found"}})})
    out = client(google, tmp_path).respond("uid-1@google.com", "20261029T190000Z", "accept")
    assert out == {"outcome": "refused", "error": "Google Calendar has no such occurrence."}


def test_an_event_changed_since_it_was_read_is_refused(tmp_path):
    google = Google({LIST: (200, {"items": [event()]}),
                     ("PATCH", "/calendars/primary/events/ev1"): (412, {"error": {"message": "Precondition Failed"}})})
    out = client(google, tmp_path).respond("uid-1@google.com", None, "accept")
    assert out == {"outcome": "refused", "error": "The Event changed in Google Calendar; try again."}


@pytest.mark.parametrize("failure", [TimeoutError("timed out"), OSError("connection reset"),
                                     (503, {"error": {"message": "Backend Error"}})])
def test_a_lost_or_failed_write_is_unknown_and_sent_once(tmp_path, failure):
    google = Google({LIST: (200, {"items": [event()]}), ("PATCH", "/calendars/primary/events/ev1"): failure})
    assert client(google, tmp_path).respond("uid-1@google.com", None, "accept")["outcome"] == "unknown"
    assert len(google.sent("PATCH")) == 1


def test_a_failed_read_is_unavailable_because_nothing_changed(tmp_path):
    google = Google({LIST: TimeoutError("timed out")})
    out = client(google, tmp_path).respond("uid-1@google.com", None, "accept")
    assert out["outcome"] == "unavailable" and "broken" not in out  # a blip, not a lost sign-in
    assert google.sent("PATCH") == []


def test_a_revoked_sign_in_is_unavailable_and_reads_nothing(tmp_path):
    google = Google(token=(400, {"error": "invalid_grant"}))
    out = client(google, tmp_path).respond("uid-1@google.com", None, "accept")
    assert out["outcome"] == "unavailable" and "sign-in" in out["error"] and out["broken"] is True
    assert [r["method"] for r in google.requests] == ["POST"]


def test_a_rejected_access_token_refreshes_once(tmp_path):
    google = Google({LIST: (401, {"error": {"message": "Invalid Credentials"}})})
    out = client(google, tmp_path).respond("uid-1@google.com", None, "accept")
    assert out["outcome"] == "unavailable" and out["broken"] is True
    assert [r["method"] for r in google.requests] == ["POST", "GET", "POST", "GET"]


def test_the_refresh_sends_the_client_secret_and_reuses_the_access_token(tmp_path):
    google = Google({LIST: (200, {"items": []})})
    c = client(google, tmp_path)
    c.respond("uid-1@google.com", None, "accept")
    c.respond("uid-1@google.com", None, "accept")
    posts = google.sent("POST")
    assert len(posts) == 1
    assert urllib.parse.parse_qs(posts[0]["body"].decode()) == {
        "grant_type": ["refresh_token"], "refresh_token": ["rt-1"], "client_id": ["cid.apps.example"],
        "client_secret": ["s3cret"]}


def test_a_rotated_refresh_token_is_kept_0600_and_preferred(tmp_path):
    state = tmp_path / "google-calendar-oauth.json"
    google = Google({LIST: (200, {"items": []})},
                    token=(200, {"access_token": "at-2", "expires_in": 3600, "refresh_token": "rt-2"}))
    client(google, tmp_path).respond("uid-1@google.com", None, "accept")
    assert json.loads(state.read_text()) == {"refresh_token": "rt-2"}
    assert stat.S_IMODE(os.stat(state).st_mode) == 0o600
    loaded = gcal.Client.load(json.dumps(CRED).encode(), state)
    assert loaded.cred["refresh_token"] == "rt-2" and loaded.cred["client_secret"] == "s3cret"


def test_errors_never_carry_a_token(tmp_path):
    google = Google(token=(400, {"error": "invalid_grant", "error_description": "Token rt-1 has been revoked"}))
    out = client(google, tmp_path).respond("uid-1@google.com", None, "accept")
    assert "rt-1" not in out["error"] and "s3cret" not in out["error"]
