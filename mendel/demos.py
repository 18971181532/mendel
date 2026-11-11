"""Self-checking demos.

Each demo prints numbers it actually measured, and the test suite asserts the
claims it makes: the retracted count matches the rederived set, the rebuilt
database is identical, the explained fact is genuinely in the relation.  A demo
that only prints prose is a demo that can quietly go stale.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List

from . import bench
from . import provenance as prov
from . import render
from .engine import Engine
from .program import Program

DEMO_NAMES = ("reach", "why", "incremental", "policy", "bench")


@dataclass
class Demo:
    name: str
    title: str
    body: str


def reach_demo() -> Demo:
    instance = bench.family()
    engine = Engine(instance.program)
    target = ("ancestor", ("ada", "fay"))
    tree = prov.build_tree(engine, target)
    assert tree is not None
    lines = [
        f"{len(engine.facts_of('parent'))} given parent facts over one small tree",
        f"{len(engine.facts_of('ancestor'))} ancestor facts derived in "
        f"{engine.stats.rounds} rounds ({render.seconds(engine.stats.seconds)})",
        "",
        f"why is {prov.fact_text(target)} true?",
        "",
    ]
    lines.extend(prov.format_tree(tree))
    lines.append("")
    lines.append(
        f"{len(prov.derivations(engine, target))} distinct proof found by backward search "
        f"(the graph is a tree, so the path is unique)"
    )
    lines.append(f"{len(engine.facts_of('sibling'))} sibling facts were derived as a side effect")
    return Demo("reach", "Reachability, with the derivation attached", "\n".join(lines))


def why_demo() -> Demo:
    instance = bench.family()
    engine = Engine(instance.program)
    missing = ("ancestor", ("fay", "ada"))
    report = prov.why_not(engine, missing)
    assert not report.exists
    lines = [
        "fay is the youngest person in the tree; nothing points away from her.",
        "",
    ]
    lines.extend(prov.format_why_not(report))
    lines.append("")
    existing = prov.format_why_not(prov.why_not(engine, ("ancestor", ("ada", "fay"))))
    lines.append(f"control: {existing[0]}")
    return Demo("why", "Why a fact is missing", "\n".join(lines))


def _retract_trial(nodes: int, middle: bool):
    instance = bench.chain(nodes)
    engine = Engine(instance.program)
    before = sum(len(rel) for rel in engine.relations.values())
    target = (
        ("edge", (nodes // 2, nodes // 2 + 1))
        if middle
        else ("edge", (nodes - 2, nodes - 1))
    )
    started = time.perf_counter()
    removed = engine.remove_fact(target[0], target[1])
    incremental = time.perf_counter() - started
    trimmed = Program(
        [a for a in instance.program.facts if a.as_fact() != target],
        instance.program.rules,
        name=instance.program.name,
    )
    started = time.perf_counter()
    rebuilt = Engine(trimmed)
    full = time.perf_counter() - started
    identical = engine.all_facts() == rebuilt.all_facts()
    assert identical, "incremental retraction disagreed with a rebuild"
    return instance, engine, before, target, removed, incremental, full


def incremental_demo() -> Demo:
    nodes = 160
    instance, engine, before, leaf, leaf_lost, leaf_time, leaf_full = _retract_trial(nodes, False)
    _, _, _, middle, middle_lost, middle_time, middle_full = _retract_trial(nodes, True)
    lines = [
        f"chain of {nodes} nodes: {before:,} facts, built in "
        f"{render.seconds(engine.stats.seconds)}",
        "",
        f"retract {prov.fact_text(leaf)}   (the last edge)",
        f"  {len(leaf_lost):,} facts disappear; "
        f"incremental {render.seconds(leaf_time)}, rebuild {render.seconds(leaf_full)} "
        f"({leaf_full / leaf_time:.1f}x slower to rebuild)",
        "",
        f"retract {prov.fact_text(middle)}   (the centre edge)",
        f"  {len(middle_lost):,} facts disappear; "
        f"incremental {render.seconds(middle_time)}, rebuild {render.seconds(middle_full)} "
        f"({middle_full / middle_time:.1f}x)",
        "",
        "both results are identical to a rebuild. The cost tracks the support",
        "region, not the size of the database: when half the closure rests on the",
        "retracted edge, re-deriving it is as expensive as starting over, and the",
        "engine does not pretend otherwise.",
    ]
    return Demo("incremental", "Retracting one fact, without recomputing the world", "\n".join(lines))


def policy_demo() -> Demo:
    instance = bench.policy(users=6, resources=4)
    engine = Engine(instance.program)
    denied = sorted(engine.facts_of("denied"))
    target = ("denied", denied[0]) if denied else None
    lines = [
        f"{len(engine.facts_of('user'))} users, {len(engine.facts_of('resource'))} resources",
        f"{len(engine.facts_of('can'))} grants, {len(denied)} denials",
        f"{len(engine.program.strata)} strata, one per strongly connected component; "
        f"'denied' must follow 'can' because it negates it",
        "",
        "denials, each justified by an absent grant:",
        render.table(
            ["user", "resource"],
            [[row[0], row[1]] for row in denied],
        ),
    ]
    if target is not None:
        lines.append("")
        lines.append(f"why is {prov.fact_text(target)} true?")
        lines.append("")
        tree = prov.build_tree(engine, target)
        assert tree is not None
        lines.extend(prov.format_tree(tree))
    return Demo("policy", "Access control where 'no' is a derived answer", "\n".join(lines))


def bench_demo() -> Demo:
    measurements = bench.run("quick")
    total = bench.totals(measurements)
    rows = [
        [
            m.name,
            render.number(m.given),
            render.number(m.total),
            render.number(m.derived),
            render.number(m.rounds),
            render.seconds(m.seconds),
        ]
        for m in measurements
    ]
    lines = [
        render.table(
            ["instance", "given", "facts", "derived", "rounds", "time"],
            rows,
            aligns=["left", "right", "right", "right", "right", "right"],
        ),
        "",
        f"{total['instances']} instances, {render.number(int(total['facts']))} facts, "
        f"{render.seconds(total['seconds'])} total",
    ]
    return Demo("bench", "The quick suite", "\n".join(lines))


_BUILDERS = {
    "reach": reach_demo,
    "why": why_demo,
    "incremental": incremental_demo,
    "policy": policy_demo,
    "bench": bench_demo,
}


def get(name: str) -> Demo:
    try:
        builder = _BUILDERS[name]
    except KeyError:
        raise KeyError(f"unknown demo {name!r}; choose from {', '.join(DEMO_NAMES)}") from None
    return builder()


def all_demos() -> List[Demo]:
    return [get(name) for name in DEMO_NAMES]
