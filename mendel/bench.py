"""Benchmark instances and measurement.

The generators are deliberately small and deterministic.  A benchmark that
takes minutes does not get run, and a benchmark with a random seed that changes
does not produce a number you can compare across commits.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Dict, List, Sequence

from .engine import Engine
from .program import Program
from .syntax import Atom, Const, parse_program

TRANSITIVE = """
path(?x, ?y) :- edge(?x, ?y).
@recursive path(?x, ?z) :- edge(?x, ?y), path(?y, ?z).
"""


@dataclass
class Instance:
    name: str
    program: Program
    note: str = ""


@dataclass
class Measurement:
    name: str
    given: int
    total: int
    derived: int
    rounds: int
    firings: int
    seconds: float

    def as_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "given": self.given,
            "total": self.total,
            "derived": self.derived,
            "rounds": self.rounds,
            "firings": self.firings,
            "seconds": round(self.seconds, 6),
        }


@dataclass
class IncrementalMeasurement:
    name: str
    full_seconds: float
    incremental_seconds: float
    retracted: int
    rederived: int
    identical: bool
    fact: str = ""

    def as_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "full_seconds": round(self.full_seconds, 6),
            "incremental_seconds": round(self.incremental_seconds, 6),
            "retracted": self.retracted,
            "rederived": self.rederived,
            "identical": self.identical,
            "fact": self.fact,
        }


def _rules(text: str):
    return parse_program(text)[1]


def _edges(pairs: Sequence[tuple]) -> List[Atom]:
    return [Atom("edge", (Const(a), Const(b))) for a, b in pairs]


def chain(n: int) -> Instance:
    pairs = [(i, i + 1) for i in range(n - 1)]
    program = Program(_edges(pairs), _rules(TRANSITIVE), f"chain({n})")
    return Instance(f"chain-{n}", program, f"{n} nodes in a line")


def grid(width: int, height: int) -> Instance:
    pairs: List[tuple] = []
    for y in range(height):
        for x in range(width):
            here = y * width + x
            if x + 1 < width:
                pairs.append((here, here + 1))
                pairs.append((here + 1, here))
            if y + 1 < height:
                pairs.append((here, here + width))
                pairs.append((here + width, here))
    program = Program(_edges(pairs), _rules(TRANSITIVE), f"grid({width}x{height})")
    return Instance(f"grid-{width}x{height}", program, f"{width * height} nodes, four-way")


def random_graph(nodes: int, edges: int, seed: int = 1) -> Instance:
    rng = random.Random(seed)
    seen = set()
    pairs: List[tuple] = []
    while len(pairs) < edges:
        a = rng.randrange(nodes)
        b = rng.randrange(nodes)
        if a == b or (a, b) in seen:
            continue
        seen.add((a, b))
        pairs.append((a, b))
    program = Program(_edges(pairs), _rules(TRANSITIVE), f"random({nodes},{edges})")
    return Instance(f"random-{nodes}-{edges}", program, f"seed {seed}")


def family() -> Instance:
    facts = [
        Atom("parent", (Const("ada"), Const("bob"))),
        Atom("parent", (Const("ada"), Const("cy"))),
        Atom("parent", (Const("bob"), Const("dee"))),
        Atom("parent", (Const("cy"), Const("eve"))),
        Atom("parent", (Const("dee"), Const("fay"))),
    ]
    rules = _rules(
        """
        ancestor(?x, ?y) :- parent(?x, ?y).
        @step ancestor(?x, ?z) :- parent(?x, ?y), ancestor(?y, ?z).
        sibling(?x, ?y) :- parent(?p, ?x), parent(?p, ?y), ?x != ?y.
        """
    )
    return Instance("family", Program(facts, rules, "family"), "one small tree")


def policy(users: int = 6, resources: int = 4) -> Instance:
    facts: List[Atom] = []
    for index in range(users):
        name = f"u{index}"
        facts.append(Atom("user", (Const(name),)))
        role = ("admin" if index == 0 else "dev" if index % 2 else "viewer")
        facts.append(Atom("role", (Const(name), Const(role))))
    for index in range(resources):
        facts.append(Atom("resource", (Const(f"r{index}"),)))
    rules = _rules(
        """
        can(?u, ?r) :- role(?u, "admin"), resource(?r).
        can(?u, ?r) :- role(?u, "dev"), resource(?r), ?r != "r0".
        can(?u, ?r) :- role(?u, "viewer"), resource(?r), ?r = "r0".
        denied(?u, ?r) :- user(?u), resource(?r), not can(?u, ?r).
        """
    )
    return Instance("policy", Program(facts, rules, "policy"), "negation over three roles")


SUITES: Dict[str, List[Instance]] = {
    "quick": [chain(60), grid(6, 6), random_graph(30, 80, seed=7), family(), policy()],
    "core": [chain(200), grid(12, 12), random_graph(80, 240, seed=11), policy(users=40, resources=12)],
    "deep": [chain(600), grid(20, 20), random_graph(200, 900, seed=3)],
}


def suite_names() -> List[str]:
    return list(SUITES)


def instances(suite: str) -> List[Instance]:
    try:
        return SUITES[suite]
    except KeyError:
        raise KeyError(f"unknown suite {suite!r}; choose from {', '.join(SUITES)}") from None


def run(suite: str) -> List[Measurement]:
    out: List[Measurement] = []
    for instance in instances(suite):
        out.append(run_one(instance))
    return out


def run_one(instance: Instance) -> Measurement:
    program = instance.program
    given = len(program.facts)
    started = time.perf_counter()
    engine = Engine(program)
    seconds = time.perf_counter() - started
    total = sum(len(rel) for rel in engine.relations.values())
    return Measurement(
        name=instance.name,
        given=given,
        total=total,
        derived=total - given,
        rounds=engine.stats.rounds,
        firings=engine.stats.firings,
        seconds=seconds,
    )


def incremental(nodes: int = 200, middle: bool = False) -> IncrementalMeasurement:
    """Build a closure, retract one edge, and compare with a rebuild.

    ``middle=False`` retracts the last edge, whose support region is a slice of
    the closure.  ``middle=True`` retracts the centre edge, where half the
    closure depends on it -- the case where DRed has no advantage and the
    benchmark says so.
    """
    instance = chain(nodes)
    engine = Engine(instance.program)
    target = (
        ("edge", (nodes // 2, nodes // 2 + 1))
        if middle
        else ("edge", (nodes - 2, nodes - 1))
    )

    started = time.perf_counter()
    removed = engine.remove_fact(target[0], target[1])
    incremental_seconds = time.perf_counter() - started

    trimmed = Program(
        [a for a in instance.program.facts if a.as_fact() != target],
        instance.program.rules,
        name=instance.program.name,
    )
    started = time.perf_counter()
    rebuilt = Engine(trimmed)
    full_seconds = time.perf_counter() - started

    current = engine.all_facts()
    identical = current == rebuilt.all_facts()
    return IncrementalMeasurement(
        name=f"chain-{nodes}/{'middle' if middle else 'leaf'}",
        full_seconds=full_seconds,
        incremental_seconds=incremental_seconds,
        retracted=len(removed),
        rederived=sum(1 for fact in removed if fact in current),
        identical=identical,
        fact=f"edge({target[1][0]}, {target[1][1]})",
    )


def totals(measurements: Sequence[Measurement]) -> Dict[str, float]:
    return {
        "instances": len(measurements),
        "facts": sum(m.total for m in measurements),
        "seconds": sum(m.seconds for m in measurements),
        "firings": sum(m.firings for m in measurements),
    }
