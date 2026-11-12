"""Independent end-to-end application and strong native-baseline assertions."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from benchmarks.run import CppKernel
from examples.generate import generate_records
from mairg.interpreter import records_to_columns
from mairg.ir import load
from mairg.native import compile_program, prepare_batch

ROOT = Path(__file__).resolve().parents[1]
MASK = (1 << 64) - 1


def independent_expected(workload, records):
    """Application mathematics independent of the graph interpreter and compiler."""
    if workload == "simple":
        return {"total_bytes": sum(row["bytes"] for row in records) & MASK}
    if workload == "selective":
        chosen = [row for row in records if row["status"] >= 500 and not row["cached"]]
        return {"uncached_errors": len(chosen) & MASK,
                "uncached_error_bytes": sum(row["bytes"] for row in chosen) & MASK}
    return {"total_requests": len(records) & MASK,
            "errors": sum(row["status"] >= 500 for row in records) & MASK,
            "slow_requests": sum(row["latency_us"] >= 1000 for row in records) & MASK,
            "total_bytes": sum(row["bytes"] for row in records) & MASK,
            "error_bytes": sum(row["bytes"] for row in records if row["status"] >= 500) & MASK,
            "max_latency": max((row["latency_us"] for row in records), default=0),
            "weighted_score": sum(row["latency_us"] * 3 + row["bytes"] for row in records) & MASK}


class IndependentApplicationTests(unittest.TestCase):
    def call(self, *args, stdin=None):
        return subprocess.run([sys.executable, "-m", "mairg", *args], cwd=ROOT,
                              input=stdin, capture_output=True, text=True,
                              timeout=30, check=False)

    def test_all_three_native_apps_and_cpp_match_independent_mathematics(self):
        boundaries = [
            {"status": 499, "latency_us": 999, "bytes": 0, "cached": False},
            {"status": 500, "latency_us": 1000, "bytes": MASK, "cached": False},
            {"status": MASK, "latency_us": MASK, "bytes": MASK, "cached": True},
            {"status": 0, "latency_us": 0, "bytes": 1, "cached": True},
        ]
        cases = [[], boundaries[:1], boundaries, *[generate_records(97, seed) for seed in range(4)]]
        for workload in ("simple", "selective", "telemetry"):
            program = load(ROOT / "examples" / f"{workload}.mairg.json")
            with compile_program(program) as native, CppKernel(workload, native.compiler_command[0]) as cpp:
                for records in cases:
                    with self.subTest(workload=workload, rows=len(records)):
                        batch = prepare_batch(program, records_to_columns(program, records))
                        expected = independent_expected(workload, records)
                        self.assertEqual(native.run_prepared(batch), expected)
                        self.assertEqual(cpp.run_prepared(program, batch), expected)

    def test_cli_structural_change_updates_real_app_without_mutating_original(self):
        graph = ROOT / "examples" / "telemetry.mairg.json"
        original = graph.read_bytes()
        document = json.loads(original)
        rows_by_id = {row[0]: row for row in document["nodes"]}
        predicate_ids = {row[-1] for row in document["outputs"] if row[0] in ("errors", "error_bytes")}
        new_threshold_id = max(rows_by_id) + 1
        replacements = [[new_threshold_id, "u64", 600]] + [
            [ident, "ge", rows_by_id[ident][2], new_threshold_id] for ident in sorted(predicate_ids)]
        checked = self.call("check", str(graph))
        self.assertEqual(checked.returncode, 0, checked.stderr)
        revision = json.loads(checked.stdout)["source_revision"]
        patch = {"base": revision, "set_nodes": replacements}
        changed = self.call("patch", str(graph), "-", stdin=json.dumps(patch))
        self.assertEqual(changed.returncode, 0, changed.stderr)
        self.assertEqual(graph.read_bytes(), original)
        with tempfile.TemporaryDirectory() as directory:
            updated = Path(directory) / "updated.mairg.json"
            updated.write_text(changed.stdout, encoding="utf-8")
            for engine in ("native", "reference"):
                result = self.call("run", str(updated), str(ROOT / "examples" / "telemetry.jsonl"),
                                   "--engine", engine)
                self.assertEqual(result.returncode, 0, result.stderr)
                output = json.loads(result.stdout)
                self.assertEqual(output["errors"], 0)
                self.assertEqual(output["error_bytes"], 0)
                self.assertEqual(output["total_requests"], 4)
            before = hashlib.sha256(updated.read_bytes()).hexdigest()
            stale = self.call("patch", str(updated), "-", stdin=json.dumps(patch))
            self.assertEqual(stale.returncode, 2)
            self.assertEqual(stale.stdout, "")
            self.assertEqual(hashlib.sha256(updated.read_bytes()).hexdigest(), before)

    def test_cli_bad_final_rows_reject_both_engines_with_no_partial_aggregate(self):
        graph = str(ROOT / "examples" / "telemetry.mairg.json")
        good = {"status": 500, "latency_us": 1000, "bytes": 2, "cached": False}
        bad_rows = []
        for field, value in (("status", True), ("bytes", -1), ("latency_us", 1.0),
                             ("bytes", 1 << 64), ("cached", 0), ("cached", None)):
            bad_rows.append(dict(good, **{field: value}))
        bad_rows += [{key: value for key, value in good.items() if key != "bytes"},
                     dict(good, unexpected=9)]
        for engine in ("native", "reference"):
            for bad in bad_rows:
                with self.subTest(engine=engine, bad=bad):
                    result = self.call("run", graph, "-", "--engine", engine,
                                       stdin=json.dumps(good) + "\n" + json.dumps(bad) + "\n")
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stdout, "")
                    self.assertIn("code", json.loads(result.stderr)["error"])

    def test_benchmark_smoke_has_verified_samples_and_reproducible_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.json"
            result = subprocess.run([sys.executable, str(ROOT / "benchmarks" / "run.py"),
                                     "--sizes", "0", "32", "--repetitions", "3", "--warmups", "1",
                                     "--e2e-rows", "4", "--e2e-repetitions", "1", "--output", str(report)],
                                    cwd=ROOT, capture_output=True, text=True, timeout=60, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            evidence = json.loads(report.read_text())
            self.assertEqual(evidence["claims"]["ai_productivity"], "not measured")
            self.assertIn(evidence["metadata"]["rss_unit"], ("bytes", "KiB"))
            self.assertEqual(len(evidence["metadata"]["dataset_generator_sha256"]), 64)
            self.assertEqual(set(evidence["workloads"]), {"simple", "selective", "telemetry"})
            for name, workload in evidence["workloads"].items():
                self.assertIn("-O3", workload["build"]["mairg_command"])
                self.assertIn("-O3", workload["build"]["cpp_command"])
                for digest in workload["provenance"].values():
                    self.assertEqual(len(digest), 64)
                    self.assertTrue(all(char in "0123456789abcdef" for char in digest))
                self.assertEqual(workload["provenance"]["graph_sha256"],
                                 hashlib.sha256((ROOT / "examples" / f"{name}.mairg.json").read_bytes()).hexdigest())
                self.assertEqual(workload["provenance"]["cpp_source_sha256"],
                                 hashlib.sha256((ROOT / "benchmarks" / f"{name}.cpp").read_bytes()).hexdigest())
                for case in workload["kernel"]:
                    self.assertEqual(case["correctness"], "pass")
                    self.assertEqual(case["repetitions"], 3)
                    self.assertEqual(case["outputs"], independent_expected(name, generate_records(case["rows"], case["seed"])))
                    for backend in ("mairg", "cpp"):
                        self.assertEqual(len(case["kernel_ns_per_call"][backend]["samples"]), 3)
                    self.assertEqual(case["checksums"]["mairg"], case["checksums"]["cpp"])
                self.assertTrue(workload["end_to_end"]["includes_compilation"])
                self.assertEqual(workload["end_to_end"]["correctness"], "pass")
                self.assertEqual(workload["optimization"]["row_passes"], 1)
                self.assertEqual(workload["optimization"]["intermediate_column_buffers"], 0)
                self.assertEqual(workload["authoring"]["graph_explicit_loops"], 0)


    def test_optional_branching_baseline_preserves_semantics_and_child_style(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "branching.json"
            result = subprocess.run([sys.executable, str(ROOT / "benchmarks" / "run.py"),
                                     "--workloads", "selective", "--cpp-style", "branching",
                                     "--sizes", "33", "--repetitions", "1", "--warmups", "0",
                                     "--e2e-rows", "33", "--e2e-repetitions", "1", "--output", str(report)],
                                    cwd=ROOT, capture_output=True, text=True, timeout=30, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            evidence = json.loads(report.read_text())
            self.assertEqual(evidence["cpp_baseline_style"], "branching")
            case = evidence["workloads"]["selective"]
            self.assertEqual(case["provenance"]["cpp_source_sha256"],
                             hashlib.sha256((ROOT / "benchmarks" / "selective_branching.cpp").read_bytes()).hexdigest())
            expected = independent_expected("selective", generate_records(33, 17))
            self.assertEqual(case["kernel"][0]["outputs"], expected)
            self.assertEqual(case["end_to_end"]["outputs"], expected)
            for command in case["end_to_end"]["commands"].values():
                self.assertEqual(command[command.index("--cpp-style") + 1], "branching")


if __name__ == "__main__":
    unittest.main()
