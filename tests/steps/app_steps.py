"""App steps (features/app.feature): the Local core's checks that need no window; synthetic local records only.
The laptop scenarios run a Local core on a laptop copy of test_remote's Service, its Syncer driven by hand."""

import base64
import copy
import http.server
import json
import threading
import time
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace

import pytest
from pytest_bdd import given, parsers, then, when

from comms_terminal import core, files, inbox, remote
from test_remote import LocalLink, service  # noqa: F401 (fixture)

GIF = base64.b64decode("R0lGODlhAQABAAAAACw=")
OFFLINE = "Offline · cannot reach the inbox service; read-only."


@pytest.fixture
def app(tmp_path):
    path = str(tmp_path / "app.db")
    db = inbox.connect(path)
    inbox.migrate(db)
    ctx = SimpleNamespace(db=db, path=path, raw=None, hits=[], server=None, answer=None, draft=None, released=None)
    yield ctx
    if ctx.server:
        ctx.server.shutdown()
    db.close()


@pytest.fixture
def laptop(service, tmp_path, monkeypatch):
    """A Local core on a laptop copy of the test Service, with the events it publishes."""
    path, sdb, sock, tid = service
    monkeypatch.setattr(files, "file_url", files.file_url)
    rem, caps = remote.Remote(LocalLink(sock), remote.post), {}
    files.file_url = remote.signed_urls(caps)
    (tmp_path / "laptop").mkdir()
    copy_path = str(tmp_path / "laptop" / "inbox.db")
    syncer = remote.Syncer(rem, copy_path, caps, tmp_path / "laptop" / "service-build")
    c = core.Core(copy_path, None, rem, syncer)
    events = []
    c.subscribe(events.append)
    ctx = SimpleNamespace(core=c, rem=rem, syncer=syncer, sdb=sdb, tid=tid, events=events, answer=None, draft=None)

    def sync(news=False):  # one round of the Syncer's loop; with news, rounds until the Service's change arrives
        for _ in range(50):
            body = syncer.once(timeout=2)
            if body is not None:
                return c.call_from_thread(c.apply_snapshot, syncer, body)
            if not news:
                return
            time.sleep(0.05)
        raise AssertionError("the Service's change never arrived")
    ctx.sync = sync
    ctx.sync()
    rem.online = True
    return ctx


def call(ctx, method, origin="view", **params):
    target = ctx.core if hasattr(ctx, "core") else core.Core(ctx.path)
    return target.handle({"jsonrpc": "2.0", "id": 1, "method": method, "params": {"origin": origin, **params}})


def published(ctx, kind, until=bool, timeout=2.0):
    """The data of each `kind` event the core published, waiting up to `timeout` until `until(those)` holds."""
    deadline = time.monotonic() + timeout
    while True:
        got = [m["params"]["data"] for m in ctx.events if m["params"]["kind"] == kind]
        if until(got) or time.monotonic() > deadline:
            return got
        time.sleep(0.02)


def logging_server(ctx):
    """An HTTP server on loopback that records every request and answers images with a 1-pixel GIF."""
    hits = ctx.hits

    class Logger(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server's name
            hits.append(self.path)
            self.send_response(200)
            if self.path.endswith(".gif"):
                self.send_header("Content-Type", "image/gif")
                self.end_headers()
                self.wfile.write(GIF)
            else:
                self.end_headers()

        def log_message(self, *args):
            pass

    ctx.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Logger)
    threading.Thread(target=ctx.server.serve_forever, daemon=True).start()
    ctx.url = f"http://127.0.0.1:{ctx.server.server_port}"
    return ctx.url


# --- AS-1: inline images show, remote ones wait for Load images ---

@given(parsers.parse('an email whose HTML has an inline image "{cid}" and a remote image with alt "{alt}"'))
def newsletter(app, cid, alt):
    url = logging_server(app)
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = "deals@example.test", "me@example.test", "This week"
    msg.set_content("This week")
    msg.add_alternative(f'<p>This week</p><img src="cid:{cid}" alt="Logo"><img src="{url}/deals.gif" alt="{alt}">',
                        subtype="html")
    msg.get_payload()[1].add_related(GIF, "image", "gif", cid=f"<{cid}>")
    app.raw, app.alt = msg.as_bytes(), alt


@then("the inline image shows")
def inline_image_shows(app):
    assert f'src="data:image/gif;base64,{base64.b64encode(GIF).decode()}"' in app.answer["html"], app.answer["html"]


@then(parsers.parse('"{alt}" shows where the remote image was'))
def alt_shows(app, alt):
    assert f'<span class="ct-alt">{alt}</span>' in app.answer["html"] and app.url not in app.answer["html"]


@then("no network request leaves the App")
def no_network_request(app):
    assert app.hits == []


@when("Jeremy chooses Load images")
def load_images(app):
    name = files.store_raw(app.db, app.raw)
    app.answer = call(app, "message.load_images", raw=name)["result"]


@then("the remote image shows")
def remote_image_shows(app):
    assert app.hits == ["/deals.gif"]
    assert app.answer["html"].count('src="data:image/gif;base64,') == 2 and "ct-alt" not in app.answer["html"]


# --- AS-2: no remote reference is fetched ---

@given("an email whose HTML uses a stylesheet link, CSS url(), srcset, an iframe and a script")
def email_with_every_reference(app):
    url = logging_server(app)
    html = (f'<html><head><link rel="stylesheet" href="{url}/a.css"><style>@import "{url}/b.css";'
            f' body {{ background: url({url}/c.png) }}</style></head><body>'
            f'<img src="{url}/d.png" srcset="{url}/e.png 2x" alt="Deals"><iframe src="{url}/f"></iframe>'
            f'<script src="{url}/g.js"></script><div style="background-image: url({url}/h.png)">Hi</div></body></html>')
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = "ana@example.test", "me@example.test", "Deals"
    msg.set_content("Hi")
    msg.add_alternative(html, subtype="html")
    app.raw = msg.as_bytes()


@when("the App opens its Message Body")
def open_message_body(app):
    name = files.store_raw(app.db, app.raw)
    app.answer = call(app, "message.body", raw=name)["result"]


@then("a logging HTTP server receives no request")
def no_request(app):
    assert app.answer and "Hi" in app.answer["html"]
    assert app.url not in app.answer["html"], app.answer["html"]
    assert app.hits == []


# --- AS-8: a Panel cannot send ---

@given("a Panel that calls the send command for a Draft")
def panel_and_draft(app):
    with app.db:
        app.db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                       " VALUES ('1', 'gmail', 'me@example.test', 'me@example.test', 'Mail', '2026-10-01T10:00:00+00:00')")
    app.draft = inbox.save_draft(app.db, dict(connection_id="1", kind="email", sender="me@example.test",
                                              to="ana@example.test", cc="", subject="Hi", body="Hello"))


@when("the Panel runs")
def panel_runs(app):
    app.answer = call(app, "send.draft", origin="panel", draft_id=app.draft["id"], revision=app.draft["revision"])


@then("the Local core refuses the call for the Panel Origin")
def refused(app):
    assert "error" in app.answer and "view" in app.answer["error"]["message"], app.answer


@then("the Draft is not sent")
def not_sent(app):
    d = inbox.get_draft(app.db, app.draft["id"])
    assert d["sending_at"] is None and d["revision"] == app.draft["revision"]


# --- AS-11: every Console key reaches the App ---

@given("the Console's key bindings")
def console_bindings(app):
    import test_app_parity
    app.parity = test_app_parity


@then("each one maps to a key in the App's default \"keys.json\" or is listed with a reason")
def every_key_placed(app):
    app.parity.test_every_console_binding_reaches_the_app_or_says_why()
    app.parity.test_each_mapped_command_is_bound_and_handled()
    app.parity.test_each_typed_key_is_in_its_view()


# --- AS-12: the Local API only grows ---

@given("the released Local API document")
def released(app):
    app.released = core.openrpc()


@when("a released method loses a parameter")
def lose_parameter(app):
    app.changed = copy.deepcopy(app.released)
    next(m for m in app.changed["methods"] if m["name"] == "thread.view")["params"] = []


@then("the compatibility check fails")
def check_fails(app):
    assert core.breaks(app.released, app.changed)
    assert json.dumps(core.breaks(app.released, app.released)) == "[]"


# --- AS-5: a write made just before the App quits still reaches the Service ---

@given(parsers.parse('a Thread in Status "{status}"'))
def thread_in_status(laptop, status):
    assert inbox.get_thread(laptop.sdb, laptop.tid).status == status


@when("Jeremy marks it Done in the App and quits the App at once")
def done_then_quit(laptop):
    laptop.answer = call(laptop, "command.inbox.act", thread_id=laptop.tid, action=remote.encode(inbox.Done()))
    # The window closes here; the Local core outlives it (FR-2) and, if it stops too, drains its queue first.
    assert laptop.rem.drain(10)


@then(parsers.parse('the Service\'s Thread has Status "{status}"'))
def service_status(laptop, status):
    assert "result" in laptop.answer, laptop.answer
    assert inbox.get_thread(laptop.sdb, laptop.tid).status == status


# --- AS-6: an agent's Draft edit reaches the open App ---

@given("a Draft open in the App")
def draft_open(laptop):
    values = {"connection_id": None, "kind": "email", "sender": "", "to": "ana@example.test", "cc": "",
              "subject": "Proposal", "body": "Jeremy's first words"}
    laptop.draft = inbox.save_draft(laptop.sdb, values)
    laptop.values = values
    laptop.sync(news=True)
    assert call(laptop, "draft.view", draft_id=laptop.draft["id"])["result"]["draft"]["body"] == values["body"]


@when("an agent saves new text for that Draft through the CLI")
def agent_saves(laptop):
    laptop.saved_at = time.monotonic()
    inbox.save_draft(laptop.sdb, laptop.values | {"body": "The agent's text"}, draft_id=laptop.draft["id"],
                     expected_revision=laptop.draft["revision"])


@then(parsers.parse("the App shows the agent's text within {seconds:d} seconds"))
def app_shows_agent_text(laptop, seconds):
    while True:
        laptop.sync()
        body = call(laptop, "draft.view", draft_id=laptop.draft["id"])["result"]["draft"]["body"]
        elapsed = time.monotonic() - laptop.saved_at
        if body == "The agent's text" or elapsed > seconds:
            break
    assert body == "The agent's text" and elapsed <= seconds, (body, elapsed)
    edited = ["composition_drafts", laptop.draft["id"]]
    assert published(laptop, "rows", lambda got: any(edited in (r["changed"] or []) for r in got))


# --- AS-9: a new Service build makes the App read-only until it updates ---

@given("the App and the Local core on the Service's build")
def same_build(laptop, monkeypatch):
    monkeypatch.setattr(core, "build", lambda: "aaaaaaa")
    monkeypatch.setattr(remote, "build", lambda: "aaaaaaa")
    inbox.act(laptop.sdb, laptop.tid, inbox.Wait())
    laptop.sync(news=True)
    assert laptop.syncer.service_build == "aaaaaaa" and laptop.rem.online
    laptop.monkeypatch = monkeypatch


@when("the Service reports a different build")
def service_updates(laptop):
    laptop.monkeypatch.setattr(remote, "build", lambda: "bbbbbbb")  # the Service redeployed
    inbox.act(laptop.sdb, laptop.tid, inbox.Done())
    laptop.sync(news=True)


@then("the App shows read-only and refuses writes")
def read_only(laptop):
    status = call(laptop, "core.status")["result"]
    assert status["online"] is False and "read-only" in status["problem"] and status["service_build"] == "bbbbbbb"
    assert published(laptop, "status", lambda got: got and got[-1]["problem"])[-1]["problem"] == status["problem"]
    refused = call(laptop, "command.inbox.act", thread_id=laptop.tid, action=remote.encode(inbox.Done()))
    assert refused["error"]["code"] == -32011


@then("the update installs the Service's build")
def update_installs(laptop):
    # The App quits with 75 on this event (window.rs UPDATE_EXIT); the launcher installs `service-build`'s commit.
    assert published(laptop, "update") == [{"service_build": "bbbbbbb", "build": "aaaaaaa"}]
    assert Path(laptop.syncer.build_file).read_text() == "bbbbbbb\n"
    launcher = (Path(__file__).resolve().parents[2] / "deploy" / "comms-terminal-app").read_text()
    assert 'want=$(cat "$DATA/service-build"' in launcher and "[ \"$rc\" -eq 75 ] && continue" in launcher


# --- AS-10: offline, reads work and writes are refused ---

@given("the Local core cannot reach the Service")
def cannot_reach(laptop):
    laptop.rem.link.up = False
    with pytest.raises(remote.ReplyError):
        laptop.syncer.once(timeout=2)
    laptop.syncer.offline(OFFLINE)  # what the Syncer's loop does after a failed round


@when("Jeremy opens a Thread and then marks it Done")
def open_then_done(laptop):
    laptop.view = call(laptop, "thread.view", thread_id=laptop.tid)
    laptop.answer = call(laptop, "command.inbox.act", thread_id=laptop.tid, action=remote.encode(inbox.Done()))


@then("the Thread shows")
def thread_shows(laptop):
    assert "result" in laptop.view, laptop.view


@then("the write is refused with the read-only banner")
def refused_with_banner(laptop):
    assert laptop.answer["error"] == {"code": -32011, "message": OFFLINE}
    banner = published(laptop, "status", lambda got: got and got[-1]["problem"])[-1]
    assert banner["online"] is False and banner["problem"] == OFFLINE
    assert inbox.get_thread(laptop.sdb, laptop.tid).status == "TODO"
