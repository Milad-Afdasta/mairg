import unittest

from mairg.interpreter import interpret, records_to_columns, validate_columns
from mairg.ir import MairgError, validate


def arithmetic_program():
    return validate({
        "format": "mairg-1", "inputs": [["x", "u64"], ["flag", "bool"]],
        "nodes": [[0, "input", 0], [1, "input", 1], [2, "u64", 1],
                  [3, "add", 0, 2], [4, "sub", 0, 2], [5, "bool", True],
                  [6, "u64", (1 << 64) - 1], [7, "select", 1, 0, 6]],
        "outputs": [["plus", "sum", 3, 5], ["minus", "sum", 4, 5],
                    ["selected", "sum", 7, 5], ["count", "count", 1],
                    ["max", "max", 7, 5], ["xor", "xor", 7, 5]],
    })


class InterpreterTests(unittest.TestCase):
    def test_modular_arithmetic_and_reduction_identities(self):
        program = arithmetic_program()
        maximum = (1 << 64) - 1
        self.assertEqual(
            interpret(program, {"x": [maximum, 0, 2], "flag": [True, False, True]}),
            {"plus": 4, "minus": maximum - 1, "selected": 0,
             "count": 2, "max": maximum, "xor": 2},
        )
        self.assertEqual(interpret(program, {"x": [], "flag": []}),
                         dict.fromkeys([o.name for o in program.outputs], 0))

    def test_strict_types_and_complete_batch(self):
        program = arithmetic_program()
        for value in [True, -1, 1 << 64, 1.0, "1", None]:
            with self.subTest(value=value), self.assertRaises(MairgError):
                validate_columns(program, {"x": [0, value], "flag": [True, True]})
        for columns in [
            {"x": [0], "flag": [1]}, {"x": [0], "flag": []},
            {"x": [0]}, {"x": [], "flag": [], "extra": []},
            {"x": range(2), "flag": [True, True]},
        ]:
            with self.subTest(columns=columns), self.assertRaises(MairgError):
                interpret(program, columns)

    def test_snapshots_are_immutable_and_records_reject_late_invalid_row(self):
        program = arithmetic_program()
        columns = {"x": [1], "flag": [True]}
        values, count = validate_columns(program, columns)
        columns["x"][0] = 999
        self.assertEqual((values, count), (((1,), (True,)), 1))
        self.assertEqual(records_to_columns(program, [{"x": 2, "flag": False}]),
                         {"x": [2], "flag": [False]})
        for records in [[{"x": 2, "flag": True}, {"x": False, "flag": False}],
                        [{"x": 2, "flag": True, "extra": 0}], {"x": 2}]:
            with self.subTest(records=records), self.assertRaises(MairgError):
                records_to_columns(program, records)


if __name__ == "__main__":
    unittest.main()
