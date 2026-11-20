"""The Urgent SMS (FR-PR-18, ADR 0026): once per Promise, only to Jeremy's own mobile, only when set."""

from datetime import timedelta

import pytest

from comms_terminal import promises, serve

from test_promises import NOW, anna, db, mine  # noqa: F401 - the shared fixture

MOBILE = "+15065550100"


def owed(db, what, hours, work=0, tentative=False):
    return promises.record(db, what, [anna(db)], NOW + timedelta(hours=hours), mine(db), work_minutes=work,
                           tentative=tentative, now=NOW)


def test_nothing_is_sent_until_the_mobile_is_set(db):
    owed(db, "Send the deck", 1, work=120)
    sent = []
    assert promises.alert(db, lambda to, text: sent.append(to), NOW) == 0 and sent == []


def test_one_sms_per_promise_to_jeremy_only(db):
    promises.set_alert_mobile(db, MOBILE)
    owed(db, "Send the deck", 1, work=120)            # Urgent
    owed(db, "Write the memo", 20, work=120)          # At risk: no SMS
    owed(db, "Book the venue", 72)                    # on track
    owed(db, "Maybe a demo", 1, work=120, tentative=True)  # Tentative: not owed yet
    sent = []
    send = lambda to, text: sent.append((to, text))  # noqa: E731
    assert promises.alert(db, send, NOW) == 1
    assert sent[0][0] == MOBILE
    assert sent[0][1].startswith("Urgent (") and "Send the deck for Anna Ciprick" in sent[0][1]
    assert promises.alert(db, send, NOW + timedelta(hours=2)) == 0  # now Overdue: still never again
    assert len(sent) == 1


def test_a_failed_send_is_not_repeated(db):
    promises.set_alert_mobile(db, MOBILE)
    owed(db, "Send the deck", -1)

    def broken(to, text):
        raise OSError("Quo down")
    assert promises.alert(db, broken, NOW) == 0
    sent = []
    assert promises.alert(db, lambda to, text: sent.append(text), NOW) == 0 and sent == []


def test_overdue_without_ever_being_at_risk_still_alerts(db):
    promises.set_alert_mobile(db, MOBILE)
    owed(db, "Send the deck", -1)
    sent = []
    promises.alert(db, lambda to, text: sent.append(text), NOW)
    assert sent and sent[0].startswith("Overdue: Send the deck")


@pytest.mark.parametrize("number", ["5065550100", "+0123", "+1 506 555 0100", ""])
def test_a_malformed_mobile_is_refused(db, number):
    with pytest.raises(promises.Refused):
        promises.set_alert_mobile(db, number)


def test_off_stops_them(db):
    promises.set_alert_mobile(db, MOBILE)
    promises.set_alert_mobile(db, None)
    assert promises.alert_mobile(db) is None


def test_the_service_sends_through_the_quo_connection(db):
    class Quo:
        def __init__(self):
            self.started = []

        def start(self, to, cc, subject, body, key, files=()):
            self.started.append((to, body))
            return "msg"

    with db:
        db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                   " VALUES ('q', 'quo', '+15065550199', '+15065550199', 'Quo', ?)", (NOW.isoformat(),))
    quo = Quo()
    send = serve.alert_sender(db, serve.Senders(made={"q": quo}))
    send(MOBILE, "hi")
    assert quo.started == [([MOBILE], "hi")]


def test_without_a_quo_connection_there_is_no_sender(db):
    assert serve.alert_sender(db, serve.Senders()) is None
