"""Evaluation tests, including a brute-force cross-check.

The reference evaluator below is deliberately naive: it enumerates every
variable assignment over the active domain and repeats until nothing changes.
It shares no code with the engine's semi-naive loop, so agreement between the
two is evidence, not a tautology.
"""

from __future__ import annotations

import itertools
import random
import unittest
from typing import Dict, List, Set, Tuple

from mendel.engine import Engine, Fact
from mendel.program import Program
from mendel.syntax import (
    Atom,
    Compare,
    Const,
    Goal,
    Literal,
    NegGoal,
    Rule,
    Value,
    Var,
    literal_vars,
    parse_program,
)

FAMILY = """
parent("ada", "bob").
parent("ada", "cy").
parent("bob", "dee").
parent("cy", "eve").
parent("dee", "fay").
ancestor(?x, ?y) :- parent(?x, ?y).
@step ancestor(?x, ?z) :- parent(?x, ?y), ancestor(?y, ?z).
sibling(?x, ?y) :- parent(?p, ?x), parent(?p, ?y), ?x != ?y.
"""

POLICY = """
user("ada"). user("bob"). user("cy").
role("ada", "admin"). role("bob", "dev"). role("cy", "viewer").
resource("prod"). resource("staging").
can(?u, ?r) :- role(?u, "admin"), resource(?r).
can(?u, ?r) :- role(?u, "dev"), resource(?r), ?r != "prod".
can(?u, ?r) :- role(?u, "viewer"), resource(?r), ?r = "staging".
@deny denied(?u, ?r) :- user(?u), resource(?r), not can(?u, ?r).
"""


# --------------------------------------------------------------------------
# Reference evaluator
# --------------------------------------------------------------------------


def _ground(atom: Atom, env: Dict[str, Value]) -> Fact:
    args: List[Value] = []
    for term in atom.args:
        args.append(term.value if isinstance(term, Const) else env[term.name])
    return atom.pred, tuple(args)


def _value(term, env: Dict[str, Value]) -> Value:
    return term.value if isinstance(term, Const) else env[term.name]


def _holds(literal: Literal, facts: Set[Fact], env: Dict[str, Value]) -> bool:
    if isinstance(literal, Goal):
        return _ground(literal.atom, env) in facts
    if isinstance(literal, NegGoal):
        return _ground(literal.atom, env) not in facts
    assert isinstance(literal, Compare)
    return literal.evaluate(_value(literal.left, env), _value(literal.right, env))


def naive_model(program: Program) -> Set[Fact]:
    facts: Set[Fact] = {atom.as_fact() for atom in program.facts}
    constants: Set[Value] = set()
    for atom in program.facts:
        for arg in atom.args:
            constants.add(arg.value)  # type: ignore[union-attr]
    for rule in program.rules:
        for term in rule.head.args:
            if isinstance(term, Const):
                constants.add(term.value)
        for literal in rule.body:
            assert not isinstance(literal, NegGoal), "reference model only handles positive programs"
            if isinstance(literal, Compare):
                for term in (literal.left, literal.right):
                    if isinstance(term, Const):
                        constants.add(term.value)
            else:
                for term in literal.atom.args:
                    if isinstance(term, Const):
                        constants.add(term.value)
    domain = sorted(constants)
    changed = True
    while changed:
        changed = False
        for rule in program.rules:
            names: List[str] = list(rule.head.variables())
            for literal in rule.body:
                for name in literal_vars(literal):
                    if name not in names:
                        names.append(name)
            for values in itertools.product(domain, repeat=len(names)):
                env = dict(zip(names, values))
                if all(_holds(literal, facts, env) for literal in rule.body):
                    fact = _ground(rule.head, env)
                    if fact not in facts:
                        facts.add(fact)
                        changed = True
    return facts


# --------------------------------------------------------------------------
# Random positive programs
# --------------------------------------------------------------------------

TEMPLATES = [
    "p(?x, ?z) :- e(?x, ?y), p(?y, ?z).",
    "p(?x, ?z) :- p(?x, ?y), p(?y, ?z).",
    "q(?x) :- p(?x, ?y).",
    "q(?x) :- p(?y, ?x), e(?y, ?y).",
    "p(?x, ?y) :- p(?y, ?x).",
    "p(0, ?y) :- q(?y).",
    "p(?x, ?x) :- q(?x).",
    "q(?y) :- p(?x, ?y), ?y != 2.",
    "p(?x, ?y) :- e(?x, ?y), ?x != ?y.",
]


def random_source(rng: random.Random) -> str:
    lines = ["p(?x, ?y) :- e(?x, ?y)."]
    lines.extend(rng.sample(TEMPLATES, rng.randint(1, 3)))
    facts = [
        f"e({a}, {b})."
        for a in range(3)
        for b in range(3)
        if rng.random() < 0.45
    ]
    if not facts:
        facts = ["e(0, 1)."]
    return "\n".join(facts + lines) + "\n"


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


class BasicEvaluationTests(unittest.TestCase):
    def test_family_closure(self):
        engine = Engine(Program.parse(FAMILY, "family.dl"))
        self.assertEqual(
            engine.facts_of("ancestor"),
            {
                ("ada", "bob"), ("ada", "cy"), ("ada", "dee"), ("ada", "eve"), ("ada", "fay"),
                ("bob", "dee"), ("bob", "fay"),
                ("cy", "eve"),
                ("dee", "fay"),
            },
        )
        self.assertEqual(engine.facts_of("sibling"), {("bob", "cy"), ("cy", "bob")})

    def test_constants_and_repeated_variables(self):
        program = Program.parse(
            'e(1, 1). e(1, 2). self(?x) :- e(?x, ?x). pair(?x, ?x) :- e(?x, ?y).'
        )
        engine = Engine(program)
        self.assertEqual(engine.facts_of("self"), {(1,)})
        self.assertEqual(engine.facts_of("pair"), {(1, 1)})

    def test_comparisons(self):
        program = Program.parse(
            "n(1). n(2). n(3). small(?x) :- n(?x), ?x < 3. big(?x) :- n(?x), ?x >= 2."
        )
        engine = Engine(program)
        self.assertEqual(engine.facts_of("small"), {(1,), (2,)})
        self.assertEqual(engine.facts_of("big"), {(2,), (3,)})

    def test_assignment(self):
        program = Program.parse("n(1). n(2). alias(?y) :- n(?x), ?y = ?x, ?y > 1.")
        engine = Engine(program)
        self.assertEqual(engine.facts_of("alias"), {(2,)})

    def test_negation_and_strata(self):
        program = Program.parse(POLICY, "policy.dl")
        engine = Engine(program)
        self.assertEqual(
            engine.facts_of("can"),
            {("ada", "prod"), ("ada", "staging"), ("bob", "staging"), ("cy", "staging")},
        )
        self.assertEqual(engine.facts_of("denied"), {("bob", "prod"), ("cy", "prod")})
        self.assertLess(
            [s.preds for s in program.strata].index(("can",)),
            [s.preds for s in program.strata].index(("denied",)),
        )

    def test_zero_arity_predicate(self):
        program = Program.parse('flag(). on() :- flag(). off() :- not on().')
        engine = Engine(program)
        self.assertEqual(engine.facts_of("flag"), {()})
        self.assertEqual(engine.facts_of("on"), {()})
        self.assertEqual(engine.facts_of("off"), set())

    def test_empty_program(self):
        engine = Engine(Program.parse(""))
        self.assertEqual(engine.all_facts(), set())
        self.assertEqual(engine.counts(), {})

    def test_no_facts_means_empty_relations(self):
        engine = Engine(Program.parse("p(?x) :- q(?x)."))
        self.assertEqual(engine.facts_of("p"), set())

    def test_derived_counter_matches_new_facts(self):
        program = Program.parse(FAMILY, "family.dl")
        engine = Engine(program)
        derived = sum(len(rel) for rel in engine.relations.values()) - len(program.facts)
        self.assertEqual(engine.stats.derived, derived)

    def test_mixed_type_comparison_is_a_located_error(self):
        from mendel.syntax import EvaluationError

        engine_program = Program.parse('n(1). p(?x) :- n(?x), ?x < "a".')
        with self.assertRaises(EvaluationError) as caught:
            Engine(engine_program)
        self.assertIn("cannot compare", str(caught.exception))


class MutationTests(unittest.TestCase):
    def test_add_fact_propagates(self):
        engine = Engine(Program.parse(FAMILY, "family.dl"))
        self.assertTrue(engine.add_fact("parent", ("fay", "gus")))
        self.assertIn(("fay", "gus"), engine.facts_of("ancestor"))
        self.assertIn(("dee", "gus"), engine.facts_of("ancestor"))
        self.assertIn(("ada", "gus"), engine.facts_of("ancestor"))
        self.assertFalse(engine.add_fact("parent", ("fay", "gus")))

    def test_remove_fact_retracts_consequences(self):
        engine = Engine(Program.parse(FAMILY, "family.dl"))
        gone = engine.remove_fact("parent", ("bob", "dee"))
        self.assertIn(("parent", ("bob", "dee")), gone)
        self.assertNotIn(("ada", "fay"), engine.facts_of("ancestor"))
        self.assertTrue(engine.add_fact("parent", ("bob", "dee")))
        self.assertIn(("ada", "fay"), engine.facts_of("ancestor"))

    def test_remove_derived_fact_is_rejected(self):
        from mendel.syntax import EvaluationError

        engine = Engine(Program.parse(FAMILY, "family.dl"))
        with self.assertRaises(EvaluationError) as caught:
            engine.remove_fact("ancestor", ("ada", "fay"))
        self.assertIn("derived", str(caught.exception))

    def test_unknown_predicate_and_bad_arity(self):
        from mendel.syntax import EvaluationError

        engine = Engine(Program.parse("p(1)."))
        with self.assertRaises(EvaluationError):
            engine.add_fact("nope", (1,))
        with self.assertRaises(EvaluationError):
            engine.add_fact("p", (1, 2))

    def test_promoting_a_derived_fact_removes_its_support(self):
        engine = Engine(Program.parse("e(1, 2). p(?x) :- e(?x, ?y)."))
        self.assertIsNotNone(engine.support_of(("p", (1,))))
        self.assertTrue(engine.add_fact("p", (1,)))
        self.assertIsNone(engine.support_of(("p", (1,))))
        self.assertIn(("p", (1,)), engine.edb)


class BruteForceTests(unittest.TestCase):
    def test_hundred_random_programs_match_the_reference(self):
        rng = random.Random(20261111)
        for trial in range(100):
            source = random_source(rng)
            program = Program.parse(source, f"random{trial}.dl")
            expected = naive_model(program)
            actual = Engine(program).all_facts()
            if expected != actual:
                missing = sorted(expected - actual)
                extra = sorted(actual - expected)
                self.fail(
                    f"trial {trial} disagrees\nprogram:\n{source}\n"
                    f"missing={missing}\nextra={extra}"
                )

    def test_reference_and_engine_agree_on_the_family_program(self):
        program = Program.parse(FAMILY, "family.dl")
        self.assertEqual(naive_model(program), Engine(program).all_facts())


if __name__ == "__main__":
    unittest.main()
