"""Machine-facing transport for MAIRG. Application formulas stay in graph files."""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NoReturn

from .interpreter import interpret, records_to_columns
from .ir import MairgError, decode_json, load, source_revision, to_document
from .native import compile_program
from .optimizer import optimize
from .patch import apply_patch


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise MairgError("cli", message, "arguments")


def read_json(text: str, path: str) -> Any:
    """Decode a transport JSON value without duplicate keys or non-JSON numbers."""
    try:
        return decode_json(text)
    except MairgError as exc:
        raise MairgError(exc.code, exc.message, path) from exc


def read_records(path: str) -> list[Any]:
    """The shared CLI/benchmark JSONL parser; no bad records are silently dropped."""
    try:
        if path == "-":
            lines = sys.stdin.read().splitlines()
        else:
            lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise MairgError("io", str(exc), path) from exc
    return [read_json(line, f"{path}:{index}") for index, line in enumerate(lines, 1)]


def parser() -> argparse.ArgumentParser:
    result = _Parser(prog="mairg", description="Machine-authored reduction graphs")
    sub = result.add_subparsers(dest="command", required=True, parser_class=_Parser)
    for name in ("check", "plan", "emit"):
        command = sub.add_parser(name)
        command.add_argument("graph")
    run = sub.add_parser("run")
    run.add_argument("graph")
    run.add_argument("data", help="JSONL file, or - for stdin")
    run.add_argument("--engine", choices=("native", "reference"), default="native")
    patch = sub.add_parser("patch")
    patch.add_argument("graph")
    patch.add_argument("patch", help="JSON patch file, or - for stdin")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        program = load(args.graph)
        output: Any
        if args.command == "check":
            output = {"valid": True, "source_revision": source_revision(program),
                      "inputs": len(program.inputs), "nodes": len(program.nodes),
                      "outputs": len(program.outputs)}
        elif args.command == "plan":
            plan = optimize(program)
            output = {"source_revision": source_revision(program), "fingerprint": plan.fingerprint,
                      "stats": plan.stats, "program": to_document(plan.program)}
        elif args.command == "emit":
            from .native import emit_c
            # Emission is buffered and the source is validated before stdout is touched.
            text = emit_c(optimize(program).program)
            sys.stdout.write(text)
            return 0
        elif args.command == "patch":
            text = sys.stdin.read() if args.patch == "-" else Path(args.patch).read_text(encoding="utf-8")
            patch_value = read_json(text, args.patch)
            output = to_document(apply_patch(program, patch_value))
        else:
            columns = records_to_columns(program, read_records(args.data))
            if args.engine == "reference":
                output = interpret(program, columns)
            else:
                with compile_program(program) as compiled:
                    output = compiled.run(columns)
        rendered = json.dumps(output, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        sys.stdout.write(rendered + "\n")
        return 0
    except MairgError as exc:
        sys.stderr.write(json.dumps(exc.as_dict(), ensure_ascii=True, allow_nan=False) + "\n")
        return 2
    except (OSError, UnicodeError) as exc:
        error = MairgError("io", str(exc), "transport")
        sys.stderr.write(json.dumps(error.as_dict(), ensure_ascii=True) + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
