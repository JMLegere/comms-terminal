"""App steps (features/app.feature): the Local core's checks that need no window; synthetic local records only.
The laptop scenarios run a Local core on a laptop copy of test_remote's Service, its Syncer driven by hand."""

import base64
import copy
import http.server
import json
import subprocess
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


@given(parsers.parse('an App Draft editor containing a "{kind}" with edited text and recipients'))
def edited_app_draft(app, kind):
    app.draft_kind = kind


@given(parsers.parse('an App reply box with empty "{field}"'))
def empty_app_reply_draft(app, field):
    app.draft_kind = "Reply Draft"
    app.empty_draft_field = field


def composer_view(case):
    """tests/app_composer_view.mjs: the Composer and the Draft review list (unified Drafts PRD)."""
    script = Path(__file__).resolve().parents[1] / "app_composer_view.mjs"
    result = subprocess.run(["node", "--experimental-vm-modules", str(script), case],
                            capture_output=True, text=True, timeout=10, check=True)
    return json.loads(result.stdout)


@when("Jeremy presses Ctrl Enter and chooses Send now in the Draft editor")
def send_in_app_draft_editor(app):
    app.draft_send = composer_view(f"send:{app.draft_kind}:{getattr(app, 'empty_draft_field', '')}")


@then(parsers.parse('the App calls "{method}" with the edited Draft contents'))
def app_uses_draft_send_path(app, method):
    calls = app.draft_send["calls"]
    sends = [c for c in calls if c["method"] in {"send.reply", "send.draft"}]
    assert len(sends) == 1 and sends[0]["method"] == method, sends
    if app.draft_kind == "Reply Draft":  # saved as the Thread's Reply Draft revision, then that revision sends
        assert calls[0] == {"method": "command.inbox.save_reply_draft", "params": {
            "thread_id": "thread-1", "body": "Edited text", "expected_revision": 1,
            "to": "ana@example.test, ben@example.test", "cc": "copy@example.test"}}
        assert sends[0]["params"] == {"draft_id": "draft-1", "revision": 2, "again": False}
        return
    assert app.draft_send["saved"] == {
        "connection_id": "connection-1", "kind": "email", "sender": "me@example.test",
        "to": " ana@example.test, ben@example.test ", "cc": " copy@example.test ",
        "subject": "Edited subject", "body": "Edited text",
    }
    assert calls[0]["method"] == "command.inbox.save_draft"
    assert sends[0]["params"] == {"draft_id": "draft-2", "revision": 2, "again": False}


@then(parsers.parse('the App refuses the send with "{error}"'))
def app_refuses_empty_reply(app, error):
    assert not [c for c in app.draft_send["calls"] if c["method"] in {"send.reply", "send.draft"}]
    assert app.draft_send["toasts"][-1] == error


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


# --- AS-UPD-1, AS-UPD-2: a new Service build makes the App read-only and is staged (replaces AS-9) ---

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


@then("the Service's build is staged beside the running one, which keeps running")
def staged_beside(laptop, tmp_path):
    # The App stages on this event (window.rs prepare_update) and stays open: the launcher's --prepare builds the
    # build `service-build` names into its own slot (deploy/comms-terminal-app).
    assert published(laptop, "update") == [{"service_build": "bbbbbbb", "build": "aaaaaaa"}]
    assert Path(laptop.syncer.build_file).read_text() == "bbbbbbb\n"
    m = installed_mani(tmp_path / "mani")
    m.service(m.v2)
    core = m.core()
    assert m.launch("--prepare").returncode == 0
    assert m.file("staged-build") == [m.v2] and m.current() == f"builds/{m.v1}" and core.poll() is None
    core.kill()


def installed_mani(path):
    from test_app_launcher import Mani
    path.mkdir()
    m = Mani(path)
    m.service(m.v1)
    assert m.launch().returncode == 0  # first install
    return m


# --- AS-UPD-3, AS-UPD-9: the next start opens the staged build ---

@given("the App installed on one build and the next build staged", target_fixture="mani")
def staged_mani(tmp_path):
    m = installed_mani(tmp_path / "mani")
    m.service(m.v2)
    assert m.launch("--prepare").returncode == 0
    m.old_core = m.core()
    return m


@when("Jeremy starts the App")
def start_app(mani):
    mani.result = mani.launch()


@then("it opens on the staged build with no build first")
def opens_staged(mani):
    assert mani.result.returncode == 0, mani.result.stderr
    assert mani.file("runs")[-2:] == ["run", mani.v2] and mani.file("builds.log") == ["built", "built"]


@then("the old Local core was stopped")
def old_core_stopped(mani):
    assert mani.old_core.wait(timeout=5) is not None


# --- AS-UPD-4: a second start brings the open window forward ---

@given("the App open", target_fixture="mani")
def app_open(tmp_path):
    m = installed_mani(tmp_path / "mani")
    m.first = subprocess.Popen(["sh", "-c", "sleep 60; true", "comms-terminal-app-launch"])  # its launcher, still running
    (m.data / "launch.pid").write_text(str(m.first.pid))
    return m


@when("Jeremy starts the App again")
def start_again(mani):
    try:
        mani.result = mani.launch()
    finally:
        mani.first.kill()


@then("the open window comes forward and no second App opens")
def raised(mani):
    assert mani.result.returncode == 0 and mani.file("raised") == ["raised"]
    assert mani.file("runs") == ["run", mani.v1]


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


TODAY_ROWS = {"Thread": "threads", "Task": "tasks", "Draft": "drafts", "Paused": "paused", "Unattended": "unattended"}


@given(parsers.parse('Today in the App with a Thread row selected, then a "{row}" row'))
def app_today_row(app, row):
    app.today_row = TODAY_ROWS[row]


@when(parsers.parse('Jeremy presses the Thread key for "{command}"'))
def press_thread_key_on_today(app, command):
    script = Path(__file__).resolve().parents[1] / "app_today_view.mjs"
    result = subprocess.run(["node", "--experimental-vm-modules", str(script), app.today_row, command],
                            capture_output=True, text=True, timeout=10, check=True)
    app.today = json.loads(result.stdout)


@then("the App makes no call against the earlier Thread")
def no_call_against_hidden_thread(app):
    assert app.today["thread"] is None
    assert app.today["calls"] == [] and app.today["picker"] is None, app.today


@then("the App marks that Thread Done")
def today_thread_row_done(app):
    acts = [c for c in app.today["calls"] if c["method"] == "command.inbox.act"]
    assert acts == [{"method": "command.inbox.act", "params": {"thread_id": "thread-1", "action": {"$dc": "Done", "f": {}}}}]


@given(parsers.parse('Today in the App with an Unattended "{holder}" row selected'))
def app_today_unattended(app, holder):
    app.holder = holder


@when(parsers.parse('Jeremy presses "{key}"'))
def press_key_on_unattended(app, key):
    script = Path(__file__).resolve().parents[1] / "app_today_keys_view.mjs"
    result = subprocess.run(["node", "--experimental-vm-modules", str(script), app.holder, key],
                            capture_output=True, text=True, timeout=10, check=True)
    app.today = json.loads(result.stdout)


@then(parsers.parse('the App runs "{command}"'))
def today_runs(app, command):
    assert app.today["ran"] == [command] and app.today["toasts"] == [], app.today


@then('the row\'s last message reads as an age, not "undefined NaN"')
def today_unattended_age(app):
    assert app.today["age"] == "3d", app.today


def shell_view(case):
    script = Path(__file__).resolve().parents[1] / "app_shell_view.mjs"
    result = subprocess.run(["node", "--experimental-vm-modules", str(script), case],
                            capture_output=True, text=True, timeout=10, check=True)
    return json.loads(result.stdout)


@given("the App on Threads › Todo with a Thread selected")
def app_on_todo(app):
    pass


@when("Jeremy opens the Thread's Person, then goes back twice, then picks People in the rail")
def open_person_and_back(app):
    app.shell = shell_view("stack")["looks"]


@then("the Person opens as a full page on top of Todo")
def person_on_top(app):
    start, person = app.shell[0], app.shell[1]
    assert (start["page"], start["view"], start["depth"]) == ("threads", "Todo", 1)
    assert (person["page"], person["person"], person["depth"], person["focus"]) == ("person", "person-1", 2, "detail")


@then("going back returns to Todo with the same Thread, and goes no further")
def back_to_todo(app):
    assert app.shell[2] == app.shell[0] == app.shell[3]


@then("the rail pick starts a new stack")
def rail_resets(app):
    assert (app.shell[4]["page"], app.shell[4]["depth"]) == ("people", 1)


@given("the App with a Thread selected")
def app_thread_selected(app):
    pass


@when("Jeremy marks it Done, then presses z twice")
def done_then_undo_twice(app):
    app.shell = shell_view("undo")


@then(parsers.parse('the Done toast ends with "{suffix}"'))
def done_toast_offers_undo(app, suffix):
    assert app.shell["toasts"][0].endswith(f"  {suffix}")


@then(parsers.parse('the first z restores the Thread and says "{message}"'))
def undo_restores(app, message):
    assert app.shell["calls"] == ["thread.snapshot", "command.inbox.act", "command.inbox.restore"]
    assert app.shell["toasts"][1] == message


@then(parsers.parse('the second z says "{message}"'))
def undo_once(app, message):
    assert app.shell["toasts"][2] == message


def phase1_view(case):
    script = Path(__file__).resolve().parents[1] / "app_phase1_view.mjs"
    result = subprocess.run(["node", "--experimental-vm-modules", str(script), case],
                            capture_output=True, text=True, timeout=10, check=True)
    return json.loads(result.stdout)


@given("an App Draft whose send outcome is unknown")
def locked_draft(app):
    pass


@when("Jeremy types in its body, then presses ctrl-s and esc")
def type_in_locked(app):
    app.phase1 = phase1_view("locked")


@then("the body still shows the stored text")
def locked_body_kept(app):
    assert app.phase1["body"] == "Locked text"


@then("the App saves nothing")
def locked_no_save(app):
    assert app.phase1["saves"] == 0


@given("a Step open in the App's Step editor")
def step_open(app):
    pass


@when("Jeremy changes its subject and leaves the field, then presses esc with a bad gap, then with a good one")
def step_edits(app):
    app.phase1 = phase1_view("step")


@then("leaving the field saves the new subject")
def step_blur_saves(app):
    assert app.phase1["blur"] == [{"step_id": "step-1", "subject": "New subject", "body": "Brief", "gap_days": 2}]


@then(parsers.parse('the bad gap is refused with "{message}" and the editor stays open'))
def step_refused(app, message):
    assert app.phase1["refused"] == {"open": True, "message": message}


@then("esc with a good gap saves it and closes the editor")
def step_escape_saves(app):
    assert [c["gap_days"] for c in app.phase1["escape"]] == [3]
    assert app.phase1["closed"]


@given("Ana Ng selected on the App's People page")
def ana_selected(app):
    pass


@when("Jeremy presses g, picks Ben Ode, then accepts the preview")
def merge_flow(app):
    app.phase1 = phase1_view("merge")


@then(parsers.parse('the picker asks "{title}"'))
def merge_pick_title(app, title):
    assert app.phase1["pick"] == title


@then(parsers.parse('the preview says "{line}" before any merge'))
def merge_preview_first(app, line):
    assert app.phase1["preview"]["title"] == "Merge people"
    assert app.phase1["preview"]["body"][0] == line
    assert not app.phase1["preview"]["merged"]


@then("the merge keeps Ben Ode and drops Ana Ng")
def merge_direction(app):
    assert app.phase1["merge"] == {"keep": "p-ben", "drop": "p-ana"}


@given("a Draft, an unused Sequence and a Step in the App")
def draft_and_sequence(app):
    pass


@when("Jeremy deletes each one and presses z after each")
def delete_and_undo(app):
    app.phase1 = phase1_view("delete")


@then("no confirmation picker opens")
def no_confirm(app):
    assert app.phase1["pickers"] is None


@then(parsers.parse('each delete toast ends with "{suffix}" and z restores what was deleted'))
def delete_undone(app, suffix):
    writes = [m for m, _ in app.phase1["calls"] if m.startswith("command.")]  # reads (the Steps reloading) aside
    assert writes == ["command.inbox.delete_draft", "command.inbox.restore_draft",
                                                   "command.sequences.delete_sequence", "command.sequences.undelete_sequence",
                                                   "command.sequences.delete_step", "command.sequences.restore_step"]
    assert app.phase1["calls"][1][1] == {"draft": {"id": "draft-1", "body": "x"}}
    assert app.phase1["toasts"] == ["Draft deleted.  z undo", "Undone: back to how it was.",
                                    "Deleted Warm intro.  z undo", "Undone: back to how it was.",
                                    "Step 2 deleted. 1 Enrollment on it moved on.  z undo", "Undone: back to how it was."]


def phase3_view(case):
    script = Path(__file__).resolve().parents[1] / "app_phase3_view.mjs"
    result = subprocess.run(["node", "--experimental-vm-modules", str(script), case],
                            capture_output=True, text=True, timeout=10, check=True)
    return json.loads(result.stdout)


@given("Todo in the App with a ready Reply Draft, a saved reply that is not ready, and Threads with and without a Case")
def todo_mix(app):
    pass


@when("the Threads list shows them")
def list_shows(app):
    app.phase3 = phase3_view("groups")


@then('Status headers come in the legend\'s order, with "No case" and Case headers inside them')
def legend_order(app):
    assert app.phase3["items"][:5] == ["status:Needs first response", "case:No case:1 thread", "row:d",
                                       "case:Acme rollout:2 todo · 1 done", "row:c"]
    assert [i for i in app.phase3["items"] if i.startswith("status:")] == [
        "status:Needs first response", "status:Needs next response", "status:Reply Draft"]


@then('the saved reply that is not ready stays under "Needs next response"')
def not_ready_stays(app):
    items = app.phase3["items"]
    assert items.index("row:a") == items.index("status:Needs next response") + 1
    assert items.index("row:b") == items.index("status:Reply Draft") + 1


@given("a Case header selected in the App\'s Threads list")
def case_header_selected(app):
    pass


@when("Jeremy presses Enter")
def press_enter(app):
    app.phase3 = phase3_view("case")


@then("the Case opens as a full page on the stack")
def case_pushed(app):
    assert app.phase3 == {"push": {"page": "case", "case": "case-1"}, "ran": ["nav.push"]}


@given("three Todo Threads in the App with the first selected")
def three_threads(app):
    pass


@when("Jeremy changes its Status detail with s")
def change_status(app):
    app.phase3 = phase3_view("advance")


@given("three Todo Threads on the App's Today with the first selected")
def three_threads_on_today(app):
    pass


@when("Jeremy changes its Status detail with s on Today")
def change_status_on_today(app):
    app.phase3 = phase3_view("today-advance")


@then("the selection moves to the Thread below it")
def moved_on(app):
    assert app.phase3["thread"] == "b"
    if "handoff" in app.phase3:  # Today: the Thread view marks the move before its write
        assert app.phase3["handoff"] == "c"


@given("the App\'s Threads list")
def threads_list(app):
    pass


@when("it shows ages and snooze countdowns")
def ages(app):
    app.phase3 = phase3_view("ages")


@then(parsers.parse('ages read {ages} and countdowns read {lefts}'))
def ages_read(app, ages, lefts):
    parse = lambda text: [x.strip().strip('"') for x in text.split(",")]  # noqa: E731
    assert app.phase3["when"] == parse(ages)
    assert app.phase3["left"] == parse(lefts)


# --- Phase 4: Drafts and Today (AS-CONF-12, AS-CONF-13) ---

def phase4_view(case):
    script = Path(__file__).resolve().parents[1] / "app_phase4_view.mjs"
    result = subprocess.run(["node", "--experimental-vm-modules", str(script), case],
                            capture_output=True, text=True, timeout=10, check=True)
    return json.loads(result.stdout)


@given("App Drafts that failed to send, are unsent, need an edit and are scheduled")
def drafts_mix(app):
    pass


@when("the Drafts list shows them")
def drafts_shown(app):
    app.phase4 = phase4_view("order")


@then("failed sends come first, then unsent, then scheduled by send time")
def drafts_order(app):
    assert app.phase4["order"] == ["failed", "unsent", "edit", "soon", "later"]


@then(parsers.parse('their status words read "{a}", "{b}", "{c}" and the weekday they send'))
def drafts_words(app, a, b, c):
    assert app.phase4["words"] == [a, b, c, "\U000f0150 Tue", "\U000f0150 Fri"]


@given("a Reply Draft and a New Thread Draft in the App's Drafts list and Today's drafts pane")
def reply_and_new_draft_listed(app):
    pass


@when("Jeremy selects each")
def select_each_draft(app):
    app.ud = composer_view("inplace")


@then("the Reply Draft shows as its Thread with the Composer open on it, on the same page, the keyboard left in the list")
def reply_draft_in_place(app):
    shown = {"detail": "thread", "thread": "thread-1", "ran": ["thread.show", "thread.show_reply"]}
    assert app.ud["drafts_reply"] == {"page": "drafts", **shown}
    assert app.ud["today_reply"] == {"page": "today", "draft": "draft-1", **shown}
    assert app.ud["thread"] == {"open": "thread-1", "body": "Old text", "focused": 0}


@then("the New Thread Draft shows in the Draft view")
def new_thread_draft_in_draft_view(app):
    assert app.ud["drafts_new"] == {"detail": None, "ran": ["draft.show"]}


@then("Enter gives the Reply Draft's Composer the keyboard, and Ctrl Enter from the list goes to that Composer")
def enter_focuses_composer(app):
    assert app.ud["today_enter"] == ["thread.open_reply"] and app.ud["enter_focused"] == 1
    assert app.ud["list_send"] == ["thread.send"]


@given("an App Composer on a Reply Draft and one on a New Thread Draft")
def composer_on_both(app):
    pass


@when("Jeremy presses ctrl+enter, ctrl+s, esc and ctrl+o in each")
def composer_keys(app):
    app.ud = composer_view("keys")


@then("each key does the same in both: send, save, close and attach")
def composer_same_keys(app):
    keys = {"ctrl-enter": "send", "ctrl-s": "save", "escape": "close", "ctrl-o": "app.attach"}
    assert app.ud["reply"]["keys"] == keys and app.ud["new"]["keys"] == keys
    assert app.ud["reply"]["editor"] == app.ud["new"]["editor"] == "Composer"


@then(parsers.parse('the Reply Draft shows Reply, Reply all and its "Re:" subject, the New Thread Draft its Connection and Subject'))
def composer_fields_by_kind(app):
    reply, new = app.ud["reply"]["fields"], app.ud["new"]["fields"]
    assert any(f.endswith(" reply") for f in reply) and any(f.endswith(" reply all") for f in reply), reply
    assert "Re: Plans" in reply and "Conn." not in reply
    assert new == ["Conn.", "To", "Cc", "Subject"]


@then("esc on the Reply Draft saves its text and keeps the Draft")
def composer_esc_keeps(app):
    assert app.ud["esc"] == {"open": False, "calls": [["command.inbox.save_reply_draft", "Kept words"]]}


@given("an App Composer on a Slack New Thread Draft and one on an SMS Reply Draft")
def composer_slack_sms(app):
    pass


@when("the Composer shows them")
def composer_shows_slack_sms(app):
    app.ud = composer_view("fields")


@then("each shows its destination, and neither shows a Cc or a Subject")
def composer_destination_only(app):
    assert app.ud["slack"] == {"to": True, "labels": ["Conn.", "To"]}
    assert app.ud["sms"] == {"labels": ["To"], "where": "+1 555 0100 · in thread"}


@given("an App Composer on a Reply Draft")
def composer_on_reply(app):
    pass


@when("Jeremy sends it and the answer is lost")
def composer_send_lost(app):
    app.ud = composer_view("unknown")


@then("the Composer says the send failed and, once the Draft reloads locked, ignores typing")
def composer_locked(app):
    assert app.ud["said"].startswith("Not sent · ")
    assert app.ud["locked"] == {"body": "Old text", "unsaved": False}


@then("Ctrl Enter offers Keep locked, Send again anyway and Mark as sent, and Send again sends the revision with again")
def composer_send_again(app):
    assert app.ud["menu"] == {"title": "Send outcome unknown", "options": ["keep", "again", "sent"]}
    assert app.ud["again"] == [{"draft_id": "draft-1", "revision": 1, "again": True}]


@given("App Drafts that are a Reply Draft, a New Thread Draft, a failed send and scheduled ones")
def every_kind_of_draft(app):
    pass


@when("the Drafts page and Today's drafts pane list them")
def both_lists(app):
    app.ud = composer_view("order")


@then("both list them in the same order: failed sends, then unsent, then scheduled by send time")
def one_order(app):
    assert app.ud["drafts"] == app.ud["today"] == ["failed", "reply", "new", "soon", "later"]


@given("three Drafts in the App's Drafts list and in Today's drafts pane")
def three_drafts(app):
    pass


@when("Jeremy sends, schedules and deletes them in turn")
def act_on_drafts(app):
    app.ud = composer_view("next") | composer_view("today-next")


@then("the Draft below is selected after each, and the one above after the last one")
def next_draft_selected(app):
    assert app.ud == {"sent": "c", "scheduled": "c", "deleted_last": "c", "after_middle": "drafts-c", "after_last": "drafts-a"}


@given("Today in the App with two Threads, the first selected")
def today_two_threads(app):
    pass


@when("Jeremy's typed reply is saved as its Reply Draft and Today reloads, twice")
def reply_saved_on_today(app):
    app.ud = composer_view("keep")


@then("the Thread stays selected in the threads pane and its Reply Draft is not in the drafts pane")
def thread_keeps_place(app):
    assert app.ud["typing"]["selected"] == app.ud["again"] == "threads-t1"
    assert app.ud["typing"]["thread"] == "t1"
    assert app.ud["typing"]["panes"]["threads"] == ["threads-t1", "threads-t2"] and app.ud["typing"]["panes"]["drafts"] == []


@then("once the selection moves to the next Thread, the Reply Draft is in the drafts pane and the Thread is not")
def thread_takes_new_place(app):
    assert app.ud["left"]["selected"] == "threads-t2"
    assert app.ud["left"]["panes"]["threads"] == ["threads-t2"] and app.ud["left"]["panes"]["drafts"] == ["drafts-d9"]


@given("a New Thread Draft in the App with no subject")
def draft_without_subject(app):
    pass


@when("Jeremy presses Ctrl Enter")
def press_ctrl_enter_on_draft(app):
    app.phase4 = phase4_view("d75")


@then(parsers.parse('the editor says "{said}" and no send menu opens'))
def draft_refused(app, said):
    assert app.phase4["said"] == said and app.phase4["picker"] is None


@then("a complete Draft's send menu lists its Connection, From, To and Subject")
def send_menu_body(app):
    assert app.phase4["menu"]["title"] == "Send"
    assert app.phase4["menu"]["body"] == [{"label": "Connection", "text": "Gmail · me@example.test"},
                                          {"label": "From", "text": "me@example.test"},
                                          {"label": "To", "text": "ana@example.test"},
                                          {"label": "Subject", "text": "Hello"}]


@given("Today in the App with a Thread, a Task, a Draft and a Paused Enrollment")
def today_mix(app):
    pass


@when("Jeremy presses x and r on each row and tab from the Thread")
def today_keys(app):
    app.phase4 = phase4_view("keys")


@then("x ignores the Thread, skips the Task and deletes the Draft, r resumes the Paused Enrollment, and tab moves to the Task")
def today_routes(app):
    assert app.phase4["x"] == {"threads": ["thread.ignore"], "tasks": ["tasks.skip"], "drafts": ["drafts.delete"], "paused": []}
    assert app.phase4["r"] == {"threads": ["thread.reply_sender"], "tasks": [], "drafts": [], "paused": ["person.resume"]}
    assert app.phase4["tab"] == "tasks"


@given("Today in the App with an Unattended Organization in two Stages")
def today_unattended(app):
    pass


@when("Today shows it, and later shows nothing")
def today_unattended_shown(app):
    app.phase4 = phase4_view("unattended")


@then(parsers.parse('its row reads "{stages}" and All clear reads "{clear}"'))
def unattended_and_clear(app, stages, clear):
    assert app.phase4["stages"] == stages
    assert app.phase4["wake"] == clear


# --- Phase 5: Calendar, Cases and Tasks (AS-CONF-14..16) ---

def phase5_view(case):
    script = Path(__file__).resolve().parents[1] / "app_phase5_view.mjs"
    result = subprocess.run(["node", "--experimental-vm-modules", str(script), case],
                            capture_output=True, text=True, timeout=10, check=True)
    return json.loads(result.stdout)


@given("the App's Calendar around the turn of a month")
def calendar_month_turn(app):
    pass


@when("it lays out its weeks")
def calendar_weeks(app):
    app.phase5 = phase5_view("grid")


@then(parsers.parse('a separator row starts each month and splits the week it falls in, and cells read "{a}" and "{b}"'))
def calendar_rows(app, a, b):
    assert app.phase5["rows"] == ["sep 10/26", "26,27,28,29,30,31,-", "sep 11/1", "-,-,-,-,-,-,1", "2,3,4,5,6,7,8"]
    assert app.phase5["lines"] == [a, b]


@given("an Event with a join link in the App's day pane and on its Event page")
def event_with_link(app):
    pass


@when("Jeremy presses o in each")
def press_o(app):
    app.phase5 = phase5_view("join")


@then("each toasts the link, which opens on Ctrl+click, and nothing opens at once")
def join_toasts(app):
    assert app.phase5 == {"toasts": ["https://meet.example/abc", "https://meet.example/xyz"], "opened": []}
    toasts = Path(__file__).resolve().parents[2] / "app/defaults/views/toasts/main.js"
    assert "ev?.modifiers?.control || ev?.modifiers?.platform" in toasts.read_text()


@given("a Case page in the App with two Thread cards")
def case_two_cards(app):
    pass


@when("Jeremy moves to the second card and presses Enter twice")
def case_move_fold(app):
    app.phase5 = phase5_view("fold")


@then("the Thread keys act on the second card's Thread, and the card folds and unfolds")
def case_folds(app):
    assert app.phase5 == {"thread": "t2", "folded": ["t2"], "unfolded": []}


@given("a folded Case card whose Thread has a saved reply")
def case_folded_reply(app):
    pass


@when("Jeremy presses a on it")
def case_press_a(app):
    app.phase5 = phase5_view("reply")


@then("the card unfolds with its reply row open on the saved words, To filled from the Thread")
def case_reply_open(app):
    assert app.phase5 == {"reply": "t1", "text": "Saved words", "to": "ana@example.test", "unfolded": True}


@given("email Tasks in the App with a Reply Draft and with a New Thread Draft")
def email_tasks(app):
    pass


@when("Jeremy presses Enter on each")
def task_enter(app):
    app.phase5 = phase5_view("task-enter")


@then("the Reply Draft shows in place as its Thread with the Composer taking the keyboard, and the New Thread Draft's editor takes the keyboard")
def task_opens(app):
    assert app.phase5 == {"reply": ["thread.show", "thread.open_reply"], "thread": "t9", "detail": "thread",
                           "focus": "detail", "fresh": ["draft.edit"]}


@given("a reply box whose Cc offers a completion")
def cc_completion(app):
    pass


@when("Jeremy presses → at the end of the field")
def press_right(app):
    app.phase4 = phase4_view("complete")


@then("the Cc takes the completion, and Tab leaves it to element focus")
def completion_taken(app):
    assert app.phase4 == {"tab": False, "right": True, "value": "ana@example.test, ben@example.test", "again": False}


@given("a Thread the App is about to open")
def thread_to_open(app):
    pass


@when("it opens")
def thread_opens(app):
    app.phase4 = phase4_view("pin")


@then("the Thread pane scrolls to its newest entries")
def scrolled_to_newest(app):
    assert app.phase4["opened"] >= 1


@then("once Jeremy scrolls, or the Thread reloads, it stays where he is")
def stays_put(app):
    assert app.phase4["after_scroll"] == 0 and app.phase4["reload"] == 0


@given("the App's New case title prompt")
def new_case_prompt(app):
    pass


@when("Jeremy opens it and types a title")
def open_prompt(app):
    app.phase4 = phase4_view("prompt")


@then(parsers.parse('"{hint}" is the box\'s placeholder, as in the Console'))
def prompt_placeholder(app, hint):
    assert app.phase4["placeholder"] == hint and app.phase4["fresh"]


@then("Tab stays in the prompt")
def tab_stays(app):
    keys = json.loads((Path(__file__).resolve().parents[2] / "app/defaults/keys.json").read_text())
    assert keys["view:overlay"] == {"tab": "overlay.tab", "shift-tab": "overlay.tab"}  # bound above the shell's Tab
    view = (Path(__file__).resolve().parents[2] / "app/defaults/views/overlay/main.js").read_text()
    assert 'key_context("View view=overlay")' in view


@then("Enter creates the Case with the typed title")
def prompt_enter(app):
    assert app.phase4["ran"] == ['thread.case-named:{"value":null,"text":"Seed Round"}']


@given("Today with an email Task whose New Thread Draft was shown")
def task_draft_shown(app):
    pass


@when("Jeremy selects a Reply Draft row and then the inbox changes")
def select_reply_draft_then_change(app):
    app.phase5 = phase5_view("task-stale")


@then("the pane keeps the Reply Draft he selected")
def pane_keeps_reply_draft(app):
    assert app.phase5["shown"] == ["d-task", "draft"]
    assert app.phase5["selected"] == ["d-reply", "thread", None]  # a Reply Draft shows in its Thread (decision #144)
    assert app.phase5["after"] == ["d-reply", "thread"]


@given("two due Tasks in the App with the first selected")
def two_tasks(app):
    pass


@when("Jeremy skips it with x")
def skip_task(app):
    app.phase5 = phase5_view("undo")


@then("the toast offers z undo, z would restore it, and the next Task is selected")
def skip_undo(app):
    assert app.phase5["toast"].endswith("  z undo")
    assert app.phase5["undo"]["steps"] == [["sequences.undo_task", {"undo": {"task_id": "k1", "restore": True}}]]
    assert app.phase5["task"] == "k2"


# --- Phase 6: Sequences, Organizations and People (AS-CONF-17..19) ---

def phase6_view(case):
    script = Path(__file__).resolve().parents[1] / "app_phase6_view.mjs"
    result = subprocess.run(["node", "--experimental-vm-modules", str(script), case],
                            capture_output=True, text=True, timeout=10, check=True)
    return json.loads(result.stdout)


@given("a Sequence in the App with three live Enrollments")
def sequence_live(app):
    pass


@when("Jeremy presses a and cancels the live Enrollments")
def sequence_archive(app):
    app.phase6 = phase6_view("archive")


@then("the picker explains archiving and offers to cancel the 3 live Enrollments or let them finish")
def archive_picker(app):
    assert app.phase6["picker"] == {"title": "Archive Warm intro",
                                    "options": ["Cancel the 3 live Enrollments · Finished: Canceled", "Let them finish · no new Enrollments"],
                                    "body": ["Archived Sequences take no new Enrollments and can be restored (a)."]}
    assert app.phase6["write"] == {"sequence_id": "q1", "cancel_live": True}


@then(parsers.parse('the toast says "{message}" and z restores it'))
def archive_undo(app, message):
    assert app.phase6["undo"] == {"message": message, "steps": [["sequences.restore_sequence", {"sequence_id": "q1", "undo": [{"enrollment": "e1"}]}]]}


@given("an Organization in the App at Commit in Revenue only")
def org_commit(app):
    pass


@when("Jeremy presses s and picks Onboarding")
def org_stage(app):
    app.phase6 = phase6_view("stage")


@then(parsers.parse('the picker marks "{current}" and offers no other Pipeline'))
def stage_picker(app, current):
    assert app.phase6["picker"] == {"title": "Stage · Acme (Revenue)", "options": ["  Selection", current, "  Onboarding"]}
    assert app.phase6["write"] == {"org_id": "o1", "pipeline": "REVENUE", "stage": "Onboarding"}


@given("a Person in the App in no Pipeline")
def person_no_pipeline(app):
    pass


@when("Jeremy presses p, picks Fundraising and then Meeting")
def person_stage(app):
    app.phase6 = phase6_view("person-stage")


@then(parsers.parse('the first picker offers "{first}" and the next "{title}" with a switch to Talent'))
def person_pickers(app, first, title):
    assert app.phase6["first"] == {"title": "Stage · Ana", "options": first.split(", ")}
    assert app.phase6["picker"] == {"title": title, "options": ["  Research", "  Meeting", "  Talent…"]}


@then(parsers.parse('the Person\'s Fundraising Stage is set to "{stage}", the toast says "{message}" and z undoes it'))
def person_stage_written(app, stage, message):
    assert app.phase6["write"] == {"org_id": "p1", "pipeline": "FUNDRAISING", "stage": stage}
    assert app.phase6["toast"] == f"{message}  z undo"
    assert app.phase6["undo"] == [["pipeline.undo", {"entry_id": 7}]]


@then(parsers.parse('the toast says "{message}" and z undoes it'))
def stage_undo(app, message):
    assert app.phase6["toast"] == f"{message}  z undo"
    assert app.phase6["undo"] == [["pipeline.undo", {"entry_id": 42}]]


@given("a Person in the App with one Paused Enrollment, and a Person with an Active and a Finishing one")
def person_enrollments(app):
    pass


@when("Jeremy presses r on the first and f on the second")
def press_r_f(app):
    app.phase6 = phase6_view("enrollments")


@then(parsers.parse('the Paused one resumes at once, f asks "{title}", and a Finishing one offers only Canceled and Unsubscribed'))
def resume_finish(app, title):
    r = app.phase6["resume"]
    assert r["picker"] is None and r["write"] == {"enrollment_id": "e1"}
    assert r["toast"] == "Resumed Warm intro: each Task keeps its due date."
    assert app.phase6["which"] == {"title": title, "options": ["Warm intro · Active", "Bench · Finishing"]}
    assert app.phase6["reasons"] == {"title": "Finish Bench", "options": ["CANCELED", "UNSUBSCRIBED"]}


@then("finishing can be undone with z")
def finish_undo(app):
    f = app.phase6["finished"]
    assert f["toast"] == "Finished Bench: Canceled. Its unsent Task Drafts are gone.  z undo"
    assert f["steps"] == [["sequences.restore_finish", {"undo": [{"undo": 1}]}]]



# --- NFR-1: a slow Context source never holds up Jeremy's next action ---

@given("a Context source that takes 2 seconds to answer")
def slow_context(laptop, monkeypatch):
    def stub(socket, source):
        def fetch(emails, domains):
            time.sleep(2)
            return None
        return fetch
    monkeypatch.setattr(remote, "socket_context", stub)


@when("the App asks for the Thread's context and then marks it Done, on one Local API connection")
def context_then_done(laptop, tmp_path):
    import socket
    path = tmp_path / "run" / "core.sock"
    server = core.serve(laptop.core, path)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    s = socket.socket(socket.AF_UNIX)
    s.connect(str(path))
    f = s.makefile("rwb")
    try:
        start = time.monotonic()
        for req in ({"id": 1, "method": "context", "params": {"origin": "view", "source": "attio",
                                                            "emails": ["ana@example.test"], "domains": []}},
                    {"id": 2, "method": "command.inbox.act",
                     "params": {"origin": "view", "thread_id": laptop.tid, "action": remote.encode(inbox.Done())}}):
            f.write(json.dumps({"jsonrpc": "2.0", **req}).encode() + b"\n")
        f.flush()
        laptop.arrived = {}
        while len(laptop.arrived) < 2:
            answer = json.loads(f.readline())
            laptop.arrived[answer["id"]] = (time.monotonic() - start, answer)
    finally:
        s.close()
        server.shutdown()
        server.server_close()


@then(parsers.parse("the Done answer arrives within {seconds:f} seconds, before the context answer"))
def done_first(laptop, seconds):
    (done_at, done), (context_at, _) = laptop.arrived[2], laptop.arrived[1]
    assert "result" in done, done
    assert done_at < seconds and done_at < context_at, laptop.arrived


@then(parsers.parse('the laptop copy\'s Thread has Status "{status}"'))
def copy_status(laptop, status):
    assert laptop.core.call_from_thread(inbox.get_thread, laptop.core.db, laptop.tid).status == status


# --- NFR-1: one reload wave computes each query once ---

@given("a laptop copy with Threads, Tasks and Events")
def laptop_copy(laptop):
    pass


@when("the Views reload after a write and two of them ask the same query")
def reload_twice(laptop, monkeypatch):
    fn, spec, summary = core.QUERIES["shell.summary"]
    laptop.computed = []

    def counted(c, db, **params):
        laptop.computed.append(1)
        return fn(c, db, **params)
    monkeypatch.setitem(core.QUERIES, "shell.summary", (counted, spec, summary))
    monkeypatch.setattr(core, "_minute", lambda: 0)
    call(laptop, "command.inbox.act", thread_id=laptop.tid, action=remote.encode(inbox.Done()))
    answers = [call(laptop, "shell.summary") for _ in range(2)]
    assert answers[0] == answers[1] and "result" in answers[0], answers


@then("the Local core computes that query once")
def computed_once(laptop):
    assert len(laptop.computed) == 1
    call(laptop, "command.inbox.create_case", title="Next write")
    assert "result" in call(laptop, "shell.summary")
    assert len(laptop.computed) == 2  # the next write's event makes it compute again


# --- NFR-1: a reply key pressed while the Thread loads ---

@given("a Thread the App is still loading")
def thread_loading(app):
    pass


@when("Jeremy presses r before it arrives")
def r_before_load(app):
    app.phase4 = phase4_view("early")


@then("the reply box opens to the sender once the Thread arrives")
def opens_on_arrival(app):
    assert app.phase4["before"] is None
    assert app.phase4["after_load"] == ["sender", "thread-7"]


@then("a reply key left from a Thread he moved away from opens nothing")
def stale_key_ignored(app):
    assert app.phase4["after_move"] is None
