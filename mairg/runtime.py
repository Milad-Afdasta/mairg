"""Request-local generated-function execution and generic value operations."""
from __future__ import annotations

import math
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .language import (
    HOSTS,
    MAX_COLLECTION,
    MAX_INT_BITS,
    MAX_VALUE,
    OPS,
    AppError,
    check_type,
    decode_json,
    encode_json,
    json_copy,
    strict_equal,
)

GeneratedFunction = Callable[[Any, list[Any]], Any]


class CompiledProject:
    def __init__(self, metadata: dict[str, Any], functions: dict[int, GeneratedFunction],
                 *, root: str | Path | None = None):
        self._metadata = json_copy(metadata, max_bytes=None)
        self._functions = dict(functions)
        self.root = Path(root or '.').resolve()
        self.source_revision: str = metadata['source_revision']
        self.generated_source = ''

    @property
    def manifest(self) -> dict[str, Any]:
        return json_copy(self._metadata['manifest'])  # type: ignore[no-any-return]

    @property
    def document(self) -> dict[str, Any]:
        return json_copy(self._metadata['document'])  # type: ignore[no-any-return]

    @property
    def assets(self) -> dict[str, str]:
        return dict(self._metadata['assets'])

    def describe(self) -> dict[str, Any]:
        return json_copy({'format': 'mairg-description-1', 'name': self._metadata['manifest']['name'],
                'source_revision': self.source_revision,
                'exports': {name: {'params': self._metadata['functions'][str(fid)]['params'],
                                   'result': self._metadata['functions'][str(fid)]['result'],
                                   'effects': self._metadata['functions'][str(fid)]['effects']}
                            for name, fid in self._metadata['document']['exports'].items()},
                'operations': OPS, 'hosts': HOSTS})

    def call(self, export: str, arguments: Any, *, state_dir: str | Path | None = None) -> Any:
        exports = self._metadata['document']['exports']
        if type(export) is not str or export not in exports:
            raise AppError('unknown_export', 'Unknown exported function')
        args = json_copy(arguments)
        if type(args) is not list:
            raise AppError('type_error', 'Invocation arguments must be a JSON array')
        fid = exports[export]
        _validate_arguments(self._metadata['functions'][str(fid)], args, fid)
        # Construction is after all invocation boundary checks; host owns resource creation.
        from .hosts import HostRuntime
        host = HostRuntime(self, state_dir=state_dir)
        try:
            return json_copy(_Context(self, host).invoke(fid, args))
        finally:
            host.close()


def project_from_generated(metadata: dict[str, Any], functions: dict[int, GeneratedFunction],
                           *, root: str | Path | None = None) -> CompiledProject:
    """Load already-generated executable metadata; bundle digest checking owns its provenance."""
    if set(functions) != {int(fid) for fid in metadata['functions']}:
        raise AppError('bundle_error', 'Generated function metadata does not match executable')
    return CompiledProject(metadata, functions, root=root)


def _validate_arguments(info: dict[str, Any], args: list[Any], fid: int) -> None:
    if len(args) != len(info['params']):
        raise AppError('type_error', 'Function argument count mismatch', function=fid)
    for value, atom in zip(args, info['params']):
        try:
            check_type(value, atom)
        except AppError as exc:
            exc.function = fid
            raise


def _need(value: Any, *kinds: type) -> None:
    if type(value) not in kinds:
        raise AppError('type_error', 'Invalid operation operand type')


def _size(length: int) -> None:
    if length > MAX_COLLECTION:
        raise AppError('resource_limit', 'Collection exceeds limit')


def _text_size(length: int) -> None:
    if length > MAX_VALUE:
        raise AppError('resource_limit', 'Text exceeds byte limit')


class _Context:
    def __init__(self, project: CompiledProject, host: Any):
        self.project, self.host = project, host
        self.steps = 0
        self.depth = 0
        self.in_transaction = False

    def invoke(self, fid: int, args: list[Any]) -> Any:
        if self.depth >= 64:
            raise AppError('resource_limit', 'Call depth exceeds limit', function=fid)
        info = self.project._metadata['functions'][str(fid)]
        _validate_arguments(info, args, fid)
        self.depth += 1
        try:
            result = self.project._functions[fid](self, args)
            result = json_copy(result)
            check_type(result, info['result'])
            return result
        except RecursionError as exc:
            raise AppError('resource_limit', 'Execution depth exceeds host limit', function=fid) from exc
        except AppError as exc:
            if exc.function is None:
                exc.function = fid
            raise
        finally:
            self.depth -= 1

    def evaluate(self, fid: int, nid: int, op: str, args: list[Any], extra: dict[str, Any],
                 params: list[Any]) -> Any:
        self.steps += 1
        if self.steps > 1_000_000:
            raise AppError('resource_limit', 'Node evaluation budget exhausted', function=fid, node=nid)
        try:
            return json_copy(self._operation(op, args, extra, params))
        except AppError as exc:
            if exc.function is None:
                exc.function = fid
            if exc.node is None:
                exc.node = nid
            raise
        except (ArithmeticError, ValueError, TypeError, IndexError, KeyError, UnicodeError) as exc:
            raise AppError('operation_error', str(exc), function=fid, node=nid) from exc

    def _operation(self, op: str, a: list[Any], e: dict[str, Any], params: list[Any]) -> Any:
        if op == 'param':
            return params[e['index']]
        if op == 'const':
            return e['value']
        if op == 'list':
            _size(len(a))
            return a
        if op == 'record':
            _size(len(a))
            return dict(zip(e['keys'], a))
        if op == 'field':
            _need(a[0], dict)
            _need(a[1], str)
            if a[1] not in a[0]:
                raise AppError('missing_field', f'Missing record field: {a[1]}')
            return a[0][a[1]]
        if op == 'index':
            _need(a[0], list, str)
            _need(a[1], int)
            if not 0 <= a[1] < len(a[0]):
                raise AppError('index_error', 'Index is outside the collection')
            return a[0][a[1]]
        if op == 'set_field':
            _need(a[0], dict)
            _need(a[1], str)
            _size(len(a[0]) + (a[1] not in a[0]))
            return {**a[0], a[1]: a[2]}
        if op == 'length':
            _need(a[0], str, list, dict)
            return len(a[0])
        if op == 'concat':
            _need(a[0], str, list)
            _need(a[1], type(a[0]))
            if type(a[0]) is list:
                _size(len(a[0]) + len(a[1]))
            else:
                _text_size(len(a[0].encode('utf-8')) + len(a[1].encode('utf-8')))
            return a[0] + a[1]
        if op == 'keys':
            _need(a[0], dict)
            return sorted(a[0])
        if op == 'contains':
            _need(a[0], str, list, dict)
            if type(a[0]) is list:
                return any(strict_equal(item, a[1]) for item in a[0])
            _need(a[1], str)
            return a[1] in a[0]
        if op == 'eq':
            return strict_equal(a[0], a[1])
        if op in ('lt', 'le', 'gt', 'ge'):
            _need(a[0], int, float, str)
            _need(a[1], type(a[0]))
            if op == 'lt':
                return a[0] < a[1]
            if op == 'le':
                return a[0] <= a[1]
            if op == 'gt':
                return a[0] > a[1]
            return a[0] >= a[1]
        if op in ('add', 'sub', 'mul'):
            _need(a[0], int, float)
            _need(a[1], type(a[0]))
            if op == 'mul' and type(a[0]) is int and a[0] and a[1]:
                if a[0].bit_length() + a[1].bit_length() - 1 > MAX_INT_BITS:
                    raise AppError('resource_limit', 'Integer product exceeds magnitude limit')
            result = a[0] + a[1] if op == 'add' else a[0] - a[1] if op == 'sub' else a[0] * a[1]
            if type(result) is float and not math.isfinite(result):
                raise AppError('arithmetic_error', 'Floating operation is nonfinite')
            return result
        if op == 'div':
            _need(a[0], int, float)
            _need(a[1], int, float)
            if a[1] == 0:
                raise AppError('arithmetic_error', 'Division by zero')
            try:
                result = a[0] / a[1]
            except OverflowError as exc:
                raise AppError('arithmetic_error', 'Division is outside finite float range') from exc
            if not math.isfinite(result):
                raise AppError('arithmetic_error', 'Division is nonfinite')
            return result
        if op == 'mod':
            _need(a[0], int)
            _need(a[1], int)
            if a[1] == 0:
                raise AppError('arithmetic_error', 'Modulo by zero')
            return a[0] % a[1]
        if op == 'neg':
            _need(a[0], int, float)
            return -a[0]
        if op in ('and', 'or'):
            _need(a[0], bool)
            _need(a[1], bool)
            return a[0] and a[1] if op == 'and' else a[0] or a[1]
        if op == 'not':
            _need(a[0], bool)
            return not a[0]
        if op in ('text.trim', 'text.lower', 'text.upper'):
            _need(a[0], str)
            if op != 'text.trim':
                # Unicode case mapping has a maximum expansion of three codepoints.
                _text_size(sum(len((char.lower() if op == 'text.lower' else char.upper()).encode('utf-8')) for char in a[0]))
            return a[0].strip() if op == 'text.trim' else a[0].lower() if op == 'text.lower' else a[0].upper()
        if op == 'text.split':
            _need(a[0], str)
            _need(a[1], str)
            if not a[1]:
                raise AppError('value_error', 'Split separator must be nonempty')
            _size(a[0].count(a[1]) + 1)
            return a[0].split(a[1])
        if op == 'text.join':
            _need(a[0], str)
            _need(a[1], list)
            for text in a[1]:
                _need(text, str)
            _text_size(sum(len(text.encode('utf-8')) for text in a[1]) +
                       max(0, len(a[1]) - 1) * len(a[0].encode('utf-8')))
            return a[0].join(a[1])
        if op == 'text.replace':
            for text in a:
                _need(text, str)
            if not a[1]:
                raise AppError('value_error', 'Replace old text must be nonempty')
            _text_size(len(a[0].encode('utf-8')) + a[0].count(a[1]) *
                       (len(a[2].encode('utf-8')) - len(a[1].encode('utf-8'))))
            return a[0].replace(a[1], a[2])
        if op == 'json.parse':
            _need(a[0], str)
            return decode_json(a[0])
        if op in ('json.stringify', 'to_text'):
            return a[0] if op == 'to_text' and type(a[0]) is str else encode_json(a[0])
        if op == 'parse_int':
            _need(a[0], str)
            if re.fullmatch(r'-?[0-9]+', a[0]) is None:
                raise AppError('value_error', 'Expected decimal integer text')
            # Decode accepts arbitrary decimal zero prefixes only after explicit trimming.
            sign = '-' if a[0].startswith('-') else ''
            digits = a[0].lstrip('-').lstrip('0') or '0'
            return decode_json(sign + digits)
        if op == 'asset.text':
            _need(a[0], str)
            if a[0] not in self.project._metadata['assets']:
                raise AppError('asset_error', 'Asset is not declared')
            return self.project._metadata['assets'][a[0]]
        if op == 'call':
            return self.invoke(e['target'], a)
        if op == 'choose':
            _need(a[0], bool)
            return self.invoke(e['then'] if a[0] else e['else'], a[1:])
        if op in ('map', 'filter'):
            _need(a[0], list)
            output: list[Any] = []
            output_bytes = 2
            for item in a[0]:
                value = self.invoke(e['target'], [item])
                if op == 'map':
                    output_bytes += len(encode_json(value).encode('utf-8')) + (1 if output else 0)
                    _text_size(output_bytes)
                    output.append(value)
                else:
                    _need(value, bool)
                    if value:
                        output.append(item)
            return output
        if op == 'fold':
            _need(a[0], list)
            accumulator = a[1]
            for item in a[0]:
                accumulator = self.invoke(e['target'], [accumulator, item])
            return accumulator
        if op == 'attempt':
            try:
                return {'ok': True, 'value': self.invoke(e['target'], a)}
            except AppError as exc:
                if exc.code in ('resource_limit', 'cancelled', 'cancellation'):
                    raise
                return {'ok': False, 'error': exc.as_dict()['error']}
        if op == 'fail':
            _need(a[0], str)
            _need(a[1], str)
            raise AppError(a[0], a[1])
        if op == 'host':
            return self.host.call(e['binding'], a)
        if op == 'transaction':
            if self.in_transaction:
                raise AppError('transaction_error', 'Nested transactions are forbidden')
            self.in_transaction = True
            try:
                with self.host.transaction():
                    return self.invoke(e['target'], a)
            finally:
                self.in_transaction = False
        raise AppError('invalid_program', 'Unknown generated operation')


IMPLEMENTED_OPS = frozenset({
    'param', 'const', 'record', 'list', 'field', 'index', 'set_field', 'length', 'concat', 'keys',
    'contains', 'eq', 'lt', 'le', 'gt', 'ge', 'add', 'sub', 'mul', 'div', 'mod', 'neg', 'and', 'or',
    'not', 'text.trim', 'text.lower', 'text.upper', 'text.split', 'text.join', 'text.replace',
    'json.parse', 'json.stringify', 'parse_int', 'to_text', 'asset.text', 'call', 'choose', 'map',
    'filter', 'fold', 'attempt', 'fail', 'host', 'transaction',
})
if IMPLEMENTED_OPS != OPS.keys():
    raise RuntimeError('Operation catalog and runtime dispatch differ')
