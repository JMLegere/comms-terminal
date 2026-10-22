"""Fill a database with synthetic Threads covering every console state (visual QA, Phase 6.3 demo).

    uv run python scripts/seed_demo.py --db /tmp/demo.db [--broken]
"""

import argparse
from datetime import datetime, timedelta, timezone

from comms_terminal import inbox

ME = "jeremy@agentmaple.com"
LONG = "\n\n".join(f"Paragraph {n}: details about the rollout plan, owners, and the dates we discussed." for n in range(1, 12))


def seed(db, broken=False):
    inbox.migrate(db)
    now = datetime.now(timezone.utc)
    with db:
        db.execute("INSERT OR IGNORE INTO connections (id, platform, external_id, self_id, label, connected_at)"
                   " VALUES (1, 'gmail', ?, ?, 'Gmail', ?)", (ME, ME, now.isoformat()))
        db.execute("INSERT OR IGNORE INTO connections (id, platform, external_id, self_id, label, connected_at)"
                   " VALUES (2, 'slack', 'T0AGENTMAPLE', 'U0JEREMY', 'Slack', ?)", (now.isoformat(),))
        db.execute("INSERT OR IGNORE INTO channels (connection_id, native_id, name, kind) VALUES (2, 'C0EXTACME', 'ext-acme', 'channel')")
        db.execute("INSERT OR IGNORE INTO channels (connection_id, native_id, name, kind) VALUES (2, 'C0RANDOM', 'random', 'channel')")
    inbox.add_channel_rule(db, 2, "prefix", "ext-")
    n = iter(range(1, 1000))

    def mail(frm, name, title, body, ago, refs=(), cc=()):
        at = now - ago
        mid = f"<demo{next(n)}@{frm.split('@')[1]}>"
        return inbox.ingest(db, inbox.Incoming(1, ME, "mailbox", "MESSAGE", mid, inbox.Identity("email", frm, name), at,
                                               body, list(refs), title, {"from": frm, "to": [ME], "cc": list(cc), "subject": title}))

    def slack(user, name, channel, kind, text, ago, edit_of=None, delete_of=None, sender=True):
        at = now - ago
        ts = f"{at.timestamp():.6f}"
        ident = inbox.Identity("slack_user", user, name) if sender else None
        if delete_of:
            return inbox.ingest(db, inbox.Incoming(2, channel, kind, "DELETE", delete_of, ident, at, None))
        native = f"{edit_of}:{ts}" if edit_of else f"{channel}:{ts}"
        return inbox.ingest(db, inbox.Incoming(2, channel, kind, "EDIT" if edit_of else "MESSAGE", native, ident, at,
                                               text, [], None, {"channel": channel, "ts": ts}))

    def first(t):
        return db.execute("SELECT native_id FROM activities WHERE thread_id = ? ORDER BY id LIMIT 1", (t.id,)).fetchone()[0]

    ana = mail("ana@acme.com", "Ana Ruiz", "Pricing for 40 seats?", "Hi Jeremy, can we talk pricing for 40 seats?\nWe'd like to start in November.", timedelta(hours=3), cc=["ops@acme.com"])
    logan = slack("U0LOGAN", "Logan Carr", "D0LOGAN", "im", "staging looks good", timedelta(hours=5))
    inbox.ingest(db, inbox.Incoming(2, "D0LOGAN", "im", "MESSAGE", f"D0LOGAN:{(now - timedelta(hours=4, minutes=50)).timestamp():.6f}", None, now - timedelta(hours=4, minutes=50), "nice, any blockers?", [], None, {"channel": "D0LOGAN"}))
    slack("U0LOGAN", "Logan Carr", "D0LOGAN", "im", "none. deploy now?", timedelta(hours=4, minutes=40))
    mail("bob@gmail.com", None, "Invoice #2041", "Attached is invoice #2041 for October.", timedelta(days=2))
    mia = slack("U0MIA", "Mia Chen", "C0EXTACME", "channel", "Can you review the integration spec by Wednesday?", timedelta(days=3))
    inbox.act(db, mia.id, inbox.Investigate())
    spec = mail("dana@northwind.io", "Dana Kim", "Rollout plan (long)", LONG, timedelta(hours=8))
    priya = mail("priya@northwind.io", "Priya Nair", "Intro call", "Would love a quick intro.", timedelta(days=4))
    inbox.ingest(db, inbox.Incoming(1, ME, "mailbox", "MESSAGE", "<demo-out@agentmaple.com>", None, now - timedelta(days=3), "Sure — Tuesday 2pm?", [first(priya)], "Re: Intro call", {"to": ["priya@northwind.io"]}))
    later = mail("sam@globex.com", "Sam Ortiz", "Partnership idea", "Let's revisit this next quarter.", timedelta(days=5))
    inbox.act(db, later.id, inbox.Snooze(now + timedelta(days=30)))
    edited = slack("U0RAJ", "Raj Patel", "D0RAJ", "im", "contract attached", timedelta(days=1))
    slack("U0RAJ", "Raj Patel", "D0RAJ", "im", "contract attached (v2, signed)", timedelta(hours=23), edit_of=first(edited))
    gone = slack("U0LEE", "Lee Wong", "D0LEE", "im", "oops wrong chat", timedelta(days=6))
    slack("U0LEE", "Lee Wong", "D0LEE", "im", None, timedelta(days=6) - timedelta(minutes=1), delete_of=first(gone))
    inbox.act(db, gone.id, inbox.Done())
    inbox.block(db, "email", "alerts@signoz.io")
    mail("alerts@signoz.io", "SigNoz", "[Alert] p99 latency", "p99 > 2s on api", timedelta(hours=1))
    inbox.act(db, ana.id, inbox.SetCase(new_title="Acme rollout"))
    inbox.act(db, spec.id, inbox.SetCase(case_id=inbox.get_thread(db, ana.id).case_id))
    inbox.add_identity(db, db.execute("SELECT person_id FROM identities WHERE value = 'ana@acme.com'").fetchone()[0], "email", "ana.ruiz@gmail.com")
    inbox.block(db, "email", "ana.ruiz@gmail.com")
    if broken:
        inbox.set_auth(db, 2, False)
    return {"ana": ana.id, "logan": logan.id, "spec": spec.id, "raj": edited.id, "lee": gone.id}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True)
    p.add_argument("--broken", action="store_true")
    a = p.parse_args()
    print(seed(inbox.connect(a.db), a.broken))
