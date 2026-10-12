"""Slack beyond ruled channels (#114): mentions and Jeremy's threads from any channel, and Slack formatting."""

from datetime import datetime, timezone

from universal_inbox import inbox, render, slack

ME = "U0ME"


class Client:
    def __init__(self, threads=()):
        self.threads = set(threads)

    def conversation_name(self, channel):
        return {"C1": "general", "C2": "customer-acme"}.get(channel)

    def user_email(self, user):
        return None

    def name_mentions(self, text):
        return text.replace(f"<@{ME}>", f"<@{ME}|Jeremy>")

    def in_thread(self, channel, thread_ts, user):
        return (channel, thread_ts) in self.threads and user == ME


def setup():
    db = inbox.connect()
    inbox.migrate(db)
    with db:
        db.execute("INSERT INTO connections (platform, external_id, self_id, label, connected_at)"
                   " VALUES ('slack', 'T1', ?, 'Slack', ?)", (ME, datetime.now(timezone.utc).isoformat()))
    return db, db.execute("SELECT * FROM connections").fetchone()


def event(text, ts, user="U1", channel="C1", thread_ts=None):
    return {"type": "message", "channel": channel, "channel_type": "channel", "user": user, "text": text,
            "ts": ts, "event_ts": ts, **({"thread_ts": thread_ts} if thread_ts else {})}


def hook(db, conn, ev, client, rules=()):
    def member(channel, thread_ts):
        r = db.execute("SELECT native_id FROM activities WHERE native_id = ? OR (json_extract(data, '$.channel') = ?"
                       " AND json_extract(data, '$.thread_ts') = ?) LIMIT 1",
                       (f"{channel}:{thread_ts}", channel, thread_ts)).fetchone()
        return r[0] if r else None
    inc = slack.parse(ev, conn, list(rules), client, member)
    return inbox.ingest(db, inc) if inc else None


def test_unruled_channel_keeps_only_mentions_and_their_thread():
    db, conn = setup()
    c = Client()
    assert hook(db, conn, event("lunch?", "100.1"), c) is None  # chatter in #general is not Jeremy's business
    t = hook(db, conn, event(f"<@{ME}> can you review the deck?", "100.2"), c)
    assert t is not None and t.status_detail == "CREATED"
    v = inbox.thread_view(db, t.id)
    assert v["messages"][0]["content"] == f"<@{ME}|Jeremy> can you review the deck?"
    # A reply in that Slack thread joins it, mention or not.
    assert hook(db, conn, event("link is in the doc", "100.3", thread_ts="100.2"), c).id == t.id


def test_threads_jeremy_posted_in_are_followed_and_his_reply_waits():
    db, conn = setup()
    c = Client(threads={("C1", "200.0")})
    t = hook(db, conn, event("any update on this?", "200.5", thread_ts="200.0"), c)
    assert t is not None
    t2 = hook(db, conn, event("on it", "200.6", user=ME, thread_ts="200.0"), c)
    assert t2.id == t.id and t2.status_detail == "WAITING_FOR_CUSTOMER"
    assert hook(db, conn, event("other thread", "300.1", thread_ts="300.0"), c) is None


def test_ruled_channels_are_unchanged():
    db, conn = setup()
    assert hook(db, conn, event("hello", "400.1", channel="C2"), Client(), [("prefix", "customer-")]) is not None


def test_slack_formatting_renders_like_email():
    text = (f"Hey <@{ME}|Jeremy>, see <https://docs.acme.com/x|the plan> *now* _please_ `code` <!here> "
            "<#C1|general>\n&gt; quoted line\n5 &lt; 6 &amp; ok")
    shown = render.plain(render.slack_markup(text))
    assert shown == "Hey @Jeremy, see the plan ↗ docs.acme.com now please code @here #general\n│ quoted line\n5 < 6 & ok"
    assert "[b]now[/b]" in render.slack_markup(text)
