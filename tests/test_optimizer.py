import copy
import random
import unittest

from mairg.ir import (
    OPS,
    U64_MAX,
    MairgError,
    Node,
    Program,
    source_revision,
    to_document,
    validate,
)
from mairg.optimizer import _fold, optimize
from tests.test_ir import sample


class OptimizerTests(unittest.TestCase):
    def test_canonical_under_id_renaming_and_source_order(self):
        original = sample()
        expected = optimize(validate(original))
        rng = random.Random(31)
        for _ in range(20):
            doc = copy.deepcopy(original)
            remap = dict(zip([row[0] for row in doc["nodes"]], rng.sample(range(1000), len(doc["nodes"]))))
            for row in doc["nodes"]:
                row[0] = remap[row[0]]
                if row[1] not in ("input", "u64", "bool"):
                    row[2:] = [remap[arg] for arg in row[2:]]
            for out in doc["outputs"]:
                out[2:] = [remap[arg] for arg in out[2:]]
            rng.shuffle(doc["nodes"])
            plan = optimize(validate(doc))
            self.assertEqual(plan.fingerprint, expected.fingerprint)
            self.assertEqual(to_document(plan.program), to_document(expected.program))

    def test_exact_cse_commutative_order_and_dead_pruning(self):
        doc = sample()
        doc["nodes"] += [[60, "add", 20, 20], [61, "u64", 13]]
        doc["outputs"].append(["again", "sum", 60, 50])
        plan = optimize(validate(doc))
        self.assertEqual(plan.stats["merged_nodes"], 1)
        self.assertEqual(plan.stats["dead_nodes"], 1)
        self.assertEqual(plan.stats["row_passes"], 1)
        self.assertEqual(plan.stats["intermediate_column_buffers"], 0)
        first, _, third = plan.program.outputs
        self.assertEqual(first.value, third.value)

    def test_fold_all_scalar_operations_and_modular_boundaries(self):
        cases = {
            "add": ((U64_MAX, 1), 0), "sub": ((0, 1), U64_MAX),
            "mul": ((U64_MAX, 2), U64_MAX - 1), "bit_and": ((6, 3), 2),
            "bit_or": ((6, 3), 7), "bit_xor": ((6, 3), 5),
            "eq": ((True, False), False), "lt": ((3, 4), True),
            "le": ((4, 4), True), "gt": ((4, 3), True), "ge": ((4, 4), True),
            "and": ((True, False), False), "or": ((True, False), True),
            "not": ((True,), False), "select": ((False, 9, 10), 10),
        }
        self.assertEqual(set(cases), set(OPS) - {"input", "u64", "bool"})
        for op, (values, result) in cases.items():
            with self.subTest(op=op):
                self.assertEqual(_fold(op, values), result)
                nodes = [[i, "bool" if type(value) is bool else "u64", value] for i, value in enumerate(values)]
                result_id = len(nodes)
                nodes.append([result_id, op, *range(len(values))])
                if type(result) is bool:
                    outputs = [["out", "count", result_id]]
                else:
                    nodes.append([result_id + 1, "bool", True])
                    outputs = [["out", "sum", result_id, result_id + 1]]
                plan = optimize(validate({"format": "mairg-1", "inputs": [["x", "u64"]],
                                          "nodes": nodes, "outputs": outputs}))
                self.assertEqual(plan.stats["folded_nodes"], 1)
                self.assertTrue(all(n.op in ("bool", "u64") for n in plan.program.nodes))

    def test_source_identity_distinct_from_compiled_identity(self):
        doc = sample()
        before = validate(doc)
        doc["nodes"].append([77, "u64", 123])
        after = validate(doc)
        self.assertNotEqual(source_revision(before), source_revision(after))
        self.assertEqual(optimize(before).fingerprint, optimize(after).fingerprint)

    def test_validation_precedes_optimization(self):
        p = validate(sample())
        forged = Program(p.inputs, p.nodes + (Node(99, "u64", (-1,), "u64"),), p.outputs)
        with self.assertRaises(MairgError):
            optimize(forged)


if __name__ == "__main__":
    unittest.main()
