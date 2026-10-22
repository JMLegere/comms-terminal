"""Linear's private service route: strict keys, local names, and optional synthetic credentials."""

import http.client
import json
import os
import socket
import stat
import threading
from types import SimpleNamespace

import pytest

from comms_terminal import inbox, linear, serve


class Context:
    def __init__(self, error=None):
        self.calls = []
        self.error = error
        self.result = {"issues": []}

    def get(self, emails, domains, person_names, organization_names):
        self.calls.append((emails, domains, person_names, organization_names))
        if self.error:
            raise self.error
        return self.result


@pytest.fixture
def database(tmp_path):
    path = str(tmp_path / "inbox.db")
    db = inbox.connect(path)
    inbox.migrate(db)
    yield path, db
    db.close()


def post(path, route, body):
    connection = http.client.HTTPConnection("localhost", timeout=5)
    connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.sock.settimeout(5)
    try:
        connection.sock.connect(path)
        connection.request("POST", route, body, {"Content-Type": "application/json"})
        response = connection.getresponse()
        assert response.getheader("Content-Type") == "application/json"
        return response.status, json.loads(response.read())
    finally:
        connection.close()


@pytest.mark.parametrize("body", [
    b"not json", b"\xff", b"null", b"[]", b'"text"', b"12",
    b'{"emails":null}', b'{"domains":null}',
    b'{"emails":"ana@acme.com"}', b'{"domains":{}}',
    b'{"emails":[null]}', b'{"emails":[1]}', b'{"domains":[true]}',
    b'{"emails":[[]]}', b'{"domains":[{}]}',
])
def test_malformed_keys_are_400_without_fetching(database, body):
    _, db = database
    context = Context()
    assert serve.handle_linear(db, body, context)[0] == 400
    assert serve.handle_linear(db, body, None)[0] == 400
    assert context.calls == []


def test_optional_keys_empty_and_unconnected(database):
    _, db = database
    context = Context()
    assert serve.handle_linear(db, b"{}", None) == (503, {"error": "Linear is not connected"})
    for body in (b"{}", b'{"emails":[]}', b'{"domains":[]}',
                 b'{"emails":[" ",""],"domains":["\\t"]}'):
        assert serve.handle_linear(db, body, context) == (200, {"issues": []})
    assert context.calls == []
    assert serve.handle_linear(db, b'{"emails":[" ANA@ACME.COM ","ana@acme.com"]}', context)[0] == 200
    assert context.calls[-1] == (["ana@acme.com"], [], [], [])
    assert serve.handle_linear(db, b'{"domains":[" ACME.COM "]}', context)[0] == 200
    assert context.calls[-1] == ([], ["acme.com"], [], [])


def test_local_names_are_globally_unique_and_selected_by_exact_keys(database):
    _, db = database
    people = [" Ana  Silva\t", "Pat Lee", " pat\tLEE ", "ana@acme.com", "acme.com", " ",
              "+1 (506) 555-0100", "Ana", "Acme Labs", "Unrelated Person", "Slack Only"]
    organizations = [("acme.com", " Acme\tInc "), ("dup.com", "Dup Corp"),
                     ("elsewhere.com", " dup  CORP "), ("placeholder.com", "placeholder.com"),
                     ("empty.com", " "), ("person-collision.com", "Acme Labs"),
                     ("email-name.com", "mail@name.com"), ("phone.com", "+1 5065550100"),
                     ("irrelevant.com", "Other Corp")]
    emails = ["ana@acme.com"] + [f"person{i}@acme.com" for i in range(2, 10)]
    domains = [domain for domain, _ in organizations if domain not in ("elsewhere.com", "irrelevant.com")]
    with db:
        db.executemany("INSERT INTO persons (id, display_name, created_at) VALUES (?, ?, 'now')",
                       enumerate(people, 1))
        db.executemany("INSERT INTO identities (person_id, kind, value) VALUES (?, 'email', ?)",
                       zip([1] + list(range(2, 10)), emails))
        db.execute("INSERT INTO identities (person_id, kind, value) VALUES (1, 'email', 'alias@acme.com')")
        db.execute("INSERT INTO identities (person_id, kind, value) VALUES (11, 'slack_user', 'U-SLACK')")
        db.execute("INSERT INTO identities (person_id, kind, value) VALUES (NULL, 'email', 'detached@acme.com')")
        db.executemany("INSERT INTO organizations (domain, name, created_at) VALUES (?, ?, 'now')", organizations)
    before, changes = tuple(db.iterdump()), db.total_changes
    context = Context()
    body = json.dumps({"emails": emails + ["alias@acme.com", "detached@acme.com", "U-SLACK", "ana@acme.com.evil"],
                       "domains": domains + ["irrelevant.com.evil"],
                       "person_names": ["Untrusted Injected Name"], "organization_names": ["Other Corp"]}).encode()
    assert serve.handle_linear(db, body, context) == (200, {"issues": []})
    assert context.calls[-1][2:] == (["Ana Silva"], ["Acme Inc"])
    assert db.total_changes == changes and tuple(db.iterdump()) == before
    assert db.execute("SELECT count(*) FROM connections").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM threads").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM activities").fetchone()[0] == 0
    serve.handle_linear(db, b'{"emails":["ANA@ACME.COM"]}', context)
    assert context.calls[-1] == (["ana@acme.com"], [], ["Ana Silva"], [])
    serve.handle_linear(db, b'{"domains":["ACME.COM"]}', context)
    assert context.calls[-1] == ([], ["acme.com"], [], ["Acme Inc"])


def test_names_are_resolved_again_after_rename_and_new_ambiguity(database):
    _, db = database
    with db:
        db.execute("INSERT INTO persons (id, display_name, created_at) VALUES (1, 'Ana Silva', 'now')")
        db.execute("INSERT INTO identities (person_id, kind, value) VALUES (1, 'email', 'ana@acme.com')")
        db.execute("INSERT INTO organizations VALUES (1, 'acme.com', 'Acme Inc', 'now')")
    context = Context()
    body = b'{"emails":["ana@acme.com"],"domains":["acme.com"]}'
    serve.handle_linear(db, body, context)
    assert context.calls[-1][2:] == (["Ana Silva"], ["Acme Inc"])
    with db:
        db.execute("UPDATE persons SET display_name = 'Ana Pereira' WHERE id = 1")
        db.execute("UPDATE organizations SET name = 'Acme Group' WHERE id = 1")
    serve.handle_linear(db, body, context)
    assert context.calls[-1][2:] == (["Ana Pereira"], ["Acme Group"])
    with db:
        db.execute("INSERT INTO persons (id, display_name, created_at) VALUES (2, ' ana  PEREIRA ', 'now')")
        db.execute("INSERT INTO organizations VALUES (2, 'other.com', ' acme  GROUP ', 'now')")
    before, changes = tuple(db.iterdump()), db.total_changes
    serve.handle_linear(db, body, context)
    assert context.calls[-1][2:] == ([], [])
    assert db.total_changes == changes and tuple(db.iterdump()) == before


@pytest.mark.parametrize("error", [linear.LinearError("synthetic private response"),
                                 OSError("synthetic secret header"), ValueError("synthetic payload"),
                                 KeyError("synthetic private field"), IndexError("synthetic private index"),
                                 TypeError("synthetic private type")])
def test_adapter_errors_are_safe_502(database, error):
    _, db = database
    before, changes = tuple(db.iterdump()), db.total_changes
    assert serve.handle_linear(db, b'{"domains":["acme.com"]}', Context(error)) == (
        502, {"error": "Linear context could not be loaded"})
    assert db.total_changes == changes and tuple(db.iterdump()) == before


def test_optional_synthetic_systemd_credential(database, tmp_path, monkeypatch):
    _, db = database
    credentials = tmp_path / "credentials"
    credentials.mkdir()
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(credentials))
    assert serve.linear_context(db) is None
    key = credentials / "linear-token"
    key.write_text("  \n")
    assert serve.linear_context(db) is None
    key.write_text("synthetic-linear-key\n")
    client, context, calls = object(), object(), []
    monkeypatch.setattr(serve.linear, "Client", lambda token: calls.append(token) or client)
    monkeypatch.setattr(serve.linear, "Context", lambda given: calls.append(given) or context)
    before, changes = tuple(db.iterdump()), db.total_changes
    assert serve.linear_context(db) is context
    assert calls == ["synthetic-linear-key", client]
    assert db.total_changes == changes and tuple(db.iterdump()) == before
    monkeypatch.delenv("CREDENTIALS_DIRECTORY")
    assert serve.linear_context(db) is None


def test_linear_and_existing_routes_over_private_unix_socket(database, tmp_path):
    path, db = database
    with db:
        db.execute("INSERT INTO persons (id, display_name, created_at) VALUES (1, 'Ana Silva', 'now')")
        db.execute("INSERT INTO identities (person_id, kind, value) VALUES (1, 'email', 'ana@acme.com')")
        db.execute("INSERT INTO organizations VALUES (1, 'acme.com', 'Acme Inc', 'now')")
        db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                   " VALUES (1, 'slack', 'T-SYNTHETIC', 'U-SELF', 'Slack', 'now')")
        db.execute("INSERT INTO composition_drafts (id, revision, connection_id, kind, sender, \"to\", cc,"
                   " subject, body, created_at, updated_at) VALUES (1, 1, 1, 'slack', 'Slack', 'C0', '',"
                   " NULL, 'keep draft', 'now', 'now')")
    before = tuple(db.iterdump())
    context = Context()
    context.result = {"issues": [{
        "id": "synthetic-issue", "identifier": "DEV-1", "title": "Synthetic issue",
        "url": "https://linear.app/synthetic/issue/DEV-1", "state": "Backlog", "priority": "High",
        "assignee": "", "project": "", "updated": "", "reasons": ["email: ana@acme.com"], "requests": [],
    }]}

    class LegacyContext:
        def get(self, emails, domains):
            assert emails == ["ana@acme.com"] and domains == ["acme.com"]
            return {"records": []}

    contexts = {"linear": context, "attio": LegacyContext(), "tana": LegacyContext()}
    sock = str(tmp_path / "inbox.sock")
    server = serve.reply_server(path, sock, serve.Senders(), contexts)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        assert stat.S_IMODE(os.stat(sock).st_mode) == 0o600
        body = b'{"emails":["ana@acme.com"],"domains":["acme.com"]}'
        assert post(sock, "/linear", body) == (200, context.result)
        assert context.calls == [(["ana@acme.com"], ["acme.com"], ["Ana Silva"], ["Acme Inc"])]
        assert post(sock, "/linear", b'{"emails":null}')[0] == 400
        assert post(sock, "/linear", b"{}") == (200, {"issues": []})
        assert len(context.calls) == 1
        context.error = linear.LinearError("synthetic private provider data")
        assert post(sock, "/linear", body) == (502, {"error": "Linear context could not be loaded"})
        contexts["linear"] = None
        assert post(sock, "/linear", body) == (503, {"error": "Linear is not connected"})
        for source in ("attio", "tana"):
            assert post(sock, f"/{source}", body) == (200, {"records": []})
        for route in ("reply", "send-draft", "slack-conversations", "attio-people"):
            assert post(sock, f"/{route}", b"{}")[0] == 400
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
    assert tuple(db.iterdump()) == before


def test_run_composes_linear_context(database, monkeypatch):
    path, _ = database
    seen = {}
    server = SimpleNamespace(serve_forever=lambda: None, shutdown=lambda: None)
    monkeypatch.setattr(serve, "fastmail_client", lambda db: None)
    for name in ("attio", "tana", "linear"):
        monkeypatch.setattr(serve, f"{name}_context", lambda db, name=name: name)
    monkeypatch.setattr(serve, "hooks_server", lambda *args, **kwargs: server)

    def reply_server(db_path, socket_path, senders, contexts):
        seen.update(contexts)
        return server

    def interrupt(_):
        raise KeyboardInterrupt

    monkeypatch.setattr(serve, "reply_server", reply_server)
    monkeypatch.setattr(serve, "credential", lambda name: None)
    monkeypatch.setattr(serve, "threading", SimpleNamespace(
        Event=threading.Event, Thread=lambda **kwargs: SimpleNamespace(start=lambda: None)))
    monkeypatch.setattr(serve, "time", SimpleNamespace(sleep=interrupt))
    serve.run(path, "/unused-synthetic.sock", "127.0.0.1", 0)
    assert seen == {"tana": "tana", "attio": "attio", "linear": "linear"}
