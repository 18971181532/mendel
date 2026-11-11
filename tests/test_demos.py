"""Demos: each one must still make the claim it prints."""

from __future__ import annotations

import unittest

from mendel import bench, demos
from mendel.engine import Engine


class DemoTests(unittest.TestCase):
    def test_all_demos_run(self):
        results = demos.all_demos()
        self.assertEqual([demo.name for demo in results], list(demos.DEMO_NAMES))
        for demo in results:
            self.assertTrue(demo.title)
            self.assertTrue(demo.body.strip())

    def test_reach_demo_counts_match_a_fresh_engine(self):
        engine = Engine(bench.family().program)
        body = demos.get("reach").body
        self.assertIn(f"{len(engine.facts_of('ancestor'))} ancestor facts", body)
        self.assertIn(f"{len(engine.facts_of('sibling'))} sibling facts", body)
        self.assertIn("1 distinct proof", body)

    def test_why_demo_shows_both_sides(self):
        body = demos.get("why").body
        self.assertIn("no fact matches", body)
        self.assertIn('stuck at: parent("fay", "ada")', body)
        self.assertIn("control:", body)

    def test_incremental_demo_claims_identity(self):
        demo = demos.get("incremental")
        self.assertIn("identical to a rebuild", demo.body)
        self.assertIn("the last edge", demo.body)
        self.assertIn("the centre edge", demo.body)
        # Two trials, each with a timing comparison.
        self.assertEqual(demo.body.count("incremental "), 2)

    def test_policy_demo_shows_absences(self):
        body = demos.get("policy").body
        self.assertIn("denials, each justified by an absent grant", body)
        self.assertIn("(absent)", body)
        self.assertIn("not can(", body)

    def test_bench_demo_lists_every_instance(self):
        body = demos.get("bench").body
        for name in ("chain-60", "grid-6x6", "random-30-80", "family", "policy"):
            self.assertIn(name, body)
        self.assertIn("5 instances", body)

    def test_unknown_demo(self):
        with self.assertRaises(KeyError):
            demos.get("nope")


if __name__ == "__main__":
    unittest.main()
