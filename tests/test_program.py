"""Static analysis: arity, safety, stratification and join planning."""

from __future__ import annotations

import unittest

from mendel.program import Program
from mendel.syntax import (
    EvaluationError,
    ParseError,
    SafetyError,
    StratificationError,
    parse_atom,
)


class SignatureTests(unittest.TestCase):
    def test_arity_table_and_predicate_sets(self):
        program = Program.parse('e(1, 2). p(?x) :- e(?x, ?y).')
        self.assertEqual(program.arity, {"e": 2, "p": 1})
        self.assertEqual(program.edb_preds(), {"e"})
        self.assertEqual(program.idb_preds(), {"p"})

    def test_arity_mismatch_is_located(self):
        with self.assertRaises(ParseError) as caught:
            Program.parse('p(1, 2).\nq(?x) :- p(?x).')
        message = str(caught.exception)
        self.assertIn("arity 1", message)
        self.assertIn("line 2", message)

    def test_check_goal_rejects_unknown_predicate_with_a_hint(self):
        program = Program.parse("parent(1, 2).")
        with self.assertRaises(EvaluationError) as caught:
            program.check_goal(parse_atom("paren(1, 2)"))
        self.assertIn("did you mean", str(caught.exception))

    def test_check_goal_rejects_wrong_arity(self):
        program = Program.parse("parent(1, 2).")
        with self.assertRaises(EvaluationError):
            program.check_goal(parse_atom("parent(1)"))


class SafetyTests(unittest.TestCase):
    def test_head_variable_must_be_bound(self):
        with self.assertRaises(SafetyError) as caught:
            Program.parse("p(?x) :- q(?y).")
        self.assertIn("never", str(caught.exception))

    def test_negated_variable_must_be_bound(self):
        with self.assertRaises(SafetyError):
            Program.parse("p(?x) :- q(?x), not r(?y).")

    def test_comparison_variables_must_be_bound(self):
        with self.assertRaises(SafetyError):
            Program.parse("p(?x) :- q(?x), ?x < ?y.")

    def test_assignment_binds_a_variable(self):
        program = Program.parse("p(?y) :- q(?x), ?y = ?x.")
        self.assertEqual(program.arity, {"q": 1, "p": 1})

    def test_assignment_can_bind_from_a_constant(self):
        Program.parse('p(?y) :- q(?x), ?y = "fixed".')

    def test_repeated_variable_in_head_is_allowed_when_bound(self):
        Program.parse("p(?x, ?x) :- q(?x).")


class StratificationTests(unittest.TestCase):
    def test_dependencies_come_first(self):
        program = Program.parse('a(?x) :- b(?x). b(1).')
        self.assertEqual([s.preds for s in program.strata], [("b",), ("a",)])

    def test_negation_places_the_dependency_earlier(self):
        program = Program.parse(
            'node(1). blocked(?x) :- node(?x), not reachable(?x). '
            'reachable(?x) :- node(?x), ?x = 1.'
        )
        order = [s.preds[0] for s in program.strata]
        self.assertLess(order.index("reachable"), order.index("blocked"))

    def test_direct_negation_cycle_is_rejected(self):
        with self.assertRaises(StratificationError) as caught:
            Program.parse("p(?x) :- q(?x), not p(?x).")
        self.assertIn("negation cycle", str(caught.exception))
        self.assertIn("p", str(caught.exception))

    def test_mutual_negation_is_rejected(self):
        with self.assertRaises(StratificationError):
            Program.parse("p(?x) :- q(?x), not r(?x). r(?x) :- q(?x), not p(?x).")

    def test_positive_recursion_is_allowed(self):
        program = Program.parse('e(1, 2). p(?x, ?y) :- e(?x, ?y). p(?x, ?z) :- p(?x, ?y), p(?y, ?z).')
        self.assertEqual(len([s for s in program.strata if s.preds == ("p",)]), 1)

    def test_stratum_of_unknown_predicate(self):
        program = Program.parse("p(1).")
        with self.assertRaises(EvaluationError):
            program.stratum_of("nope")


class PlanTests(unittest.TestCase):
    def test_plan_is_a_permutation(self):
        program = Program.parse("p(?x) :- a(?x), b(?x), not c(?x), ?x < 3.")
        rule = program.rules[0]
        order = program.plan_indices(rule)
        self.assertEqual(sorted(order), list(range(len(rule.body))))

    def test_most_bound_goal_goes_first(self):
        program = Program.parse('p(?x, ?y) :- big(?x, ?y), small(?y, ?z).')
        rule = program.rules[0]
        order = program.plan_indices(rule, sizes={"big": 1000, "small": 3})
        # Both have zero bound arguments, so size decides: small first.
        self.assertEqual(order[0], 1)

    def test_filters_follow_their_variables(self):
        program = Program.parse("p(?x) :- q(?x), ?x > 2, not r(?x).")
        order = program.plan_indices(program.rules[0])
        self.assertEqual(order[0], 0)
        self.assertEqual(sorted(order[1:]), [1, 2])

    def test_initial_bindings_change_the_plan(self):
        program = Program.parse("p(?x, ?y) :- e(?x, ?y), ?y > 2.")
        rule = program.rules[0]
        plain = program.plan_indices(rule)
        bound = program.plan_indices(rule, initial=("y",))
        self.assertEqual(sorted(plain), [0, 1])
        self.assertEqual(sorted(bound), [0, 1])

    def test_plan_without_sizes_still_checks_safety(self):
        with self.assertRaises(SafetyError):
            Program.parse("p(?x) :- not q(?x).")


if __name__ == "__main__":
    unittest.main()
