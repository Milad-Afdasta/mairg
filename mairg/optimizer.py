"""Exact graph rewrites and canonical dependency scheduling, without user loops."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from .ir import (
    LEAF_OPS,
    OPS,
    U64_MAX,
    Node,
    Output,
    Program,
    canonical_bytes,
    checked_program,
    to_document,
)


@dataclass(frozen=True)
class Plan:
    program: Program
    fingerprint: str
    stats: dict[str, int]


def _fold(op: str, args: tuple[Any, ...]) -> int | bool:
    """Literal evaluation, deliberately separate from the reference interpreter."""
    a = args[0]
    b: Any = args[1] if len(args) > 1 else None
    if op == "add":
        return (a + b) & U64_MAX
    if op == "sub":
        return (a - b) & U64_MAX
    if op == "mul":
        return (a * b) & U64_MAX
    if op == "bit_and":
        return a & b
    if op == "bit_or":
        return a | b
    if op == "bit_xor":
        return a ^ b
    if op == "eq":
        return a == b
    if op == "lt":
        return a < b
    if op == "le":
        return a <= b
    if op == "gt":
        return a > b
    if op == "ge":
        return a >= b
    if op == "and":
        return bool(a and b)
    if op == "or":
        return bool(a or b)
    if op == "not":
        return not a
    if op == "select":
        return args[1] if a else args[2]
    raise AssertionError(f"missing optimizer implementation for {op}")


def optimize(program: Program) -> Plan:
    source = checked_program(program)
    by_id = {node.id: node for node in source.nodes}
    live: set[int] = set()
    pending = [ref for output in source.outputs for ref in
               ((output.where,) if output.value is None else (output.value, output.where))]
    while pending:
        ident = pending.pop()
        if ident in live:
            continue
        live.add(ident)
        node = by_id[ident]
        if node.op not in LEAF_OPS:
            pending.extend(node.args)

    # Interning uses exact keys. Hashes only order dependency-equivalent graphs.
    intern: dict[tuple[Any, ...], int] = {}
    records: list[Node] = []
    digests: list[str] = []
    translated: dict[int, int] = {}
    folded = merged = 0
    for node in source.nodes:
        if node.id not in live:
            continue
        op, args = node.op, node.args
        if op not in LEAF_OPS:
            args = tuple(translated[arg] for arg in args)
            if all(records[arg].op in ("u64", "bool") for arg in args):
                value = _fold(op, tuple(records[arg].args[0] for arg in args))
                op, args = node.type, (value,)
                folded += 1
            elif OPS[op].commutative:
                args = tuple(sorted(args, key=lambda arg: digests[arg]))
        key = (op, node.type, args)
        if key in intern:
            translated[node.id] = intern[key]
            merged += 1
            continue
        ident = len(records)
        intern[key] = ident
        records.append(Node(ident, op, args, node.type))
        descriptor = [op, node.type, list(args) if op in LEAF_OPS else [digests[arg] for arg in args]]
        digests.append(hashlib.sha256(canonical_bytes(descriptor)).hexdigest())
        translated[node.id] = ident

    outputs = tuple(Output(output.name, output.op,
                           None if output.value is None else translated[output.value],
                           translated[output.where]) for output in source.outputs)
    ordered: list[Node] = []
    canonical_ids: dict[int, int] = {}
    # Output schema order is contractual; dependency traversal is canonical.
    for output in outputs:
        roots = (output.where,) if output.value is None else (output.value, output.where)
        for root in roots:
            stack = [(root, False)]
            while stack:
                ident, expanded = stack.pop()
                if ident in canonical_ids:
                    continue
                node = records[ident]
                if not expanded and node.op not in LEAF_OPS:
                    stack.append((ident, True))
                    stack.extend((arg, False) for arg in reversed(node.args))
                    continue
                canonical_id = len(ordered)
                args = node.args if node.op in LEAF_OPS else tuple(canonical_ids[arg] for arg in node.args)
                canonical_ids[ident] = canonical_id
                ordered.append(Node(canonical_id, node.op, args, node.type))
    canonical_outputs = tuple(Output(output.name, output.op,
                                     None if output.value is None else canonical_ids[output.value],
                                     canonical_ids[output.where]) for output in outputs)
    canonical = Program(source.inputs, tuple(ordered), canonical_outputs)
    canonical = checked_program(canonical)
    fingerprint = hashlib.sha256(canonical_bytes(to_document(canonical))).hexdigest()
    stats = {
        "source_nodes": len(source.nodes),
        "live_nodes": len(live),
        "optimized_nodes": len(canonical.nodes),
        "folded_nodes": folded,
        "merged_nodes": merged,
        "dead_nodes": len(source.nodes) - len(live),
        "row_passes": 1,
        "intermediate_column_buffers": 0,
    }
    return Plan(canonical, fingerprint, stats)
