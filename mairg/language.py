"""The sole language/value and generic host catalog for MAIRG."""
from __future__ import annotations

import json
import math
from typing import Any

MAX_SOURCE = 1_048_576
MAX_VALUE = 16_777_216
MAX_COLLECTION = 100_000
MAX_DEPTH = 128
MAX_INT_BITS = 65_536
TYPES = frozenset({'any', 'null', 'bool', 'int', 'float', 'number', 'str', 'list', 'record'})


class AppError(Exception):
    def __init__(self, code: str, message: str, *, function: int | None = None,
                 node: int | None = None, details: Any = None):
        super().__init__(message)
        self.code, self.message = code, message
        self.function, self.node, self.details = function, node, details

    def as_dict(self) -> dict[str, Any]:
        return {'error': {'code': self.code, 'message': self.message,
                          'function': self.function, 'node': self.node, 'details': self.details}}


def _spec(lo: int, hi: int | None = None, fields: tuple[str, ...] = ()) -> dict[str, Any]:
    return {'min_args': lo, 'max_args': lo if hi is None else hi, 'fields': list(fields)}


OPS: dict[str, dict[str, Any]] = {
    'param': _spec(0, fields=('index',)), 'const': _spec(0, fields=('value',)),
    'record': _spec(0, 100_000, ('keys',)), 'list': _spec(0, 100_000),
    'field': _spec(2), 'index': _spec(2), 'set_field': _spec(3), 'length': _spec(1),
    'concat': _spec(2), 'keys': _spec(1), 'contains': _spec(2), 'eq': _spec(2),
    **{op: _spec(2) for op in ('lt', 'le', 'gt', 'ge', 'add', 'sub', 'mul', 'div', 'mod', 'and', 'or')},
    'neg': _spec(1), 'not': _spec(1),
    **{f'text.{op}': _spec(1) for op in ('trim', 'lower', 'upper')},
    'text.split': _spec(2), 'text.join': _spec(2), 'text.replace': _spec(3),
    'json.parse': _spec(1), 'json.stringify': _spec(1), 'parse_int': _spec(1),
    'to_text': _spec(1), 'asset.text': _spec(1), 'call': _spec(0, 64, ('target',)),
    'choose': _spec(1, 65, ('then', 'else')), 'map': _spec(1, fields=('target',)),
    'filter': _spec(1, fields=('target',)), 'fold': _spec(2, fields=('target',)),
    'attempt': _spec(0, 64, ('target',)), 'fail': _spec(2),
    'host': _spec(0, 64, ('binding',)), 'transaction': _spec(0, 64, ('target',)),
}
HOSTS: dict[str, dict[str, Any]] = {
    'file.read_text': {'params': ['str'], 'result': 'str', 'effects': [['file', 'read']]},
    'file.write_text': {'params': ['str', 'str'], 'result': 'null', 'effects': [['file', 'write']]},
    'file.read_csv': {'params': ['str'], 'result': 'list', 'effects': [['file', 'read']]},
    'sqlite.query': {'params': ['str', 'list'], 'result': 'list', 'effects': [['db', 'read']]},
    'sqlite.execute': {'params': ['str', 'list'], 'result': 'record', 'effects': [['db', 'write']]},
}


def json_copy(value: Any, *, max_bytes: int | None = MAX_VALUE) -> Any:
    """Validate and independently copy bounded JSON without accepting Python coercions."""
    active: set[int] = set()
    size = 0

    def walk(v: Any, depth: int) -> Any:
        nonlocal size
        if depth > MAX_DEPTH:
            raise AppError('resource_limit', 'JSON nesting exceeds limit')
        kind = type(v)
        if v is None or kind is bool:
            size += 5
            result = v
        elif kind is int:
            if v.bit_length() > MAX_INT_BITS:
                raise AppError('resource_limit', 'Integer magnitude exceeds limit')
            size += max(1, (v.bit_length() * 30103 // 100000) + 2)
            result = v
        elif kind is float:
            if not math.isfinite(v):
                raise AppError('invalid_value', 'Numbers must be finite')
            size += 25
            result = v
        elif kind is str:
            # UTF-8 disallows lone surrogates, as do the file/HTTP boundaries.
            try:
                size += len(v.encode('utf-8')) + 2
            except UnicodeError as exc:
                raise AppError('invalid_value', 'String is not valid Unicode') from exc
            result = v
        elif kind in (list, dict):
            if len(v) > MAX_COLLECTION:
                raise AppError('resource_limit', 'Collection exceeds limit')
            identity = id(v)
            if identity in active:
                raise AppError('invalid_value', 'Cyclic values are not JSON')
            active.add(identity)
            size += 2 + len(v)
            if kind is list:
                result = [walk(item, depth + 1) for item in v]
            else:
                result = {}
                for key, item in v.items():
                    if type(key) is not str:
                        raise AppError('invalid_value', 'Record keys must be strings')
                    walk(key, depth + 1)
                    result[key] = walk(item, depth + 1)
            active.remove(identity)
        else:
            raise AppError('invalid_value', f'Unsupported JSON value: {kind.__name__}')
        if max_bytes is not None and size > max_bytes:
            raise AppError('resource_limit', 'Value exceeds byte limit')
        return result

    return walk(value, 0)


def decode_json(text: str | bytes, *, max_bytes: int = MAX_VALUE) -> Any:
    if not isinstance(text, (str, bytes)):
        raise AppError('invalid_json', 'JSON input must be text')
    try:
        if isinstance(text, bytes):
            if len(text) > max_bytes:
                raise AppError('resource_limit', 'JSON input exceeds byte limit')
            text = text.decode('utf-8')
        elif len(text.encode('utf-8')) > max_bytes:
            raise AppError('resource_limit', 'JSON input exceeds byte limit')

        def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in items:
                if key in result:
                    raise AppError('invalid_json', f'Duplicate JSON key: {key}')
                result[key] = value
            return result

        def integer(raw: str) -> int:
            # Python's configurable decimal conversion limit must not define our language.
            negative = raw.startswith('-')
            digits = raw[1:] if negative else raw
            if len(digits) > 19730:
                raise AppError('resource_limit', 'Integer magnitude exceeds limit')
            number = 0
            for offset in range(0, len(digits), 9):
                chunk = digits[offset:offset + 9]
                number = number * (10 ** len(chunk)) + int(chunk)
            return -number if negative else number

        value = json.loads(text, object_pairs_hook=pairs, parse_int=integer,
                           parse_constant=lambda raw: (_ for _ in ()).throw(
                               AppError('invalid_json', f'Invalid JSON number: {raw}')))
        return json_copy(value, max_bytes=max_bytes)
    except AppError:
        raise
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise AppError('invalid_json', 'Malformed JSON input') from exc


def _int_text(value: int) -> str:
    if value == 0:
        return '0'
    sign = '-' if value < 0 else ''
    value = abs(value)
    parts = []
    while value:
        value, part = divmod(value, 1_000_000_000)
        parts.append(part)
    return sign + str(parts[-1]) + ''.join(f'{part:09d}' for part in reversed(parts[:-1]))


def encode_json(value: Any, *, ensure_ascii: bool = True, max_bytes: int | None = MAX_VALUE) -> str:
    checked = json_copy(value, max_bytes=max_bytes)

    def encode(v: Any) -> str:
        if type(v) is int:
            return _int_text(v)
        if type(v) is list:
            return '[' + ','.join(encode(item) for item in v) + ']'
        if type(v) is dict:
            return '{' + ','.join(json.dumps(key, ensure_ascii=ensure_ascii) + ':' + encode(v[key])
                                  for key in sorted(v)) + '}'
        return json.dumps(v, ensure_ascii=ensure_ascii, allow_nan=False, separators=(',', ':'))

    text = encode(checked)
    if max_bytes is not None and len(text.encode('utf-8')) > max_bytes:
        raise AppError('resource_limit', 'Encoded JSON exceeds byte limit')
    return text


def check_type(value: Any, atom: str) -> None:
    kind = type(value)
    valid = atom == 'any' or (atom == 'null' and value is None) or (
        atom == 'number' and kind in (int, float)) or {
            'bool': bool, 'int': int, 'float': float, 'str': str, 'list': list, 'record': dict
        }.get(atom) is kind
    if atom not in TYPES or not valid:
        raise AppError('type_error', f'Expected {atom}, got {kind.__name__}')


def strict_equal(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if type(left) is list:
        return len(left) == len(right) and all(strict_equal(a, b) for a, b in zip(left, right))
    if type(left) is dict:
        return left.keys() == right.keys() and all(strict_equal(v, right[k]) for k, v in left.items())
    return bool(left == right)
