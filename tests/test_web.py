import http.client
import io
import json
import select
import signal
import subprocess
import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from mairg.language import AppError
from mairg.web import make_wsgi_app, serve_project, validate_routes


class Project:
    def __init__(self, handler=None, routes=None, startup=None):
        self.manifest = {"routes": routes if routes is not None else [{"method": "POST", "path": "/items/{item}", "export": "handle"}], "startup": startup}
        self.calls = []
        self.handler = handler or (lambda request: {"status": 200, "headers": {}, "body": request})

    def call(self, export, args, *, state_dir=None):
        self.calls.append((export, args, state_dir))
        if export == "init":
            return None
        return self.handler(args[0])


def request(app, method="POST", path="/items/x", body=b"", query="", extra=None):
    environ = {"REQUEST_METHOD": method, "PATH_INFO": path.encode("utf-8").decode("latin-1"),
               "QUERY_STRING": query, "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body)}
    environ.update(extra or {})
    output = []
    data = b"".join(app(environ, lambda status, headers: output.append((status, dict(headers)))))
    return int(output[0][0].split()[0]), output[0][1], data


class WebTests(unittest.TestCase):
    def test_exact_input_unicode_framing_and_request_isolation(self):
        project = Project()
        app = make_wsgi_app(project, state_dir="explicit")
        status, headers, data = request(app, path="/items/é", body='{"note":"🦉"}'.encode(), query="tag=a&tag=b&empty=", extra={"HTTP_X_TEST": "yes"})
        self.assertEqual(status, 200)
        self.assertEqual(int(headers["Content-Length"]), len(data))
        self.assertIn("🦉".encode(), data)
        result = json.loads(data)
        self.assertEqual(set(result), {"method", "path", "params", "query", "headers", "body"})
        self.assertEqual(result["params"], {"item": "é"})
        self.assertEqual(result["query"], {"tag": ["a", "b"], "empty": [""]})
        self.assertEqual(result["headers"]["x-test"], "yes")
        request(app)
        self.assertEqual(len(project.calls), 2)
        self.assertIsNot(project.calls[0][1][0], project.calls[1][1][0])

    def test_bad_input_never_invokes_handler(self):
        project = Project()
        app = make_wsgi_app(project)
        for data in [b"{", b'{"x":1,"x":2}', b"NaN", b"\xff"]:
            self.assertEqual(request(app, body=data)[0], 400)
        self.assertEqual(request(app, query="x=%GG")[0], 400)
        self.assertEqual(request(app, body=b" " * (1024 * 1024 + 1))[0], 413)
        self.assertEqual(project.calls, [])

    def test_routes_literal_precedence_ambiguity_and_exact_method(self):
        project = Project(routes=[{"method": "GET", "path": "/items/{item}", "export": "parameter"},
                                  {"method": "GET", "path": "/items/new", "export": "literal"}])
        app = make_wsgi_app(project)
        self.assertEqual(request(app, method="GET", path="/items/new")[0], 200)
        self.assertEqual(project.calls[-1][0], "literal")
        self.assertEqual(request(app, method="HEAD", path="/items/new")[0], 405)
        self.assertEqual(request(app, method="GET", path="/missing")[0], 404)
        project.manifest["routes"] = [{"method": "GET", "path": "/a/{x}", "export": "x"}, {"method": "GET", "path": "/{y}/b", "export": "y"}]
        with self.assertRaises(AppError):
            validate_routes(project.manifest)

    def test_response_schema_framing_and_header_rejection(self):
        invalid = [{"status": True, "headers": {}, "body": None}, {"status": 200, "headers": {}, "body": None, "extra": 1},
                   {"status": 204, "headers": {}, "body": "invalid"}]
        for name, value in [("Content-Length", "1"), ("content-TYPE", "text/plain"), ("Connection", "close"),
                            ("Transfer-Encoding", "chunked"), ("Trailer", "x"), ("Trailers", "x"), ("X-Fail", "bad\r\nX: yes"),
                            ("X-Fail", "🦉"), ("X-🐍", "yes"), ("X Bad", "yes"), ("X-Fail", "a\x00b")]:
            invalid.append({"status": 200, "headers": {name: value}, "body": None})
        for result in invalid:
            with self.subTest(result=result):
                app = make_wsgi_app(Project(handler=lambda request, result=result: result))
                self.assertEqual(request(app)[0], 500)
        app = make_wsgi_app(Project(handler=lambda request: {"status": 204, "headers": {"X-Note": "café"}, "body": None}))
        self.assertEqual(request(app)[2], b"")

    def test_large_integer_response_uses_canonical_json_encoder(self):
        value = 10 ** 5000
        app = make_wsgi_app(Project(handler=lambda request: {"status": 200, "headers": {}, "body": value}))
        status, headers, data = request(app)
        self.assertEqual(status, 200)
        self.assertEqual(data, b"1" + b"0" * 5000)
        self.assertEqual(int(headers["Content-Length"]), 5001)

    def test_startup_failure_never_creates_socket(self):
        project = Project(startup="init")
        with patch.object(project, "call", side_effect=AppError("startup_failed", "no")), patch("waitress.create_server") as create, self.assertRaises(AppError):
            serve_project(project, "127.0.0.1", 0)
        create.assert_not_called()

    def test_startup_before_socket_readiness_and_cleanup(self):
        project = Project(startup="init")
        events = []
        class Dispatcher:
            def shutdown(self, *, timeout):
                events.append(("shutdown", timeout))
        class Server:
            effective_port = "41234"
            task_dispatcher = Dispatcher()
            adj = SimpleNamespace(asyncore_loop_timeout=1, asyncore_use_poll=False)
            _map = {}
            @property
            def asyncore(self):
                return self
            def loop(self, **kwargs):
                events.append("run")
                raise KeyboardInterrupt()
            def close(self):
                events.append("close")
        def create(application, **kwargs):
            self.assertEqual(project.calls[0][0], "init")
            events.append("create")
            return Server()
        output = io.StringIO()
        with patch("waitress.create_server", side_effect=create), patch("sys.stdout", output), self.assertRaises(KeyboardInterrupt):
            serve_project(project, "127.0.0.1", 0, state_dir="explicit")
        self.assertEqual(events, ["create", "run", "close", ("shutdown", 2)])
        self.assertEqual(json.loads(output.getvalue()), {"event": "ready", "host": "127.0.0.1", "port": 41234})

    def test_actual_waitress_active_request_has_bounded_drain_and_no_replay(self):
        script = """
import json, time
from tests.test_web import Project
from mairg.web import serve_project
def handler(request):
    print(json.dumps({'event':'entered'}), flush=True)
    time.sleep(10)
    return {'status':200, 'headers':{}, 'body':None}
try:
    serve_project(Project(handler=handler), '127.0.0.1', 0)
except KeyboardInterrupt:
    raise SystemExit(130)
"""
        process = subprocess.Popen([sys.executable, "-u", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        outcome = []
        client = None
        try:
            self.assertTrue(select.select([process.stdout], [], [], 3)[0], "server readiness deadline")
            port = json.loads(process.stdout.readline())["port"]
            def send():
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=4)
                try:
                    connection.request("POST", "/items/slow", body=b"{}")
                    outcome.append(connection.getresponse().status)
                except (OSError, http.client.HTTPException) as exc:
                    outcome.append(type(exc).__name__)
                finally:
                    connection.close()
            client = threading.Thread(target=send)
            client.start()
            self.assertTrue(select.select([process.stdout], [], [], 3)[0], "handler entry deadline")
            self.assertEqual(json.loads(process.stdout.readline()), {"event": "entered"})
            started = time.monotonic()
            process.send_signal(signal.SIGINT)
            _, errors = process.communicate(timeout=4)
            self.assertLess(time.monotonic() - started, 3.5)
            self.assertEqual(process.returncode, 130, errors)
            client.join(timeout=4)
            self.assertFalse(client.is_alive())
            self.assertEqual(outcome, ["RemoteDisconnected"])
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
            if client is not None:
                client.join(timeout=4)

    def test_actual_waitress_unicode_input_and_invalid_response(self):
        script = """
from tests.test_web import Project
from mairg.web import serve_project
project = Project(handler=lambda request: {
    'status':200, 'headers':{'Content-Length':'1'} if request['params']['item']=='bad' else {}, 'body':request})
try:
    serve_project(project, '127.0.0.1', 0)
except KeyboardInterrupt:
    raise SystemExit(130)
"""
        process = subprocess.Popen([sys.executable, "-u", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertTrue(select.select([process.stdout], [], [], 3)[0], "server readiness deadline")
            line = process.stdout.readline()
            self.assertTrue(line, "server exited before readiness")
            port = json.loads(line)["port"]
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            payload = '{"note":"é🦉"}'.encode()
            connection.request("POST", "/items/%C3%A9?tag=a&tag=b", body=payload, headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            data = response.read()
            self.assertEqual(response.status, 200)
            self.assertEqual(int(response.getheader("Content-Length")), len(data))
            self.assertEqual(json.loads(data)["params"], {"item": "é"})
            connection.close()
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            connection.request("POST", "/items/bad", body=b"{}")
            response = connection.getresponse()
            self.assertEqual(response.status, 500)
            self.assertIsInstance(json.loads(response.read())["error"], dict)
            connection.close()
        finally:
            process.send_signal(signal.SIGINT)
            try:
                _, errors = process.communicate(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                self.fail("server did not finish bounded shutdown")
        self.assertEqual(process.returncode, 130, errors)


if __name__ == "__main__":
    unittest.main()
