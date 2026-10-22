from comms_terminal.email import reply_all, reply_sender

ME = "me@example.com"


def test_reply_all_outgoing_only_uses_latest_stored_recipients():
    history = [
        {"data": {"to": ["old@example.net"], "cc": ["old-team@example.net"]}},
        {"data": {
            "to": [ME, "marc@example.net", "marc@example.net", "other@example.net"],
            "cc": [ME, "marc@example.net", "other@example.net", "team@example.net", "team@example.net"],
        }},
    ]

    assert reply_all(history, ME) == (["marc@example.net", "other@example.net"], ["team@example.net"])


def test_reply_sender_outgoing_only_uses_stored_to_without_cc():
    history = [{"data": {
        "to": [ME, "marc@example.net", "marc@example.net", "other@example.net"],
        "cc": ["team@example.net"],
    }}]

    assert reply_sender(history, ME) == (["marc@example.net", "other@example.net"], [])


def test_outgoing_message_with_self_from_uses_stored_recipients():
    history = [{"data": {
        "from": ME,
        "to": [ME, "marc@example.net"],
        "cc": [ME, "team@example.net"],
    }}]

    assert reply_all(history, ME.upper()) == (["marc@example.net"], ["team@example.net"])
    assert reply_sender(history, ME.upper()) == (["marc@example.net"], [])


def test_latest_inbound_takes_precedence_over_newer_outgoing_messages():
    history = [
        {"data": {"from": "old@example.net", "to": [ME], "cc": ["old-team@example.net"]}},
        {"data": {
            "from": "marc@example.net",
            "to": [ME, "marc@example.net", "other@example.net", "other@example.net"],
            "cc": [ME, "marc@example.net", "other@example.net", "team@example.net"],
        }},
        {"data": {"to": ["outgoing@example.net"], "cc": ["outgoing-team@example.net"]}},
        {"data": {"from": ME, "to": ["imported@example.net"], "cc": []}},
    ]

    assert reply_all(history, ME) == (["marc@example.net", "other@example.net"], ["team@example.net"])
    assert reply_sender(history, ME) == (["marc@example.net"], [])
