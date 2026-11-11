"""Parser and term-model tests."""

from __future__ import annotations

import unittest

from mendel.syntax import (
    Atom,
    Compare,
    Const,
    Goal,
    NegGoal,
    ParseError,
    Rule,
    Var,
    format_value,
    parse_atom,
    parse_program,
    tokenize,
)


class TermTests(unittest.TestCase):
    def test_str_forms(self):
        self.assertEqual(str(Var("x")), "?x")
        self.assertEqual(str(Const("a")), '"a"')
        self.assertEqual(str(Const(-4)), "-4")
        self.assertEqual(str(Atom("p", (Var("x"), Const(1)))), "p(?x, 1)")

    def test_format_value_round_trips_through_the_parser(self):
        for value in ("plain", 'has "quotes"', "back\\slash", "tab\there", -17, 0):
            atom = parse_atom(f"p({format_value(value)})")
            self.assertEqual(atom.args[0], Const(value))

    def test_string_escapes(self):
        atom = parse_atom(r'p("a\"b", "line\n", "\\")')
        self.assertEqual(atom.args[0].value, 'a"b')
        self.assertEqual(atom.args[1].value, "line\n")
        self.assertEqual(atom.args[2].value, "\\")

    def test_negative_integer_is_one_token(self):
        atom = parse_atom("p(-12)")
        self.assertEqual(atom.args, (Const(-12),))

    def test_zero_arity(self):
        atom = parse_atom("flag()")
        self.assertEqual(atom.args, ())
        self.assertTrue(atom.is_ground())

    def test_variables_deduplicate_in_order(self):
        atom = parse_atom("p(?y, ?x, ?y)")
        self.assertEqual(atom.variables(), ("y", "x"))

    def test_ground_fact(self):
        self.assertEqual(parse_atom('p("a", 2)').as_fact(), ("p", ("a", 2)))

    def test_non_ground_atom_rejects_as_fact(self):
        with self.assertRaises(ValueError):
            parse_atom("p(?x)").as_fact()


class ParserTests(unittest.TestCase):
    def test_facts_and_rules(self):
        facts, rules = parse_program(
            """
            # a comment
            parent("ada", "bob").
            parent("bob", "cy").
            ancestor(?x, ?y) :- parent(?x, ?y).
            @step ancestor(?x, ?z) :- parent(?x, ?y), ancestor(?y, ?z).
            """
        )
        self.assertEqual(len(facts), 2)
        self.assertEqual(len(rules), 2)
        self.assertEqual(rules[1].name, "step")
        self.assertIsInstance(rules[1].body[0], Goal)
        self.assertEqual(rules[1].line, 6)

    def test_percent_comments_and_blank_lines(self):
        facts, rules = parse_program("% leading\n\n p(1). % trailing\n")
        self.assertEqual(len(facts), 1)

    def test_negation_and_comparison_literals(self):
        _, rules = parse_program('a(?x) :- b(?x), not c(?x), ?x != 3, ?x < 10.')
        kinds = [type(literal) for literal in rules[0].body]
        self.assertEqual(kinds, [Goal, NegGoal, Compare, Compare])
        self.assertEqual(rules[0].body[2].op, "!=")

    def test_rule_str_round_trip(self):
        _, rules = parse_program('@step a(?x, ?z) :- b(?x, ?y), c(?y, ?z), ?x != ?z.')
        text = str(rules[0])
        _, again = parse_program(text)
        self.assertEqual(again[0].name, "step")
        self.assertEqual(again[0].head, rules[0].head)
        self.assertEqual(again[0].body, rules[0].body)

    def test_fact_with_variable_requires_a_body(self):
        with self.assertRaises(ParseError) as caught:
            parse_program("p(?x).")
        self.assertIn("has variables", str(caught.exception))

    def test_missing_full_stop(self):
        with self.assertRaises(ParseError):
            parse_program("p(1)")

    def test_label_on_a_fact_is_rejected(self):
        with self.assertRaises(ParseError):
            parse_program('@oops p(1).')

    def test_empty_body_is_rejected(self):
        with self.assertRaises(ParseError):
            parse_program("p(1) :- .")

    def test_unexpected_character_reports_position(self):
        with self.assertRaises(ParseError) as caught:
            parse_program("p(1) $\n")
        self.assertEqual(caught.exception.line, 1)
        self.assertIn("unexpected character", str(caught.exception))

    def test_duplicate_label_is_rejected_by_the_program(self):
        from mendel.program import Program

        with self.assertRaises(ParseError) as caught:
            Program.parse("@same p(1) :- q(1). @same r(2) :- q(2).")
        self.assertIn("duplicate rule label", str(caught.exception))

    def test_trailing_input_after_goal(self):
        with self.assertRaises(ParseError) as caught:
            parse_atom("p(1) q(2)")
        self.assertIn("trailing input", str(caught.exception))

    def test_tokenize_tracks_lines(self):
        tokens = tokenize("a(1).\nb(2).")
        lines = [token[2] for token in tokens]
        self.assertEqual(lines[0], 1)
        self.assertEqual(lines[-1], 2)

    def test_comparison_operators_lex_longest_first(self):
        _, rules = parse_program("p(?x) :- q(?x), ?x <= 2, ?x >= 0.")
        self.assertEqual(rules[0].body[1].op, "<=")
        self.assertEqual(rules[0].body[2].op, ">=")


if __name__ == "__main__":
    unittest.main()
