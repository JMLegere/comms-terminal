"""Calendar steps (features/calendar.feature): invite Responses, FR-13e.

Skeletons only. Every scenario stays @wip until the invite responses PRD's Phases 2-4 build Respond
(serve.handle_respond), the Google Calendar Writer (gcal.Client), and meetings.respond_recorded.
"""

from pytest_bdd import given, parsers, then, when

PENDING = "FR-13e not built yet: invite responses PRD Phases 2-4 (.agents/prd/2026-10-27-invite-responses.md)"


def pending():
    raise NotImplementedError(PENDING)


@given(parsers.parse('a Meeting Thread for the Event "{title}" organized by "{organizer}" with Jeremy\'s Invite state "{state}"'))
def meeting_thread_with_state(title, organizer, state):
    pending()


@given(parsers.parse('a Meeting Thread for the Event "{title}" organized by "{organizer}"'))
def meeting_thread(title, organizer):
    pending()


@given(parsers.parse('a Meeting Thread for the weekly Event "{title}"'))
def weekly_meeting_thread(title):
    pending()


@given("a Meeting Thread for an Event organized by Jeremy")
def own_meeting_thread():
    pending()


@given("a Meeting Thread for the same Event")
def meeting_thread_same_event():
    pending()


@given(parsers.parse('an invitation email Thread whose stored message carries a calendar part for the Event "{title}" organized by "{organizer}"'))
def invitation_email_thread(title, organizer):
    pending()


@given(parsers.parse('the Thread has Status "{status}" and Status Detail "{detail}"'))
def thread_status(status, detail):
    pending()


@given("the Google Calendar Writer times out")
def writer_times_out():
    pending()


@given("Google refuses the refresh token")
def refresh_refused():
    pending()


@when(parsers.parse('Jeremy Responds "{answer}" on that Thread'))
def respond(answer):
    pending()


@when(parsers.parse('Jeremy Responds "{answer}" on the invitation email Thread'))
def respond_on_invitation(answer):
    pending()


@when("Jeremy opens the Response picker on that Thread and presses Escape")
def picker_escape():
    pending()


@then(parsers.parse('the Google Calendar Writer sets Jeremy\'s response to "{status}" for the whole Event and notifies every guest'))
def sets_whole_event(status):
    pending()


@then(parsers.parse('the Google Calendar Writer sets Jeremy\'s response to "{status}" for the Event with that UID'))
def sets_by_uid(status):
    pending()


@then(parsers.parse('the Google Calendar Writer sets Jeremy\'s response to "{status}" for the series, not one occurrence'))
def sets_series(status):
    pending()


@then(parsers.parse('the outcome is "{outcome}"'))
def outcome_is(outcome):
    pending()


@then(parsers.parse('the outcome is "{outcome}" with the reason "{reason}"'))
def outcome_with_reason(outcome, reason):
    pending()


@then(parsers.parse('the Thread has a Meeting Activity from Jeremy saying "{text}"'))
def thread_activity(text):
    pending()


@then(parsers.parse('the Meeting Thread has a Meeting Activity from Jeremy saying "{text}"'))
def meeting_thread_activity(text):
    pending()


@then("the Google Calendar Writer received no request")
def no_request():
    pending()


@then("the Google Calendar Writer received exactly one request")
def one_request():
    pending()


@then("Google Calendar writes health is broken")
def writes_broken():
    pending()
