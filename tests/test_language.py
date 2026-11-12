from __future__ import annotations

import unittest
from typing import Any

from mairg.compiler import compile_documents
from mairg.language import (
    HOSTS,
    OPS,
    AppError,
    check_type,
    decode_json,
    encode_json,
    json_copy,
    strict_equal,
)


def manifest(**updates: Any) -> dict[str, Any]:
    return {'format': 'mairg-project-1', 'name': 'test', 'program': 'program.json',
            'bindings': {}, 'database': None, 'assets': [], 'routes': [], 'startup': None,
            'tests': [], **updates}


def fn(nodes: list[dict[str, Any]], *, fid: int = 0, params: list[str] | None = None,
       result: str = 'any', ret: int | None = None) -> dict[str, Any]:
    return {'id': fid, 'params': params or [], 'result': result, 'nodes': nodes,
            'return': nodes[-1]['id'] if ret is None else ret}


def const(nid: int, value: Any) -> dict[str, Any]:
    return {'id': nid, 'op': 'const', 'args': [], 'value': value}


def compile_functions(functions: list[dict[str, Any]], **updates: Any) -> Any:
    return compile_documents(manifest(**updates), {'format': 'mairg-app-1',
        'functions': functions, 'exports': {'main': functions[0]['id']}}, root='/tmp', assets={})


class JsonTests(unittest.TestCase):
    def test_strict_json_and_large_integer_roundtrip(self) -> None:
        for text in ('{"a":1,"a":2}', 'NaN', '[Infinity]', '{', '1e999'):
            with self.subTest(text=text), self.assertRaises(AppError):
                decode_json(text)
        value = {'large': 2**20000, 's': 'a\nλ', 'zero': -0.0}
        self.assertEqual(decode_json(encode_json(value)), value)
        for invalid in (float('nan'), {1: 2}, (1, 2), b'x'):
            with self.assertRaises(AppError):
                json_copy(invalid)
        cycle: list[Any] = []
        cycle.append(cycle)
        with self.assertRaises(AppError):
            json_copy(cycle)

    def test_types_do_not_coerce_boolean_or_float(self) -> None:
        for value, atom in ((True, 'int'), (1, 'bool'), (1.0, 'int'), (True, 'number')):
            with self.assertRaises(AppError):
                check_type(value, atom)
        self.assertFalse(strict_equal([True], [1]))
        self.assertFalse(strict_equal({'x': 1}, {'x': 1.0}))
        self.assertTrue(strict_equal({'x': [1]}, {'x': [1]}))
        self.assertIn('fold', OPS)
        self.assertIn('sqlite.query', HOSTS)


class CompilerTests(unittest.TestCase):
    def test_empty_program_checks_and_unknown_export_has_no_effects(self) -> None:
        project = compile_documents(manifest(), {'format': 'mairg-app-1',
            'functions': [], 'exports': {}}, root='/tmp', assets={})
        self.assertEqual(project.describe()['exports'], {})
        with self.assertRaises(AppError) as caught:
            project.call('main', [])
        self.assertEqual(caught.exception.code, 'unknown_export')

    def test_forward_refs_and_opaque_ids(self) -> None:
        project = compile_functions([fn([
            {'id': 99, 'op': 'add', 'args': [5, 2]}, const(2, 8), const(5, 3)], ret=99)])
        self.assertEqual(project.call('main', []), 11)
        self.assertIn('def _f_0', project.generated_source)
        self.assertEqual(project.describe()['exports']['main']['result'], 'any')

    def test_all_dead_nodes_validate_and_cycles_reject(self) -> None:
        for dead in ({'id': 2, 'op': 'unknown', 'args': []},
                     {'id': 2, 'op': 'not', 'args': [2]},
                     {'id': 2, 'op': 'not', 'args': [50]}):
            with self.subTest(dead=dead), self.assertRaises(AppError):
                compile_functions([fn([const(0, 1), dead], ret=0)])
        with self.assertRaises(AppError):
            compile_documents(manifest(), {'format': 'mairg-1', 'functions': [], 'exports': {}}, root='/tmp', assets={})

    def test_effect_aliases_do_not_create_separate_resources(self) -> None:
        write = {'id': 2, 'op': 'host', 'args': [0, 1], 'binding': 'a'}
        other = {'id': 3, 'op': 'host', 'args': [0, 1], 'binding': 'b'}
        nodes = [const(0, 'f'), const(1, 'text'), write, other, {'id': 4, 'op': 'list', 'args': [2, 3]}]
        bindings = {'a': 'file.write_text', 'b': 'file.write_text'}
        with self.assertRaises(AppError):
            compile_functions([fn(nodes)], bindings=bindings)
        other['after'] = [2]
        compile_functions([fn(nodes)], bindings=bindings)
        with self.assertRaises(AppError):
            compile_functions([fn(nodes, ret=0)], bindings=bindings)

    def test_transitive_callback_and_transaction_effects_reject(self) -> None:
        effect = fn([const(0, 'p'), {'id': 1, 'op': 'host', 'args': [0], 'binding': 'read'}],
                    fid=1, params=['any'])
        mapped = fn([const(0, []), {'id': 1, 'op': 'map', 'args': [0], 'target': 1}])
        with self.assertRaises(AppError):
            compile_functions([mapped, effect], bindings={'read': 'file.read_text'})
        transaction = fn([const(0, 1), {'id': 1, 'op': 'transaction', 'args': [0], 'target': 1}])
        with self.assertRaises(AppError):
            compile_functions([transaction, effect], bindings={'read': 'file.read_text'})

    def test_dependency_depth_is_declaration_order_independent(self) -> None:
        nodes = [const(0, True)] + [{'id': i, 'op': 'not', 'args': [i-1]} for i in range(1, 257)]
        with self.assertRaises(AppError):
            compile_functions([fn(nodes)])


if __name__ == '__main__':
    unittest.main()
