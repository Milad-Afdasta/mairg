"""Fresh-author acceptance through documented CLI commands only, no runtime imports."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "examples" / "changelog"
CLI = [sys.executable, "-m", "mairg"]
CSV = 'category,title,issue\n Fixed ,"Fix café, quoting",0012\nADDED,"Ship 日本語 support",42\nchanged,"Keep ""quoted"" words",\nfixed,"Fix café, quoting",12\n'
EXPECTED = '# Release 1.2.0\n\n## Added\n- Ship 日本語 support (#42)\n\n## Changed\n- Keep "quoted" words\n\n## Fixed\n- Fix café, quoting (#12)\n- Fix café, quoting (#12)\n\n'


class IndependentApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mairg-independent-changelog-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.state = self.base / "state"
        self.files = self.state / "files"
        self.files.mkdir(parents=True)
        (self.files / "changes.csv").write_text(CSV, encoding="utf-8")

    def cli(self, *args, success=True):
        result = subprocess.run(
            [*CLI, *map(str, args)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=40,
        )
        self.assertEqual(
            result.returncode, 0 if success else 2, result.stdout + result.stderr
        )
        return json.loads(result.stdout if success else result.stderr)

    def generate(self, name="changes.csv", version=" 1.2.0 ", success=True):
        return self.cli(
            "run",
            PROJECT,
            "generate",
            "--args",
            json.dumps([name, "release.md", version]),
            "--state-dir",
            self.state,
            success=success,
        )

    def test_documented_check_fixtures_and_actual_written_markdown(self):
        self.assertTrue(self.cli("check", PROJECT)["valid"])
        self.assertTrue(self.cli("test", PROJECT)["success"])
        description = self.cli("describe", PROJECT)
        self.assertEqual(
            description["exports"]["generate"]["params"], ["str", "str", "str"]
        )
        self.assertEqual(self.generate(), EXPECTED)
        self.assertEqual(
            (self.files / "release.md").read_text(encoding="utf-8"), EXPECTED
        )
        self.assertEqual(
            sorted(p.name for p in self.files.iterdir()), ["changes.csv", "release.md"]
        )

    def test_all_later_invalid_rows_preserve_existing_output(self):
        sentinel = b"EXISTING RELEASE\n\x00must survive byte-for-byte\n"
        output = self.files / "release.md"
        failures = [
            ("unknown,Bad,2\n", "invalid_category"),
            ("fixed,   ,2\n", "invalid_title"),
            ("fixed,Bad,xyz\n", "invalid_issue"),
            ("fixed,Bad,0\n", "invalid_issue"),
            ("fixed,Bad,-2\n", "invalid_issue"),
            ("fixed,Bad,1.5\n", "invalid_issue"),
        ]
        for later_row, code in failures:
            with self.subTest(later_row=later_row):
                output.write_bytes(sentinel)
                (self.files / "bad.csv").write_text(
                    "category,title,issue\nadded,Valid first row,1\n" + later_row,
                    encoding="utf-8",
                )
                error = self.generate("bad.csv", success=False)
                self.assertEqual(error["error"]["code"], code)
                self.assertEqual(output.read_bytes(), sentinel)
                self.assertEqual(
                    sorted(p.name for p in self.files.iterdir()),
                    ["bad.csv", "changes.csv", "release.md"],
                )

    def test_header_and_version_rejections_precede_publication(self):
        output = self.files / "release.md"
        output.write_text("preserve", encoding="utf-8")
        for csv_text, code in [
            ("category,title,issue,extra\nfixed,Title,2,no\n", "invalid_columns"),
            ("category,title,issue\n", "empty_csv"),
        ]:
            (self.files / "bad.csv").write_text(csv_text, encoding="utf-8")
            self.assertEqual(
                self.generate("bad.csv", success=False)["error"]["code"], code
            )
            self.assertEqual(output.read_text(encoding="utf-8"), "preserve")
        for version in ("   ", "v1\nv2", "x" * 65):
            self.assertEqual(
                self.generate(version=version, success=False)["error"]["code"],
                "invalid_version",
            )
            self.assertEqual(output.read_text(encoding="utf-8"), "preserve")

    def test_multiline_titles_markdown_escape_and_empty_sections(self):
        (self.files / "changes.csv").write_text(
            'category,title,issue\nadded,"  First *bold* [label]\nnext\tpart  ", 0007 \n',
            encoding="utf-8",
        )
        expected = "# Release 1.2.0\n\n## Added\n- First \\*bold\\* \\[label\\] next part (#7)\n\n## Changed\n- None.\n\n## Fixed\n- None.\n\n"
        self.assertEqual(self.generate(), expected)
        self.assertEqual(
            (self.files / "release.md").read_text(encoding="utf-8"), expected
        )

    def test_standalone_bundle_and_graph_only_customization(self):
        bundle = self.base / "bundle"
        self.cli("build", PROJECT, "--output", bundle)
        # A new interpreter without pip/site packages proves no installed MAIRG.
        clean_env = self.base / "clean-python"
        subprocess.run(
            [sys.executable, "-m", "venv", "--without-pip", str(clean_env)],
            check=True,
            capture_output=True,
            timeout=30,
        )
        python = clean_env / "bin" / "python"
        env = {**os.environ, "PYTHONPATH": ""}
        probe = subprocess.run(
            [
                str(python),
                "-I",
                "-c",
                "import importlib.util; assert importlib.util.find_spec('mairg') is None",
            ],
            cwd=self.base,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(probe.returncode, 0, probe.stderr)

        def standalone(path):
            result = subprocess.run(
                [
                    str(python),
                    "-I",
                    str(path / "run.py"),
                    "generate",
                    "--args",
                    json.dumps(["changes.csv", "release.md", "1.2.0"]),
                    "--state-dir",
                    str(self.state),
                ],
                cwd=self.base,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

        self.assertEqual(standalone(bundle), EXPECTED)
        self.assertEqual(
            (self.files / "release.md").read_text(encoding="utf-8"), EXPECTED
        )
        # Change only application source; no Python callback or runtime edit.
        customized = self.base / "custom-project"
        shutil.copytree(PROJECT, customized)
        source_path = customized / "program.json"
        source = json.loads(source_path.read_text(encoding="utf-8"))
        changed = 0
        for function in source["functions"]:
            for node in function["nodes"]:
                if node["op"] == "const" and node.get("value") == "Fixed":
                    node["value"] = "Resolved"
                    changed += 1
        self.assertEqual(changed, 1)
        source_path.write_text(json.dumps(source), encoding="utf-8")
        customized_bundle = self.base / "custom-bundle"
        self.cli("build", customized, "--output", customized_bundle)
        customized_expected = EXPECTED.replace("## Fixed\n", "## Resolved\n")
        self.assertEqual(standalone(customized_bundle), customized_expected)
        self.assertEqual(
            (self.files / "release.md").read_text(encoding="utf-8"), customized_expected
        )


if __name__ == "__main__":
    unittest.main()
