"""Independent semantic tests against the approved MAIRG-M2 contract.

The oracle consumes unordered source documents and does not use the production
interpreter, optimizer, fold helpers, or emitted expressions for evaluation.
"""

import copy
import operator
import random
import shutil
import unittest
from unittest.mock import Mock

from mairg.interpreter import interpret, records_to_columns, validate_columns
from mairg.ir import (
    LEAF_OPS,
    MAX_DEPTH,
    OPS,
    REDUCTIONS,
    MairgError,
    Node,
    Output,
    Program,
    checked_program,
    source_revision,
    to_document,
    validate,
)
from mairg.native import compile_program, emit_c, prepare_batch
from mairg.optimizer import optimize
from mairg.patch import apply_patch

MASK = (1 << 64) - 1
HAS_COMPILER = bool(shutil.which("clang") or shutil.which("gcc"))
U_OPS = ("add", "sub", "mul", "bit_and", "bit_or", "bit_xor")
COMPARE = ("eq", "lt", "le", "gt", "ge")
PURE_FUNCTIONS = {
    "add": operator.add,
    "sub": operator.sub,
    "mul": operator.mul,
    "bit_and": operator.and_,
    "bit_or": operator.or_,
    "bit_xor": operator.xor,
    "eq": operator.eq,
    "lt": operator.lt,
    "le": operator.le,
    "gt": operator.gt,
    "ge": operator.ge,
    "and": lambda a, b: bool(a and b),
    "or": lambda a, b: bool(a or b),
    "not": operator.not_,
    "select": lambda condition, yes, no: yes if condition else no,
}


def oracle(document, columns):
    """Evaluate dependency meaning directly, with a memoized unordered graph."""
    definitions = {row[0]: row[1:] for row in document["nodes"]}
    result = {row[0]: 0 for row in document["outputs"]}
    names = [pair[0] for pair in document["inputs"]]
    for index in range(len(columns[names[0]])):
        values = {}

        def value(ident):
            if ident not in values:
                op, *args = definitions[ident]
                if op == "input":
                    computed = columns[names[args[0]]][index]
                elif op in ("u64", "bool"):
                    computed = args[0]
                else:
                    computed = PURE_FUNCTIONS[op](*(value(arg) for arg in args))
                    if op in U_OPS:
                        computed &= MASK
                values[ident] = computed
            return values[ident]

        for row in document["outputs"]:
            name, reduction, *args = row
            if not value(args[-1]):
                continue
            if reduction == "count":
                result[name] = (result[name] + 1) & MASK
            elif reduction == "sum":
                result[name] = (result[name] + value(args[0])) & MASK
            elif reduction == "max":
                result[name] = max(result[name], value(args[0]))
            elif reduction == "xor":
                result[name] ^= value(args[0])
            else:
                raise AssertionError(reduction)
    return result


def whole_catalog_document():
    nodes = [[0, "input", 0], [1, "input", 1], [2, "input", 2],
             [3, "input", 3], [4, "u64", MASK], [5, "u64", 1],
             [6, "bool", True], [7, "bool", False]]
    outputs = []
    for op in U_OPS:
        ident = len(nodes)
        nodes.append([ident, op, 0, 1])
        outputs.extend([[op + "_sum", "sum", ident, 6],
                        [op + "_max", "max", ident, 2],
                        [op + "_xor", "xor", ident, 3]])
    for op in COMPARE:
        ident = len(nodes)
        nodes.append([ident, op, 0, 1])
        outputs.append([op, "count", ident])
    for op, args in (("and", [2, 3]), ("or", [2, 3]), ("not", [2]),
                     ("eq", [2, 3]), ("select", [2, 3, 7])):
        ident = len(nodes)
        nodes.append([ident, op, *args])
        outputs.append([op + "_bool_" + str(ident), "count", ident])
    ident = len(nodes)
    nodes.extend([[ident, "select", 2, 0, 1],
                  [ident + 1, "add", 4, 5],
                  [ident + 2, "sub", 5, 4],
                  [ident + 3, "mul", 4, 4]])
    outputs.extend([["selected", "sum", ident, 6],
                    ["literal_add_wrap", "sum", ident + 1, 6],
                    ["literal_sub_wrap", "sum", ident + 2, 6],
                    ["literal_mul_wrap", "sum", ident + 3, 6],
                    ["never", "sum", 0, 7], ["always", "count", 6]])
    return {"format": "mairg-1",
            "inputs": [["x", "u64"], ["y", "u64"], ["a", "bool"], ["b", "bool"]],
            "nodes": nodes, "outputs": outputs}


def random_document(rng, length=45):
    nodes = [[0, "input", 0], [1, "input", 1], [2, "input", 2],
             [3, "u64", rng.choice((0, 1, MASK, 1 << 63, rng.getrandbits(64)))],
             [4, "bool", bool(rng.getrandbits(1))]]
    unsigned, boolean = [0, 1, 3], [2, 4]
    for ident in range(5, length):
        scalar_type = rng.choice(("u64", "bool"))
        op = rng.choice((*U_OPS, "select")) if scalar_type == "u64" else rng.choice(
            (*COMPARE, "and", "or", "not", "select"))
        if op in U_OPS or op in ("lt", "le", "gt", "ge"):
            args = rng.choices(unsigned, k=2)
        elif op == "eq":
            args = rng.choices(rng.choice((unsigned, boolean)), k=2)
        elif op in ("and", "or"):
            args = rng.choices(boolean, k=2)
        elif op == "not":
            args = [rng.choice(boolean)]
        else:
            args = [rng.choice(boolean), *rng.choices(unsigned if scalar_type == "u64" else boolean, k=2)]
        nodes.append([ident, op, *args])
        (unsigned if scalar_type == "u64" else boolean).append(ident)
    outputs = [["count", "count", rng.choice(boolean)]]
    outputs.extend([[op, op, rng.choice(unsigned), rng.choice(boolean)] for op in ("sum", "max", "xor")])
    rng.shuffle(nodes)
    return {"format": "mairg-1", "inputs": [["x", "u64"], ["y", "u64"], ["flag", "bool"]],
            "nodes": nodes, "outputs": outputs}


def renamed(document, rng):
    result = copy.deepcopy(document)
    mapping = dict(zip([row[0] for row in result["nodes"]], rng.sample(range(1, 1 << 30), len(result["nodes"]))))
    for row in result["nodes"]:
        row[0] = mapping[row[0]]
        if row[1] not in LEAF_OPS:
            row[2:] = [mapping[arg] for arg in row[2:]]
    for row in result["outputs"]:
        row[2:] = [mapping[arg] for arg in row[2:]]
    rng.shuffle(result["nodes"])
    return result


def random_columns(document, rng, count):
    columns = {}
    for name, scalar_type in document["inputs"]:
        if scalar_type == "bool":
            columns[name] = [bool(rng.getrandbits(1)) for _ in range(count)]
        else:
            edges = [0, MASK, 1, 1 << 63, (1 << 63) - 1]
            columns[name] = (edges + [rng.getrandbits(64) for _ in range(count)])[:count]
            rng.shuffle(columns[name])
    return columns


class IndependentSemanticsTests(unittest.TestCase):
    def test_catalog_coverage_and_independent_oracle(self):
        document = whole_catalog_document()
        self.assertEqual({row[1] for row in document["nodes"]}, set(OPS))
        self.assertEqual({row[1] for row in document["outputs"]}, set(REDUCTIONS))
        rng = random.Random(7919)
        rng.shuffle(document["nodes"])
        program = validate(document)
        for count in (0, 1, 64):
            columns = random_columns(document, rng, count)
            expected = oracle(document, columns)
            self.assertEqual(interpret(program, columns), expected)
            self.assertEqual(interpret(optimize(program).program, columns), expected)
        if HAS_COMPILER:
            for enabled in (False, True):
                with compile_program(program, enabled) as compiled:
                    for count in (0, 1, 64):
                        columns = random_columns(document, rng, count)
                        self.assertEqual(compiled.run(columns), oracle(document, columns))

    def test_randomized_native_original_and_optimized_match_independent_oracle(self):
        rng = random.Random(104729)
        for case in range(32):
            document = random_document(rng)
            program = validate(document)
            columns = random_columns(document, rng, rng.choice((0, 1, 7, 31)))
            expected = oracle(document, columns)
            with self.subTest(case=case):
                self.assertEqual(interpret(program, columns), expected)
                self.assertEqual(interpret(optimize(program).program, columns), expected)
                if HAS_COMPILER:
                    for enabled in (False, True):
                        with compile_program(program, enabled) as compiled:
                            self.assertEqual(compiled.run(columns), expected)

    def test_five_hundred_unordered_graphs_and_opaque_id_renamings(self):
        rng = random.Random(15485863)
        for case in range(500):
            document = random_document(rng)
            program = validate(document)
            alternate = renamed(document, rng)
            for row in alternate["nodes"]:
                if row[1] in OPS and OPS[row[1]].commutative and rng.getrandbits(1):
                    row[2:] = reversed(row[2:])
            plan, alternate_plan = optimize(program), optimize(validate(alternate))
            with self.subTest(case=case):
                self.assertEqual(plan.fingerprint, alternate_plan.fingerprint)
                self.assertEqual(to_document(plan.program), to_document(alternate_plan.program))
                reordered = copy.deepcopy(document)
                rng.shuffle(reordered["nodes"])
                self.assertEqual(source_revision(program), source_revision(validate(reordered)))

    def test_patch_source_identity_atomicity_and_replay(self):
        source = validate(whole_catalog_document())
        before = to_document(source)
        base = source_revision(source)
        changed = apply_patch(source, {"base": base, "set_nodes": [[99999, "u64", 17]]})
        self.assertNotEqual(source_revision(changed), base)
        self.assertEqual(optimize(source).fingerprint, optimize(changed).fingerprint)
        for patch in ({"base": base},
                      {"base": source_revision(changed), "remove_nodes": [0]},
                      {"base": source_revision(changed), "set_nodes": [[6, "u64", 1]]},
                      {"base": source_revision(changed), "remove_nodes": [99999], "set_nodes": [[99999, "u64", 9]]}):
            snapshot = to_document(changed)
            with self.subTest(patch=patch), self.assertRaises(MairgError):
                apply_patch(changed, patch)
            self.assertEqual(to_document(changed), snapshot)
        restored = apply_patch(changed, {"base": source_revision(changed), "remove_nodes": [99999]})
        self.assertEqual(source_revision(restored), base)
        self.assertEqual(to_document(source), before)

    def test_public_consumers_reject_forged_types_literals_and_dropped_metadata(self):
        valid = validate(whole_catalog_document())
        malformed = [
            Program(valid.inputs, (Node(0, "input", (0,), "bool"), *valid.nodes[1:]), valid.outputs),
            Program(valid.inputs, (*valid.nodes, Node(999, "u64", (-1,), "u64")), valid.outputs),
            Program(valid.inputs, (*valid.nodes, Node(999, "bool", (1,), "bool")), valid.outputs),
            Program(valid.inputs, valid.nodes, (Output("forged", "count", 999, 6),)),
            Program(valid.inputs, valid.nodes, (Output("forged", "sum", 0, True),)),
        ]
        consumers = [checked_program, source_revision, optimize, emit_c,
                     lambda p: apply_patch(p, {"base": "incorrect"}),
                     lambda p: interpret(p, {}), lambda p: validate_columns(p, {}),
                     lambda p: records_to_columns(p, []), lambda p: prepare_batch(p, {})]
        if HAS_COMPILER:
            consumers.append(compile_program)
        for program in malformed:
            for consume in consumers:
                with self.subTest(consumer=consume, program=program), self.assertRaises(MairgError):
                    consume(program)

    @unittest.skipUnless(HAS_COMPILER, "native compiler required")
    def test_late_bad_values_reject_before_kernel_and_prepared_data_is_pinned(self):
        document = whole_catalog_document()
        program = validate(document)
        rng = random.Random(32452843)
        columns = random_columns(document, rng, 9)
        with compile_program(program) as compiled:
            prepared = compiled.prepare(columns)
            expected = oracle(document, columns)
            columns["x"][-1] = (columns["x"][-1] + 1234) & MASK
            self.assertEqual(compiled.run_prepared(prepared), expected)
            real_kernel = compiled.kernel
            compiled.kernel = Mock()
            bad = []
            for invalid in (True, -1, 1 << 64, 0.0, "1", None):
                item = copy.deepcopy(columns)
                item["x"][-1] = invalid
                bad.append(item)
            for invalid in (1, 0, None, "true"):
                item = copy.deepcopy(columns)
                item["a"][-1] = invalid
                bad.append(item)
            bad.extend([{key: value for key, value in columns.items() if key != "x"},
                        {**columns, "extra": []}, {**columns, "x": columns["x"][:-1]}])
            for item in bad:
                with self.subTest(columns=item), self.assertRaises(MairgError):
                    compiled.run(item)
            compiled.kernel.assert_not_called()
            compiled.kernel = real_kernel

    def test_maximum_depth_repeated_edges_and_unreachable_invalid_state(self):
        document = {"format": "mairg-1", "inputs": [["flag", "bool"]],
                    "nodes": [[0, "input", 0]] + [[i, "and", i - 1, i - 1] for i in range(1, MAX_DEPTH)],
                    "outputs": [["count", "count", MAX_DEPTH - 1]]}
        document["nodes"].reverse()
        program = validate(document)
        self.assertEqual(interpret(program, {"flag": [True, False, True]}), {"count": 2})
        self.assertEqual(interpret(optimize(program).program, {"flag": [True, False, True]}), {"count": 2})
        excessive = copy.deepcopy(document)
        excessive["nodes"].append([MAX_DEPTH, "not", MAX_DEPTH - 1])
        with self.assertRaises(MairgError):
            validate(excessive)
        for suffix in ([[10000, "not", 10000]],
                       [[10000, "not", 10001], [10001, "not", 10000]],
                       [[10000, "add", 0, 0]], [[10000, "not", 20000]]):
            invalid = copy.deepcopy(document)
            invalid["nodes"].extend(suffix)
            with self.subTest(suffix=suffix), self.assertRaises(MairgError):
                validate(invalid)


if __name__ == "__main__":
    unittest.main()
