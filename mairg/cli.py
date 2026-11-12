"""Structured JSON command transport for practical application graphs."""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NoReturn

from .language import AppError, decode_json, encode_json


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise AppError("arguments", message)


def parser() -> argparse.ArgumentParser:
    result = _Parser(prog="mairg", description="Machine-authored application graphs (practical POC)")
    sub = result.add_subparsers(dest="command", required=True, parser_class=_Parser)
    new = sub.add_parser("new")
    new.add_argument("directory")
    new.add_argument("--template", choices=("cli", "api"), default="cli")
    for name in ("describe", "check", "test"):
        command = sub.add_parser(name)
        command.add_argument("project")
    run = sub.add_parser("run")
    run.add_argument("project")
    run.add_argument("export")
    run.add_argument("--args", required=True)
    run.add_argument("--state-dir")
    serve = sub.add_parser("serve")
    serve.add_argument("project")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--state-dir", required=True)
    build = sub.add_parser("build")
    build.add_argument("project")
    build.add_argument("--output", required=True)
    patch = sub.add_parser("patch")
    patch.add_argument("project")
    patch.add_argument("patchfile")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        from .project import build_project, new_project, patch_project, test_project
        output: Any
        if args.command == "new":
            output = new_project(args.directory, args.template)
        else:
            from .compiler import compile_project
            project = compile_project(args.project)
            if args.command == "describe":
                output = project.describe()
            elif args.command == "check":
                output = {"valid": True, "source_revision": project.source_revision}
            elif args.command == "run":
                arguments = decode_json(args.args)
                if not isinstance(arguments, list):
                    raise AppError("arguments", "Run arguments must be a JSON array")
                output = project.call(args.export, arguments, state_dir=args.state_dir)
            elif args.command == "serve":
                from .web import serve_project
                serve_project(project, args.host, args.port, state_dir=args.state_dir)
                return 0
            elif args.command == "test":
                output = test_project(project)
                if not output["success"]:
                    raise AppError("tests_failed", "Project fixture failed", details=output)
            elif args.command == "build":
                output = build_project(project, args.output)
            else:
                text = sys.stdin.read() if args.patchfile == "-" else Path(args.patchfile).read_text(encoding="utf-8")
                output = patch_project(project, decode_json(text, max_bytes=1024 * 1024))
        sys.stdout.write(encode_json(output) + "\n")
        return 0
    except KeyboardInterrupt:
        return 130
    except AppError as error:
        sys.stderr.write(encode_json(error.as_dict()) + "\n")
        return 2
    except (OSError, UnicodeError, ValueError) as error:
        sys.stderr.write(encode_json(AppError("io", str(error)).as_dict()) + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
