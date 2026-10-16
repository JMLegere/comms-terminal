"""inbox.transition against design §4 and decision #68, one row per rule."""
import pytest

from universal_inbox.inbox import Arrived, Reopen, Unignore, Woke, transition

ROWS = [
    # current, event, kwargs, expected
    (None, Arrived(), {}, ("TODO", "CREATED")),  # unknown sender, FR-6a
    (None, Arrived(blocked=True), {}, ("DONE", "IGNORED")),  # FR-7a
    # a Phone call is never from Jeremy: blocked caller as above; his own outgoing call starts in Todo
    (None, Arrived(from_jeremy=False, blocked=False), {}, ("TODO", "CREATED")),
    (None, Arrived(from_jeremy=True), {}, ("SNOOZED", "WAITING_FOR_CUSTOMER")),  # #61
    (("DONE", "DONE_MANUALLY_SET"), Arrived(), {}, ("TODO", "NEW_REPLY")),
    (("TODO", "CREATED"), Arrived(), {}, None),  # #68: first response still owed
    (("TODO", "IN_PROGRESS"), Arrived(), {}, ("TODO", "NEW_REPLY")),  # #68
    (("TODO", "NEW_REPLY"), Arrived(from_jeremy=True), {}, ("SNOOZED", "WAITING_FOR_CUSTOMER")),  # FR-6b
    (("TODO", "NEW_REPLY"), Arrived("EDIT", from_jeremy=True), {}, None),
    (("DONE", "IGNORED"), Arrived(), {}, None),  # FR-6e
    (("DONE", "IGNORED"), Arrived(from_jeremy=True), {}, None),
    (("SNOOZED", "WAITING_FOR_CUSTOMER"), Woke(), {}, ("TODO", "NEW_REPLY")),  # #116
    (("SNOOZED", "WAITING_FOR_DURATION"), Woke(), {"before": "IN_PROGRESS"}, ("TODO", "IN_PROGRESS")),  # #68
    (("SNOOZED", "WAITING_FOR_DURATION"), Woke(), {"has_replied": True}, ("TODO", "NEW_REPLY")),
    (("SNOOZED", "WAITING_FOR_DURATION"), Woke(), {}, ("TODO", "CREATED")),
    (("DONE", "IGNORED"), Unignore(), {"has_replied": True}, ("TODO", "NEW_REPLY")),  # #68
    (("DONE", "IGNORED"), Unignore(), {}, ("TODO", "CREATED")),
    (("DONE", "DONE_MANUALLY_SET"), Unignore(), {}, None),
    (("TODO", "IN_PROGRESS"), Reopen(), {}, None),
    (("SNOOZED", "WAITING_FOR_CUSTOMER"), Reopen(), {"has_replied": True}, ("TODO", "NEW_REPLY")),
]


@pytest.mark.parametrize("current, event, kwargs, expected", ROWS)
def test_transition(current, event, kwargs, expected):
    assert transition(current, event, **kwargs) == expected
