"""End-to-end CLI tests: output, JSON and exit codes."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from mendel import cli

FAMILY = """
parent("ada", "bob").
parent("ada", "cy").
parent("bob", "dee").
parent("cy", "eve").
parent("dee", "fay").
ancestor(?x, ?y) :- parent(?x, ?y).
@step ancestor(?x, ?z) :- parent(?x, ?y), ancestor(?y, ?z).
"""

DIAMOND = """
parent("ada", "bob").
parent("ada", "cy").
parent("bob", "dee").
parent("cy", "dee").
ancestor(?x, ?y) :- parent(?x, ?y).
@step ancestor(?x, ?z) :- parent(?x, ?y), ancestor(?y, ?z).
"""


class CliTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def write(self, name: str, content: str) -> str:
        path = os.path.join(self.directory.name, name)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        return path

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    # -- run ------------------------------------------------------------
    def test_run_prints_the_summary(self):
        path = self.write("family.dl", FAMILY)
        code, out, _ = self.run_cli("run", path)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("program   family.dl", out)
        self.assertIn("facts     14 (5 given, 9 derived)", out)
        self.assertIn("strata", out)

    def test_run_json(self):
        path = self.write("family.dl", FAMILY)
        code, out, _ = self.run_cli("run", path, "--json")
        payload = json.loads(out)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(payload["given"], 5)
        self.assertEqual(payload["derived"], 9)
        self.assertEqual(payload["predicates"]["ancestor"], 9)

    def test_goal_with_answers(self):
        path = self.write("family.dl", FAMILY)
        code, out, _ = self.run_cli("run", path, "--goal", 'ancestor("ada", ?y)')
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("5 answers", out)
        self.assertIn('ancestor("ada", "fay")', out)

    def test_a_bare_query_does_not_print_the_summary(self):
        path = self.write("family.dl", FAMILY)
        _, out, _ = self.run_cli("run", path, "--goal", 'ancestor("ada", ?y)')
        self.assertNotIn("program   ", out)
        self.assertNotIn("firings", out)
        # ... but asking for stats brings it back.
        _, out, _ = self.run_cli("run", path, "--goal", 'ancestor("ada", ?y)', "--stats")
        self.assertIn("program   ", out)

    def test_goal_without_answers_exits_three(self):
        path = self.write("family.dl", FAMILY)
        code, out, _ = self.run_cli("run", path, "--goal", 'ancestor("fay", ?y)')
        self.assertEqual(code, cli.EXIT_NO)
        self.assertIn("no answers", out)

    def test_unknown_predicate_in_a_goal(self):
        path = self.write("family.dl", FAMILY)
        code, _, err = self.run_cli("run", path, "--goal", "nope(1)")
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("unknown predicate", err)

    def test_stats_table(self):
        path = self.write("family.dl", FAMILY)
        code, out, _ = self.run_cli("run", path, "--stats")
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("predicate", out)
        self.assertIn("ancestor", out)

    def test_add_and_remove_flags(self):
        path = self.write("family.dl", FAMILY)
        code, out, _ = self.run_cli("run", path, "--remove", 'parent("bob", "dee")')
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("- parent(\"bob\", \"dee\")", out)
        # The count is the net change in true facts: the retracted edge plus
        # the four ancestor facts that rested on it.
        self.assertIn("5 facts removed", out)
        code, out, _ = self.run_cli("run", path, "--add", 'parent("fay", "gus")')
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("+ parent(\"fay\", \"gus\")", out)
        self.assertIn("5 facts added", out)

    # -- explain ---------------------------------------------------------
    def test_explain(self):
        path = self.write("family.dl", FAMILY)
        code, out, _ = self.run_cli("explain", path, 'ancestor("ada", "fay")')
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn('ancestor("ada", "fay")', out)
        self.assertIn("[@step]", out)
        self.assertIn('parent("ada", "bob")', out)

    def test_explain_missing_fact(self):
        path = self.write("family.dl", FAMILY)
        code, out, _ = self.run_cli("explain", path, 'ancestor("fay", "ada")')
        self.assertEqual(code, cli.EXIT_NO)
        self.assertIn("not derivable", out)

    def test_explain_all_finds_alternative_proofs(self):
        path = self.write("diamond.dl", DIAMOND)
        code, out, _ = self.run_cli("explain", path, 'ancestor("ada", "dee")', "--all")
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("derivation 1 of 2", out)
        self.assertIn("derivation 2 of 2", out)

    def test_explain_json(self):
        path = self.write("family.dl", FAMILY)
        code, out, _ = self.run_cli("explain", path, 'ancestor("ada", "fay")', "--json")
        payload = json.loads(out)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertTrue(payload["derivable"])
        self.assertEqual(payload["tree"]["rule"], "step")
        self.assertEqual(len(payload["tree"]["premises"]), 2)

    # -- why-not / impact -------------------------------------------------
    def test_why_not(self):
        path = self.write("family.dl", FAMILY)
        code, out, _ = self.run_cli("why-not", path, 'ancestor("fay", "ada")')
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("no fact matches", out)
        self.assertIn("stuck at", out)

    def test_why_not_on_an_existing_fact(self):
        path = self.write("family.dl", FAMILY)
        code, out, _ = self.run_cli("why-not", path, 'ancestor("ada", "fay")')
        self.assertEqual(code, cli.EXIT_NO)
        self.assertIn("is derivable", out)

    def test_why_not_json(self):
        path = self.write("family.dl", FAMILY)
        code, out, _ = self.run_cli("why-not", path, 'ancestor("fay", "ada")', "--json")
        payload = json.loads(out)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertFalse(payload["derivable"])
        self.assertGreaterEqual(len(payload["attempts"]), 1)

    def test_impact_exact(self):
        path = self.write("family.dl", FAMILY)
        code, out, _ = self.run_cli("impact", path, 'parent("bob", "dee")', "--exact")
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("4 facts", out)
        self.assertIn("ancestor", out)

    def test_impact_rejects_a_derived_fact(self):
        path = self.write("family.dl", FAMILY)
        code, _, err = self.run_cli("impact", path, 'ancestor("ada", "fay")')
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("not a given fact", err)

    # -- plan / bench / demo ----------------------------------------------
    def test_plan_lists_strata_and_steps(self):
        path = self.write("family.dl", FAMILY)
        code, out, _ = self.run_cli("plan", path)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("stratum 0", out)
        self.assertIn("@step", out)
        self.assertIn("bound", out)

    def test_bench_json(self):
        code, out, _ = self.run_cli("bench", "quick", "--json", "--incremental")
        payload = json.loads(out)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(len(payload["instances"]), 5)
        self.assertEqual(len(payload["incremental"]), 2)
        self.assertTrue(all(item["identical"] for item in payload["incremental"]))

    def test_demo_list_and_one_demo(self):
        code, out, _ = self.run_cli("demo")
        self.assertEqual(code, cli.EXIT_OK)
        for name in ("reach", "why", "incremental", "policy", "bench"):
            self.assertIn(name, out)
        code, out, _ = self.run_cli("demo", "reach")
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("Reachability", out)

    # -- failures ----------------------------------------------------------
    def test_missing_file(self):
        code, _, err = self.run_cli("run", os.path.join(self.directory.name, "nope.dl"))
        # "my file is missing" (1) is a different problem from "my rules are
        # wrong" (2), and both differ from "the answer is no" (3).
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn("cannot read", err)

    def test_a_file_that_is_not_utf8(self):
        path = os.path.join(self.directory.name, "latin1.dl")
        with open(path, "wb") as handle:
            handle.write(b'parent("caf\xe9", "bob").\n')
        code, _, err = self.run_cli("run", path)
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn("not valid UTF-8", err)

    def test_parse_error_is_located(self):
        path = self.write("bad.dl", "p(?x).\n")
        code, _, err = self.run_cli("run", path)
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("bad.dl:1", err)

    def test_version(self):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as caught:
            cli.main(["--version"])
        self.assertEqual(caught.exception.code, 0)
        self.assertIn("mendel", out.getvalue())


if __name__ == "__main__":
    unittest.main()
