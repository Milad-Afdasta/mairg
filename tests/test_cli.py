from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from mairg.cli import main


class CliTests(unittest.TestCase):
    def invoke(self, args: list[str]) -> tuple[int, str, str]:
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            result = main(args)
        return result, output.getvalue(), error.getvalue()

    def test_real_new_check_describe_run_test_patch_build_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "application"
            for args in (["new", str(project)], ["check", str(project)], ["describe", str(project)],
                         ["run", str(project), "main", "--args", '["  useful  "]'], ["test", str(project)]):
                code, output, error = self.invoke(args)
                self.assertEqual(code, 0, error)
                json.loads(output)
            code, output, error = self.invoke(["run", str(project), "main", "--args", '["  useful  "]'])
            self.assertEqual(json.loads(output), "useful")
            check = json.loads(self.invoke(["check", str(project)])[1])
            original = (project / "program.json").read_bytes()
            replacement = json.loads(original)
            replacement["functions"][0]["nodes"][1]["op"] = "text.upper"
            patch = root / "patch.json"
            patch.write_text(json.dumps({"base": check["source_revision"], "program": replacement}))
            code, output, error = self.invoke(["patch", str(project), str(patch)])
            self.assertEqual(code, 0, error)
            self.assertEqual(json.loads(output), replacement)
            self.assertEqual((project / "program.json").read_bytes(), original)
            code, output, error = self.invoke(["build", str(project), "--output", str(root / "bundle")])
            self.assertEqual(code, 0, error)
            self.assertTrue((Path(json.loads(output)["built"]) / "run.py").exists())

    def test_bad_arguments_and_removed_commands_are_structured_errors(self) -> None:
        for args in ([], ["plan", "old.json"], ["emit", "old.json"], ["run", "missing", "main", "--args", "NaN"]):
            code, output, error = self.invoke(args)
            self.assertEqual(code, 2)
            self.assertEqual(output, "")
            self.assertIn("error", json.loads(error))

    def test_run_rejects_nonarray_duplicate_keys_and_nonfinite_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = str(Path(temporary) / "application")
            self.assertEqual(self.invoke(["new", project])[0], 0)
            for text in ('{"x":1}', '[{"x":1,"x":2}]', '[NaN]'):
                code, output, error = self.invoke(["run", project, "main", "--args", text])
                self.assertEqual(code, 2)
                self.assertEqual(output, "")
                self.assertIn("error", json.loads(error))
