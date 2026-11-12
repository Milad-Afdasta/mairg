import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mairg.interpreter import interpret
from mairg.ir import OPS, MairgError, Node, Output, Program, validate
from mairg.native import compile_program, emit_c, prepare_batch
from tests.test_interpreter import arithmetic_program


def operation_program():
    nodes = [[0, "input", 0], [1, "input", 1], [2, "input", 2], [3, "input", 3],
             [4, "u64", (1 << 64) - 1], [5, "bool", True]]
    outputs = []
    for op in OPS:
        if op in ("input", "u64", "bool"):
            continue
        node_id = len(nodes)
        if op in ("and", "or"):
            args, reduction = [2, 3], "count"
        elif op == "not":
            args, reduction = [2], "count"
        elif op == "select":
            args, reduction = [2, 0, 1], "sum"
        else:
            args = [0, 1]
            reduction = "count" if op in ("eq", "lt", "le", "gt", "ge") else "sum"
        nodes.append([node_id, op, *args])
        outputs.append([op, reduction, node_id] if reduction == "count"
                       else [op, reduction, node_id, 5])
    # Both overloaded bool-equality and bool-selection must survive native lowering.
    nodes.extend([[len(nodes), "eq", 2, 3], [len(nodes) + 1, "select", 2, 3, 5]])
    outputs.extend([["bool_eq", "count", nodes[-2][0]],
                    ["bool_select", "count", nodes[-1][0]],
                    ["max", "max", 0, 2], ["xor", "xor", 0, 2]])
    return validate({"format": "mairg-1",
                     "inputs": [["x", "u64"], ["y", "u64"], ["a", "bool"], ["b", "bool"]],
                     "nodes": nodes, "outputs": outputs})


@unittest.skipUnless(shutil.which("clang") or shutil.which("gcc"), "native compiler required")
class NativeTests(unittest.TestCase):
    def test_every_catalog_operation_native_reference_and_empty(self):
        program = operation_program()
        columns = {"x": [0, (1 << 64) - 1, 17, 1], "y": [1, 1, 3, 0],
                   "a": [True, False, True, False], "b": [False, False, True, True]}
        expected = interpret(program, columns)
        for enabled in (True, False):
            with self.subTest(optimized=enabled), compile_program(program, enabled) as compiled:
                self.assertEqual(compiled.run(columns), expected)
                self.assertEqual(compiled.run({name: [] for name in columns}),
                                 dict.fromkeys(expected, 0))
                self.assertEqual(compiled.source.count("for ("), 1)
                batch = compiled.prepare(columns)
                self.assertEqual(compiled.run_prepared(batch), expected)
                self.assertEqual(compiled.run_prepared(batch), expected)

    def test_closed_owner_and_cross_schema_are_rejected(self):
        program = arithmetic_program()
        compiled = compile_program(program)
        directory = compiled.directory
        with self.assertRaises(MairgError):
            compiled.run_prepared(prepare_batch(operation_program(),
                {"x": [], "y": [], "a": [], "b": []}))
        compiled.close()
        self.assertFalse(directory.exists())
        compiled.close()
        for action in [lambda: compiled.prepare({"x": [], "flag": []}),
                       lambda: compiled.run({"x": [], "flag": []})]:
            with self.assertRaises(MairgError):
                action()

    def test_invalid_batch_does_not_call_native(self):
        with compile_program(arithmetic_program()) as compiled:
            with patch.object(compiled, "kernel") as kernel:
                with self.assertRaises(MairgError):
                    compiled.run({"x": [1, True], "flag": [True, False]})
                kernel.assert_not_called()

    def test_forged_ir_rejected_before_compiler_start(self):
        forged = Program((("x", "u64"),),
                         (Node(0, "input", (0,), "bool"), Node(1, "bool", (True,), "bool")),
                         (Output("total", "sum", 0, 1),))
        with patch("mairg.native.subprocess.Popen") as start:
            with self.assertRaises(MairgError):
                compile_program(forged)
            start.assert_not_called()
        with self.assertRaises(MairgError):
            emit_c(forged)

    def test_names_cannot_enter_generated_source(self):
        name = 'x"; system("touch injected"); /*'
        program = validate({"format": "mairg-1", "inputs": [[name, "u64"]],
                            "nodes": [[0, "input", 0], [1, "bool", True]],
                            "outputs": [[name, "sum", 0, 1]]})
        with compile_program(program) as compiled:
            self.assertNotIn(name, compiled.source)
            self.assertEqual(compiled.run({name: [2, 3]}), {name: 5})

    def test_missing_nonzero_and_invalid_library_cleanup(self):
        program = arithmetic_program()
        with self.assertRaises(MairgError) as context:
            compile_program(program, compiler="/nonexistent-mairg-compiler")
        self.assertEqual(context.exception.code, "compiler_missing")
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            for body, code in [("raise SystemExit(7)", "compiler_failed"),
                               ("pass", "library_load")]:
                compiler = parent / "compiler"
                compiler.write_text(f"#!{sys.executable}\n{body}\n")
                compiler.chmod(0o700)
                with self.subTest(code=code), patch("mairg.native.tempfile.tempdir", directory):
                    with self.assertRaises(MairgError) as context:
                        compile_program(program, compiler=compiler)
                    self.assertEqual(context.exception.code, code)
                    self.assertEqual(list(parent.glob("mairg-build-*")), [])

    def test_timeout_stops_owned_child_and_cleans_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            pidfile = parent / "child.pid"
            compiler = parent / "compiler"
            compiler.write_text(
                f"#!{sys.executable}\nimport subprocess,sys,time\n"
                "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])\n"
                f"open({str(pidfile)!r},'w').write(str(child.pid))\n"
                "time.sleep(30)\n"
            )
            compiler.chmod(0o700)
            with patch("mairg.native.tempfile.tempdir", directory):
                with self.assertRaises(MairgError) as context:
                    compile_program(arithmetic_program(), compiler=compiler, timeout=0.3)
            self.assertEqual(context.exception.code, "compiler_timeout")
            self.assertEqual(list(parent.glob("mairg-build-*")), [])
            pid = int(pidfile.read_text())
            state = subprocess.run(["ps", "-p", str(pid), "-o", "stat="],
                                   capture_output=True, text=True).stdout.strip()
            self.assertTrue(not state or state.startswith("Z"), state)
            # The kernel group has stopped running; orphan zombie reaping belongs to the OS.
            if state and not state.startswith("Z"):
                os.kill(pid, 9)


if __name__ == "__main__":
    unittest.main()
