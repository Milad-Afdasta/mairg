"""Validated in-memory graph transactions with an exact source-revision base."""

from __future__ import annotations

from typing import Any

from .ir import (
    MAX_ID,
    MairgError,
    Program,
    checked_program,
    source_revision,
    to_document,
    validate,
)


def apply_patch(program: Program, patch: Any) -> Program:
    source = checked_program(program)
    if type(patch) is not dict or "base" not in patch or not set(patch) <= {"base", "set_nodes", "remove_nodes", "outputs"}:
        raise MairgError("invalid_patch", "expected base and only set_nodes, remove_nodes, outputs fields")
    if type(patch["base"]) is not str or patch["base"] != source_revision(source):
        raise MairgError("stale_patch", "patch base does not match source revision", "$.base")
    set_nodes = patch.get("set_nodes", [])
    remove_nodes = patch.get("remove_nodes", [])
    if type(set_nodes) is not list or type(remove_nodes) is not list:
        raise MairgError("invalid_patch", "set_nodes and remove_nodes must be arrays")
    document = to_document(source)
    rows = {row[0]: row for row in document["nodes"]}
    changed: set[int] = set()
    for i, ident in enumerate(remove_nodes):
        if type(ident) is not int or not 0 <= ident <= MAX_ID:
            raise MairgError("invalid_patch", "invalid removal ID", f"$.remove_nodes[{i}]")
        if ident in changed or ident not in rows:
            raise MairgError("invalid_patch", "duplicate removal or nonexistent node", f"$.remove_nodes[{i}]")
        changed.add(ident)
        del rows[ident]
    for i, row in enumerate(set_nodes):
        if type(row) is not list or not row or type(row[0]) is not int or not 0 <= row[0] <= MAX_ID:
            raise MairgError("invalid_patch", "invalid set node row", f"$.set_nodes[{i}]")
        ident = row[0]
        if ident in changed:
            raise MairgError("invalid_patch", "duplicate or overlapping node mutation", f"$.set_nodes[{i}]")
        changed.add(ident)
        rows[ident] = row[:]
    document["nodes"] = list(rows.values())
    if "outputs" in patch:
        document["outputs"] = patch["outputs"]
    # No original objects or files are mutated; validation precedes publication.
    return validate(document)
