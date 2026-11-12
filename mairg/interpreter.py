"""Independent source semantics and strict whole-batch input validation."""

from collections.abc import Iterable, Mapping
from typing import Any

from .ir import MairgError, Program, checked_program

MASK = (1 << 64) - 1
Scalar = int | bool


def _scalar(value: Any, scalar_type: str, path: str) -> None:
    if scalar_type == "u64":
        valid = type(value) is int and 0 <= value <= MASK
    else:
        valid = type(value) is bool
    if not valid:
        raise MairgError("input_type", f"expected {scalar_type}", path)


def _validate_columns_checked(
    program: Program, columns: Any
) -> tuple[tuple[tuple[Scalar, ...], ...], int]:
    if not isinstance(columns, Mapping):
        raise MairgError("input_shape", "columns must be a mapping", "columns")
    expected = {name for name, _ in program.inputs}
    if set(columns) != expected:
        raise MairgError(
            "input_fields", "columns must exactly match the declared inputs", "columns"
        )
    snapshots: list[tuple[Scalar, ...]] = []
    count: int | None = None
    for name, scalar_type in program.inputs:
        values = columns[name]
        if type(values) not in (list, tuple):
            raise MairgError("input_shape", "column must be a list or tuple", f"columns.{name}")
        snapshot = tuple(values)
        if count is None:
            count = len(snapshot)
        elif count != len(snapshot):
            raise MairgError("input_length", "columns must have equal lengths", f"columns.{name}")
        for index, value in enumerate(snapshot):
            _scalar(value, scalar_type, f"columns.{name}[{index}]")
        snapshots.append(snapshot)
    return tuple(snapshots), count if count is not None else 0


def validate_columns(
    program: Program, columns: Any
) -> tuple[tuple[tuple[Scalar, ...], ...], int]:
    """Validate and snapshot every value before any kernel can run."""
    return _validate_columns_checked(checked_program(program), columns)


def records_to_columns(program: Program, records: Any) -> dict[str, list[Scalar]]:
    """Convert complete, strictly typed records; never drop invalid records."""
    program = checked_program(program)
    if not isinstance(records, Iterable) or isinstance(records, (str, bytes, Mapping)):
        raise MairgError("input_shape", "records must be an iterable of mappings", "records")
    columns: dict[str, list[Scalar]] = {name: [] for name, _ in program.inputs}
    expected = set(columns)
    for index, row in enumerate(records):
        if not isinstance(row, Mapping) or set(row) != expected:
            raise MairgError(
                "input_fields", "record must exactly match the declared inputs", f"records[{index}]"
            )
        for name, scalar_type in program.inputs:
            value = row[name]
            _scalar(value, scalar_type, f"records[{index}].{name}")
            columns[name].append(value)
    return columns


def interpret(program: Program, columns: Any) -> dict[str, int]:
    """Evaluate the original graph without optimizer/codegen evaluation helpers."""
    program = checked_program(program)
    data, count = _validate_columns_checked(program, columns)
    result = {output.name: 0 for output in program.outputs}
    for row in range(count):
        values: dict[int, Scalar] = {}
        for node in program.nodes:
            args = node.args
            op = node.op
            if op == "input":
                value: Scalar = data[args[0]][row]
            elif op in ("u64", "bool"):
                value = args[0]
            elif op == "add":
                value = (values[args[0]] + values[args[1]]) & MASK
            elif op == "sub":
                value = (values[args[0]] - values[args[1]]) & MASK
            elif op == "mul":
                value = (values[args[0]] * values[args[1]]) & MASK
            elif op == "bit_and":
                value = values[args[0]] & values[args[1]]
            elif op == "bit_or":
                value = values[args[0]] | values[args[1]]
            elif op == "bit_xor":
                value = values[args[0]] ^ values[args[1]]
            elif op == "eq":
                value = values[args[0]] == values[args[1]]
            elif op == "lt":
                value = values[args[0]] < values[args[1]]
            elif op == "le":
                value = values[args[0]] <= values[args[1]]
            elif op == "gt":
                value = values[args[0]] > values[args[1]]
            elif op == "ge":
                value = values[args[0]] >= values[args[1]]
            elif op == "and":
                value = bool(values[args[0]] and values[args[1]])
            elif op == "or":
                value = bool(values[args[0]] or values[args[1]])
            elif op == "not":
                value = not values[args[0]]
            elif op == "select":
                value = values[args[1]] if values[args[0]] else values[args[2]]
            else:
                raise MairgError("internal_op", "validated operation has no interpreter", "nodes")
            values[node.id] = value
        for output in program.outputs:
            if not values[output.where]:
                continue
            if output.op == "count":
                result[output.name] = (result[output.name] + 1) & MASK
            else:
                assert output.value is not None
                number = int(values[output.value])
                if output.op == "sum":
                    result[output.name] = (result[output.name] + number) & MASK
                elif output.op == "max":
                    result[output.name] = max(result[output.name], number)
                elif output.op == "xor":
                    result[output.name] ^= number
                else:
                    raise MairgError("internal_op", "validated reduction has no interpreter", "outputs")
    return result
