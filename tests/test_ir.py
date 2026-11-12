import copy
import json
import unittest

from mairg.ir import (
    MAX_DEPTH,
    MairgError,
    Node,
    Output,
    Program,
    checked_program,
    decode_json,
    loads,
    source_revision,
    to_document,
    validate,
)


def sample():
    return {"format": "mairg-1", "inputs": [["x", "u64"], ["flag", "bool"]],
            "nodes": [[30, "add", 20, 20], [20, "input", 0], [50, "input", 1]],
            "outputs": [["total", "sum", 30, 50], ["count", "count", 50]]}


class IRTests(unittest.TestCase):
    def test_forward_references_and_repeated_edges_are_topological(self):
        p = validate(sample())
        self.assertLess([n.id for n in p.nodes].index(20), [n.id for n in p.nodes].index(30))
        self.assertEqual({n.id: n.type for n in p.nodes}, {20: "u64", 30: "u64", 50: "bool"})

    def test_every_node_validated_even_if_dead(self):
        bad_cases = [[99, "unknown", 0], [99, "add", 99, 20],
                     [99, "add", 20, 888], [99, "add", 20, 50], [99, "u64", True]]
        for bad in bad_cases:
            with self.subTest(bad=bad):
                doc = sample()
                doc["nodes"].append(bad)
                with self.assertRaises(MairgError):
                    validate(doc)

    def test_reject_wrong_ids_constants_shapes_names_and_output_types(self):
        mutators = [
            lambda d: d.update(extra=True),
            lambda d: d["nodes"].append([True, "u64", 1]),
            lambda d: d["nodes"].append([99, "u64", -1]),
            lambda d: d["nodes"].append([99, "u64", 2**64]),
            lambda d: d["nodes"].append([99, "bool", 1]),
            lambda d: d["nodes"].append([99, "input", True]),
            lambda d: d["nodes"].append([99, "input", 2]),
            lambda d: d["nodes"].append([20, "input", 0]),
            lambda d: d["inputs"].append(["x", "u64"]),
            lambda d: d["outputs"].append(["total", "count", 50]),
            lambda d: d["outputs"].append(["bad", "count", 20]),
            lambda d: d["outputs"].append(["bad", "sum", 50, 50]),
            lambda d: d["nodes"].append([99, "select", 50, 20, 50]),
        ]
        for mutate in mutators:
            doc = sample()
            mutate(doc)
            with self.subTest(doc=doc), self.assertRaises(MairgError):
                validate(doc)

    def test_strict_json_and_source_size(self):
        for text in ['{"format":1,"format":2}', 'NaN', 'Infinity', '[', '[' * 10000,
                     b'\xff', ' ' * ((1 << 20) + 1)]:
            with self.subTest(text=str(text)[:40]), self.assertRaises(MairgError):
                loads(text)
        self.assertEqual(decode_json('{"a":1}'), {"a": 1})
        self.assertEqual(loads(json.dumps(sample())), validate(sample()))

    def test_depth_limit_counts_leaves(self):
        doc = {"format": "mairg-1", "inputs": [["flag", "bool"]],
               "nodes": [[0, "input", 0]] + [[i, "not", i - 1] for i in range(1, MAX_DEPTH)],
               "outputs": [["n", "count", MAX_DEPTH - 1]]}
        validate(doc)
        doc["nodes"].append([MAX_DEPTH, "not", MAX_DEPTH - 1])
        with self.assertRaises(MairgError):
            validate(doc)

    def test_source_revision_includes_dead_nodes_but_ignores_declaration_order(self):
        doc = sample()
        p = validate(doc)
        doc["nodes"].reverse()
        self.assertEqual(source_revision(p), source_revision(validate(doc)))
        doc["nodes"].append([100, "u64", 7])
        self.assertNotEqual(source_revision(p), source_revision(validate(doc)))

    def test_forged_ir_rejected_instead_of_metadata_repaired(self):
        p = validate(sample())
        nodes = list(p.nodes)
        nodes[0] = Node(nodes[0].id, nodes[0].op, nodes[0].args, "wrong")
        bad_type = Program(p.inputs, tuple(nodes), p.outputs)
        bad_count = Program(p.inputs, p.nodes, p.outputs[:-1] + (Output("count", "count", 999, 50),))
        bad_id = Program(p.inputs, (Node(True, "u64", (0,), "u64"),), p.outputs)
        for bad in [bad_type, bad_count, bad_id]:
            with self.subTest(bad=bad), self.assertRaises(MairgError):
                checked_program(bad)
            with self.assertRaises(MairgError):
                source_revision(bad)
        self.assertEqual(checked_program(p), p)

    def test_serialization_is_independent_copy(self):
        p = validate(sample())
        doc = to_document(p)
        snapshot = copy.deepcopy(doc)
        doc["nodes"].clear()
        self.assertEqual(to_document(p), snapshot)


if __name__ == "__main__":
    unittest.main()
