#!/usr/bin/env python3
"""Retract one edge from a closure and compare with a full rebuild.

Run from anywhere:  python examples/incremental.py
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mendel import Engine, Program, bench, render  # noqa: E402

NODES = 140


def main() -> None:
    instance = bench.chain(NODES)
    engine = Engine(instance.program)
    total = sum(len(relation) for relation in engine.relations.values())
    print(f"chain of {NODES} nodes: {total:,} facts in {render.seconds(engine.stats.seconds)}")

    # The last edge: its support region is a slice of the closure.
    target = ("edge", (NODES - 2, NODES - 1))
    started = time.perf_counter()
    removed = engine.remove_fact(target[0], target[1])
    elapsed = time.perf_counter() - started

    trimmed = Program(
        [atom for atom in instance.program.facts if atom.as_fact() != target],
        instance.program.rules,
        name=instance.program.name,
    )
    started = time.perf_counter()
    rebuilt = Engine(trimmed)
    rebuild_time = time.perf_counter() - started

    print(f"\nretract edge({NODES - 2}, {NODES - 1})")
    print(f"  {len(removed):,} facts disappeared")
    print(f"  incremental maintenance   {render.seconds(elapsed)}")
    print(f"  rebuild from scratch      {render.seconds(rebuild_time)}")
    print(f"  identical to rebuild      {engine.all_facts() == rebuilt.all_facts()}")

    engine.add_fact(target[0], target[1])
    print(f"  after adding it back      {sum(len(r) for r in engine.relations.values()):,} facts")


if __name__ == "__main__":
    main()
