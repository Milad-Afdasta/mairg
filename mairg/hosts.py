"""Generic, invocation-local file and SQLite resource owners."""
from __future__ import annotations

import csv
import io
import math
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .language import HOSTS, AppError, json_copy

MAX_BYTES = 16 * 1024 * 1024
MAX_ITEMS = 100_000
csv.field_size_limit(MAX_BYTES)
IMPLEMENTED_HOSTS = {"file.read_text", "file.read_csv", "file.write_text", "sqlite.query", "sqlite.execute"}
if IMPLEMENTED_HOSTS != set(HOSTS):
    raise RuntimeError("host implementation differs from canonical catalog")
_READ_PRAGMAS = {
    "table_info", "table_xinfo", "index_list", "index_info", "index_xinfo",
    "foreign_key_list", "foreign_key_check", "integrity_check", "quick_check",
    "database_list", "compile_options", "function_list", "module_list",
    "pragma_list", "collation_list",
}
_READ_SETTINGS = {
    "user_version", "application_id", "schema_version", "data_version",
    "foreign_keys", "synchronous", "journal_mode", "busy_timeout", "page_count",
    "freelist_count", "page_size", "encoding", "auto_vacuum", "cache_size",
    "query_only", "recursive_triggers", "locking_mode", "journal_size_limit",
    "wal_autocheckpoint", "read_uncommitted", "secure_delete", "mmap_size",
}


def _relative(value):
    if (type(value) is not str or not value or "\\" in value or
            any(part in {"", ".", ".."} for part in value.split("/")) or
            Path(value).is_absolute()):
        raise AppError("state_error", "resource path must be canonical and relative")
    return Path(value)


def _text(value):
    if type(value) is not str:
        raise AppError("type_error", "expected a string")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError as exc:
        raise AppError("type_error", "text must be valid Unicode") from exc
    if size > MAX_BYTES:
        raise AppError("resource_limit", "text exceeds 16 MiB")
    return value


class HostRuntime:
    """One call's resource scope; no application schema or policy lives here."""

    def __init__(self, project, *, state_dir=None):
        self.project = project
        self.bindings = project.manifest["bindings"]
        self.database = project.manifest["database"]
        self._requested_root = Path(state_dir).absolute() if state_dir is not None else None
        self._root: Path | None = None
        self._identities: dict[Path, tuple[int, int]] = {}
        self._connection: sqlite3.Connection | None = None
        self._transaction_error: BaseException | None = None
        self._closed = False

    def _state_root(self):
        if self._closed:
            raise AppError("state_error", "resource scope is closed")
        if self._requested_root is None:
            raise AppError("state_error", "file and database effects require an explicit state_dir")
        requested = self._requested_root
        if requested.is_symlink():
            raise AppError("state_error", "state root cannot be a symbolic link")
        requested.mkdir(parents=True, exist_ok=True)
        root = requested.resolve(strict=True)
        if not root.is_dir():
            raise AppError("state_error", "state root must be a directory")
        if self._root is None:
            self._root = root
        if root != self._root:
            raise AppError("state_error", "state root identity changed")
        self._pin(root)
        for name in ("files", "db"):
            subtree = root / name
            if subtree.is_symlink():
                raise AppError("state_error", "resource subtrees cannot be symbolic links")
            if subtree.exists() and not subtree.is_dir():
                raise AppError("state_error", "resource subtree must be a directory")
            if subtree.exists():
                self._pin(subtree)
        return root

    def _pin(self, path):
        stat = path.stat(follow_symlinks=False)
        identity = (stat.st_dev, stat.st_ino)
        old = self._identities.setdefault(path, identity)
        if old != identity:
            raise AppError("state_error", "resource directory identity changed")

    def _path(self, domain, relative, *, create_parent=False):
        relative = _relative(relative)
        root = self._state_root()
        base = root / domain
        current = root
        parts = (domain, *relative.parts)
        for index, part in enumerate(parts):
            current = current / part
            if current.is_symlink():
                raise AppError("state_error", "resource paths cannot contain symbolic links")
            if index < len(parts) - 1:
                if create_parent:
                    current.mkdir(exist_ok=True)
                if current.exists():
                    if not current.is_dir():
                        raise AppError("state_error", "resource parent must be a directory")
                    self._pin(current)
        if current.exists():
            if not current.is_file():
                raise AppError("state_error", "resource path must name a regular file")
            if current.stat().st_nlink != 1:
                raise AppError("state_error", "resource files cannot have hard-link aliases")
        if not current.resolve(strict=False).is_relative_to(base):
            raise AppError("state_error", "resource path escaped its owner")
        # Hard links must not allow a generic file adapter to touch the DB or sidecars.
        if domain == "files" and current.exists() and self.database is not None:
            db = root / "db" / _relative(self.database)
            for candidate in (db, Path(str(db) + "-wal"), Path(str(db) + "-shm"), Path(str(db) + "-journal")):
                if candidate.exists() and os.path.samefile(current, candidate):
                    raise AppError("state_error", "file resource aliases a database resource")
        return current

    def call(self, binding, args):
        if self._closed:
            raise AppError("state_error", "resource scope is closed")
        builtin = self.bindings.get(binding)
        if builtin is None:
            raise AppError("host_error", "undeclared host binding")
        if type(args) is not list:
            raise AppError("type_error", "host arguments must be a list")
        if builtin not in HOSTS or len(args) != len(HOSTS[builtin]["params"]):
            raise AppError("host_error", "invalid host binding or argument count")
        if builtin.startswith("file."):
            if self._connection is not None:
                raise AppError("host_error", "file effects are forbidden inside a database transaction")
            try:
                if builtin == "file.write_text":
                    return self._write_text(*args)
                text = self._read_text(args[0])
                return text if builtin == "file.read_text" else self._read_csv(text)
            except (OSError, UnicodeError, csv.Error) as exc:
                raise AppError("io_error", str(exc)) from exc
        return self._sql(args[0], args[1], readonly=builtin == "sqlite.query")

    def _read_text(self, relative):
        path = self._path("files", relative)
        with path.open("rb") as stream:
            data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise AppError("resource_limit", "file exceeds 16 MiB")
        return data.decode("utf-8", errors="strict")

    def _write_text(self, relative, text):
        text = _text(text)
        path = self._path("files", relative, create_parent=True)
        temp = None
        replaced = False
        try:
            with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, prefix=".mairg-", delete=False) as stream:
                temp = Path(stream.name)
                stream.write(text.encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            self._path("files", relative, create_parent=False)
            os.replace(temp, path)
            replaced = True
            temp = None
            # Confirm directory durability as well as the replacement itself.
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError as exc:
            if replaced:
                raise AppError("io_error", "file replacement completed; durability outcome may be unknown", details={"reason": str(exc)}) from exc
            raise
        finally:
            if temp is not None:
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass  # Preserve the primary write failure; this owned temp may remain.
        return None

    @staticmethod
    def _read_csv(text):
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        headers = next(reader, None)
        if not headers or any(not h for h in headers) or len(headers) != len(set(headers)):
            raise AppError("io_error", "CSV requires distinct nonempty headers")
        result = []
        elements = len(headers)
        for row in reader:
            if len(row) != len(headers):
                raise AppError("io_error", "CSV row width differs from its headers")
            elements += len(row)
            if elements > MAX_ITEMS:
                raise AppError("resource_limit", "CSV exceeds collection limit")
            result.append(dict(zip(headers, row, strict=True)))
        return json_copy(result)

    def _connect(self):
        if self.database is None:
            raise AppError("state_error", "project has no configured database")
        path = self._path("db", self.database, create_parent=True)
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(str(path) + suffix)
            if sidecar.is_symlink() or (sidecar.exists() and sidecar.stat().st_nlink != 1):
                raise AppError("state_error", "database sidecars cannot have aliases")
        connection = sqlite3.connect(path, isolation_level=None, timeout=2.0)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA busy_timeout=2000")
        except BaseException:
            connection.close()
            raise
        return connection

    @staticmethod
    def _authorizer(readonly, observed):
        def authorize(action, first, second, database, trigger):
            observed.append(True)
            if action in {sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT,
                          sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH}:
                return sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_FUNCTION and str(second).lower() == "load_extension":
                return sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_PRAGMA:
                name = str(first).lower()
                safe = name in _READ_PRAGMAS or (name in _READ_SETTINGS and second is None)
                if not readonly and name == "user_version":
                    safe = True
                return sqlite3.SQLITE_OK if safe else sqlite3.SQLITE_DENY
            if readonly and action not in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ,
                                           sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        return authorize

    @staticmethod
    def _parameters(params):
        if type(params) is not list:
            raise AppError("type_error", "SQL parameters must be a list")
        result: list[Any] = []
        for value in params:
            if value is None or type(value) is bool:
                result.append(int(value) if type(value) is bool else None)
            elif type(value) is int and -(2**63) <= value < 2**63:
                result.append(value)
            elif type(value) is float and math.isfinite(value):
                result.append(value)
            elif type(value) is str:
                result.append(_text(value))
            else:
                raise AppError("type_error", "SQL parameters must be null, bool, int64, finite float, or text")
        return result

    @staticmethod
    def _rows(cursor):
        if cursor.description is None:
            return []
        names = [item[0] for item in cursor.description]
        if len(names) != len(set(names)):
            raise AppError("sql_error", "SQL result column names must be unique")
        rows = []
        count = 0
        size = 0
        for values in cursor:
            count += len(values) + 1
            if count > MAX_ITEMS:
                raise AppError("resource_limit", "SQL result exceeds collection limit")
            for value in values:
                if value is not None and type(value) not in (int, float, str):
                    raise AppError("sql_error", "SQL result contains a non-JSON value")
                if type(value) is float and not math.isfinite(value):
                    raise AppError("sql_error", "SQL result contains a nonfinite number")
                if type(value) is str:
                    size += len(value.encode("utf-8"))
                else:
                    size += 32
                if size > MAX_BYTES:
                    raise AppError("resource_limit", "SQL result exceeds 16 MiB")
            rows.append(dict(zip(names, values, strict=True)))
        return json_copy(rows)

    def _sql(self, sql, params, *, readonly):
        sql = _text(sql)
        params = self._parameters(params)
        owned = self._connection is None
        connection = None
        try:
            connection = self._connect() if owned else self._connection
            assert connection is not None
            if self._transaction_error is not None:
                raise AppError("sql_error", "database transaction has already failed")
            if owned and not readonly:
                connection.execute("BEGIN IMMEDIATE")
            before = connection.total_changes
            observed: list[bool] = []
            connection.set_authorizer(self._authorizer(readonly, observed))
            try:
                cursor = connection.execute(sql, params)
                if not observed:
                    raise AppError("sql_error", "SQL must contain exactly one statement")
                rows = self._rows(cursor)
                result = rows if readonly else {"rows": rows, "changes": connection.total_changes - before}
                result = json_copy(result)
            finally:
                connection.set_authorizer(None)
            if owned and not readonly:
                self._commit(connection)
            return result
        except (sqlite3.Error, OSError) as exc:
            error = AppError("sql_error", str(exc))
            if not owned:
                self._transaction_error = error
            raise error from exc
        except BaseException as exc:
            if not owned:
                self._transaction_error = exc
            raise
        finally:
            if owned and connection is not None:
                self._rollback_close(connection)

    @staticmethod
    def _commit(connection):
        try:
            connection.commit()
        except sqlite3.Error as exc:
            raise AppError("sql_error", "database commit failed; outcome may be unknown", details={"reason": str(exc)}) from exc

    @staticmethod
    def _rollback_close(connection):
        try:
            if connection.in_transaction:
                connection.rollback()
        except BaseException:
            pass  # Preserve the operation's primary failure; no rollback claim is made.
        finally:
            try:
                connection.close()
            except BaseException:
                pass

    @contextmanager
    def transaction(self):
        if self._connection is not None:
            raise AppError("host_error", "nested database transactions are forbidden")
        connection = None
        try:
            connection = self._connect()
            connection.execute("BEGIN IMMEDIATE")
            self._connection = connection
            self._transaction_error = None
            yield self
            if self._transaction_error is not None:
                raise self._transaction_error
            self._commit(connection)
        except sqlite3.Error as exc:
            raise AppError("sql_error", str(exc)) from exc
        finally:
            self._connection = None
            self._transaction_error = None
            if connection is not None:
                self._rollback_close(connection)

    def close(self):
        if self._connection is not None:
            self._rollback_close(self._connection)
            self._connection = None
        self._closed = True
