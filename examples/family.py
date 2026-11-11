#!/usr/bin/env python3
"""Evaluate a program and print the derivation of one fact.

Run from anywhere:  python examples/family.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mendel import Engine, Program  # noqa: E402
from mendel.provenance import (  # noqa: E402
    build_tree,
    derivations,
    fact_text,
    format_tree,
)

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> None:
    with open(os.path.join(HERE, "data", "family.dl"), encoding="utf-8") as handle:
        program = Program.parse(handle.read(), "family.dl")
    engine = Engine(program)
    print(
        f"{len(engine.facts_of('parent'))} given parent facts; "
        f"{len(engine.facts_of('ancestor'))} ancestor facts derived in "
        f"{engine.stats.rounds} rounds"
    )

    target = ("ancestor", ("ada", "fay"))
    print(f"\n{fact_text(target)} is true because:")
    tree = build_tree(engine, target)
    assert tree is not None
    print("\n".join(format_tree(tree)))

    proofs = derivations(engine, target)
    print(f"\nbackward search found {len(proofs)} proof of this fact")
    print(f"{len(engine.facts_of('sibling'))} sibling facts were derived as well")


if __name__ == "__main__":
    main()
