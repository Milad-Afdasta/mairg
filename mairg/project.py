"""Project scaffolding, isolated fixtures, immutable builds, and reviewed patches."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .language import AppError, decode_json, encode_json, json_copy

BUNDLE_FORMAT = "mairg-bundle-1"


def source_revision(manifest: dict[str, Any], document: dict[str, Any], assets: dict[str, str]) -> str:
    """Content revision preserves opaque IDs but ignores declaration order."""
    program = json_copy(document)
    program["functions"] = sorted(program["functions"], key=lambda item: item["id"])
    for function in program["functions"]:
        function["nodes"] = sorted(function["nodes"], key=lambda item: item["id"])
    value = {"manifest": manifest, "program": program,
             "assets": {name: hashlib.sha256(text.encode("utf-8")).hexdigest()
                        for name, text in assets.items()}}
    encoded = encode_json(value)
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def _relative(root: Path, name: str) -> Path:
    if not isinstance(name, str) or not name or "\\" in name:
        raise AppError("path", "Expected a canonical relative path")
    part = Path(name)
    if part.is_absolute() or any(piece in (".", "..") for piece in part.parts) or part.as_posix() != name:
        raise AppError("path", "Expected a canonical relative path")
    target = (root / part).resolve()
    if not target.is_relative_to(root.resolve()):
        raise AppError("path", "Path escapes its owner")
    return target


def new_project(directory: str | Path, template: str = "cli") -> dict[str, Any]:
    if template not in ("cli", "api"):
        raise AppError("template", "Unknown project template")
    root = Path(directory).absolute()
    if os.path.lexists(root):
        raise AppError("exists", "Project destination already exists")
    nodes: list[dict[str, Any]] = [{"id": 0, "op": "param", "args": [], "index": 0}]
    if template == "cli":
        nodes.append({"id": 1, "op": "text.trim", "args": [0]})
        result, result_type, argument_type, export = 1, "str", "str", "main"
        routes: list[dict[str, str]] = []
    else:
        nodes.extend([
            {"id": 1, "op": "const", "args": [], "value": 200},
            {"id": 2, "op": "record", "args": [], "keys": []},
            {"id": 3, "op": "record", "args": [1, 2, 0], "keys": ["status", "headers", "body"]},
        ])
        result, result_type, argument_type, export = 3, "record", "record", "request"
        routes = [{"method": "POST", "path": "/echo", "export": export}]
    program = {"format": "mairg-app-1", "functions": [
        {"id": 0, "params": [argument_type], "result": result_type, "nodes": nodes, "return": result}
    ], "exports": {export: 0}}
    manifest: dict[str, Any] = {"format": "mairg-project-1", "name": root.name, "program": "program.json",
                "bindings": {}, "database": None, "assets": [], "routes": routes,
                "startup": None, "tests": ["tests.json"]}
    fixture: dict[str, Any] = {"files": {}, "cases": []}
    if template == "cli":
        fixture["cases"] = [{"export": "main", "args": ["  hello  "], "expect": "hello"}]
    root.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{root.name}.new-", dir=root.parent))
    try:
        for name, value in (("mairg.json", manifest), ("program.json", program), ("tests.json", fixture)):
            (stage / name).write_text(json.dumps(value, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
        if os.path.lexists(root):
            raise AppError("exists", "Project destination already exists")
        os.rename(stage, root)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return {"created": str(root), "template": template}


def test_project(project: Any) -> dict[str, Any]:
    """Each fixture owns new state; an unexpected outcome stops only that fixture."""
    fixtures: list[dict[str, Any]] = []
    success = True
    for name in project.manifest["tests"]:
        fixture = decode_json(_relative(project.root, name).read_bytes(), max_bytes=1024 * 1024)
        if not isinstance(fixture, dict) or set(fixture) != {"files", "cases"}:
            raise AppError("fixture", "Fixture must contain exactly files and cases")
        if not isinstance(fixture["files"], dict) or not isinstance(fixture["cases"], list):
            raise AppError("fixture", "Invalid fixture files or cases")
        for case in fixture["cases"]:
            if (not isinstance(case, dict) or set(case) not in ({"export", "args", "expect"}, {"export", "args", "error"})
                    or not isinstance(case["export"], str) or not isinstance(case["args"], list)
                    or ("error" in case and not isinstance(case["error"], str))):
                raise AppError("fixture", "Invalid fixture case")
        cases = []
        with tempfile.TemporaryDirectory(prefix="mairg-test-") as temporary:
            state = Path(temporary)
            file_root = state / "files"
            file_root.mkdir()
            resolved: set[Path] = set()
            for filename, content in fixture["files"].items():
                path = _relative(file_root, filename)
                if path in resolved or not isinstance(content, str):
                    raise AppError("fixture", "Duplicate path or nontext fixture file")
                resolved.add(path)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            stopped = False
            for index, case in enumerate(fixture["cases"]):
                if stopped:
                    cases.append({"case": index, "status": "not_run"})
                    continue
                try:
                    actual = project.call(case["export"], case["args"], state_dir=state)
                    passed = "expect" in case and _strict_equal(actual, case["expect"])
                    evidence: dict[str, Any] = {"value": actual}
                except AppError as error:
                    passed = "error" in case and error.code == case["error"]
                    evidence = error.as_dict()
                cases.append({"case": index, "status": "pass" if passed else "fail", **evidence})
                if not passed:
                    success, stopped = False, True
        fixtures.append({"fixture": name, "cases": cases})
    return {"success": success, "fixtures": fixtures}


def _strict_equal(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        return len(left) == len(right) and all(_strict_equal(a, b) for a, b in zip(left, right, strict=True))
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_strict_equal(left[key], right[key]) for key in left)
    return bool(left == right)


def patch_project(project: Any, patch: Any) -> dict[str, Any]:
    from .compiler import compile_documents
    if not isinstance(patch, dict) or set(patch) != {"base", "program"}:
        raise AppError("patch", "Patch must contain exactly base and program")
    if patch["base"] != project.source_revision:
        raise AppError("stale_patch", "Patch base does not match source revision")
    replacement = compile_documents(project.manifest, patch["program"], root=project.root, assets=project.assets)
    return replacement.document


_BUNDLE_RUNNER = r'''"""Run an immutable MAIRG application bundle; Python 3.11+ only."""
import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
SERIALIZER = None
sys.path.insert(0, str(ROOT))

def emit(value, stream=sys.stdout):
    text = SERIALIZER(value) if SERIALIZER else json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    stream.write(text + "\n")

class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)

def external_state(value):
    if value is None:
        return None
    state = Path(value).resolve()
    for name in ("files", "db"):
        resource = (state / name).resolve()
        if resource.is_relative_to(ROOT) or ROOT.is_relative_to(resource):
            from mairg.language import AppError
            raise AppError("bundle_state", "State resources must be external to the immutable bundle")
    return value

def main():
    try:
        if sys.version_info < (3, 11):
            raise ValueError("Python 3.11 or newer is required")
        manifest = json.loads((ROOT / "bundle.json").read_text("utf-8"))
        if manifest["format"] != "mairg-bundle-1" or manifest["runtime"] != "mairg-app-1":
            raise ValueError("Unsupported bundle or runtime format")
        for name, digest in manifest["files"].items():
            path = ROOT / name
            if Path(name).is_absolute() or ".." in Path(name).parts or not path.resolve().is_relative_to(ROOT):
                raise ValueError("Invalid bundle inventory path")
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError("Bundle digest mismatch: " + name)
        from mairg.language import AppError, decode_json, encode_json
        global SERIALIZER
        SERIALIZER = encode_json
        spec = importlib.util.spec_from_file_location("generatedprogram", ROOT / "program.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        project = module.load_project(ROOT)
        if len(sys.argv) == 2 and sys.argv[1] == "--describe":
            emit(project.describe())
            return 0
        if len(sys.argv) > 1 and sys.argv[1] == "serve":
            parser = Parser()
            parser.add_argument("serve")
            parser.add_argument("--host", default="127.0.0.1")
            parser.add_argument("--port", type=int, default=8000)
            parser.add_argument("--state-dir", required=True)
            args = parser.parse_args()
            from mairg.web import serve_project
            serve_project(project, args.host, args.port, state_dir=external_state(args.state_dir))
        else:
            parser = Parser()
            parser.add_argument("export")
            parser.add_argument("--args", required=True)
            parser.add_argument("--state-dir")
            args = parser.parse_args()
            arguments = decode_json(args.args)
            if not isinstance(arguments, list):
                raise AppError("arguments", "Run arguments must be a JSON array")
            emit(project.call(args.export, arguments, state_dir=external_state(args.state_dir)))
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        value = error.as_dict() if hasattr(error, "as_dict") else {"error": {"code": "bundle", "message": str(error)}}
        emit(value, sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
'''


def build_project(project: Any, output: str | Path) -> dict[str, Any]:
    output = Path(output).absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    lock = output.parent / ("." + output.name + ".mairg-lock")
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise AppError("build_locked", "Another build owns this output") from error
    stage: Path | None = None
    try:
        os.close(descriptor)
        if os.path.lexists(output):
            raise AppError("exists", "Build output already exists")
        if importlib.metadata.version("waitress") != "3.0.2":
            raise AppError("dependency", "Build requires Waitress 3.0.2")
        import waitress  # type: ignore[import-untyped]
        stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.stage-", dir=output.parent))
        runtime = stage / "mairg"
        runtime.mkdir()
        source = Path(__file__).parent
        for name in ("__init__.py", "language.py", "runtime.py", "hosts.py", "web.py"):
            shutil.copyfile(source / name, runtime / name)
        shutil.copytree(Path(waitress.__file__).parent, stage / "waitress", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for package in ("mairg", "waitress"):
            distribution = importlib.metadata.distribution(package)
            notices = [entry for entry in distribution.files or []
                       if Path(str(entry)).name in ("LICENSE", "LICENSE.txt", "COPYRIGHT")]
            if not notices:
                raise AppError("dependency", f"Installed {package} distribution has no license notice")
            for entry in notices:
                target = stage / "licenses" / (package + "-" + Path(str(entry)).name)
                target.parent.mkdir(exist_ok=True)
                shutil.copyfile(str(distribution.locate_file(entry)), target)
        (stage / "program.py").write_text(project.generated_source, encoding="utf-8")
        (stage / "run.py").write_text(_BUNDLE_RUNNER, encoding="utf-8")
        asset_root = stage / "assets"
        for name, content in project.assets.items():
            path = _relative(asset_root, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        files = {path.relative_to(stage).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in sorted(stage.rglob("*")) if path.is_file()}
        inventory = {"format": BUNDLE_FORMAT, "runtime": "mairg-app-1", "python": ">=3.11",
                     "waitress": "3.0.2", "source_revision": project.source_revision, "files": files}
        (stage / "bundle.json").write_text(encode_json(inventory) + "\n", encoding="utf-8")
        smoke = subprocess.run([sys.executable, "-I", str(stage / "run.py"), "--describe"],
                               cwd=stage, capture_output=True, text=True, timeout=30)
        if smoke.returncode:
            raise AppError("build_smoke", "Generated bundle failed import/describe smoke", details={"stderr": smoke.stderr})
        decode_json(smoke.stdout)
        if os.path.lexists(output):
            raise AppError("exists", "Build output already exists")
        os.rename(stage, output)
        stage = None
        return {"built": str(output), "source_revision": project.source_revision,
                "files": len(files), "bytes": sum((output / name).stat().st_size for name in files)}
    except (OSError, subprocess.SubprocessError) as error:
        raise AppError("build", str(error)) from error
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
        lock.unlink(missing_ok=True)
