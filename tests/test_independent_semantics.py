"""Independent contract tests: public source graphs and actual resource outcomes.

These helpers deliberately do not import builder-test fixtures or implement an
alternate language runtime. Random cases use an integer expression oracle.
"""
from __future__ import annotations

import ast
import copy
import random
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from typing import Any

from mairg.compiler import compile_documents
from mairg.language import OPS, AppError, decode_json


def literal(identity: int, value: Any) -> dict[str, Any]:
    return {'id': identity, 'op': 'const', 'args': [], 'value': value}


def node(identity: int, operation: str, *arguments: int, **extra: Any) -> dict[str, Any]:
    return {'id': identity, 'op': operation, 'args': list(arguments), **extra}


def function(identity: int, nodes: list[dict[str, Any]], *, params: list[str] | None = None,
             result: str = 'any', returns: int | None = None) -> dict[str, Any]:
    return {'id': identity, 'params': [] if params is None else params, 'result': result,
            'nodes': nodes, 'return': nodes[-1]['id'] if returns is None else returns}


def compile_graph(functions: list[dict[str, Any]], *, bindings: dict[str, str] | None = None,
                  assets: dict[str, str] | None = None, database: str | None = None,
                  exports: dict[str, int] | None = None) -> Any:
    snapshots = {} if assets is None else assets
    manifest = {'format': 'mairg-project-1', 'name': 'independent-semantics',
                'program': 'application.json', 'bindings': bindings or {},
                'database': database, 'assets': list(snapshots), 'routes': [],
                'startup': None, 'tests': []}
    document = {'format': 'mairg-app-1', 'functions': functions,
                'exports': exports or {'main': functions[0]['id']}}
    return compile_documents(manifest, document, root='/tmp', assets=snapshots)


def primitive(operation: str, operands: list[Any], **extra: Any) -> Any:
    return compile_graph([function(0, [literal(i, v) for i, v in enumerate(operands)] +
                           [node(len(operands), operation, *range(len(operands)), **extra)])])


def rename_program(functions: list[dict[str, Any]], seed: int) -> list[dict[str, Any]]:
    """Bijections and shuffles preserve graph edges, never business values."""
    rng = random.Random(seed)
    changed = copy.deepcopy(functions)
    fids = dict(zip([f['id'] for f in changed], rng.sample(range(100, 100_000), len(changed))))
    for f in changed:
        nids = dict(zip([n['id'] for n in f['nodes']], rng.sample(range(100, 100_000), len(f['nodes']))))
        f['id'] = fids[f['id']]
        f['return'] = nids[f['return']]
        for n in f['nodes']:
            n['id'] = nids[n['id']]
            n['args'] = [nids[a] for a in n['args']]
            if 'after' in n:
                n['after'] = [nids[a] for a in n['after']]
            for field in ('target', 'then', 'else'):
                if field in n:
                    n[field] = fids[n[field]]
        rng.shuffle(f['nodes'])
    rng.shuffle(changed)
    return changed


class IndependentSemanticsTests(unittest.TestCase):
    def assert_error(self, code: str, action: Any) -> AppError:
        with self.assertRaises(AppError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_all_primitive_dispatch_and_exact_types(self) -> None:
        cases = [
            ('record', [1, ['λ']], {'a': 1, 'b': ['λ']}, {'keys': ['a', 'b']}),
            ('list', [None, True, 1, 1.0, 'λ'], [None, True, 1, 1.0, 'λ'], {}),
            ('field', [{'zero': None}, 'zero'], None, {}),
            ('index', ['🦉λ', 1], 'λ', {}),
            ('set_field', [{'a': []}, 'b', {'x': 1}], {'a': [], 'b': {'x': 1}}, {}),
            ('length', ['🦉λ'], 2, {}),
            ('concat', [[1, True], [1.0]], [1, True, 1.0], {}),
            ('keys', [{'λ': 0, 'A': 1, 'a': 2}], ['A', 'a', 'λ'], {}),
            ('contains', [[{'x': [True]}], {'x': [1]}], False, {}),
            ('eq', [{'a': [1, False]}, {'a': [1.0, False]}], False, {}),
            ('lt', ['Z', 'a'], True, {}), ('le', [-2, -2], True, {}),
            ('gt', [1.0, -0.0], True, {}), ('ge', [-4, 0], False, {}),
            ('add', [2**400, 2**400], 2**401, {}), ('sub', [-7, 2], -9, {}),
            ('mul', [-7, 2], -14, {}), ('div', [3, 2.0], 1.5, {}),
            ('mod', [-5, 3], 1, {}), ('neg', [-0.0], 0.0, {}),
            ('and', [True, False], False, {}), ('or', [False, True], True, {}),
            ('not', [True], False, {}), ('text.trim', ['\u2003λ\n'], 'λ', {}),
            ('text.lower', ['ΟΣ'], 'ος', {}), ('text.upper', ['straße'], 'STRASSE', {}),
            ('text.split', ['a||b|', '|'], ['a', '', 'b', ''], {}),
            ('text.join', ['🦉', ['a', '', 'b']], 'a🦉🦉b', {}),
            ('text.replace', ['aaaa', 'aa', 'λ'], 'λλ', {}),
            ('json.parse', ['{"z":[null,true,1,1.0]}'], {'z': [None, True, 1, 1.0]}, {}),
            ('json.stringify', [{'z': [], 'a': 'λ'}], '{"a":"\\u03bb","z":[]}', {}),
            ('parse_int', ['-000012'], -12, {}),
            ('to_text', [[True, None, 1.0]], '[true,null,1.0]', {}),
        ]
        covered = {op for op, *_ in cases}
        # Every other catalog entry has a dedicated control/resource test below.
        self.assertEqual(set(OPS) - covered, {'const', 'param', 'asset.text', 'call', 'choose',
                         'map', 'filter', 'fold', 'attempt', 'fail', 'host', 'transaction'})
        for op, operands, expected, extra in cases:
            with self.subTest(op=op):
                actual = primitive(op, operands, **extra).call('main', [])
                self.assertEqual(actual, expected)
                self.assertIs(type(actual), type(expected))

    def test_operand_types_never_coerce_and_errors_are_local(self) -> None:
        wrong = [('add', [True, 1]), ('sub', [1, 1.0]), ('mul', [2.0, 2]),
                 ('lt', [0, 0.0]), ('div', [True, 2]), ('mod', [1.0, 2]),
                 ('neg', [False]), ('not', [1]), ('and', [True, 0]), ('or', [False, 1]),
                 ('index', [[1], True]), ('contains', ['1', 1]),
                 ('concat', ['a', []]), ('text.join', [',', ['a', 1]]),
                 ('field', [[1], 'x']), ('set_field', [{}, 1, 0]),
                 ('length', [1]), ('keys', [[]]), ('parse_int', [3]),
                 ('json.parse', [None]), ('text.trim', [False])]
        for op, values in wrong:
            with self.subTest(op=op, values=values):
                error = self.assert_error('type_error', lambda: primitive(op, values).call('main', []))
                self.assertEqual(error.function, 0)
                self.assertEqual(error.node, len(values))
        for op, values, code in [('field', [{}, 'x'], 'missing_field'),
                                 ('index', [[1], -1], 'index_error'),
                                 ('div', [1, 0], 'arithmetic_error'),
                                 ('mod', [1, 0], 'arithmetic_error'),
                                 ('text.split', ['x', ''], 'value_error'),
                                 ('text.replace', ['x', '', 'z'], 'value_error')]:
            with self.subTest(op=op):
                self.assert_error(code, lambda: primitive(op, values).call('main', []))
        for raw in ['+1', ' 1', '1 ', '1_000', '1e3', '１２']:
            with self.subTest(raw=raw):
                self.assert_error('value_error', lambda: primitive('parse_int', [raw]).call('main', []))
        for left, right, expected in [(True, 1, False), (1, 1.0, False),
                                      ({'a': [True]}, {'a': [1]}, False),
                                      ({'x': 1, 'y': 2}, {'y': 2, 'x': 1}, True)]:
            self.assertIs(primitive('eq', [left, right]).call('main', []), expected)

    def test_raw_documents_dead_nodes_and_removed_format_fail_closed(self) -> None:
        good = function(0, [literal(0, 1)])
        dead_nodes = [node(1, 'not', 1), node(1, 'add', 99, 99),
                      node(1, 'const', value=1, invented=True),
                      node(1, 'call', target=999), node(1, 'param', index=0),
                      node(1, 'record', 0, 0, keys=['same', 'same']),
                      node(1, 'not', 0, after=[0, 0])]
        for dead in dead_nodes:
            with self.subTest(dead=dead):
                self.assert_error('invalid_program', lambda: compile_graph([
                    function(0, good['nodes'] + [dead], returns=0)]))
        self.assert_error('invalid_program', lambda: compile_graph([
            good, function(1, [node(0, 'no_such_op')])]))
        # Caller dictionaries never acquire trusted-IR status through a previous check.
        source = [copy.deepcopy(good)]
        self.assertEqual(compile_graph(source).call('main', []), 1)
        source[0]['nodes'][0]['op'] = 'python.eval'
        self.assert_error('invalid_program', lambda: compile_graph(source))
        manifest = {'format': 'mairg-project-1', 'name': 'reject-old', 'program': 'a.json',
                    'bindings': {}, 'database': None, 'assets': [], 'routes': [],
                    'startup': None, 'tests': []}
        self.assert_error('invalid_program', lambda: compile_documents(manifest,
            {'format': 'mairg-1', 'functions': [], 'exports': {}}, root='/tmp', assets={}))
        for text in ['{"a":1,"a":2}', 'NaN', 'Infinity', '[1e999]', '{', '"\\ud800"']:
            with self.subTest(text=text), self.assertRaises(AppError):
                decode_json(text)
        cycle: list[Any] = []
        cycle.append(cycle)
        identity = compile_graph([function(0, [node(0, 'param', index=0)], params=['any'])])
        for value in [cycle, {1: 'bad'}, b'bytes', (1, 2), float('inf')]:
            with self.subTest(value_type=type(value).__name__), self.assertRaises(AppError):
                identity.call('main', [value])

    def test_boundary_values_source_assets_and_description_are_detached(self) -> None:
        value = {'items': [{'s': 'original'}]}
        source = [function(0, [literal(0, value)])]
        project = compile_graph(source)
        value['items'][0]['s'] = 'source changed'
        actual = project.call('main', [])
        actual['items'].clear()
        project.document['functions'].clear()
        project.manifest['bindings']['bad'] = 'file.write_text'
        self.assertEqual(project.call('main', []), {'items': [{'s': 'original'}]})
        identity = compile_graph([function(0, [node(0, 'param', index=0)], params=['record'])])
        arg = {'nested': []}
        returned = identity.call('main', [arg])
        returned['nested'].append('changed')
        self.assertEqual(arg, {'nested': []})
        description = identity.describe()
        description['exports']['main']['params'][0] = 'bool'
        description['exports']['main']['effects'].append(['file', 'write'])
        self.assertEqual(identity.call('main', [{'nested': []}]), {'nested': []})
        self.assertEqual(identity.describe()['exports']['main']['effects'], [])
        assets = {'message.txt': 'immutable λ'}
        text = compile_graph([function(0, [literal(0, 'message.txt'), node(1, 'asset.text', 0)])], assets=assets)
        assets['message.txt'] = 'changed'
        text.assets['message.txt'] = 'changed again'
        self.assertEqual(text.call('main', []), 'immutable λ')
        payload = "__import__('pathlib').Path('/tmp/mairg-injection-marker').touch()"
        self.assertEqual(primitive('to_text', [payload]).call('main', []), payload)

    def test_direct_ast_has_one_live_assignment_per_node(self) -> None:
        nodes = [literal(11, 7), node(12, 'add', 11, 11), node(13, 'mul', 12, 12), literal(99, 'dead')]
        project = compile_graph([function(42, nodes, returns=13)])
        generated = ast.parse(project.generated_source)
        body = next(n for n in generated.body if isinstance(n, ast.FunctionDef) and n.name == '_f_42')
        assignments = [n for n in body.body if isinstance(n, ast.Assign)]
        self.assertEqual(len(assignments), 3)
        self.assertEqual([n.targets[0].id for n in assignments], ['n_11', 'n_12', 'n_13'])
        self.assertFalse(any(isinstance(n, (ast.For, ast.While, ast.Lambda, ast.Try)) for n in ast.walk(body)))
        self.assertEqual(project.call('main', []), 196)

    def test_calls_selected_signatures_and_attempt_success_enforce_actual_types(self) -> None:
        text_branch = function(1, [node(0, 'param', index=0)], params=['str'], result='str')
        integer_branch = function(2, [node(0, 'param', index=0)], params=['int'], result='int')
        main = function(0, [node(0, 'param', index=0), node(1, 'param', index=1),
            node(2, 'choose', 0, 1, then=1, **{'else': 2})], params=['bool', 'any'])
        project = compile_graph([main, text_branch, integer_branch])
        self.assertEqual(project.call('main', [True, 'yes']), 'yes')
        self.assertIs(type(project.call('main', [False, 7])), int)
        self.assert_error('type_error', lambda: project.call('main', [True, 7]))
        self.assert_error('type_error', lambda: project.call('main', [False, 'wrong']))
        self.assert_error('type_error', lambda: project.call('main', [1, 'yes']))
        attempted = function(0, [literal(0, 'confirmed'), node(1, 'attempt', 0, target=1)])
        self.assertEqual(compile_graph([attempted, text_branch]).call('main', []),
                         {'ok': True, 'value': 'confirmed'})
        for operation in ['call', 'attempt', 'transaction', 'map', 'filter', 'fold']:
            arguments = [0, 0] if operation == 'fold' else []
            # A two-argument target cannot be used by these non-fold forms.
            target = function(1, [literal(0, None)], params=['any'] * (1 if operation == 'fold' else 2))
            source = function(0, [literal(0, []), node(1, operation, *arguments, target=1)])
            with self.subTest(operation=operation):
                self.assert_error('invalid_program', lambda: compile_graph([source, target]))

    def test_eager_dependencies_and_rejected_root_arguments_precede_effects(self) -> None:
        for operation in ['and', 'or']:
            source = function(0, [literal(0, operation == 'or'), literal(1, 'eager'),
                literal(2, 'argument evaluated'), node(3, 'fail', 1, 2), node(4, operation, 0, 3)])
            self.assert_error('eager', lambda: compile_graph([source]).call('main', []))
        source = function(0, [node(0, 'param', index=0), literal(1, 'never.txt'),
            node(2, 'host', 1, 0, binding='write')], params=['str'])
        project = compile_graph([source], bindings={'write': 'file.write_text'})
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'state-not-created'
            for arguments in [[True], [], ['x', 'extra'], 'not-array']:
                with self.subTest(arguments=arguments):
                    self.assert_error('type_error', lambda: project.call('main', arguments, state_dir=state))
                    self.assertFalse(state.exists())

    def test_lazy_branch_shared_effect_once_and_distinct_calls_use_real_sqlite(self) -> None:
        increment = function(1, [literal(0, 'INSERT INTO events(value) VALUES (?) RETURNING value'),
            literal(1, ['once']), node(2, 'host', 0, 1, binding='write')], result='record')
        forbidden = function(2, [literal(0, 'unselected.txt'), literal(1, 'must not write'),
            node(2, 'host', 0, 1, binding='file')])
        main = function(0, [literal(0, True), node(1, 'choose', 0, then=1, **{'else': 2}),
            node(2, 'list', 1, 1)])
        bindings = {'write': 'sqlite.execute', 'file': 'file.write_text'}
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / 'db').mkdir()
            with closing(sqlite3.connect(state / 'db' / 'proof.sqlite3')) as db:
                db.execute('CREATE TABLE events(value TEXT)')
                db.commit()
            project = compile_graph([main, increment, forbidden], bindings=bindings, database='proof.sqlite3')
            values = project.call('main', [], state_dir=state)
            self.assertEqual(values, [{'rows': [{'value': 'once'}], 'changes': 1}] * 2)
            self.assertFalse((state / 'files' / 'unselected.txt').exists())
            with closing(sqlite3.connect(state / 'db' / 'proof.sqlite3')) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone(), (1,))
            twice = function(0, [node(5, 'call', target=1), node(6, 'call', target=1, after=[5]),
                                 node(7, 'list', 5, 6)])
            compile_graph([twice, increment], bindings=bindings, database='proof.sqlite3').call('main', [], state_dir=state)
            with closing(sqlite3.connect(state / 'db' / 'proof.sqlite3')) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone(), (3,))

    def test_attempt_invokes_once_and_does_not_catch_its_argument_failure(self) -> None:
        failing = function(1, [literal(0, 'INSERT INTO events(value) VALUES (?)'), literal(1, ['committed']),
            node(2, 'host', 0, 1, binding='write'), literal(3, 'business_failure'),
            literal(4, 'after confirmed standalone commit'), node(5, 'fail', 3, 4, after=[2])])
        main = function(0, [node(0, 'attempt', target=1), node(1, 'list', 0, 0)])
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / 'db').mkdir()
            with closing(sqlite3.connect(state / 'db' / 'proof.sqlite3')) as db:
                db.execute('CREATE TABLE events(value TEXT)')
                db.commit()
            project = compile_graph([main, failing], bindings={'write': 'sqlite.execute'}, database='proof.sqlite3')
            result = project.call('main', [], state_dir=state)
            self.assertEqual(result[0], result[1])
            self.assertFalse(result[0]['ok'])
            self.assertEqual(result[0]['error']['code'], 'business_failure')
            with closing(sqlite3.connect(state / 'db' / 'proof.sqlite3')) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone(), (1,))
        identity = function(1, [node(0, 'param', index=0)], params=['any'])
        outside = function(0, [literal(0, 'outside'), literal(1, 'argument fails first'),
                              node(2, 'fail', 0, 1), node(3, 'attempt', 2, target=1)])
        self.assert_error('outside', lambda: compile_graph([outside, identity]).call('main', []))

    def test_transaction_result_validation_rolls_back_real_database(self) -> None:
        bad_result = function(1, [literal(0, 'INSERT INTO events(value) VALUES (?)'), literal(1, ['rolled back']),
            node(2, 'host', 0, 1, binding='write'), literal(3, 'not int'),
            node(4, 'to_text', 3, after=[2])], result='int')
        transaction = function(2, [node(0, 'transaction', target=1)])
        main = function(0, [node(0, 'attempt', target=2)])
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / 'db').mkdir()
            with closing(sqlite3.connect(state / 'db' / 'proof.sqlite3')) as db:
                db.execute('CREATE TABLE events(value TEXT)')
                db.commit()
            project = compile_graph([main, bad_result, transaction], bindings={'write': 'sqlite.execute'}, database='proof.sqlite3')
            self.assertEqual(project.call('main', [], state_dir=state)['error']['code'], 'type_error')
            with closing(sqlite3.connect(state / 'db' / 'proof.sqlite3')) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone(), (0,))

    def test_fixed_point_effects_alias_conflicts_and_unreachable_effects_reject(self) -> None:
        leaf = function(9, [literal(0, 'x'), literal(1, 'text'), node(2, 'host', 0, 1, binding='alias_b')], params=['any'])
        middle = function(8, [node(0, 'param', index=0), node(1, 'call', 0, target=9)], params=['any'])
        mapped = function(0, [literal(0, []), node(1, 'map', 0, target=8)])
        bindings = {'alias_a': 'file.write_text', 'alias_b': 'file.write_text'}
        self.assert_error('invalid_program', lambda: compile_graph([mapped, middle, leaf], bindings=bindings))
        filtered = copy.deepcopy(mapped)
        filtered['nodes'][1]['op'] = 'filter'
        self.assert_error('invalid_program', lambda: compile_graph([filtered, middle, leaf], bindings=bindings))
        unordered = function(0, [literal(0, 'x'), literal(1, 'first'), node(2, 'host', 0, 1, binding='alias_a'),
            node(3, 'call', 1, target=8), node(4, 'list', 2, 3)])
        self.assert_error('invalid_program', lambda: compile_graph([unordered, middle, leaf], bindings=bindings))
        unreachable = copy.deepcopy(unordered)
        unreachable['return'] = 0
        self.assert_error('invalid_program', lambda: compile_graph([unreachable, middle, leaf], bindings=bindings))
        # Fixed point must cross recursive call cycles, even for an empty map.
        recursive = function(7, [node(0, 'param', index=0), node(1, 'call', 0, target=8)])
        recursive['params'] = ['any']
        cycle_middle = function(8, [node(0, 'param', index=0), node(1, 'call', 0, target=7),
            node(2, 'call', 0, target=9, after=[1])], params=['any'])
        self.assert_error('invalid_program', lambda: compile_graph([mapped, recursive, cycle_middle, leaf], bindings=bindings))

    def test_pure_map_filter_and_effectful_fold_preserve_order(self) -> None:
        double = function(1, [node(0, 'param', index=0), node(1, 'mul', 0, 0)], params=['int'], result='int')
        positive = function(2, [node(0, 'param', index=0), literal(1, 0), node(2, 'gt', 0, 1)], params=['int'], result='bool')
        main = function(0, [literal(0, [-2, 0, 3]), node(1, 'filter', 0, target=2), node(2, 'map', 1, target=1)])
        self.assertEqual(compile_graph([main, double, positive]).call('main', []), [9])
        wrong = function(2, [literal(0, 1)], params=['int'], result='int')
        self.assert_error('type_error', lambda: compile_graph([main, double, wrong]).call('main', []))
        folder = function(3, [node(0, 'param', index=0), node(1, 'param', index=1),
            literal(2, 'log.txt'), node(3, 'concat', 0, 1),
            node(4, 'host', 2, 3, binding='write'), node(5, 'to_text', 3, after=[4])],
            params=['str', 'str'], result='str')
        folded = function(0, [literal(0, ['a', 'β', 'c']), literal(1, ''), node(2, 'fold', 0, 1, target=3)])
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(compile_graph([folded, folder], bindings={'write': 'file.write_text'}).call('main', [], state_dir=tmp), 'aβc')
            self.assertEqual((Path(tmp) / 'files' / 'log.txt').read_text(), 'aβc')

    def test_recursion_and_nested_attempts_share_uncatchable_root_limits(self) -> None:
        main = function(0, [node(0, 'attempt', target=1)])
        recurse = function(1, [node(0, 'call', target=1)])
        self.assert_error('resource_limit', lambda: compile_graph([main, recurse]).call('main', []))
        # Each callback is small; aggregate work alone exhausts the root budget.
        callback_nodes = [node(0, 'param', index=0)] + [node(i, 'not', i - 1) for i in range(1, 11)]
        callback = function(2, callback_nodes, params=['bool'], result='bool')
        mapped = function(1, [literal(0, [False] * 100_000), node(1, 'map', 0, target=2)])
        self.assert_error('resource_limit', lambda: compile_graph([main, mapped, callback]).call('main', []))

    def test_seeded_integer_dags_and_opaque_id_declaration_metamorphisms(self) -> None:
        rng = random.Random(74219)
        for case in range(32):
            values = [rng.randrange(-20, 21) for _ in range(3)]
            nodes = [literal(i, value) for i, value in enumerate(values)]
            for index in range(3, 18):
                left, right = rng.randrange(index), rng.randrange(index)
                operation = rng.choice(['add', 'sub', 'mul'])
                a, b = values[left], values[right]
                values.append(a + b if operation == 'add' else a - b if operation == 'sub' else a * b)
                nodes.append(node(index, operation, left, right))
            graph = [function(0, nodes)]
            original = compile_graph(graph)
            with self.subTest(case=case):
                self.assertEqual(original.call('main', []), values[-1])
                renamed = rename_program(graph, case)
                self.assertEqual(compile_graph(renamed).call('main', []), values[-1])
                shuffled = copy.deepcopy(graph)
                rng.shuffle(shuffled[0]['nodes'])
                self.assertEqual(compile_graph(shuffled).source_revision, original.source_revision)

    def test_declared_effect_order_survives_id_and_function_renaming(self) -> None:
        writer = function(2, [node(0, 'param', index=0), literal(1, 'ordered.txt'),
            node(2, 'host', 1, 0, binding='write')], params=['str'])
        main = function(0, [literal(10, 'first'), literal(20, 'second'),
            node(30, 'call', 10, target=2), node(40, 'call', 20, target=2, after=[30]),
            literal(50, 'ordered.txt'), node(60, 'host', 50, binding='read', after=[40])])
        for seed in range(8):
            graph = rename_program([main, writer], seed)
            # Identify the exported main by its zero-argument public signature.
            main_id = next(f['id'] for f in graph if not f['params'])
            with self.subTest(seed=seed), tempfile.TemporaryDirectory() as tmp:
                project = compile_graph(graph, bindings={'write': 'file.write_text', 'read': 'file.read_text'}, exports={'main': main_id})
                self.assertEqual(project.call('main', [], state_dir=tmp), 'second')
                self.assertEqual((Path(tmp) / 'files' / 'ordered.txt').read_text(), 'second')


if __name__ == '__main__':
    unittest.main()
