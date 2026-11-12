import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from mairg.hosts import HostRuntime
from mairg.language import AppError


class Project:
    manifest = {"bindings": {"read": "file.read_text", "write": "file.write_text", "csv": "file.read_csv",
                             "query": "sqlite.query", "execute": "sqlite.execute"}, "database": "app.sqlite3"}


class HostsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.state = Path(self.temporary.name) / "state"
        self.hosts = HostRuntime(Project(), state_dir=self.state)

    def tearDown(self):
        self.hosts.close()
        self.temporary.cleanup()

    def execute(self, sql, params=None):
        return self.hosts.call("execute", [sql, params or []])

    def query(self, sql, params=None):
        return self.hosts.call("query", [sql, params or []])

    def test_explicit_state_and_validation_before_creation(self):
        with self.assertRaises(AppError):
            HostRuntime(Project()).call("read", ["x"])
        with self.assertRaises(AppError):
            self.hosts.call("write", ["x", 3])
        self.assertFalse(self.state.exists())
        for name in ["../x", "/x", "a/../x", "a//x", "./x", ""]:
            with self.subTest(name=name), self.assertRaises(AppError):
                self.hosts.call("write", [name, "no"])
        self.assertFalse(self.state.exists())

    def test_unicode_atomic_file_csv_and_replacement_failure(self):
        self.hosts.call("write", ["nested/data.txt", "café 🦉"])
        self.assertEqual(self.hosts.call("read", ["nested/data.txt"]), "café 🦉")
        with patch("mairg.hosts.os.replace", side_effect=OSError("replace failed")):
            with self.assertRaises(AppError):
                self.hosts.call("write", ["nested/data.txt", "new"])
        self.assertEqual(self.hosts.call("read", ["nested/data.txt"]), "café 🦉")
        self.assertEqual(list((self.state / "files" / "nested").glob(".mairg-*")), [])
        self.hosts.call("write", ["data.csv", 'name,note\n"é","a,b"\n'])
        self.assertEqual(self.hosts.call("csv", ["data.csv"]), [{"name": "é", "note": "a,b"}])
        for contents in ["x,x\n1,2", ",b\n1,2", "a,b\n1", "a,b\n1,2,3"]:
            self.hosts.call("write", ["bad.csv", contents])
            with self.assertRaises(AppError):
                self.hosts.call("csv", ["bad.csv"])

    def test_replacement_durability_and_commit_unknown_outcomes_are_not_retried(self):
        self.hosts.call("write", ["value.txt", "before"])
        with patch("mairg.hosts.os.fsync", side_effect=[None, OSError("directory sync failed")]) as fsync:
            with self.assertRaisesRegex(AppError, "outcome may be unknown"):
                self.hosts.call("write", ["value.txt", "after"])
        self.assertEqual(fsync.call_count, 2)
        self.assertEqual(self.hosts.call("read", ["value.txt"]), "after")
        connection = Mock(spec=sqlite3.Connection)
        connection.commit.side_effect = sqlite3.OperationalError("commit interrupted")
        with self.assertRaisesRegex(AppError, "outcome may be unknown"):
            self.hosts._commit(connection)
        connection.commit.assert_called_once_with()

    def test_subtree_root_path_and_hardlink_alias_rejection(self):
        self.execute("CREATE TABLE sample(value TEXT)")
        (self.state / "files").mkdir()
        for target in ["db", "files"]:
            alias_state = Path(self.temporary.name) / f"alias-{target}"
            alias_state.mkdir()
            (alias_state / target).symlink_to(self.state / "db", target_is_directory=True)
            runtime = HostRuntime(Project(), state_dir=alias_state)
            with self.assertRaises(AppError):
                runtime.call("write", ["app.sqlite3", "no"])
            runtime.close()
        (self.state / "files" / "link").symlink_to(self.state / "db" / "app.sqlite3")
        with self.assertRaises(AppError):
            self.hosts.call("write", ["link", "no"])
        (self.state / "files" / "link").unlink()
        os.link(self.state / "db" / "app.sqlite3", self.state / "files" / "hardlink")
        with self.assertRaises(AppError):
            self.hosts.call("read", ["hardlink"])
        with self.assertRaises(AppError):
            self.query("SELECT * FROM sample")
        with closing(sqlite3.connect(self.state / "db" / "app.sqlite3")) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM sample").fetchone(), (0,))

    def test_readonly_authorizer_and_owner_settings(self):
        self.execute("CREATE TABLE sample(value TEXT)")
        for sql in ["INSERT INTO sample VALUES ('x') RETURNING value", "DELETE FROM sample", "COMMIT", "BEGIN",
                    "SAVEPOINT user", "ATTACH DATABASE ':memory:' AS extra", "PRAGMA user_version=7",
                    "PRAGMA journal_mode=WAL", "PRAGMA wal_checkpoint", "CREATE TABLE bad(x)", "", "-- only a comment"]:
            with self.subTest(sql=sql), self.assertRaises(AppError):
                self.query(sql)
        for sql in ["COMMIT", "ROLLBACK", "SAVEPOINT user", "ATTACH DATABASE ':memory:' AS extra",
                    "PRAGMA foreign_keys=OFF", "PRAGMA synchronous=OFF", "PRAGMA journal_mode=WAL"]:
            with self.subTest(sql=sql), self.assertRaises(AppError):
                self.execute(sql)
        self.execute("PRAGMA user_version=1")
        self.assertEqual(self.query("PRAGMA user_version"), [{"user_version": 1}])
        self.assertEqual(self.query("PRAGMA foreign_keys"), [{"foreign_keys": 1}])
        self.assertEqual(self.query("SELECT count(*) AS count FROM sample"), [{"count": 0}])

    def test_parameterized_returning_and_precommit_result_validation(self):
        self.execute("CREATE TABLE sample(value TEXT)")
        self.assertEqual(self.execute("INSERT INTO sample VALUES (?) RETURNING value", ["O'Reilly 🦉"]),
                         {"rows": [{"value": "O'Reilly 🦉"}], "changes": 1})
        for sql in ["INSERT INTO sample VALUES ('bad') RETURNING value AS x, value AS x",
                    "INSERT INTO sample VALUES ('bad') RETURNING zeroblob(2) AS value"]:
            with self.assertRaises(AppError):
                self.execute(sql)
        self.assertEqual(self.query("SELECT value FROM sample"), [{"value": "O'Reilly 🦉"}])
        for params in [[2**63], [[1]], [{"x": 1}], [float("inf")]]:
            with self.subTest(params=params), self.assertRaises(AppError):
                self.query("SELECT ? AS value", params)
        self.assertEqual(self.query("SELECT ? AS value", [True]), [{"value": 1}])

    def test_region_rollback_baseexception_and_caught_sql_failure(self):
        self.execute("CREATE TABLE sample(value TEXT)")
        with self.assertRaises(KeyboardInterrupt):
            with self.hosts.transaction():
                self.execute("INSERT INTO sample VALUES ('cancel')")
                raise KeyboardInterrupt()
        self.assertEqual(self.query("SELECT * FROM sample"), [])
        with self.assertRaises(AppError):
            with self.hosts.transaction():
                self.execute("INSERT INTO sample VALUES ('rollback')")
                try:
                    self.query("DELETE FROM sample")
                except AppError:
                    pass
        self.assertEqual(self.query("SELECT * FROM sample"), [])
        with self.hosts.transaction():
            self.execute("INSERT INTO sample VALUES ('commit')")
        self.assertEqual(self.query("SELECT * FROM sample"), [{"value": "commit"}])
        with self.assertRaises(AppError):
            with self.hosts.transaction():
                self.hosts.call("write", ["invalid.txt", "no"])
        self.assertFalse((self.state / "files" / "invalid.txt").exists())


if __name__ == "__main__":
    unittest.main()
