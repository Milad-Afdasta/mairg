"""Independent native boundary, lifecycle, and sanitizer tests for MAIRG-M2."""

from __future__ import annotations

import gc
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import chdir
from pathlib import Path
from unittest.mock import patch

from mairg.interpreter import interpret
from mairg.ir import MairgError, Node, Output, Program, validate
from mairg.native import compile_program, emit_c, prepare_batch

MAX = (1 << 64) - 1
COMPILER = shutil.which("clang") or shutil.which("gcc")


def boundary_program() -> Program:
    nodes = [[0, "input", 0], [1, "input", 1], [2, "input", 2],
             [3, "bool", True], [4, "u64", MAX], [5, "bool", False]]
    outputs = []
    for op in ("add", "sub", "mul", "bit_and", "bit_or", "bit_xor",
               "eq", "lt", "le", "gt", "ge"):
        ident = len(nodes)
        nodes.append([ident, op, 0, 1])
        outputs.append([op, "count", ident] if op in ("eq", "lt", "le", "gt", "ge")
                       else [op, "sum", ident, 3])
    for op, args in (("and", [2, 3]), ("or", [2, 5]), ("not", [2]),
                     ("eq", [2, 5]), ("select", [2, 3, 5])):
        ident = len(nodes)
        nodes.append([ident, op, *args])
        outputs.append([f"bool_{ident}", "count", ident])
    ident = len(nodes)
    nodes.append([ident, "select", 2, 0, 4])
    outputs.extend([["selected", "sum", ident, 3], ["selected_max", "max", ident, 3],
                    ["selected_xor", "xor", ident, 3], ["masked", "sum", 0, 2]])
    return validate({"format": "mairg-1", "inputs": [["x", "u64"], ["y", "u64"],
                     ["flag", "bool"]], "nodes": nodes, "outputs": outputs})


def columns() -> dict[str, list[int] | list[bool]]:
    return {"x": [0, 1, MAX, MAX - 1, 1 << 63, 17, 99, 0],
            "y": [0, MAX, 1, MAX, 2, 3, 99, 1],
            "flag": [True, False, True, False, True, False, True, False]}


def process_state(pid: int) -> str:
    result = subprocess.run(["ps", "-p", str(pid), "-o", "stat="],
                            capture_output=True, text=True, check=False, timeout=3)
    return result.stdout.strip()


@unittest.skipUnless(COMPILER and sys.platform in ("darwin", "linux"),
                     "native compiler and supported POSIX platform required")
class IndependentNativeTests(unittest.TestCase):
    def test_all_forged_ir_is_rejected_before_process_or_c_emission(self):
        correct = boundary_program()
        first = correct.nodes[0]
        corrupted = [
            Program(correct.inputs, (Node(first.id, first.op, first.args, "bool"),
                                    *correct.nodes[1:]), correct.outputs),
            Program(correct.inputs, (Node(True, first.op, first.args, first.type),
                                    *correct.nodes[1:]), correct.outputs),
            Program(correct.inputs, (Node(first.id, "u64", ("0);system(1);",), "u64"),
                                    *correct.nodes[1:]), correct.outputs),
            Program(correct.inputs, correct.nodes,
                    (Output("count", "count", 123, 3),)),
            Program(correct.inputs, (*correct.nodes, Node(100, "add", (101, 0), "u64"),
                                    Node(101, "add", (100, 0), "u64")), correct.outputs),
        ]
        for index, program in enumerate(corrupted):
            with self.subTest(case=index), patch("mairg.native.subprocess.Popen") as start:
                for action in (lambda: compile_program(program), lambda: emit_c(program),
                               lambda: prepare_batch(program, columns())):
                    with self.assertRaises(MairgError):
                        action()
                start.assert_not_called()

    def test_late_invalid_values_and_shapes_never_call_kernel(self):
        with compile_program(boundary_program()) as compiled:
            cases = [
                {"x": [1, True], "y": [1, 2], "flag": [True, False]},
                {"x": [1, MAX + 1], "y": [1, 2], "flag": [True, False]},
                {"x": [1, -1], "y": [1, 2], "flag": [True, False]},
                {"x": [1, float("nan")], "y": [1, 2], "flag": [True, False]},
                {"x": [1, 2], "y": [1, 2], "flag": [True, 0]},
                {"x": [1, 2], "y": [1], "flag": [True, False]},
                {"x": [], "y": [], "flag": [], "extra": []},
                {"x": [], "y": []},
            ]
            for index, data in enumerate(cases):
                with self.subTest(case=index), patch.object(compiled, "kernel") as kernel:
                    with self.assertRaises(MairgError):
                        compiled.run(data)
                    kernel.assert_not_called()

    def test_preparation_pins_snapshot_and_outputs_are_fresh(self):
        program = boundary_program()
        data = columns()
        expected = interpret(program, data)
        batch = prepare_batch(program, data)
        for values in data.values():
            values.clear()
        del data
        gc.collect()
        with compile_program(program) as first, compile_program(program, False) as second:
            result = first.run_prepared(batch)
            self.assertEqual(result, expected)
            result["add"] = 999
            self.assertEqual(first.run_prepared(batch), expected)
            self.assertEqual(second.run_prepared(batch), expected)
        for compiled in (first, second):
            with self.assertRaises(MairgError) as raised:
                compiled.run_prepared(batch)
            self.assertEqual(raised.exception.code, "closed")

    def test_context_exception_removes_build_and_rejects_reentry(self):
        compiled = compile_program(boundary_program())
        directory = compiled.directory
        with self.assertRaisesRegex(RuntimeError, "consumer failure"):
            with compiled:
                raise RuntimeError("consumer failure")
        self.assertFalse(directory.exists())
        with self.assertRaises(MairgError):
            compiled.__enter__()

    def test_os_launch_exception_removes_private_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("mairg.native.tempfile.tempdir", directory), \
                 patch("mairg.native.subprocess.Popen", side_effect=PermissionError("denied")):
                with self.assertRaises(MairgError) as raised:
                    compile_program(boundary_program())
            self.assertEqual(raised.exception.code, "compiler_io")
            self.assertEqual(list(Path(directory).glob("mairg-build-*")), [])

    def test_keyboard_interrupt_stops_term_resistant_descendant_and_cleans(self):
        actual_popen = subprocess.Popen
        processes = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "ready.pid"
            driver = root / "compiler"
            child = (
                "import os,signal,time;from pathlib import Path;"
                "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                f"Path({str(marker)!r}).write_text(str(os.getpid()));time.sleep(30)"
            )
            driver.write_text(f"#!{sys.executable}\nimport subprocess,sys,time\n"
                              f"subprocess.Popen([sys.executable,'-c',{child!r}],"
                              "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
                              "time.sleep(30)\n")
            driver.chmod(0o700)

            def interrupting_popen(*args, **kwargs):
                process = actual_popen(*args, **kwargs)
                processes.append(process)
                actual_communicate = process.communicate
                interrupted = False

                def communicate(*inner_args, **inner_kwargs):
                    nonlocal interrupted
                    if not interrupted:
                        interrupted = True
                        deadline = time.monotonic() + 2
                        while not marker.exists() and time.monotonic() < deadline:
                            time.sleep(0.01)
                        raise KeyboardInterrupt
                    return actual_communicate(*inner_args, **inner_kwargs)

                process.communicate = communicate
                return process

            try:
                with patch("mairg.native.tempfile.tempdir", directory), \
                     patch("mairg.native.subprocess.Popen", side_effect=interrupting_popen):
                    with self.assertRaises(KeyboardInterrupt):
                        compile_program(boundary_program(), compiler=driver)
                self.assertTrue(marker.exists(), "compiler child did not establish readiness")
                self.assertEqual(list(root.glob("mairg-build-*")), [])
                self.assertTrue(processes)
                self.assertIsNotNone(processes[0].returncode, "direct compiler driver not reaped")
                pid = int(marker.read_text())
                deadline = time.monotonic() + 1
                state = process_state(pid)
                while state and not state.startswith("Z") and time.monotonic() < deadline:
                    time.sleep(0.01)
                    state = process_state(pid)
                self.assertTrue(not state or state.startswith("Z"), state)
            finally:
                if marker.exists():
                    pid = int(marker.read_text())
                    state = process_state(pid)
                    if state and not state.startswith("Z"):
                        os.kill(pid, signal.SIGKILL)
                for process in processes:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=2)

    def test_hostile_names_and_compiler_path_remain_data_and_argv(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            name = 'x\x00\n"; system("INJECTED"); /* ☃'
            program = validate({"format": "mairg-1", "inputs": [[name, "u64"]],
                                "nodes": [[0, "input", 0], [1, "bool", True]],
                                "outputs": [[name, "sum", 0, 1]]})
            compiler = root / "compiler ; touch INJECTED"
            compiler.write_text(f"#!{sys.executable}\nimport os,sys\n"
                                f"os.execv({COMPILER!r},[{COMPILER!r},*sys.argv[1:]])\n")
            compiler.chmod(0o700)
            with chdir(root), compile_program(program, compiler=compiler) as compiled:
                self.assertNotIn("INJECTED", compiled.source)
                self.assertEqual(compiled.run({name: [MAX, 1, 5]}), {name: 5})
                self.assertEqual(compiled.compiler_command[0], str(compiler))
            self.assertFalse((root / "INJECTED").exists())

    def test_emitted_boundary_program_under_address_and_undefined_sanitizers(self):
        program = boundary_program()
        data = columns()
        expected = interpret(program, data)
        arrays = []
        for index, (name, _) in enumerate(program.inputs):
            literals = ",".join(f"UINT64_C({int(value)})" for value in data[name])
            arrays.append(f"const uint64_t c{index}[] = {{{literals}}};")
        checks = "\n".join(
            f"if (out[{index}] != UINT64_C({expected[output.name]})) return {index + 1};"
            for index, output in enumerate(program.outputs)
        )
        main = "\n".join([
            "int main(void) {", *arrays,
            "const uint64_t *cols[] = {c0,c1,c2};",
            f"uint64_t guarded[{len(program.outputs) + 2}] = {{0}};",
            f"guarded[0] = UINT64_C(12345); guarded[{len(program.outputs) + 1}] = UINT64_C(54321);",
            "uint64_t *out = guarded + 1;",
            f"mairg_kernel({len(data['x'])}, cols, out);", checks,
            f"if (guarded[0] != UINT64_C(12345) || guarded[{len(program.outputs) + 1}] != UINT64_C(54321)) return 99;",
            "mairg_kernel(0, NULL, out);",
            f"for (size_t i=0; i<{len(program.outputs)}; ++i) if (out[i] != 0) return 100;",
            "return 0;", "}",
        ])
        with tempfile.TemporaryDirectory(prefix="mairg-sanitizer-") as directory:
            source = Path(directory) / "boundary.c"
            executable = Path(directory) / "boundary"
            source.write_text(emit_c(program) + main, encoding="ascii")
            command = [COMPILER, "-O1", "-g", "-std=c11", "-fsanitize=address,undefined",
                       "-fno-sanitize-recover=all", "-fno-omit-frame-pointer",
                       str(source), "-o", str(executable)]
            build = subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)
            self.assertEqual(build.returncode, 0, build.stderr)
            env = dict(os.environ, ASAN_OPTIONS="detect_leaks=0:halt_on_error=1",
                       UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1")
            run = subprocess.run([str(executable)], capture_output=True, text=True,
                                 check=False, timeout=10, env=env)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(run.stderr, "")


if __name__ == "__main__":
    unittest.main()
