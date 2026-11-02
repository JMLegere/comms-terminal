"""App steps (features/app.feature): the Local core's checks that need no window; synthetic local records only."""

import copy
import http.server
import json
import threading
from email.message import EmailMessage
from types import SimpleNamespace

import pytest
from pytest_bdd import given, then, when

from comms_terminal import core, files, inbox


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


def call(ctx, method, origin="view", **params):
    return core.Core(ctx.path).handle({"jsonrpc": "2.0", "id": 1, "method": method, "params": {"origin": origin, **params}})


# --- AS-2: no remote reference is fetched ---

@given("an email whose HTML uses a stylesheet link, CSS url(), srcset, an iframe and a script")
def email_with_every_reference(app):
    hits = app.hits

    class Logger(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server's name
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    app.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Logger)
    threading.Thread(target=app.server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{app.server.server_port}"
    html = (f'<html><head><link rel="stylesheet" href="{url}/a.css"><style>@import "{url}/b.css";'
            f' body {{ background: url({url}/c.png) }}</style></head><body>'
            f'<img src="{url}/d.png" srcset="{url}/e.png 2x" alt="Deals"><iframe src="{url}/f"></iframe>'
            f'<script src="{url}/g.js"></script><div style="background-image: url({url}/h.png)">Hi</div></body></html>')
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = "ana@example.test", "me@example.test", "Deals"
    msg.set_content("Hi")
    msg.add_alternative(html, subtype="html")
    app.raw = msg.as_bytes()
    app.url = url


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
