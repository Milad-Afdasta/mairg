from __future__ import annotations

import concurrent.futures
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from mairg.compiler import compile_documents, compile_project
from mairg.language import AppError
from mairg.project import build_project, new_project, patch_project, source_revision
from mairg.project import test_project as run_fixtures

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "examples" / "inventory"


class ProjectTests(unittest.TestCase):
    def test_templates_are_editable_graphs_and_fixtures_are_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "app"
            new_project(path)
            project = compile_project(path)
            self.assertEqual(project.call("main", [" hello "]), "hello")
            self.assertTrue(run_fixtures(project)["success"])
            self.assertFalse((path / "files").exists())
            with self.assertRaises(AppError):
                new_project(path)
            api = Path(temporary) / "api"
            new_project(api, "api")
            request: dict[str, Any] = {"method": "POST", "path": "/echo", "params": {}, "query": {}, "headers": {}, "body": {"answer": 42}}
            self.assertEqual(compile_project(api).call("request", [request]), {"status": 200, "headers": {}, "body": request})

    def test_fixture_files_are_under_files_and_stop_after_unexpected_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "app"
            new_project(path)
            manifest = json.loads((path / "mairg.json").read_text())
            manifest["bindings"] = {"read": "file.read_text"}
            manifest["tests"] = ["tests.json", "second.json"]
            (path / "mairg.json").write_text(json.dumps(manifest))
            program = {"format": "mairg-app-1", "functions": [{"id": 0, "params": ["str"], "result": "str", "nodes": [
                {"id": 0, "op": "param", "args": [], "index": 0}, {"id": 1, "op": "host", "args": [0], "binding": "read"}
            ], "return": 1}], "exports": {"main": 0}}
            (path / "program.json").write_text(json.dumps(program))
            fixture = {"files": {"input.txt": "owned"}, "cases": [
                {"export": "main", "args": ["input.txt"], "expect": "owned"},
                {"export": "main", "args": ["input.txt"], "expect": "unexpected"},
                {"export": "main", "args": ["input.txt"], "expect": "owned"},
            ]}
            (path / "tests.json").write_text(json.dumps(fixture))
            (path / "second.json").write_text(json.dumps({"files": {"input.txt": "fresh"}, "cases": [{"export": "main", "args": ["input.txt"], "expect": "fresh"}]}))
            result = run_fixtures(compile_project(path))
            self.assertFalse(result["success"])
            self.assertEqual([case["status"] for case in result["fixtures"][0]["cases"]], ["pass", "fail", "not_run"])
            self.assertEqual(result["fixtures"][1]["cases"][0]["status"], "pass")
            self.assertFalse((path / "files").exists())

    def test_patch_is_whole_document_validated_in_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "app"
            new_project(path)
            project = compile_project(path)
            before = (path / "program.json").read_bytes()
            replacement = project.document
            replacement["functions"][0]["nodes"][1]["op"] = "text.upper"
            result = patch_project(project, {"base": project.source_revision, "program": replacement})
            self.assertEqual(compile_documents(project.manifest, result, root=path, assets={}).call("main", ["hello"]), "HELLO")
            self.assertEqual((path / "program.json").read_bytes(), before)
            with self.assertRaises(AppError) as stale:
                patch_project(project, {"base": "old", "program": replacement})
            self.assertEqual(stale.exception.code, "stale_patch")
            replacement["functions"][0]["nodes"][1]["op"] = "removed_op"
            with self.assertRaises(AppError):
                patch_project(project, {"base": project.source_revision, "program": replacement})
            self.assertEqual((path / "program.json").read_bytes(), before)

    def test_revision_ignores_declarations_but_preserves_ids(self) -> None:
        project = compile_project(INVENTORY)
        document = project.document
        document["functions"].reverse()
        for function in document["functions"]:
            function["nodes"].reverse()
        self.assertEqual(project.source_revision, source_revision(project.manifest, document, project.assets))
        document["functions"][0]["nodes"][0]["id"] += 100000
        self.assertNotEqual(project.source_revision, source_revision(project.manifest, document, project.assets))

    def test_inventory_migration_master_schema_restart_and_rollback(self) -> None:
        project = compile_project(INVENTORY)
        self.assertTrue(run_fixtures(project)["success"])
        schema = json.loads(project.assets["schema.json"])
        migration = json.loads(project.assets["migrations/001.json"])
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state"
            project.call("init", [], state_dir=state)
            created = project.call("create_item", ["O'Reilly café", 1], state_dir=state)
            self.assertEqual(created["stock"], 1)
            restarted = compile_project(INVENTORY)
            self.assertEqual(restarted.call("startup", [], state_dir=state), {"ready": True, "version": 1})
            self.assertEqual(restarted.call("get_item", [1], state_dir=state), created)
            with self.assertRaises(AppError):
                project.call("fail_reservation", [1, 1, "rollback"], state_dir=state)
            self.assertEqual(project.call("get_item", [1], state_dir=state)["stock"], 1)
            self.assertEqual(project.call("list_reservations", [], state_dir=state), [])
            with closing(sqlite3.connect(state / "db" / "inventory.sqlite3")) as live, closing(sqlite3.connect(":memory:")) as master, closing(sqlite3.connect(":memory:")) as migrated:
                for statement in schema:
                    master.execute(statement)
                for statement in migration:
                    migrated.execute(statement)
                query = "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name"
                self.assertEqual(live.execute(query).fetchall(), master.execute(query).fetchall())
                self.assertEqual(migrated.execute(query).fetchall(), master.execute(query).fetchall())
                self.assertEqual(live.execute("PRAGMA user_version").fetchone(), (1,))
                live.execute("PRAGMA user_version=2")
                live.commit()
            with self.assertRaises(AppError) as unsupported:
                restarted.call("startup", [], state_dir=state)
            self.assertEqual(unsupported.exception.code, "unsupported_schema")

    def test_inventory_failed_initialization_rolls_back_and_concurrency_cannot_oversell(self) -> None:
        project = compile_project(INVENTORY)
        assets = project.assets
        statements = json.loads(assets["migrations/001.json"])
        statements.append("INVALID SQL")
        assets["migrations/001.json"] = json.dumps(statements)
        broken = compile_documents(project.manifest, project.document, root=project.root, assets=assets)
        with tempfile.TemporaryDirectory() as temporary:
            broken_state = Path(temporary) / "broken"
            with self.assertRaises(AppError):
                broken.call("init", [], state_dir=broken_state)
            with closing(sqlite3.connect(broken_state / "db" / "inventory.sqlite3")) as connection:
                self.assertEqual(connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [])
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone(), (0,))
            state = Path(temporary) / "good"
            project.call("init", [], state_dir=state)
            project.call("create_item", ["last item", 1], state_dir=state)

            def reserve(note: str) -> str:
                try:
                    project.call("reserve", [1, 1, note], state_dir=state)
                    return "reserved"
                except AppError as error:
                    return error.code

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = sorted(pool.map(reserve, ["first", "second"]))
            self.assertEqual(outcomes, ["insufficient_stock", "reserved"])
            self.assertEqual(project.call("get_item", [1], state_dir=state)["stock"], 0)
            self.assertEqual(len(project.call("list_reservations", [], state_dir=state)), 1)

    def test_bundle_runs_outside_checkout_without_source_or_install(self) -> None:
        project = compile_project(INVENTORY)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            bundle = directory / "bundle"
            state = directory / "state"
            result = build_project(project, bundle)
            self.assertEqual(result["built"], str(bundle))
            self.assertEqual((bundle / "licenses" / "mairg-LICENSE").read_bytes(),
                             (Path(__file__).resolve().parents[1] / "LICENSE").read_bytes())
            self.assertFalse((bundle / "mairg.json").exists())
            self.assertFalse((bundle / "mairg" / "compiler.py").exists())
            self.assertFalse((bundle / "db").exists())
            with self.assertRaises(AppError):
                build_project(project, bundle)
            for export, args in (("init", []), ("create_item", ["bundled", 7]), ("list_items", [])):
                process = subprocess.run([sys.executable, "-I", str(bundle / "run.py"), export, "--args", json.dumps(args), "--state-dir", str(state)],
                                         cwd=directory, capture_output=True, text=True, timeout=30)
                self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(json.loads(process.stdout), [{"id": 1, "name": "bundled", "stock": 7}])
            self.assertFalse(list(bundle.rglob("*.pyc")))
            (bundle / "program.py").write_text("raise RuntimeError('must not execute')\n")
            rejected = subprocess.run([sys.executable, "-I", str(bundle / "run.py"), "list_items", "--args", "[]", "--state-dir", str(state)],
                                      cwd=directory, capture_output=True, text=True, timeout=30)
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("digest mismatch", rejected.stderr)
            self.assertFalse(list(directory.glob(".*.stage-*")))
            self.assertFalse(list(directory.glob("*.mairg-lock")))

    def test_build_publication_is_exclusive_and_failed_smoke_leaves_no_output(self) -> None:
        project = compile_project(INVENTORY)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            output = directory / "shared"

            def build() -> str:
                try:
                    build_project(project, output)
                    return "built"
                except AppError as error:
                    return error.code

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(lambda _: build(), range(2)))
            self.assertEqual(outcomes.count("built"), 1)
            self.assertIn(next(value for value in outcomes if value != "built"), ("build_locked", "exists"))
            self.assertTrue((output / "bundle.json").exists())
            self.assertFalse(list(directory.glob(".*.stage-*")))
            broken = SimpleNamespace(generated_source="this is not python", assets=project.assets,
                                     source_revision=project.source_revision)
            with self.assertRaises(AppError) as smoke:
                build_project(broken, directory / "broken")
            self.assertEqual(smoke.exception.code, "build_smoke")
            self.assertFalse((directory / "broken").exists())
            self.assertFalse(list(directory.glob(".*.stage-*")))
            self.assertFalse(list(directory.glob(".*.mairg-lock")))

    def test_bundle_rejects_state_resource_overlap_in_both_directions(self) -> None:
        project = compile_project(INVENTORY)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            container = directory / "container"
            bundle = container / "files" / "bundle"
            build_project(project, bundle)
            for state in (bundle, container):
                process = subprocess.run([sys.executable, "-I", str(bundle / "run.py"), "init", "--args", "[]", "--state-dir", str(state)],
                                         cwd=directory, capture_output=True, text=True, timeout=30)
                self.assertEqual(process.returncode, 2, process.stdout)
                self.assertEqual(json.loads(process.stderr)["error"]["code"], "bundle_state")
                self.assertFalse((state / "db").exists())
            self.assertFalse((bundle / "files").exists())
