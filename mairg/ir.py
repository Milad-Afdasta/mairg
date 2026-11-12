"""The single owner of MAIRG's source, type, and validated-IR contract."""

from __future__ import annotations

import hashlib
import heapq
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

U64_MAX = (1 << 64) - 1
MAX_SOURCE_BYTES = 1 << 20
MAX_NODES = 4096
MAX_DEPTH = 256
MAX_ID = (1 << 31) - 1


class MairgError(Exception):
    def __init__(self, code: str, message: str, path: str = "$") -> None:
        super().__init__(message)
        self.code, self.message, self.path = code, message, path

    def as_dict(self) -> dict[str, dict[str, str]]:
        return {"error": {"code": self.code, "message": self.message, "path": self.path}}


@dataclass(frozen=True)
class OpSpec:
    arity: int
    kind: str
    commutative: bool = False


# This is the language catalog. Dispatch implementations are tested against it.
OPS = MappingProxyType({
    "input": OpSpec(1, "input"),
    "u64": OpSpec(1, "literal_u64"),
    "bool": OpSpec(1, "literal_bool"),
    "add": OpSpec(2, "u64", True),
    "sub": OpSpec(2, "u64"),
    "mul": OpSpec(2, "u64", True),
    "bit_and": OpSpec(2, "u64", True),
    "bit_or": OpSpec(2, "u64", True),
    "bit_xor": OpSpec(2, "u64", True),
    "eq": OpSpec(2, "same", True),
    "lt": OpSpec(2, "comparison"),
    "le": OpSpec(2, "comparison"),
    "gt": OpSpec(2, "comparison"),
    "ge": OpSpec(2, "comparison"),
    "and": OpSpec(2, "bool", True),
    "or": OpSpec(2, "bool", True),
    "not": OpSpec(1, "bool"),
    "select": OpSpec(3, "select"),
})
REDUCTIONS = MappingProxyType({"count": 1, "sum": 2, "max": 2, "xor": 2})
LEAF_OPS = frozenset(op for op, spec in OPS.items() if spec.kind.startswith("literal") or spec.kind == "input")


@dataclass(frozen=True)
class Node:
    id: int
    op: str
    args: tuple[Any, ...]
    type: str


@dataclass(frozen=True)
class Output:
    name: str
    op: str
    value: int | None
    where: int


@dataclass(frozen=True)
class Program:
    inputs: tuple[tuple[str, str], ...]
    nodes: tuple[Node, ...]
    outputs: tuple[Output, ...]


def _fail(message: str, path: str = "$", code: str = "invalid_program") -> None:
    raise MairgError(code, message, path)


def _array(value: Any, path: str, minimum: int, maximum: int) -> list[Any]:
    if type(value) is not list or not minimum <= len(value) <= maximum:
        _fail(f"expected array with {minimum}..{maximum} entries", path)
    return value


def _id(value: Any, path: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_ID:
        _fail(f"expected node ID in 0..{MAX_ID}", path)
    return value


def _name(value: Any, path: str) -> str:
    if type(value) is not str or not 1 <= len(value) <= 128:
        _fail("expected nonempty name of at most 128 Unicode code points", path)
    return value


def decode_json(text: str | bytes) -> Any:
    """Strict JSON transport, shared by source and patch hosts."""
    if type(text) not in (str, bytes):
        raise MairgError("invalid_json", "expected UTF-8 text or bytes")
    try:
        size = len(text.encode("utf-8")) if isinstance(text, str) else len(text)
        if size > MAX_SOURCE_BYTES:
            raise MairgError("source_limit", "source exceeds 1 MiB")
        if isinstance(text, bytes):
            text = text.decode("utf-8")

        def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in items:
                if key in result:
                    raise MairgError("invalid_json", "duplicate JSON key")
                result[key] = value
            return result

        def invalid_constant(value: str) -> None:
            raise MairgError("invalid_json", f"unsupported JSON constant {value}")

        return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except MairgError:
        raise
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise MairgError("invalid_json", "malformed, non-UTF-8, or excessively nested JSON") from exc


def loads(text: str | bytes) -> Program:
    return validate(decode_json(text))


def load(path: str | Path) -> Program:
    try:
        with Path(path).open("rb") as stream:
            return loads(stream.read(MAX_SOURCE_BYTES + 1))
    except OSError as exc:
        raise MairgError("source_io", str(exc)) from exc


def validate(document: Any) -> Program:
    """Validate the entire graph, including dead nodes, before returning typed IR."""
    if type(document) is not dict or set(document) != {"format", "inputs", "nodes", "outputs"}:
        _fail("expected exactly format, inputs, nodes, outputs keys")
    if document["format"] != "mairg-1" or type(document["format"]) is not str:
        _fail("unsupported format; expected mairg-1", "$.format")
    raw_inputs = _array(document["inputs"], "$.inputs", 1, 128)
    inputs: list[tuple[str, str]] = []
    input_names: set[str] = set()
    for i, row in enumerate(raw_inputs):
        path = f"$.inputs[{i}]"
        row = _array(row, path, 2, 2)
        name = _name(row[0], path + "[0]")
        if name in input_names:
            _fail("duplicate input name", path)
        if type(row[1]) is not str or row[1] not in ("u64", "bool"):
            _fail("input type must be u64 or bool", path + "[1]")
        input_names.add(name)
        inputs.append((name, row[1]))

    raw_nodes = _array(document["nodes"], "$.nodes", 1, MAX_NODES)
    rows: dict[int, tuple[str, tuple[Any, ...], str]] = {}
    dependencies: dict[int, tuple[int, ...]] = {}
    for i, row in enumerate(raw_nodes):
        path = f"$.nodes[{i}]"
        row = _array(row, path, 3, 5)
        ident = _id(row[0], path + "[0]")
        if ident in rows:
            _fail("duplicate node ID", path + "[0]")
        op = row[1]
        if type(op) is not str or op not in OPS:
            _fail("unknown opcode", path + "[1]")
        if len(row) != OPS[op].arity + 2:
            _fail(f"{op} expects {OPS[op].arity} operands", path)
        args = tuple(row[2:])
        if op == "input":
            if type(args[0]) is not int or not 0 <= args[0] < len(inputs):
                _fail("invalid input position", path + "[2]")
        elif op == "u64":
            if type(args[0]) is not int or not 0 <= args[0] <= U64_MAX:
                _fail("u64 literal must be an integer in 0..2^64-1", path + "[2]")
        elif op == "bool":
            if type(args[0]) is not bool:
                _fail("bool literal must be a JSON boolean", path + "[2]")
        else:
            for j, arg in enumerate(args):
                _id(arg, path + f"[{j + 2}]")
        rows[ident] = (op, args, path)
        dependencies[ident] = () if op in LEAF_OPS else args

    dependents: dict[int, list[int]] = defaultdict(list)
    remaining: dict[int, int] = {}
    for ident, refs in dependencies.items():
        # Dedup edges: x+x is one dependency, not a false cycle.
        unique = set(refs)
        remaining[ident] = len(unique)
        for ref in unique:
            if ref not in rows:
                _fail("unknown node reference", rows[ident][2])
            dependents[ref].append(ident)
    ready = [ident for ident, count in remaining.items() if count == 0]
    heapq.heapify(ready)
    nodes: list[Node] = []
    types: dict[int, str] = {}
    depths: dict[int, int] = {}
    while ready:
        ident = heapq.heappop(ready)
        op, args, path = rows[ident]
        refs = dependencies[ident]
        depth = 1 + max((depths[ref] for ref in refs), default=0)
        if depth > MAX_DEPTH:
            _fail(f"dependency depth exceeds {MAX_DEPTH}", path)
        depths[ident] = depth
        kind = OPS[op].kind
        operand_types = tuple(types[ref] for ref in refs)
        if kind == "input":
            result_type = inputs[args[0]][1]
        elif kind == "literal_u64":
            result_type = "u64"
        elif kind == "literal_bool":
            result_type = "bool"
        elif kind in ("u64", "comparison"):
            if any(t != "u64" for t in operand_types):
                _fail(f"{op} requires u64 operands", path)
            result_type = "u64" if kind == "u64" else "bool"
        elif kind == "bool":
            if any(t != "bool" for t in operand_types):
                _fail(f"{op} requires bool operands", path)
            result_type = "bool"
        elif kind == "same":
            if operand_types[0] != operand_types[1]:
                _fail("eq operands must have identical types", path)
            result_type = "bool"
        else:  # select
            if operand_types[0] != "bool" or operand_types[1] != operand_types[2]:
                _fail("select requires bool condition and identically typed branches", path)
            result_type = operand_types[1]
        types[ident] = result_type
        nodes.append(Node(ident, op, args, result_type))
        for dependent in dependents[ident]:
            remaining[dependent] -= 1
            if remaining[dependent] == 0:
                heapq.heappush(ready, dependent)
    if len(nodes) != len(rows):
        _fail("graph contains a cycle", "$.nodes")

    outputs: list[Output] = []
    output_names: set[str] = set()
    for i, row in enumerate(_array(document["outputs"], "$.outputs", 1, 128)):
        path = f"$.outputs[{i}]"
        row = _array(row, path, 3, 4)
        name = _name(row[0], path + "[0]")
        if name in output_names:
            _fail("duplicate output name", path)
        op = row[1]
        if type(op) is not str or op not in REDUCTIONS:
            _fail("unknown reduction", path + "[1]")
        if len(row) != REDUCTIONS[op] + 2:
            _fail(f"{op} has incorrect reduction arity", path)
        where = _id(row[-1], path + f"[{len(row) - 1}]")
        value = None if op == "count" else _id(row[2], path + "[2]")
        if where not in types or types[where] != "bool":
            _fail("reduction predicate must reference a bool node", path)
        if value is not None and (value not in types or types[value] != "u64"):
            _fail("reduction value must reference a u64 node", path)
        output_names.add(name)
        outputs.append(Output(name, op, value, where))
    return Program(tuple(inputs), tuple(nodes), tuple(outputs))


def to_document(program: Program) -> dict[str, Any]:
    """Serialize IR; consumers must call checked_program, not trust serialization."""
    try:
        return {
            "format": "mairg-1",
            "inputs": [list(pair) for pair in program.inputs],
            "nodes": [[node.id, node.op, *node.args] for node in program.nodes],
            "outputs": [([out.name, out.op, out.where] if out.op == "count" else
                         [out.name, out.op, out.value, out.where]) for out in program.outputs],
        }
    except (AttributeError, TypeError, ValueError) as exc:
        raise MairgError("invalid_program", "malformed Program object") from exc


def checked_program(program: Program) -> Program:
    """Reconstruct via the sole validator and reject metadata lost in serialization."""
    if type(program) is not Program:
        _fail("expected Program object")
    if type(program.inputs) is not tuple or type(program.nodes) is not tuple or type(program.outputs) is not tuple:
        _fail("Program collections must be tuples")
    if any(type(pair) is not tuple for pair in program.inputs):
        _fail("Program input declarations must be tuples")
    if any(type(n) is not Node or type(n.args) is not tuple for n in program.nodes):
        _fail("malformed Node object")
    if any(type(o) is not Output for o in program.outputs):
        _fail("malformed Output object")
    inferred = validate(to_document(program))
    inferred_nodes = {node.id: node for node in inferred.nodes}
    for node in program.nodes:
        if type(node.type) is not str or node.type != inferred_nodes[node.id].type:
            _fail("stored node type differs from inferred type", f"$.nodes[id={node.id}]")
    for supplied, rebuilt in zip(program.outputs, inferred.outputs):
        if supplied.op == "count" and supplied.value is not None:
            _fail("count output must have value=None", "$.outputs")
        if supplied != rebuilt:
            _fail("stored output metadata differs from inferred output", "$.outputs")
    return inferred


def canonical_bytes(document: Any) -> bytes:
    return json.dumps(document, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"), allow_nan=False).encode("ascii")


def source_revision(program: Program) -> str:
    program = checked_program(program)
    document = to_document(program)
    document["nodes"].sort(key=lambda row: row[0])
    return hashlib.sha256(canonical_bytes(document)).hexdigest()
