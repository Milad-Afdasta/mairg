"""Independent real-transport and persisted-state acceptance of MAIRG-M4.

All state, ports, subprocesses and artifacts belong to each test. No user DB is read.
"""
from __future__ import annotations

import concurrent.futures
import http.client
import json
import select
import signal
import socket
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from contextlib import closing, contextmanager
from pathlib import Path

from mairg.compiler import compile_project
from mairg.hosts import HostRuntime
from mairg.language import AppError
from mairg.project import build_project

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "examples" / "inventory"
PYTHON = str(ROOT / ".venv" / "bin" / "python")


@contextmanager
def serving(command, *, cwd=ROOT):
    """Use the actual socket selected by Waitress, never guess/free a port."""
    process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        if not select.select([process.stdout], [], [], 5)[0]:
            raise AssertionError("Waitress did not report readiness within 5 seconds")
        line = process.stdout.readline()
        if not line:
            _, error = process.communicate(timeout=3)
            raise AssertionError(f"server exited before readiness: {error}")
        ready = json.loads(line)
        assert ready["event"] == "ready" and ready["host"] == "127.0.0.1"
        yield ready["port"]
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
        try:
            _, error = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise AssertionError("owned server failed bounded shutdown")
        assert process.returncode == 130, f"unexpected server exit {process.returncode}: {error}"


def request(port, method, path, body=None, *, raw=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        payload = raw if raw is not None else (None if body is None else json.dumps(body, ensure_ascii=False).encode())
        connection.request(method, path, payload, {"Content-Type": "application/json"})
        response = connection.getresponse()
        data = response.read()
        assert int(response.getheader("Content-Length")) == len(data)
        assert response.getheader("Content-Type") == "application/json; charset=utf-8"
        return response.status, json.loads(data), data
    finally:
        connection.close()


class IndependentPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mairg-independent-persistence-")
        self.directory = Path(self.temp.name)
        self.state = self.directory / "state"
        self.project = compile_project(APP)

    def tearDown(self):
        self.temp.cleanup()

    def source_server(self, state=None):
        return serving([PYTHON, "-u", "-m", "mairg", "serve", str(APP), "--host", "127.0.0.1", "--port", "0", "--state-dir", str(state or self.state)])

    def init(self):
        self.assertEqual(self.project.call("init", [], state_dir=self.state), {"ready": True, "version": 1})

    def db(self, state=None):
        return sqlite3.connect((state or self.state) / "db" / "inventory.sqlite3")

    def test_actual_inventory_crud_validation_race_and_restart(self):
        self.init()
        name = "O'Reilly 🦉 café'); DROP TABLE items;--"
        with self.source_server() as port:
            status, created, encoded = request(port, "POST", "/items", {"name": name, "stock": 1})
            self.assertEqual((status, created), (200, {"id": 1, "name": name, "stock": 1}))
            self.assertIn("🦉".encode(), encoded)
            for invalid in [{"name": "\u2003\t", "stock": 1}, {"name": "bad", "stock": -1}, {"name": "bad", "stock": True}, {"name": 1, "stock": 1}]:
                with self.subTest(invalid=invalid):
                    self.assertEqual(request(port, "POST", "/items", invalid)[0], 400)
            for malformed in [b'{"name":"a","name":"b","stock":1}', b'\xff', b'NaN', b'{']:
                self.assertEqual(request(port, "POST", "/items", raw=malformed)[0], 400)
            # Waitress rejects oversized Content-Length before receiving a body.
            oversized = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                oversized.putrequest("POST", "/items")
                oversized.putheader("Content-Length", str(1024 * 1024 + 1))
                oversized.endheaders()
                response = oversized.getresponse()
                self.assertEqual(response.status, 413)
                response.read()
            finally:
                oversized.close()
            barrier = threading.Barrier(2)
            def reserve(note):
                barrier.wait(timeout=3)
                return request(port, "POST", "/reservations", {"item_id": 1, "quantity": 1, "note": note})[:2]
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(reserve, ["first's 🦉", "second's é"]))
            self.assertEqual(sorted(status for status, _ in outcomes), [200, 409])
            self.assertEqual(request(port, "GET", "/items/1")[1]["stock"], 0)
            self.assertEqual(request(port, "DELETE", "/items/1")[0], 400)  # FK owner rejects.
            self.assertEqual(request(port, "GET", "/items/not-an-id")[0], 400)
            self.assertEqual(request(port, "GET", "/items/999")[0], 404)
            self.assertEqual(request(port, "PUT", "/items/1", {"name": name, "stock": 8})[0], 200)
            temporary = request(port, "POST", "/items", {"name": "temporary", "stock": 0})[1]
            self.assertEqual(request(port, "DELETE", f"/items/{temporary['id']}")[1], temporary)
            self.assertEqual(request(port, "PATCH", "/items")[0], 405)
            self.assertEqual(request(port, "GET", "/missing")[0], 404)
        with closing(self.db()) as database:
            self.assertEqual(database.execute("SELECT id,name,stock FROM items").fetchall(), [(1, name, 8)])
            saved = database.execute("SELECT item_id,quantity,note FROM reservations").fetchall()
            self.assertEqual(len(saved), 1)
        with self.source_server() as restarted:
            self.assertEqual(request(restarted, "GET", "/items")[1], [{"id": 1, "name": name, "stock": 8}])
            self.assertEqual(len(request(restarted, "GET", "/reservations")[1]), 1)

    def test_later_graph_failure_rolls_back_both_writes_visible_to_http_and_raw_db(self):
        self.init()
        self.project.call("create_item", ["rollback", 3], state_dir=self.state)
        with self.source_server() as port:
            process = subprocess.run([PYTHON, "-m", "mairg", "run", str(APP), "fail_reservation", "--args", '[1,2,"later failure"]', "--state-dir", str(self.state)], cwd=ROOT, capture_output=True, text=True, timeout=5)
            self.assertEqual(process.returncode, 2, process.stdout)
            self.assertEqual(json.loads(process.stderr)["error"]["code"], "deliberate_failure")
            self.assertEqual(request(port, "GET", "/items/1")[1]["stock"], 3)
            self.assertEqual(request(port, "GET", "/reservations")[1], [])
        with closing(self.db()) as database:
            self.assertEqual(database.execute("SELECT stock FROM items").fetchall(), [(3,)])
            self.assertEqual(database.execute("SELECT * FROM reservations").fetchall(), [])

    def test_schema_equivalence_retained_v1_source_and_external_bundle_server(self):
        master = json.loads((APP / "schema.json").read_text())
        migration = json.loads((APP / "migrations/001.json").read_text())
        self.state.joinpath("db").mkdir(parents=True)
        with closing(self.db()) as db, closing(sqlite3.connect(":memory:")) as canonical:
            for statement in migration:
                db.execute(statement)
            for statement in master:
                canonical.execute(statement)
            schema = "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
            self.assertEqual(db.execute(schema).fetchall(), canonical.execute(schema).fetchall())
            db.execute("INSERT INTO items(id,name,stock) VALUES(?,?,?)", [2147483647, "retained ' é 🦉", 9223372036854775807])
            db.execute("INSERT INTO reservations(id,item_id,quantity,note) VALUES(?,?,?,?)", [19, 2147483647, 1, "prior order's note"])
            db.commit()
            self.assertEqual(db.execute("PRAGMA user_version").fetchone(), (1,))
        expected = [{"id": 2147483647, "name": "retained ' é 🦉", "stock": 9223372036854775807}]
        with self.source_server() as source:
            self.assertEqual(request(source, "GET", "/items")[1], expected)
        bundle = self.directory / "distribution"
        build_project(self.project, bundle)
        # Isolated mode plus external cwd cannot resolve the checkout's mairg package.
        with serving([PYTHON, "-I", "-u", str(bundle / "run.py"), "serve", "--port", "0", "--state-dir", str(self.state)], cwd=self.directory) as port:
            self.assertEqual(request(port, "GET", "/items")[1], expected)
            self.assertEqual(request(port, "GET", "/reservations")[1][0]["note"], "prior order's note")
        with closing(self.db()) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM items").fetchone(), (1,))
            self.assertEqual(db.execute("PRAGMA user_version").fetchone(), (1,))
        self.assertFalse(list(bundle.rglob("*.pyc")))
        self.assertFalse((bundle / "mairg.json").exists())
        self.assertFalse((bundle / "mairg" / "compiler.py").exists())

    def test_unsupported_and_malformed_schema_refuse_startup_before_ready(self):
        self.init()
        with closing(self.db()) as db:
            db.execute("PRAGMA user_version=2")
        for bad_shape in ["newer", "wrong_table"]:
            if bad_shape == "wrong_table":
                with closing(self.db()) as db:
                    db.execute("PRAGMA user_version=1")
                    db.execute("ALTER TABLE items ADD COLUMN removed TEXT")
            process = subprocess.run([PYTHON, "-m", "mairg", "serve", str(APP), "--port", "0", "--state-dir", str(self.state)], cwd=ROOT, capture_output=True, text=True, timeout=5)
            self.assertEqual(process.returncode, 2)
            self.assertNotIn('"ready"', process.stdout)
            self.assertEqual(json.loads(process.stderr)["error"]["code"], "unsupported_schema")

    def test_sql_authorizer_escape_forms_and_failure_poison_are_real_sqlite(self):
        self.init()
        hosts = HostRuntime(self.project, state_dir=self.state)
        try:
            for sql in ["END TRANSACTION", "RELEASE SAVEPOINT x", "PRAGMA main.user_version(8)", "PRAGMA main.journal_mode('OFF')", "PRAGMA writable_schema=ON", "VACUUM INTO '" + str(self.directory / "forbidden.db") + "'", "WITH n(x) AS (SELECT 1) INSERT INTO items(name,stock) SELECT 'bad',x FROM n RETURNING id", "ATTACH 'file:other?mode=memory&cache=shared' AS other", "SELECT load_extension('absent')"]:
                with self.subTest(sql=sql), self.assertRaises(AppError):
                    hosts.call("query", [sql, []])
            with self.assertRaises(AppError):
                with hosts.transaction():
                    hosts.call("execute", ["INSERT INTO items(name,stock) VALUES(?,?)", ["must rollback", 1]])
                    try:
                        hosts.call("query", ["PRAGMA main.user_version(8)", []])
                    except AppError:
                        pass
            self.assertEqual(hosts.call("query", ["SELECT count(*) AS n FROM items", []]), [{"n": 0}])
            self.assertEqual(hosts.call("query", ["PRAGMA user_version", []]), [{"user_version": 1}])
            self.assertFalse((self.directory / "forbidden.db").exists())
        finally:
            hosts.close()

    def test_db_sidecar_aliases_and_replaced_directory_fail_without_mutation(self):
        self.init()
        sidecar = self.state / "db" / "inventory.sqlite3-wal"
        outside = self.directory / "sentinel"
        outside.write_bytes(b"sentinel")
        sidecar.symlink_to(outside)
        with self.assertRaises(AppError):
            self.project.call("list_items", [], state_dir=self.state)
        self.assertEqual(outside.read_bytes(), b"sentinel")
        sidecar.unlink()
        hosts = HostRuntime(self.project, state_dir=self.state)
        try:
            hosts.call("query", ["SELECT 1 AS n", []])
            (self.state / "db").rename(self.state / "old-db")
            (self.state / "db").mkdir()
            with self.assertRaises(AppError):
                hosts.call("execute", ["CREATE TABLE no_effect(x)", []])
            self.assertFalse((self.state / "db" / "inventory.sqlite3").exists())
        finally:
            hosts.close()

    def test_real_graph_transport_rejects_bad_responses_after_one_commit_and_disconnect_never_replays(self):
        app = self.directory / "transport-app"
        app.mkdir()
        document = {"format": "mairg-app-1", "functions": [{
            "id": 0, "params": ["record"], "result": "record", "nodes": [
                {"id": 0, "op": "param", "args": [], "index": 0},
                {"id": 1, "op": "const", "args": [], "value": "INSERT INTO events(note) VALUES('one invocation')"},
                {"id": 2, "op": "list", "args": []},
                {"id": 3, "op": "host", "args": [1, 2], "binding": "execute"},
                {"id": 4, "op": "const", "args": [], "value": "body"},
                {"id": 5, "op": "field", "args": [0, 4], "after": [3]},
            ], "return": 5}], "exports": {"response": 0}}
        manifest = {"format": "mairg-project-1", "name": "transport-probe", "program": "program.json",
                    "bindings": {"execute": "sqlite.execute"}, "database": "transport.sqlite3", "assets": [],
                    "startup": None, "tests": [], "routes": [{"method": "POST", "path": "/", "export": "response"}]}
        (app / "mairg.json").write_text(json.dumps(manifest))
        (app / "program.json").write_text(json.dumps(document))
        self.state.joinpath("db").mkdir(parents=True)
        dbpath = self.state / "db" / "transport.sqlite3"
        with closing(sqlite3.connect(dbpath)) as db:
            db.execute("CREATE TABLE events(note TEXT)")
        command = [PYTHON, "-u", "-m", "mairg", "serve", str(app), "--port", "0", "--state-dir", str(self.state)]
        failures = [
            {"status": True, "headers": {}, "body": None},
            {"status": 204, "headers": {}, "body": "forbidden"},
            {"status": 304, "headers": {}, "body": []},
            {"status": 200, "headers": {"x": 1}, "body": None},
            {"status": 200, "headers": {"X": "yes", "x": "duplicate"}, "body": None},
        ]
        reserved = ["Content-Length", "content-TYPE", "Connection", "Keep-Alive", "Proxy-Authenticate",
                    "Proxy-Authorization", "TE", "Trailer", "Trailers", "Transfer-Encoding", "Upgrade"]
        failures += [{"status": 200, "headers": {name: "x"}, "body": None} for name in reserved]
        failures += [{"status": 200, "headers": {name: value}, "body": None} for name, value in
                     [("X-Bad", "line\r\nInjected: yes"), ("X-Bad", "\x00"), ("X-Bad", "🦉"), ("X Bad", "yes")]]
        with serving(command) as port:
            for count, result in enumerate(failures, 1):
                with self.subTest(result=result):
                    status, body, data = request(port, "POST", "/", result)
                    self.assertEqual(status, 500)
                    self.assertEqual(body["error"]["code"], "response_error")
                    with closing(sqlite3.connect(dbpath)) as db:
                        self.assertEqual(db.execute("SELECT count(*) FROM events").fetchone(), (count,))
            self.assertEqual(request(port, "POST", "/", raw=b'{"x":1,"x":2}')[0], 400)
            for status in [204, 304]:
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                try:
                    connection.request("POST", "/", json.dumps({"status": status, "headers": {}, "body": None}))
                    response = connection.getresponse()
                    self.assertEqual(response.status, status)
                    self.assertEqual(response.read(), b"")
                finally:
                    connection.close()
            # Reading the first response byte establishes handler completion/commit,
            # then a real socket disconnect must never replay that call.
            payload = json.dumps({"status": 200, "headers": {}, "body": "é🦉" * 100000}, ensure_ascii=False).encode()
            with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
                connection.sendall(b"POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: " + str(len(payload)).encode() + b"\r\n\r\n" + payload)
                self.assertTrue(connection.recv(1))
        with closing(sqlite3.connect(dbpath)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM events").fetchone(), (len(failures) + 3,))

    def test_bundle_asset_digest_fails_closed_and_runtime_has_no_business_dispatch(self):
        bundle = self.directory / "distribution"
        build_project(self.project, bundle)
        (bundle / "assets" / "schema.json").write_text("[]")
        process = subprocess.run([PYTHON, "-I", str(bundle / "run.py"), "init", "--args", "[]", "--state-dir", str(self.state)], cwd=self.directory, capture_output=True, text=True, timeout=5)
        self.assertEqual(process.returncode, 2)
        self.assertIn("digest mismatch", process.stderr)
        self.assertFalse(self.state.exists())
        for filename in ["hosts.py", "runtime.py", "web.py"]:
            text = (ROOT / "mairg" / filename).read_text()
            for business_name in ["inventory", "reservations", "item_id", "insufficient_stock"]:
                self.assertNotIn(business_name, text)


if __name__ == "__main__":
    unittest.main()
