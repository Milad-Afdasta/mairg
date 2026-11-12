"""WSGI transport and actual Waitress serving; application logic stays in graphs."""
from __future__ import annotations

import json
import re
from http import HTTPStatus
from urllib.parse import parse_qs

from .language import AppError, decode_json, encode_json, json_copy

MAX_HTTP_BYTES = 1024 * 1024
_TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z", re.ASCII)
_PARAMETER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}\Z", re.ASCII)
_BAD_PERCENT = re.compile(r"%(?![0-9A-Fa-f]{2})")
_RESERVED = {"content-length", "content-type", "connection", "keep-alive",
             "proxy-authenticate", "proxy-authorization", "te", "trailer",
             "trailers", "transfer-encoding", "upgrade"}


def validate_routes(manifest):
    routes: list[tuple[str, list[tuple[bool, str]], int, str]] = []
    for route in manifest["routes"]:
        method, path, export = route["method"], route["path"], route["export"]
        if type(method) is not str or not _TOKEN.fullmatch(method) or method != method.upper():
            raise AppError("validation_error", "route method must be an uppercase HTTP token")
        if type(path) is not str or not path.startswith("/") or "?" in path or "#" in path:
            raise AppError("validation_error", "route path must be absolute and have no query or fragment")
        segments = path.split("/")[1:]
        pattern = []
        names = set()
        for segment in segments:
            match = _PARAMETER.fullmatch(segment)
            if match:
                name = match.group(1)
                if name in names:
                    raise AppError("validation_error", "route parameter names must be distinct")
                names.add(name)
                pattern.append((True, name))
            else:
                if "{" in segment or "}" in segment:
                    raise AppError("validation_error", "route parameters must occupy a whole segment")
                pattern.append((False, segment))
        score = sum(not parameter for parameter, _ in pattern)
        for other_method, other_pattern, other_score, _ in routes:
            if method == other_method and score == other_score and len(pattern) == len(other_pattern):
                if all(a[0] or b[0] or a[1] == b[1] for a, b in zip(pattern, other_pattern, strict=True)):
                    raise AppError("validation_error", "equal-specificity route patterns overlap")
        routes.append((method, pattern, score, export))
    return sorted(routes, key=lambda route: -route[2])


def _match(pattern, path):
    segments = path.split("/")[1:]
    if not path.startswith("/") or len(segments) != len(pattern):
        return None
    params = {}
    for (parameter, value), segment in zip(pattern, segments, strict=True):
        if parameter:
            if not segment:
                return None
            params[value] = segment
        elif value != segment:
            return None
    return params


def _error(status, code, message):
    return {"status": status, "headers": {}, "body": AppError(code, message).as_dict()}


def _response(result):
    if type(result) is not dict or set(result) != {"status", "headers", "body"}:
        raise AppError("response_error", "HTTP result must contain exactly status, headers, body")
    status = result["status"]
    if type(status) is not int or not 200 <= status <= 599:
        raise AppError("response_error", "HTTP status must be an integer from 200 to 599")
    headers = result["headers"]
    if type(headers) is not dict:
        raise AppError("response_error", "HTTP headers must be a record of strings")
    checked_headers = []
    seen = set()
    for name, value in headers.items():
        if type(name) is not str or not _TOKEN.fullmatch(name):
            raise AppError("response_error", "HTTP header names must be ASCII tokens")
        folded = name.lower()
        if folded in _RESERVED or folded in seen:
            raise AppError("response_error", "application header conflicts with transport ownership")
        seen.add(folded)
        if type(value) is not str:
            raise AppError("response_error", "HTTP header values must be strings")
        try:
            value.encode("latin-1")
        except UnicodeError as exc:
            raise AppError("response_error", "HTTP header values must encode as Latin-1") from exc
        if any((ord(char) < 32 and char != "\t") or ord(char) == 127 for char in value):
            raise AppError("response_error", "HTTP header values contain forbidden controls")
        checked_headers.append((name, value))
    body = json_copy(result["body"])
    if status in {204, 304}:
        if body is not None:
            raise AppError("response_error", "204 and 304 responses require null body")
        data = b""
    else:
        data = encode_json(body, ensure_ascii=False).encode("utf-8")
    checked_headers.extend((("Content-Type", "application/json; charset=utf-8"), ("Content-Length", str(len(data)))))
    try:
        phrase = HTTPStatus(status).phrase
    except ValueError:
        phrase = "Application Response"
    return f"{status} {phrase}", checked_headers, data


def _request(environ, params):
    try:
        path = environ.get("PATH_INFO", "").encode("latin-1").decode("utf-8", errors="strict")
        query_source = environ.get("QUERY_STRING", "")
        if _BAD_PERCENT.search(query_source):
            raise ValueError("malformed percent escape")
        query = parse_qs(query_source, keep_blank_values=True, encoding="utf-8", errors="strict", max_num_fields=100_000)
        raw_length = environ.get("CONTENT_LENGTH", "")
        if raw_length:
            if not raw_length.isascii() or not raw_length.isdecimal():
                raise ValueError("invalid content length")
            length = int(raw_length)
            if length > MAX_HTTP_BYTES:
                return None, _error(413, "resource_limit", "HTTP input exceeds 1 MiB")
            data = environ["wsgi.input"].read(length)
            if len(data) != length:
                raise ValueError("truncated request body")
        elif environ.get("wsgi.input_terminated"):
            data = environ["wsgi.input"].read(MAX_HTTP_BYTES + 1)
        else:
            data = b""
        if len(data) > MAX_HTTP_BYTES:
            return None, _error(413, "resource_limit", "HTTP input exceeds 1 MiB")
        body = None if not data else decode_json(data.decode("utf-8", errors="strict"))
        headers = {}
        for name, value in environ.items():
            if name.startswith("HTTP_"):
                headers[name[5:].replace("_", "-").lower()] = value
        for name in ("CONTENT_TYPE", "CONTENT_LENGTH"):
            if name in environ and environ[name] != "":
                headers[name.replace("_", "-").lower()] = environ[name]
        return json_copy({"method": environ["REQUEST_METHOD"], "path": path, "params": params,
                          "query": query, "headers": headers, "body": body}), None
    except (AppError, ValueError, UnicodeError, KeyError, OSError) as exc:
        return None, _error(400, "invalid_request", str(exc))


def make_wsgi_app(project, *, state_dir=None):
    """Validate routes and finish startup before exposing a reusable callable."""
    manifest = project.manifest
    routes = validate_routes(manifest)
    if manifest["startup"] is not None:
        project.call(manifest["startup"], [], state_dir=state_dir)

    def application(environ, start_response):
        try:
            path = environ.get("PATH_INFO", "").encode("latin-1").decode("utf-8", errors="strict")
            method = environ.get("REQUEST_METHOD", "")
            matched = [(route, _match(route[1], path)) for route in routes]
            matching = [(route, params) for route, params in matched if params is not None]
            chosen = next(((route, params) for route, params in matching if route[0] == method), None)
            if chosen is None:
                result = _error(405, "method_not_allowed", "method is not allowed for this path") if matching else _error(404, "not_found", "route not found")
            else:
                route, params = chosen
                request, failure = _request(environ, params)
                result = failure if failure is not None else project.call(route[3], [request], state_dir=state_dir)
            status, headers, data = _response(result)
        except UnicodeError:
            status, headers, data = _response(_error(400, "invalid_request", "request path is not valid UTF-8"))
        except Exception as exc:
            error = exc.as_dict() if isinstance(exc, AppError) else AppError("internal_error", "application failed").as_dict()
            status, headers, data = _response({"status": 500, "headers": {}, "body": error})
        start_response(status, headers)
        return [data]

    return application


def serve_project(project, host, port, *, state_dir=None):
    """Serve with Waitress; cleanup drains active handlers for at most two seconds."""
    from waitress import create_server  # type: ignore[import-untyped]

    application = make_wsgi_app(project, state_dir=state_dir)
    server = create_server(application, host=host, port=port, max_request_body_size=MAX_HTTP_BYTES,
                           expose_tracebacks=False)
    try:
        print(json.dumps({"event": "ready", "host": host, "port": int(server.effective_port)}), flush=True)
        server.asyncore.loop(timeout=server.adj.asyncore_loop_timeout, map=server._map,
                             use_poll=server.adj.asyncore_use_poll)
    finally:
        try:
            server.close()
        finally:
            server.task_dispatcher.shutdown(timeout=2)
