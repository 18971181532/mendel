#!/usr/bin/env python3
"""Ask why a permission is missing, and why a denial holds.

Run from anywhere:  python examples/why_not.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mendel import Engine, Program, parse_atom  # noqa: E402
from mendel.provenance import (  # noqa: E402
    build_tree,
    fact_text,
    format_tree,
    format_why_not,
    why_not,
)

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> None:
    with open(os.path.join(HERE, "data", "policy.dl"), encoding="utf-8") as handle:
        program = Program.parse(handle.read(), "policy.dl")
    engine = Engine(program)

    print(f"{len(engine.facts_of('can'))} grants, {len(engine.facts_of('denied'))} denials\n")

    missing = ("can", ("cy", "prod"))
    print(f"why is {fact_text(missing)} missing?")
    atom = parse_atom('can("cy", "prod")')
    assert atom.is_ground()
    print("\n".join(format_why_not(why_not(engine, atom.as_fact()))))

    target = ("denied", ("cy", "prod"))
    print(f"\nwhy does {fact_text(target)} hold?")
    tree = build_tree(engine, target)
    assert tree is not None
    print("\n".join(format_tree(tree)))


if __name__ == "__main__":
    main()
