from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GRAPH = ROOT / "examples" / "telemetry.mairg.json"
DATA = ROOT / "examples" / "telemetry.jsonl"


class CliTests(unittest.TestCase):
    def call(self, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, "-m", "mairg", *args], input=stdin,
                              capture_output=True, text=True, cwd=ROOT, timeout=20)

    def test_complete_app_native_and_reference_agree_with_expected(self) -> None:
        expected = json.loads((ROOT / "examples" / "expected.json").read_text())
        for engine in ("native", "reference"):
            result = self.call("run", str(GRAPH), str(DATA), "--engine", engine)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), expected)
            self.assertEqual(result.stderr, "")

    def test_check_and_plan_have_distinct_identities_and_optimization_evidence(self) -> None:
        checked = self.call("check", str(GRAPH))
        planned = self.call("plan", str(GRAPH))
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertEqual(planned.returncode, 0, planned.stderr)
        c, p = json.loads(checked.stdout), json.loads(planned.stdout)
        self.assertTrue(c["valid"])
        self.assertEqual(c["source_revision"], p["source_revision"])
        self.assertNotEqual(p["source_revision"], p["fingerprint"])
        self.assertGreater(p["stats"]["dead_nodes"], 0)
        self.assertGreater(p["stats"]["folded_nodes"], 0)
        self.assertGreater(p["stats"]["merged_nodes"], 0)
        self.assertEqual(p["stats"]["row_passes"], 1)
        self.assertEqual(p["stats"]["intermediate_column_buffers"], 0)

    def test_emit_contains_a_native_kernel(self) -> None:
        result = self.call("emit", str(GRAPH))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("mairg_kernel", result.stdout)
        self.assertIn("uint64_t", result.stdout)

    def test_stdin_transport(self) -> None:
        result = self.call("run", str(GRAPH), "-", "--engine", "reference", stdin=DATA.read_text())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["total_requests"], 4)

    def test_invalid_last_record_returns_no_partial_result(self) -> None:
        valid = DATA.read_text()
        bad = valid + '{"status":500,"latency_us":1,"bytes":-1,"cached":false}\n'
        for engine in ("native", "reference"):
            result = self.call("run", str(GRAPH), "-", "--engine", engine, stdin=bad)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertIn("error", json.loads(result.stderr))

    def test_duplicate_json_record_keys_rejected(self) -> None:
        result = self.call("run", str(GRAPH), "-", stdin='{"status":1,"status":2}\n')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("duplicate", json.loads(result.stderr)["error"]["message"])

    def test_arguments_and_io_errors_are_structured(self) -> None:
        for args in (("unknown",), ("check", "/no/such/mairg/source")):
            result = self.call(*args)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertIn("error", json.loads(result.stderr))

    def test_patch_is_all_or_nothing_and_does_not_mutate_source(self) -> None:
        original = GRAPH.read_bytes()
        revision = json.loads(self.call("check", str(GRAPH)).stdout)["source_revision"]
        patch = {"base": revision, "set_nodes": [[5, "u64", 600]]}
        result = self.call("patch", str(GRAPH), "-", stdin=json.dumps(patch))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn([5, "u64", 600], json.loads(result.stdout)["nodes"])
        self.assertEqual(GRAPH.read_bytes(), original)
        patch["base"] = "0" * 64
        rejected = self.call("patch", str(GRAPH), "-", stdin=json.dumps(patch))
        self.assertEqual(rejected.returncode, 2)
        self.assertEqual(rejected.stdout, "")
        self.assertEqual(GRAPH.read_bytes(), original)

    def test_data_io_and_malformed_graph_errors(self) -> None:
        result = self.call("run", str(GRAPH), "/no/such/data")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "bad.json"
            source.write_text('{"format":"mairg-1","format":"mairg-1"}')
            result = self.call("check", str(source))
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertIn("error", json.loads(result.stderr))


if __name__ == "__main__":
    unittest.main()
