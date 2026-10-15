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

    def real_name(self, user):
        return None

    def conversation_label(self, channel, kind):
        return self.conversation_name(channel)

    def in_thread(self, channel, thread_ts, user):
        return (channel, thread_ts) in self.threads and user == ME


def setup():  # noqa: D103
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
    inc = slack.parse(ev, conn, list(rules), client, lambda c, t: slack.stored_member(db, c, t))
    return inbox.ingest(db, inc) if inc else None


def test_unruled_channel_keeps_only_mentions_and_their_thread():
    db, conn = setup()
    c = Client()
    assert hook(db, conn, event("lunch?", "100.1"), c) is None  # chatter in #general is not Jeremy's business
    t = hook(db, conn, event(f"<@{ME}> can you review the deck?", "100.2"), c)
    assert t is not None and t.status_detail == "CREATED"
    v = inbox.thread_view(db, t.id)
    assert v["messages"][0]["content"] == f"<@{ME}|Jeremy> can you review the deck?"
    # A reply in that Slack thread is kept, mention or not, and starts its own Thread; later replies join it.
    r = hook(db, conn, event("link is in the doc", "100.3", thread_ts="100.2"), c)
    assert r is not None and r.id != t.id and r.title == "link is in the doc"
    assert hook(db, conn, event("thanks", "100.4", thread_ts="100.2"), c).id == r.id


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


def test_import_replays_history_through_the_live_filter_into_todo():
    db, conn = setup()
    now = datetime.now(timezone.utc).timestamp()
    ts = lambda m: f"{now - m * 60:.6f}"  # noqa: E731
    pages = {
        "users.conversations": {"ok": True, "channels": [{"id": "C1"}, {"id": "C2"}, {"id": "D1", "is_im": True}]},
        ("conversations.history", "C1"): [{"user": "U1", "text": "lunch?", "ts": ts(50)},
                                          {"user": "U1", "text": f"<@{ME}> review?", "ts": ts(40)}],
        ("conversations.history", "C2"): [{"user": "U2", "text": "deal update", "ts": ts(30), "reply_count": 1,
                                           "thread_ts": ts(30)}],
        ("conversations.replies", "C2"): [{"user": "U2", "text": "deal update", "ts": ts(30), "thread_ts": ts(30)},
                                          {"user": ME, "text": "nice", "ts": ts(20), "thread_ts": ts(30)}],
        ("conversations.history", "D1"): [{"user": "U3", "text": "hey", "ts": ts(10)}],
    }

    def get(url, token, params):
        method = url.rsplit("/", 1)[1]
        if method == "users.conversations":
            return pages[method]
        if method == "conversations.info":
            return {"ok": True, "channel": {"name": {"C1": "general", "C2": "business"}.get(params["channel"])}}
        if method == "users.info":
            return {"ok": True, "user": {"name": params["user"]}}
        return {"ok": True, "messages": pages[(method, params["channel"])], "has_more": False}

    inbox.add_channel_rule(db, conn["id"], "channel", "business")
    n = slack.import_recent(db, conn, slack.SlackClient("x", get=get), 7)
    assert n == 4  # the mention, the #business thread (2), and the DM; #general chatter is skipped
    rows = db.execute("SELECT status, status_detail FROM threads ORDER BY id").fetchall()
    assert [r["status"] for r in rows] == ["TODO"] * 4  # the #business reply is its own Thread


def test_channels_get_friendly_names():
    db, conn = setup()

    def get(url, token, params):
        method = url.rsplit("/", 1)[1]
        if method == "conversations.info":
            return {"ok": True, "channel": {"C1": {"name": "business"}, "D1": {"user": "U3", "is_im": True},
                                            "G1": {"name": "mpdm-ana--bo--jeremy-1"}}[params["channel"]]}
        if method == "users.info":
            return {"ok": True, "user": {"name": "ana.ruiz", "profile": {"display_name": "Ana"}}}
        raise AssertionError(method)

    c = slack.SlackClient("x", get=get)
    assert c.conversation_label("C1", "channel") == "business"
    assert c.conversation_label("D1", "im") == "Ana"
    assert c.conversation_label("G1", "mpim") == "ana, bo, jeremy"
    with db:  # a Channel stored before names were kept
        db.execute("INSERT INTO channels (connection_id, native_id, kind) VALUES (?, 'D1', 'im')", (conn["id"],))
    assert slack.refresh_names(db, c) == 1
    ch = db.execute("SELECT * FROM channels").fetchone()
    assert inbox._where(ch, None) == "DM · Ana"


def test_people_are_named_first_and_last():
    db, conn = setup()
    profiles = {"U1": {"name": "ana.r", "profile": {"display_name": "Ana", "real_name": "Ana Ruiz"}}}

    def get(url, token, params):
        method = url.rsplit("/", 1)[1]
        if method == "users.info":
            return {"ok": True, "user": profiles[params["user"]]}
        return {"ok": True, "channel": {"user": "U1"}}

    c = slack.SlackClient("x", get=get)
    ev = {"type": "message", "channel": "D1", "channel_type": "im", "user": "U1", "text": "hi", "ts": "1.0", "event_ts": "1.0"}
    t = inbox.ingest(db, slack.parse(ev, conn, [], c))
    assert inbox.thread_view(db, t.id)["person"]["name"] == "Ana Ruiz"
    with db:  # a Person created before names were kept
        db.execute("UPDATE persons SET display_name = 'U1'")
    slack.refresh_names(db, c)
    assert db.execute("SELECT display_name FROM persons").fetchone()[0] == "Ana Ruiz"


def test_replies_post_inside_the_slack_thread():
    sent = []
    sender = slack.SlackSender("x", post=lambda url, token, payload: sent.append(payload) or {"ok": True, "ts": "9.9"})
    channel_thread = [{"native_id": "C1:100.1", "data": {"channel": "C1", "ts": "100.1", "kind": "channel"}},
                      {"native_id": "C1:100.5", "data": {"channel": "C1", "ts": "100.5", "kind": "channel",
                                                          "thread_ts": "100.1"}}]
    top_level = [{"native_id": "C1:100.1", "data": {"channel": "C1", "ts": "100.1", "kind": "channel"}},
                 {"native_id": "C1:100.3", "data": {"channel": "C1", "ts": "100.3", "thread_ts": "100.3"}},
                 {"native_id": "C1:100.4", "data": {"to": [], "cc": []}}]  # Jeremy's earlier console reply
    sender.send(None, "on it", [], [], top_level)  # under the latest top-level message
    sender.send(None, "on it", [], [], channel_thread[1:])  # a Thread of replies: inside that Slack thread
    sender.send(None, "on it", [], [], channel_thread)  # replies win over their parent
    dm = [{"native_id": "D1:5.0", "data": {"channel": "D1", "ts": "5.0", "kind": "im"}}]
    sender.send(None, "hi", [], [], dm)  # DMs stay top-level
    sender.send(None, "hi", [], [], dm + [{"native_id": "D1:5.2", "data": {"channel": "D1", "ts": "5.2", "kind": "im",
                                                                          "thread_ts": "5.0"}}])
    assert [p.get("thread_ts") for p in sent] == ["100.3", "100.1", "100.1", None, "5.0"]


def test_channel_top_level_groups_until_a_day_quiet():
    db, conn = setup()
    rules = [("channel", "general")]
    hour, day = 3600, 86400
    t = hook(db, conn, event("deploy is red", f"{1000:.1f}"), Client(), rules)
    assert hook(db, conn, event("looking", f"{1000 + 5 * hour:.1f}", user=ME), Client(), rules).id == t.id
    # A reply-thread keeps the channel active, but its messages stay in their own Thread.
    side = hook(db, conn, event("which job?", f"{1000 + 10 * hour:.1f}", thread_ts=f"{1000:.1f}"), Client(), rules)
    assert side.id != t.id
    assert hook(db, conn, event("fixed", f"{1000 + 30 * hour:.1f}", user="U2"), Client(), rules).id == t.id
    later = hook(db, conn, event("new topic", f"{1000 + 30 * hour + day + 1:.1f}"), Client(), rules)
    assert later.id not in (t.id, side.id)
