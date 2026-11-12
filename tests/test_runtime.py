from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from mairg.language import AppError
from tests.test_language import compile_functions, const, fn


class RuntimeTests(unittest.TestCase):
    def test_generic_value_catalog_semantics(self) -> None:
        cases: list[tuple[str, list[Any], Any]] = [
            ('list', [1, 'x'], [1, 'x']),
            ('field', [{'a': 7}, 'a'], 7),
            ('index', ['λx', 0], 'λ'),
            ('set_field', [{'a': 1}, 'b', 2], {'a': 1, 'b': 2}),
            ('length', [{'a': 1}], 1),
            ('concat', [[1], [2]], [1, 2]),
            ('keys', [{'λ': 1, 'a': 2}], ['a', 'λ']),
            ('contains', [[True], 1], False),
            ('eq', [{'a': [1]}, {'a': [1.0]}], False),
            ('lt', ['a', 'b'], True), ('le', [2, 2], True),
            ('gt', [3.0, 2.0], True), ('ge', [3, 4], False),
            ('add', [4, 5], 9), ('sub', [4, 5], -1), ('mul', [4, 5], 20),
            ('div', [5, 2], 2.5), ('mod', [5, -2], -1), ('neg', [4], -4),
            ('and', [True, False], False), ('or', [False, True], True), ('not', [False], True),
            ('text.trim', [' λ '], 'λ'), ('text.lower', ['ΣA'], 'σa'),
            ('text.upper', ['straße'], 'STRASSE'), ('text.split', ['a,b', ','], ['a', 'b']),
            ('text.join', [',', ['a', 'b']], 'a,b'),
            ('text.replace', ['aλa', 'a', 'b'], 'bλb'),
            ('json.parse', ['{"a":1}'], {'a': 1}),
            ('json.stringify', [{'b': 2, 'a': 1}], '{"a":1,"b":2}'),
            ('parse_int', ['-00012'], -12), ('to_text', [None], 'null'),
        ]
        for op, values, expected in cases:
            nodes = [const(i, value) for i, value in enumerate(values)]
            nodes.append({'id': len(values), 'op': op, 'args': list(range(len(values)))})
            with self.subTest(op=op):
                self.assertEqual(compile_functions([fn(nodes)]).call('main', []), expected)

    def test_lazy_choose_and_attempt(self) -> None:
        main = fn([const(0, False), {'id': 1, 'op': 'choose', 'args': [0], 'then': 1, 'else': 2}])
        bad = fn([const(0, 'bad'), const(1, 'failure'), {'id': 2, 'op': 'fail', 'args': [0, 1]}], fid=1)
        good = fn([const(0, 'safe')], fid=2)
        self.assertEqual(compile_functions([main, bad, good]).call('main', []), 'safe')
        main = fn([{'id': 0, 'op': 'attempt', 'args': [], 'target': 1}])
        result = compile_functions([main, bad]).call('main', [])
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'], 'bad')
        self.assertEqual(result['error']['function'], 1)

    def test_map_filter_and_ordered_fold(self) -> None:
        double = fn([{'id': 0, 'op': 'param', 'args': [], 'index': 0},
                     {'id': 1, 'op': 'add', 'args': [0, 0]}], fid=1, params=['int'], result='int')
        main = fn([const(0, [1, 2, 3]), {'id': 1, 'op': 'map', 'args': [0], 'target': 1}])
        self.assertEqual(compile_functions([main, double]).call('main', []), [2, 4, 6])
        add = fn([{'id': 0, 'op': 'param', 'args': [], 'index': 0},
                  {'id': 1, 'op': 'param', 'args': [], 'index': 1},
                  {'id': 2, 'op': 'add', 'args': [0, 1]}], fid=2, params=['int', 'int'])
        main = fn([const(0, [1, 2, 3]), const(1, 0), {'id': 2, 'op': 'fold', 'args': [0, 1], 'target': 2}])
        self.assertEqual(compile_functions([main, add]).call('main', []), 6)
        predicate = fn([const(0, 1)], fid=1, params=['any'])
        main = fn([const(0, [1]), {'id': 1, 'op': 'filter', 'args': [0], 'target': 1}])
        with self.assertRaises(AppError):
            compile_functions([main, predicate]).call('main', [])

    def test_shared_effect_runs_once_and_frame_failure_never_replays(self) -> None:
        main = fn([const(0, 'x'), {'id': 1, 'op': 'host', 'args': [0], 'binding': 'read'},
                   {'id': 2, 'op': 'list', 'args': [1, 1]}])
        project = compile_functions([main], bindings={'read': 'file.read_text'})
        with patch('mairg.hosts.HostRuntime.call', return_value='yes') as call:
            self.assertEqual(project.call('main', []), ['yes', 'yes'])
            call.assert_called_once()
        with patch('mairg.hosts.HostRuntime.call', side_effect=AppError('io_error', 'bad')) as call:
            with self.assertRaises(AppError):
                project.call('main', [])
            call.assert_called_once()

    def test_boundary_copies_and_pre_effect_argument_validation(self) -> None:
        project = compile_functions([fn([const(0, {'items': []})])])
        result = project.call('main', [])
        result['items'].append(1)
        self.assertEqual(project.call('main', []), {'items': []})
        snapshot = project.document
        snapshot['functions'].clear()
        self.assertEqual(project.call('main', []), {'items': []})
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'not-created'
            with self.assertRaises(AppError):
                project.call('main', [1], state_dir=state)
            self.assertFalse(state.exists())

    def test_recursion_resource_limit_cannot_be_caught(self) -> None:
        main = fn([{'id': 0, 'op': 'attempt', 'args': [], 'target': 1}])
        recurse = fn([{'id': 0, 'op': 'call', 'args': [], 'target': 1}], fid=1)
        with self.assertRaises(AppError) as caught:
            compile_functions([main, recurse]).call('main', [])
        self.assertEqual(caught.exception.code, 'resource_limit')

    def test_generated_module_runs_without_compiler(self) -> None:
        project = compile_functions([fn([const(0, 2**20000)])])
        namespace: dict[str, Any] = {}
        exec(project.generated_source, namespace)
        self.assertEqual(namespace['load_project']().call('main', []), 2**20000)


if __name__ == '__main__':
    unittest.main()
