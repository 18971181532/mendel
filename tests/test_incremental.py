"""Incremental maintenance: adds, DRed retractions, impact analysis.

The central test performs random mutations and, after every single one,
compares the maintained database with a fresh engine built from the current
EDB.  That is the only property that matters: incremental work must be
invisible in the answer.
"""

from __future__ import annotations

import random
import unittest

from mendel.engine import Engine
from mendel.program import Program
from mendel.syntax import Atom, Const, EvaluationError

from tests.test_engine import FAMILY, random_source

DIAMOND = """
parent("ada", "bob"). parent("ada", "cy"). parent("bob", "dee"). parent("cy", "dee").
ancestor(?x, ?y) :- parent(?x, ?y).
@step ancestor(?x, ?z) :- parent(?x, ?y), ancestor(?y, ?z).
"""


def rebuild(program: Program, edb) -> Engine:
    facts = [
        Atom(pred, tuple(Const(value) for value in row))
        for pred, row in sorted(edb)
    ]
    return Engine(Program(facts, program.rules, name=program.name))


class RetractionTests(unittest.TestCase):
    def test_alternative_derivation_survives(self):
        engine = Engine(Program.parse(DIAMOND, "diamond.dl"))
        self.assertIn(("ada", "dee"), engine.facts_of("ancestor"))
        gone = engine.remove_fact("parent", ("bob", "dee"))
        self.assertIn(("parent", ("bob", "dee")), gone)
        # ada -> dee still holds through cy, so it must come back.
        self.assertIn(("ada", "dee"), engine.facts_of("ancestor"))
        self.assertNotIn(("bob", "dee"), engine.facts_of("ancestor"))

    def test_leaf_retraction_matches_rebuild(self):
        engine = Engine(Program.parse(FAMILY, "family.dl"))
        target = ("parent", ("dee", "fay"))
        gone = engine.remove_fact(*target)
        expected = rebuild(Program.parse(FAMILY, "family.dl"), engine.edb)
        self.assertEqual(engine.all_facts(), expected.all_facts())
        self.assertIn(("ada", "eve"), engine.facts_of("ancestor"))
        self.assertNotIn(("ada", "fay"), engine.facts_of("ancestor"))
        self.assertGreater(len(gone), 1)

    def test_retracting_every_edge_empties_the_closure(self):
        program = Program.parse(FAMILY, "family.dl")
        engine = Engine(program)
        for fact in sorted(engine.edb):
            if fact in engine.edb:
                engine.remove_fact(fact[0], fact[1])
        self.assertEqual(engine.facts_of("ancestor"), set())
        self.assertEqual(engine.facts_of("parent"), set())

    def test_unknown_fact(self):
        engine = Engine(Program.parse("p(1)."))
        with self.assertRaises(EvaluationError):
            engine.remove_fact("p", (2,))

    def test_derived_fact_cannot_be_retracted(self):
        engine = Engine(Program.parse(DIAMOND, "diamond.dl"))
        with self.assertRaises(EvaluationError) as caught:
            engine.remove_fact("ancestor", ("ada", "dee"))
        self.assertIn("derived", str(caught.exception))


class AddTests(unittest.TestCase):
    def test_add_matches_rebuild(self):
        program = Program.parse(FAMILY, "family.dl")
        engine = Engine(program)
        engine.add_fact("parent", ("fay", "gus"))
        expected = rebuild(program, engine.edb)
        self.assertEqual(engine.all_facts(), expected.all_facts())

    def test_add_to_idb_predicate(self):
        program = Program.parse("e(1, 2). p(?x) :- e(?x, ?y).")
        engine = Engine(program)
        self.assertEqual(engine.facts_of("p"), {(1,)})
        self.assertTrue(engine.add_fact("p", (9,)))
        self.assertEqual(engine.facts_of("p"), {(1,), (9,)})

    def test_retracting_a_promoted_fact_rederives_it(self):
        # Regression: add_fact(p, (1,)) detaches p(1)'s support.  Removing the
        # assertion must not remove the fact, because the rules still derive it.
        program = Program.parse("e(1, 2). p(?x) :- e(?x, ?y).")
        engine = Engine(program)
        self.assertTrue(engine.add_fact("p", (1,)))
        self.assertIsNone(engine.support_of(("p", (1,))))
        lost = engine.remove_fact("p", (1,))
        self.assertEqual(lost, set())
        self.assertIn((1,), engine.facts_of("p"))
        self.assertIsNotNone(engine.support_of(("p", (1,))))

    def test_adding_a_lower_stratum_fact_drives_mixed_rules(self):
        # Regression: a new parent edge must reach the recursive ancestor rule,
        # which lives one stratum above the edge.
        engine = Engine(Program.parse(FAMILY, "family.dl"))
        engine.add_fact("parent", ("fay", "gus"))
        self.assertIn(("fay", "gus"), engine.facts_of("ancestor"))
        self.assertIn(("bob", "gus"), engine.facts_of("ancestor"))
        self.assertIn(("ada", "gus"), engine.facts_of("ancestor"))

    def test_add_duplicate_is_a_no_op(self):
        engine = Engine(Program.parse(FAMILY, "family.dl"))
        self.assertFalse(engine.add_fact("parent", ("ada", "bob")))

    def test_add_then_remove_returns_to_the_original(self):
        program = Program.parse(FAMILY, "family.dl")
        engine = Engine(program)
        before = engine.all_facts()
        engine.add_fact("parent", ("fay", "gus"))
        engine.remove_fact("parent", ("fay", "gus"))
        self.assertEqual(engine.all_facts(), before)


class ImpactTests(unittest.TestCase):
    def test_recorded_impact_is_an_upper_bound(self):
        engine = Engine(Program.parse(DIAMOND, "diamond.dl"))
        recorded = engine.impact(("parent", ("bob", "dee")))
        trial = engine.trial_without(("parent", ("bob", "dee")))
        actual = (engine.all_facts() - trial.all_facts()) - {("parent", ("bob", "dee"))}
        self.assertLessEqual(actual, set(recorded))
        self.assertNotIn(("ancestor", ("ada", "dee")), actual)
        self.assertIn(("ancestor", ("bob", "dee")), set(recorded))

    def test_exact_trial_matches_remove_fact(self):
        program = Program.parse(DIAMOND, "diamond.dl")
        engine = Engine(program)
        trial = engine.trial_without(("parent", ("bob", "dee")))
        lost = engine.all_facts() - trial.all_facts()
        engine.remove_fact("parent", ("bob", "dee"))
        self.assertEqual(engine.all_facts(), trial.all_facts())
        self.assertEqual(len(lost), 2)

    def test_impact_of_an_unknown_fact_is_rejected(self):
        engine = Engine(Program.parse("p(1)."))
        with self.assertRaises(EvaluationError):
            engine.trial_without(("p", (2,)))


class RandomMaintenanceTests(unittest.TestCase):
    def test_random_mutations_always_match_a_rebuild(self):
        rng = random.Random(424242)
        for trial in range(24):
            source = random_source(rng)
            program = Program.parse(source, f"random{trial}.dl")
            engine = Engine(program)
            for step in range(6):
                if engine.edb and rng.random() < 0.55:
                    fact = rng.choice(sorted(engine.edb))
                    engine.remove_fact(fact[0], fact[1])
                else:
                    pred = rng.choice(sorted(program.arity))
                    row = tuple(rng.randrange(3) for _ in range(program.arity[pred]))
                    engine.add_fact(pred, row)
                expected = rebuild(program, engine.edb)
                if engine.all_facts() != expected.all_facts():
                    missing = sorted(expected.all_facts() - engine.all_facts())
                    extra = sorted(engine.all_facts() - expected.all_facts())
                    self.fail(
                        f"trial {trial} step {step} diverged\nprogram:\n{source}\n"
                        f"missing={missing}\nextra={extra}"
                    )


if __name__ == "__main__":
    unittest.main()
