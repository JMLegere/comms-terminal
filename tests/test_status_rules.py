"""inbox.transition against design §4 and decision #68, one row per rule."""
import pytest

from comms_terminal.inbox import Arrived, Reopen, Snooze, SnoozeNeedsDate, Unignore, Woke, transition

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


def test_a_snooze_always_has_a_date():  # FR-OA-10
    with pytest.raises(SnoozeNeedsDate):
        transition(("TODO", "CREATED"), Snooze())


def test_one_rollup_for_cases_and_organizations():
    """A Case and an Organization roll up Status details by the same rule; an Organization leaves Ignored out."""
    from comms_terminal import inbox, pipeline
    assert inbox.rollup(["DONE_MANUALLY_SET", "WAITING_FOR_CUSTOMER"]) == "Snoozed"
    assert inbox.rollup(["IGNORED"]) == "Ignored" and inbox.rollup([]) == "Todo"
    assert inbox.rollup(["IGNORED"], pipeline.ROLLUP, "Done") == "Done"
    assert set(inbox.DETAIL_LABEL) == set(inbox.VIEWS["All"])  # every Status detail has a label


def test_a_ready_reply_draft_shows_its_todo_thread_as_draft_ready(tmp_path):  # FR-6f
    """Draft ready is shown, never stored: text, not Needs edit, on a Todo Thread; last in Todo."""
    from test_console import seeded
    from comms_terminal import inbox
    db = inbox.connect(seeded(tmp_path)[0])
    shown = lambda tid: next(r["detail"] for r in inbox.queue(db, "All") if r["id"] == tid)  # noqa: E731
    todo = inbox.queue(db, "Todo")[0]
    stored = todo["detail"]
    assert inbox.save_reply_draft(db, todo["id"], "  ") is None and shown(todo["id"]) == stored  # no text: no Draft
    draft = inbox.save_reply_draft(db, todo["id"], "Thanks, Tuesday works.")
    assert shown(todo["id"]) == inbox.DRAFT_READY
    assert inbox.get_thread(db, todo["id"]).status_detail == stored  # the stored Status detail is unchanged
    assert inbox.VIEWS["Todo"][-1] == inbox.DRAFT_READY and inbox.DETAIL_LABEL[inbox.DRAFT_READY] == "Draft ready"
    inbox.mark_needs_edit(db, draft["id"])
    assert shown(todo["id"]) == stored  # Needs edit: not ready to send
    snoozed = inbox.queue(db, "Snoozed")[0]
    inbox.save_reply_draft(db, snoozed["id"], "Following up.")
    assert shown(snoozed["id"]) == snoozed["detail"]  # only Todo Threads show Draft ready
