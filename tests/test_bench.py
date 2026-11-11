"""Benchmark generators and measurement."""

from __future__ import annotations

import unittest

from mendel import bench


class GeneratorTests(unittest.TestCase):
    def test_chain_shape(self):
        instance = bench.chain(5)
        self.assertEqual(len(instance.program.facts), 4)
        from mendel.engine import Engine

        engine = Engine(instance.program)
        # Every ordered pair i < j is reachable: 5 * 4 / 2.
        self.assertEqual(len(engine.facts_of("path")), 10)

    def test_grid_is_four_way(self):
        instance = bench.grid(2, 2)
        self.assertEqual(len(instance.program.facts), 8)
        from mendel.engine import Engine

        engine = Engine(instance.program)
        # A 2x2 grid is one cycle, so every node reaches every node.
        self.assertEqual(len(engine.facts_of("path")), 16)

    def test_random_graph_is_deterministic(self):
        first = bench.random_graph(12, 30, seed=5)
        second = bench.random_graph(12, 30, seed=5)
        self.assertEqual(
            [fact.as_fact() for fact in first.program.facts],
            [fact.as_fact() for fact in second.program.facts],
        )
        third = bench.random_graph(12, 30, seed=6)
        self.assertNotEqual(
            [fact.as_fact() for fact in first.program.facts],
            [fact.as_fact() for fact in third.program.facts],
        )

    def test_random_graph_has_no_self_loops_or_duplicates(self):
        instance = bench.random_graph(10, 40, seed=2)
        pairs = [fact.args for fact in instance.program.facts]
        self.assertEqual(len(pairs), len(set(pairs)))
        for left, right in pairs:
            self.assertNotEqual(left.value, right.value)  # type: ignore[union-attr]

    def test_family_and_policy_instances(self):
        family = bench.family()
        self.assertEqual(len(family.program.facts), 5)
        policy = bench.policy(users=4, resources=3)
        self.assertEqual(len(policy.program.facts), 4 + 3 + 4)

    def test_suites(self):
        self.assertEqual(set(bench.suite_names()), {"quick", "core", "deep"})
        self.assertEqual(len(bench.instances("quick")), 5)
        with self.assertRaises(KeyError):
            bench.instances("nope")


class MeasurementTests(unittest.TestCase):
    def test_run_one_counts(self):
        measurement = bench.run_one(bench.family())
        self.assertEqual(measurement.name, "family")
        self.assertEqual(measurement.given, 5)
        self.assertEqual(measurement.total, 16)
        self.assertEqual(measurement.derived, 11)
        self.assertGreater(measurement.rounds, 0)
        self.assertGreater(measurement.seconds, 0)

    def test_run_quick(self):
        measurements = bench.run("quick")
        self.assertEqual(len(measurements), 5)
        totals = bench.totals(measurements)
        self.assertEqual(totals["instances"], 5)
        self.assertGreater(totals["facts"], 0)
        self.assertEqual(totals["facts"], sum(m.total for m in measurements))

    def test_measurement_dict(self):
        payload = bench.run_one(bench.chain(4)).as_dict()
        self.assertEqual(payload["name"], "chain-4")
        self.assertIn("seconds", payload)
        self.assertIn("firings", payload)

    def test_incremental_leaf_matches_rebuild(self):
        result = bench.incremental(nodes=40, middle=False)
        self.assertTrue(result.identical)
        self.assertEqual(result.retracted, 40)
        self.assertEqual(result.rederived, 0)
        self.assertIn("leaf", result.name)

    def test_incremental_middle_matches_rebuild(self):
        result = bench.incremental(nodes=40, middle=True)
        self.assertTrue(result.identical)
        # 21 nodes on the left, 19 on the right, plus the edge itself.
        self.assertEqual(result.retracted, 21 * 19 + 1)
        self.assertIn("middle", result.name)


if __name__ == "__main__":
    unittest.main()
