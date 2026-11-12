"""Reproducible MAIRG versus independent fused C++ benchmarks.

Kernel timing excludes parsing/builds and uses identical prepared buffers. E2E timing
uses a fresh process and the same JSONL parser/type validator/buffer preparation for
both backends, including their respective compilation. No AI productivity claim.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import os
import platform
import resource
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from examples.generate import generate_records  # noqa: E402
from mairg.cli import read_records  # noqa: E402
from mairg.interpreter import interpret, records_to_columns  # noqa: E402
from mairg.ir import load  # noqa: E402
from mairg.native import compile_program, prepare_batch  # noqa: E402

WORKLOADS = ("simple", "selective", "telemetry")
U64 = ctypes.c_uint64
PTR = ctypes.POINTER(U64)


def _stop_group(process: subprocess.Popen[str]) -> None:
    """Bounded benchmark-build cleanup, including compiler descendants."""
    for sig, wait in ((signal.SIGTERM, 2.0), (signal.SIGKILL, 2.0)):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=wait)
        except subprocess.TimeoutExpired:
            continue
        if sig == signal.SIGTERM:
            # A driver can exit before its descendant. Kill the surviving group too.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        break


def _cxx_for(cc: str | None = None) -> str:
    if cc:
        cc_path = Path(cc)
        base = cc_path.name
        if "clang" in base:
            candidate = cc_path.with_name(base.replace("clang", "clang++", 1))
            if candidate.is_file():
                return str(candidate)
        if base in ("gcc", "cc"):
            candidate = cc_path.with_name("g++" if base == "gcc" else "c++")
            if candidate.is_file():
                return str(candidate)
    found = shutil.which("clang++") or shutil.which("g++") or shutil.which("c++")
    if not found:
        raise RuntimeError("benchmark requires a C++17 compiler")
    return found


class CppKernel:
    """Trusted, independent benchmark fixture; never accepts user source."""
    def __init__(self, workload: str, compiler: str | None = None, *, style: str = "tuned") -> None:
        if workload not in WORKLOADS:
            raise ValueError("unknown workload")
        if style not in ("tuned", "branching"):
            raise ValueError("unknown C++ baseline style")
        self._temporary = tempfile.TemporaryDirectory(prefix="mairg-cpp-")
        self.directory = Path(self._temporary.name)
        source_name = "selective_branching" if workload == "selective" and style == "branching" else workload
        self.source_path = ROOT / "benchmarks" / f"{source_name}.cpp"
        self.source = self.source_path.read_text(encoding="utf-8")
        suffix = ".dylib" if sys.platform == "darwin" else ".so"
        self.library_path = self.directory / ("baseline" + suffix)
        self.compiler_command = [_cxx_for(compiler), "-O3", "-std=c++17", "-fPIC",
                                 "-dynamiclib" if sys.platform == "darwin" else "-shared",
                                 str(self.source_path), "-o", str(self.library_path)]
        started = time.perf_counter()
        process: subprocess.Popen[str] | None = None
        try:
            process = subprocess.Popen(self.compiler_command, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True, start_new_session=True)
            _, stderr = process.communicate(timeout=60)
            if process.returncode:
                raise RuntimeError(f"C++ compilation failed: {stderr}")
            self.compiler_seconds = time.perf_counter() - started
            self.library = ctypes.CDLL(str(self.library_path))
            self.kernel = self.library.mairg_kernel
            self.kernel.argtypes = [ctypes.c_size_t, ctypes.POINTER(PTR), PTR]
            self.kernel.restype = None
        except BaseException:
            if process is not None:
                _stop_group(process)
            self._temporary.cleanup()
            raise

    def run_prepared(self, program: Any, batch: Any) -> dict[str, int]:
        if tuple(program.inputs) != tuple(batch.schema):
            raise ValueError("baseline input schema mismatch")
        output = (U64 * len(program.outputs))()
        self.kernel(batch.n, batch.pointers, output)
        return {item.name: int(output[index]) for index, item in enumerate(program.outputs)}

    def close(self) -> None:
        self.kernel = None
        self.library = None
        self._temporary.cleanup()

    def __enter__(self) -> CppKernel:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


def _distribution(samples: list[float]) -> dict[str, Any]:
    median = statistics.median(samples)
    return {"samples": samples, "median": median, "min": min(samples), "max": max(samples),
            "mad": statistics.median(abs(value - median) for value in samples)}


def _metadata() -> dict[str, Any]:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    status = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True)
    return {"git_commit": commit.stdout.strip() if commit.returncode == 0 else None,
            "git_dirty": bool(status.stdout.strip()),
            "dataset_generator_sha256": hashlib.sha256((ROOT / "examples" / "generate.py").read_bytes()).hexdigest(),
            "python": sys.version, "platform": platform.platform(), "machine": platform.machine(),
            "cpu_count": os.cpu_count(), "command": [sys.executable, *sys.argv],
            "whole_process_peak_rss": rss,
            "rss_unit": "bytes" if sys.platform == "darwin" else "KiB",
            "rss_scope": "cumulative peak of benchmark host, including data generation/reference; not kernel allocations",
            "clock": "time.perf_counter_ns", "single_threaded_kernels": True}


def _version(compiler: str) -> str:
    result = subprocess.run([compiler, "--version"], capture_output=True, text=True, timeout=10, check=True)
    return result.stdout.splitlines()[0]


def _sample(kernel: Any, batch: Any, output: Any, loops: int) -> tuple[float, int]:
    started = time.perf_counter_ns()
    for _ in range(loops):
        kernel(batch.n, batch.pointers, output)
    elapsed = (time.perf_counter_ns() - started) / loops
    # Consumption occurs outside timing, identically for both backends.
    return elapsed, sum(int(value) for value in output) & ((1 << 64) - 1)


def _kernel_case(program: Any, generated: Any, cpp: CppKernel, rows: int,
                 seed: int, repetitions: int, warmups: int) -> dict[str, Any]:
    records = generate_records(rows, seed)
    columns = records_to_columns(program, records)
    batch = prepare_batch(program, columns)
    expected = interpret(program, columns)
    native_result = generated.run_prepared(batch)
    cpp_result = cpp.run_prepared(program, batch)
    if expected != native_result or expected != cpp_result:
        raise AssertionError({"reference": expected, "mairg": native_result, "cpp": cpp_result})
    count = len(program.outputs)
    outputs = {"mairg": (U64 * count)(), "cpp": (U64 * count)()}
    kernels = {"mairg": generated.kernel, "cpp": cpp.kernel}
    estimate, _ = _sample(generated.kernel, batch, outputs["mairg"], 1)
    loops = max(1, min(1000, math.ceil(5_000_000 / max(estimate, 1))))
    for index in range(warmups):
        for backend in (("mairg", "cpp") if index % 2 == 0 else ("cpp", "mairg")):
            _sample(kernels[backend], batch, outputs[backend], loops)
    samples: dict[str, list[float]] = {"mairg": [], "cpp": []}
    checksums: dict[str, list[int]] = {"mairg": [], "cpp": []}
    for index in range(repetitions):
        for backend in (("mairg", "cpp") if index % 2 == 0 else ("cpp", "mairg")):
            elapsed, checksum = _sample(kernels[backend], batch, outputs[backend], loops)
            result = {item.name: int(outputs[backend][i]) for i, item in enumerate(program.outputs)}
            if result != expected:
                raise AssertionError(f"{backend} changed result during timing")
            samples[backend].append(elapsed)
            checksums[backend].append(checksum)
    timing = {backend: _distribution(values) for backend, values in samples.items()}
    ratio = timing["mairg"]["median"] / timing["cpp"]["median"]
    return {"rows": rows, "seed": seed, "outputs": expected, "correctness": "pass",
            "warmups": warmups, "repetitions": repetitions, "calls_per_sample": loops,
            "kernel_ns_per_call": timing, "checksums": checksums,
            "mairg_over_cpp_ratio": ratio, "cpp_over_mairg_speedup": 1 / ratio,
            "regression_over_25_percent": ratio > 1.25,
            "timing_scope": "prepared identical buffers; direct ctypes ABI; excludes parsing, validation, builds and serialization"}


def _child(args: argparse.Namespace) -> None:
    program = load(args.graph)
    columns = records_to_columns(program, read_records(args.data))
    batch = prepare_batch(program, columns)
    if args.backend == "mairg":
        with compile_program(program, compiler=args.compiler) as kernel:
            output = kernel.run_prepared(batch)
    else:
        with CppKernel(args.workload, args.compiler, style=args.cpp_style) as cpp:
            output = cpp.run_prepared(program, batch)
    sys.stdout.write(json.dumps(output, separators=(",", ":")) + "\n")


def _e2e_case(workload: str, program: Any, rows: int, seed: int, repetitions: int, compiler: str,
              cpp_style: str) -> dict[str, Any]:
    records = generate_records(rows, seed)
    columns = records_to_columns(program, records)
    expected = interpret(program, columns)
    samples: dict[str, list[float]] = {"mairg": [], "cpp": []}
    commands: dict[str, list[str]] = {}
    with tempfile.TemporaryDirectory(prefix="mairg-e2e-") as directory:
        data = Path(directory) / "events.jsonl"
        data.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in records), encoding="utf-8")
        for index in range(repetitions):
            for backend in (("mairg", "cpp") if index % 2 == 0 else ("cpp", "mairg")):
                command = [sys.executable, str(Path(__file__).resolve()), "--child", "--workload", workload,
                           "--backend", backend, "--graph", str(ROOT / "examples" / f"{workload}.mairg.json"),
                           "--data", str(data), "--compiler", compiler, "--cpp-style", cpp_style]
                commands[backend] = command
                started = time.perf_counter_ns()
                result = subprocess.run(command, capture_output=True, text=True, timeout=75, check=True)
                elapsed = (time.perf_counter_ns() - started) / 1e9
                actual = json.loads(result.stdout)
                if actual != expected:
                    raise AssertionError(f"{backend} E2E output mismatch")
                samples[backend].append(elapsed)
    timing = {backend: _distribution(values) for backend, values in samples.items()}
    return {"rows": rows, "seed": seed, "repetitions": repetitions, "outputs": expected,
            "correctness": "pass", "seconds": timing, "commands": commands,
            "timing_scope": "fresh Python process + identical JSONL parsing/validation/buffer preparation + backend build + kernel + JSON serialization",
            "includes_compilation": True, "input_generation_excluded": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", nargs="+", type=int, default=[10000, 100000, 1000000])
    parser.add_argument("--repetitions", type=int, default=11)
    parser.add_argument("--warmups", type=int, default=3)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--e2e-rows", type=int, default=10000)
    parser.add_argument("--e2e-repetitions", type=int, default=3)
    parser.add_argument("--workloads", nargs="+", choices=WORKLOADS, default=list(WORKLOADS))
    parser.add_argument("--output", default="-")
    parser.add_argument("--cpp-style", choices=("tuned", "branching"), default="tuned",
                        help="primary tuned baseline; branching differs only for selective workload")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--workload", choices=WORKLOADS, help=argparse.SUPPRESS)
    parser.add_argument("--backend", choices=("mairg", "cpp"), help=argparse.SUPPRESS)
    parser.add_argument("--graph", help=argparse.SUPPRESS)
    parser.add_argument("--data", help=argparse.SUPPRESS)
    parser.add_argument("--compiler", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.child:
        _child(args)
        return 0
    if any(size < 0 for size in args.sizes) or args.repetitions < 1 or args.warmups < 0 or args.e2e_rows < 0 or args.e2e_repetitions < 1:
        parser.error("sizes must be nonnegative; repetition counts positive; warmups nonnegative")
    report: dict[str, Any] = {"report_format": "mairg-benchmark-1", "metadata": _metadata(),
        "cpp_baseline_style": args.cpp_style,
        "claims": {"native_comparison": "independent fused C++17 primary baseline",
                   "ai_productivity": "not measured", "language_novelty": "graph IR, fusion and CSE are prior art",
                   "resource_comparison": "whole-process RSS recorded, per-language memory advantage not established"},
        "workloads": {}}
    for workload in args.workloads:
        started = time.perf_counter()
        program = load(ROOT / "examples" / f"{workload}.mairg.json")
        with compile_program(program) as generated:
            total_build_seconds = time.perf_counter() - started
            cc_command = list(generated.compiler_command)
            cpp_started = time.perf_counter()
            with CppKernel(workload, cc_command[0], style=args.cpp_style) as cpp:
                cpp_total_seconds = time.perf_counter() - cpp_started
                report["workloads"][workload] = {
                    "graph": str(ROOT / "examples" / f"{workload}.mairg.json"),
                    "provenance": {
                        "graph_sha256": hashlib.sha256((ROOT / "examples" / f"{workload}.mairg.json").read_bytes()).hexdigest(),
                        "generated_c_sha256": hashlib.sha256(generated.source.encode("utf-8")).hexdigest(),
                        "cpp_source_sha256": hashlib.sha256(cpp.source.encode("utf-8")).hexdigest(),
                        "mairg_library_sha256": hashlib.sha256(Path(generated.library_path).read_bytes()).hexdigest(),
                        "cpp_library_sha256": hashlib.sha256(cpp.library_path.read_bytes()).hexdigest()},
                    "plan_fingerprint": generated.plan.fingerprint, "optimization": generated.plan.stats,
                    "build": {"mairg_total_seconds": total_build_seconds,
                              "cpp_total_seconds": cpp_total_seconds,
                              "mairg_native_compile_seconds": generated.compiler_seconds,
                              "cpp_native_compile_seconds": cpp.compiler_seconds,
                              "total_scope": "source read/validation/lowering as applicable, native compilation, library load, and ABI setup",
                              "native_compile_scope": "compiler process launch through completed successful exit; excludes library loading",
                              "mairg_command": cc_command, "cpp_command": cpp.compiler_command,
                              "mairg_compiler": _version(cc_command[0]), "cpp_compiler": _version(cpp.compiler_command[0]),
                              "mairg_library_bytes": Path(generated.library_path).stat().st_size,
                              "cpp_library_bytes": cpp.library_path.stat().st_size},
                    "authoring": {"graph_serialized_bytes": (ROOT / "examples" / f"{workload}.mairg.json").stat().st_size,
                                  "generated_c_bytes": len(generated.source.encode("utf-8")),
                                  "handwritten_cpp_bytes": len(cpp.source.encode("utf-8")),
                                  "graph_explicit_loops": 0, "graph_explicit_schedules": 0,
                                  "note": "descriptive sizes and syntax counts; not tokens or AI productivity"},
                    "kernel": [_kernel_case(program, generated, cpp, size, args.seed, args.repetitions, args.warmups)
                               for size in args.sizes]}
        report["workloads"][workload]["end_to_end"] = _e2e_case(workload, program, args.e2e_rows, args.seed,
                                                               args.e2e_repetitions, cc_command[0], args.cpp_style)
    report["metadata"].update(_metadata())
    report["requires_investigation"] = [
        {"workload": name, "rows": case["rows"], "mairg_over_cpp_ratio": case["mairg_over_cpp_ratio"]}
        for name, workload in report["workloads"].items() for case in workload["kernel"]
        if case["regression_over_25_percent"]]
    rendered = json.dumps(report, indent=2, allow_nan=False) + "\n"
    if args.output == "-":
        sys.stdout.write(rendered)
    else:
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(rendered, encoding="utf-8")
        print(json.dumps({"report": str(destination), "correctness": "pass",
                          "investigate_regressions": len(report["requires_investigation"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
