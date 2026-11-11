"""Provenance: trees, alternative proofs, why-not diagnostics."""

from __future__ import annotations

import unittest

from mendel import provenance as prov
from mendel.engine import Engine
from mendel.program import Program

from tests.test_engine import FAMILY, POLICY

DIAMOND = """
parent("ada", "bob"). parent("ada", "cy"). parent("bob", "dee"). parent("cy", "dee").
ancestor(?x, ?y) :- parent(?x, ?y).
@step ancestor(?x, ?z) :- parent(?x, ?y), ancestor(?y, ?z).
"""


def engine_for(source: str) -> Engine:
    return Engine(Program.parse(source, "test.dl"))


class TreeTests(unittest.TestCase):
    def test_tree_structure(self):
        engine = engine_for(FAMILY)
        node = prov.build_tree(engine, ("ancestor", ("ada", "fay")))
        self.assertIsNotNone(node)
        self.assertEqual(node.support.rule.name, "step")
        self.assertEqual(len(node.children), 2)
        self.assertEqual(node.children[0].fact, ("parent", ("ada", "bob")))
        self.assertTrue(node.children[0].given)
        self.assertEqual(node.children[1].fact, ("ancestor", ("bob", "fay")))
        # ada -> bob -> dee -> fay is four facts deep.
        depth = 0
        cursor = node
        while cursor.children:
            cursor = cursor.children[-1]
            depth += 1
        self.assertEqual(depth, 3)

    def test_given_fact_is_a_leaf(self):
        engine = engine_for(FAMILY)
        node = prov.build_tree(engine, ("parent", ("ada", "bob")))
        self.assertTrue(node.given)
        self.assertEqual(node.children, [])
        self.assertIn("(given)", prov.format_tree(node)[0])

    def test_missing_fact_has_no_tree(self):
        engine = engine_for(FAMILY)
        self.assertIsNone(prov.build_tree(engine, ("ancestor", ("fay", "ada"))))

    def test_negated_goal_is_recorded_as_absent(self):
        engine = engine_for(POLICY)
        node = prov.build_tree(engine, ("denied", ("bob", "prod")))
        self.assertIsNotNone(node)
        self.assertEqual(node.absences, [("can", ("bob", "prod"))])
        text = "\n".join(prov.format_tree(node))
        self.assertIn('not can("bob", "prod")   (absent)', text)

    def test_format_has_no_trailing_spaces(self):
        engine = engine_for(FAMILY)
        for line in prov.format_tree(prov.build_tree(engine, ("ancestor", ("ada", "fay")))):
            self.assertEqual(line, line.rstrip(), repr(line))

    def test_depth_limit_truncates_instead_of_recursing(self):
        facts = [f"e({i}, {i + 1})." for i in range(120)]
        source = "\n".join(facts) + """
p(?x, ?y) :- e(?x, ?y).
p(?x, ?z) :- p(?x, ?y), e(?y, ?z).
"""
        engine = Engine(Program.parse(source, "chain.dl"))
        node = prov.build_tree(engine, ("p", (0, 120)), max_depth=8)
        stack = [node]
        truncated = False
        while stack:
            current = stack.pop()
            truncated = truncated or current.truncated
            stack.extend(current.children)
        self.assertTrue(truncated)
        self.assertIn("(depth limit)", "\n".join(prov.format_tree(node)))


class DerivationTests(unittest.TestCase):
    def test_unique_path_has_one_proof(self):
        engine = engine_for(FAMILY)
        nodes = prov.derivations(engine, ("ancestor", ("ada", "fay")))
        self.assertEqual(len(nodes), 1)

    def test_diamond_has_two_proofs(self):
        engine = engine_for(DIAMOND)
        nodes = prov.derivations(engine, ("ancestor", ("ada", "dee")))
        self.assertEqual(len(nodes), 2)
        firsts = {(node.children[0].fact[1]) for node in nodes}
        self.assertEqual(firsts, {("ada", "bob"), ("ada", "cy")})

    def test_limit_is_respected(self):
        engine = engine_for(DIAMOND)
        self.assertEqual(len(prov.derivations(engine, ("ancestor", ("ada", "dee")), limit=1)), 1)

    def test_given_fact_is_its_own_proof(self):
        engine = engine_for(FAMILY)
        nodes = prov.derivations(engine, ("parent", ("ada", "bob")))
        self.assertEqual(len(nodes), 1)
        self.assertTrue(nodes[0].given)

    def test_missing_fact_has_no_proofs(self):
        engine = engine_for(FAMILY)
        self.assertEqual(prov.derivations(engine, ("ancestor", ("fay", "ada"))), [])


class WhyNotTests(unittest.TestCase):
    def test_existing_fact_is_reported_as_derivable(self):
        report = prov.why_not(engine_for(FAMILY), ("ancestor", ("ada", "fay")))
        self.assertTrue(report.exists)
        text = "\n".join(prov.format_why_not(report))
        self.assertIn("is derivable", text)

    def test_blocked_goal_is_named_with_bindings(self):
        report = prov.why_not(engine_for(FAMILY), ("ancestor", ("fay", "ada")))
        self.assertFalse(report.exists)
        self.assertTrue(report.attempts)
        best = report.attempts[0]
        self.assertEqual(best.blocked_text, 'parent("fay", "ada")')
        self.assertEqual(best.satisfied, 0)
        text = "\n".join(prov.format_why_not(report))
        self.assertIn('stuck at: parent("fay", "ada")', text)

    def test_relaxation_offers_near_misses(self):
        source = """
        parent("ada", "bob"). parent("ada", "cy").
        ancestor(?x, ?y) :- parent(?x, ?y).
        """
        report = prov.why_not(engine_for(source), ("ancestor", ("ada", "zzz")))
        best = report.attempts[0]
        self.assertTrue(best.relaxations)
        relaxed = best.relaxations[0]
        self.assertEqual(relaxed.position, 1)
        self.assertEqual(relaxed.count, 2)
        self.assertEqual([fact[1] for fact in relaxed.examples], [("ada", "bob"), ("ada", "cy")])

    def test_predicate_without_rules(self):
        engine = engine_for("p(1).")
        report = prov.why_not(engine, ("p", (2,)))
        self.assertFalse(report.has_rules)
        text = "\n".join(prov.format_why_not(report))
        self.assertIn("no rule has p/1", text)

    def test_empty_relation_is_named(self):
        engine = engine_for("p(?x) :- q(?x).")
        report = prov.why_not(engine, ("p", (1,)))
        self.assertEqual(report.relation_size, 0)
        text = "\n".join(prov.format_why_not(report))
        self.assertIn("p/1 is empty", text)

    def test_comparison_can_be_the_blocker(self):
        engine = engine_for('n(1). small(?x) :- n(?x), ?x < 0.')
        report = prov.why_not(engine, ("small", (1,)))
        best = report.attempts[0]
        self.assertIn("comparison", best.note)
        self.assertEqual(best.blocked_text, "1 < 0")

    def test_negation_can_be_the_blocker(self):
        engine = engine_for('p(1). q(?x) :- p(?x), not r(?x). r(1).')
        report = prov.why_not(engine, ("q", (1,)))
        best = report.attempts[0]
        self.assertIn("negated goal", best.note)

    def test_rule_text_drops_the_label(self):
        rules = Program.parse("@named p(1) :- q(1).").rules
        self.assertEqual(prov.rule_text(rules[0]), "p(1) :- q(1).")


if __name__ == "__main__":
    unittest.main()
