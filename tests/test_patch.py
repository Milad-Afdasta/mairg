import copy
import unittest

from mairg.ir import MairgError, source_revision, to_document, validate
from mairg.optimizer import optimize
from mairg.patch import apply_patch
from tests.test_ir import sample


class PatchTests(unittest.TestCase):
    def setUp(self):
        self.source = validate(sample())
        self.base = source_revision(self.source)

    def test_transaction_supports_forward_refs_replacement_and_removal(self):
        patch = {"base": self.base, "set_nodes": [[30, "add", 20, 60], [60, "u64", 10]]}
        result = apply_patch(self.source, patch)
        self.assertEqual({n.id: n.args for n in result.nodes}[30], (20, 60))
        result = apply_patch(result, {"base": source_revision(result), "set_nodes": [[30, "add", 20, 20]], "remove_nodes": [60]})
        self.assertEqual(source_revision(result), self.base)

    def test_invalid_edits_reject_atomically_without_original_changes(self):
        original = copy.deepcopy(to_document(self.source))
        invalid = [
            {"base": "wrong"}, {"set_nodes": []},
            {"base": self.base, "inputs": []},
            {"base": self.base, "remove_nodes": [20]},
            {"base": self.base, "remove_nodes": [999]},
            {"base": self.base, "remove_nodes": [30, 30]},
            {"base": self.base, "set_nodes": [[30, "add", 20, 20], [30, "add", 20, 20]]},
            {"base": self.base, "set_nodes": [[30, "add", 20, 20]], "remove_nodes": [30]},
            {"base": self.base, "set_nodes": [[30, "add", 20, 999]]},
            {"base": self.base, "set_nodes": [[True, "u64", 1]]},
            {"base": self.base, "outputs": []},
        ]
        for patch in invalid:
            with self.subTest(patch=patch), self.assertRaises(MairgError):
                apply_patch(self.source, patch)
            self.assertEqual(to_document(self.source), original)

    def test_stale_source_rejected_even_when_plan_semantics_match(self):
        with_dead = apply_patch(self.source, {"base": self.base, "set_nodes": [[99, "u64", 123]]})
        self.assertEqual(optimize(with_dead).fingerprint, optimize(self.source).fingerprint)
        with self.assertRaises(MairgError) as ctx:
            apply_patch(with_dead, {"base": self.base})
        self.assertEqual(ctx.exception.code, "stale_patch")


if __name__ == "__main__":
    unittest.main()
