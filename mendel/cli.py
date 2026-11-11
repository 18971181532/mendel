"""Command line interface.

Exit codes are part of the contract, because these commands are meant to be
usable from a script:

0  the question was answered
1  the program file could not be read
2  the program or the arguments are wrong
3  the answer is "no" (no derivation, no answers, or the fact does exist)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List, Optional, Sequence

from . import __version__, bench, demos
from . import provenance as prov
from . import render
from .engine import Engine, Fact
from .program import Program
from .syntax import (
    Atom,
    Compare,
    Const,
    EvaluationError,
    Goal,
    MendelError,
    NegGoal,
    ParseError,
    Value,
    Var,
    parse_atom,
)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_NO = 3


class LoadError(MendelError):
    """The program file itself is unavailable.

    Kept separate from the other errors because it is not a mistake in the
    program: a script that pipes a generated file in wants to tell "my file is
    missing" (1) apart from "my rules are wrong" (2).
    """


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _load(path: str) -> Program:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        raise LoadError(f"cannot read {path}: {exc.strerror}") from None
    except UnicodeDecodeError as exc:
        raise LoadError(f"cannot read {path}: not valid UTF-8 ({exc.reason})") from None
    return Program.parse(text, os.path.basename(path))


def _parse_goal(text: str) -> Atom:
    try:
        return parse_atom(text)
    except MendelError as exc:
        raise MendelError(f"cannot parse goal {text!r}: {exc.message}") from None


def _parse_fact(program: Program, text: str) -> Fact:
    atom = _parse_goal(text)
    program.check_goal(atom)
    if not atom.is_ground():
        raise MendelError(f"{atom} has variables; a fact must be ground")
    return atom.as_fact()


def _answers(engine: Engine, atom: Atom) -> List[Fact]:
    positions: List[int] = []
    key: List[Value] = []
    for index, arg in enumerate(atom.args):
        if isinstance(arg, Const):
            positions.append(index)
            key.append(arg.value)
    rows = engine.relations[atom.pred].match(tuple(positions), tuple(key))
    return sorted((atom.pred, tuple(row)) for row in rows)


def _summary(engine: Engine) -> Dict[str, object]:
    given = len(engine.edb)
    total = sum(len(rel) for rel in engine.relations.values())
    return {
        "program": engine.program.name,
        "strata": len(engine.program.strata),
        "given": given,
        "facts": total,
        "derived": total - given,
        "stats": engine.stats.as_dict(),
    }


def _fact_count(engine: Engine) -> int:
    """Number of facts currently true, across every predicate."""
    return sum(len(rel) for rel in engine.relations.values())


def _print_summary(engine: Engine) -> None:
    info = _summary(engine)
    print(f"program   {info['program']}")
    print(f"strata    {info['strata']}")
    print(
        f"facts     {info['facts']:,} "
        f"({info['given']:,} given, {info['derived']:,} derived)"
    )
    print(f"rounds    {engine.stats.rounds:,}")
    print(f"firings   {engine.stats.firings:,}")
    print(f"time      {render.seconds(engine.stats.seconds)}")


def _json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
    program = _load(args.file)
    engine = Engine(program)
    mutations: List[Dict[str, object]] = []

    for text in args.remove or []:
        fact = _parse_fact(program, text)
        before = _fact_count(engine)
        engine.remove_fact(fact[0], fact[1])
        mutations.append(
            {"op": "remove", "fact": prov.fact_text(fact), "changed": before - _fact_count(engine)}
        )
    for text in args.add or []:
        fact = _parse_fact(program, text)
        before = _fact_count(engine)
        engine.add_fact(fact[0], fact[1])
        mutations.append(
            {"op": "add", "fact": prov.fact_text(fact), "changed": _fact_count(engine) - before}
        )

    answers: Optional[List[Fact]] = None
    if args.goal:
        atom = _parse_goal(args.goal)
        program.check_goal(atom)
        answers = _answers(engine, atom)

    if args.json:
        payload = _summary(engine)
        payload["mutations"] = mutations
        payload["predicates"] = engine.counts()
        if answers is not None:
            payload["answers"] = [prov.fact_text(f) for f in answers]
        _json(payload)
        return EXIT_OK if answers is None or answers else EXIT_NO

    # A bare query should print answers, not a five-line preamble about the
    # program.  `--stats` (or a mutation) brings the summary back.
    quiet = answers is not None and not mutations and not args.stats
    if not quiet:
        _print_summary(engine)
    for mutation in mutations:
        sign = "-" if mutation["op"] == "remove" else "+"
        count = int(mutation["changed"])
        verb = "removed" if mutation["op"] == "remove" else "added"
        print(f"{sign} {mutation['fact']}  ->  {count} fact{'' if count == 1 else 's'} {verb}")

    if args.stats:
        rows = [
            [pred, str(program.arity[pred]), f"{len(engine.relations[pred]):,}",
             "yes" if pred in engine.program.edb_preds() else ""]
            for pred in sorted(engine.relations)
            if len(engine.relations[pred])
        ]
        if rows:
            print()
            print(render.table(["predicate", "arity", "facts", "given"], rows,
                               aligns=["left", "right", "right", "left"]))

    if answers is not None:
        if not quiet:
            print()
        if not answers:
            print(f"no answers for {args.goal}.")
            return EXIT_NO
        print(f"{args.goal}   {len(answers)} answer{'s' if len(answers) != 1 else ''}")
        for fact in answers:
            print(f"  {prov.fact_text(fact)}")
    return EXIT_OK


# --------------------------------------------------------------------------
# explain
# --------------------------------------------------------------------------


def _tree_dict(node: prov.Node) -> Dict[str, object]:
    return {
        "fact": prov.fact_text(node.fact),
        "rule": node.support.rule.name if node.support else None,
        "premises": [_tree_dict(child) for child in node.children],
        "absences": [prov.fact_text(absence) for absence in node.absences],
        "truncated": node.truncated,
    }


def cmd_explain(args: argparse.Namespace) -> int:
    program = _load(args.file)
    engine = Engine(program)
    fact = _parse_fact(program, args.fact)
    if fact[1] not in engine.relations[fact[0]].rows:
        message = f"{prov.fact_text(fact)} is not derivable; try 'mendel why-not'"
        if args.json:
            _json({"fact": prov.fact_text(fact), "derivable": False, "error": message})
        else:
            print(message)
        return EXIT_NO

    if args.all:
        nodes = prov.derivations(engine, fact, limit=args.limit, max_depth=args.depth)
        if args.json:
            _json({"fact": prov.fact_text(fact), "derivable": True,
                   "derivations": [_tree_dict(n) for n in nodes]})
            return EXIT_OK
        for index, node in enumerate(nodes, 1):
            if index > 1:
                print()
            print(f"derivation {index} of {len(nodes)}")
            print("\n".join(prov.format_tree(node)))
        if len(nodes) == 1:
            print()
            print(f"no second proof found within depth {args.depth} and limit {args.limit}")
        return EXIT_OK

    tree = prov.build_tree(engine, fact, max_depth=args.depth)
    assert tree is not None
    if args.json:
        _json({"fact": prov.fact_text(fact), "derivable": True, "tree": _tree_dict(tree)})
        return EXIT_OK
    print("\n".join(prov.format_tree(tree)))
    return EXIT_OK


# --------------------------------------------------------------------------
# why-not / impact
# --------------------------------------------------------------------------


def cmd_why_not(args: argparse.Namespace) -> int:
    program = _load(args.file)
    engine = Engine(program)
    fact = _parse_fact(program, args.fact)
    report = prov.why_not(engine, fact)
    if args.json:
        payload = {
            "fact": prov.fact_text(fact),
            "derivable": report.exists,
            "relation_size": report.relation_size,
            "attempts": [
                {
                    "rule": attempt.rule.name,
                    "blocked": attempt.blocked_text,
                    "goals_matched": attempt.satisfied,
                    "candidates": attempt.inputs,
                    "note": attempt.note,
                }
                for attempt in report.attempts
            ],
        }
        _json(payload)
    else:
        print("\n".join(prov.format_why_not(report)))
    return EXIT_NO if report.exists else EXIT_OK


def cmd_impact(args: argparse.Namespace) -> int:
    program = _load(args.file)
    engine = Engine(program)
    fact = _parse_fact(program, args.fact)
    if fact not in engine.edb:
        raise EvaluationError(
            f"{prov.fact_text(fact)} is not a given fact; only given facts can be retracted"
        )
    if fact[1] not in engine.relations[fact[0]].rows:
        raise EvaluationError(f"{prov.fact_text(fact)} is not in the database")

    recorded = engine.impact(fact)
    if args.exact:
        trial = engine.trial_without(fact)
        lost = (engine.all_facts() - trial.all_facts()) - {fact}
        basis = "exact"
    else:
        lost = set(recorded)
        basis = "recorded supports"

    by_pred: Dict[str, int] = {}
    for item in lost:
        by_pred[item[0]] = by_pred.get(item[0], 0) + 1

    if args.json:
        _json({
            "fact": prov.fact_text(fact),
            "basis": basis,
            "count": len(lost),
            "recorded": len(recorded),
            "by_predicate": by_pred,
        })
        return EXIT_OK

    print(
        f"retracting {prov.fact_text(fact)} would remove {len(lost)} fact"
        f"{'' if len(lost) == 1 else 's'} ({basis})"
    )
    if args.exact and len(recorded) != len(lost):
        print(
            f"  recorded supports predicted {len(recorded)}; "
            f"{len(recorded) - len(lost)} survive through alternative derivations"
        )
    if by_pred:
        print()
        print(render.table(
            ["predicate", "facts"],
            [[pred, f"{count:,}"] for pred, count in sorted(by_pred.items())],
            aligns=["left", "right"],
        ))
    return EXIT_OK


# --------------------------------------------------------------------------
# plan
# --------------------------------------------------------------------------


def _advance(literal, bound: set) -> set:
    bound = set(bound)
    if isinstance(literal, Goal):
        bound.update(literal.atom.variables())
    elif isinstance(literal, Compare) and literal.op == "=":
        left, right = literal.left, literal.right
        if isinstance(left, Var) and left.name not in bound:
            right_vars = {right.name} if isinstance(right, Var) else set()
            if right_vars <= bound or isinstance(right, Const):
                bound.add(left.name)
        elif isinstance(right, Var) and right.name not in bound:
            left_vars = {left.name} if isinstance(left, Var) else set()
            if left_vars <= bound or isinstance(left, Const):
                bound.add(right.name)
    return bound


def cmd_plan(args: argparse.Namespace) -> int:
    program = _load(args.file)
    engine = Engine(program)
    sizes = {pred: len(rel) for pred, rel in engine.relations.items()}
    if args.json:
        payload = []
        for stratum in program.strata:
            rules = []
            for rule in stratum.rules:
                order = program.plan_indices(rule, sizes)
                steps = []
                bound: set = set()
                for index in order:
                    literal = rule.body[index]
                    atom = literal.atom if isinstance(literal, (Goal, NegGoal)) else None
                    steps.append({
                        "literal": str(literal),
                        "bound": sum(
                            1 for a in atom.args
                            if isinstance(a, Const) or (isinstance(a, Var) and a.name in bound)
                        ) if atom else 0,
                        "rows": sizes.get(atom.pred, 0) if atom else 0,
                    })
                    bound = _advance(literal, bound)
                rules.append({"name": rule.name, "text": str(rule), "steps": steps})
            payload.append({"stratum": stratum.index, "predicates": list(stratum.preds),
                            "rules": rules})
        _json(payload)
        return EXIT_OK

    for stratum in program.strata:
        preds = ", ".join(f"{p}/{program.arity[p]}" for p in stratum.preds)
        print(f"stratum {stratum.index}   {preds}")
        for rule in stratum.rules:
            order = program.plan_indices(rule, sizes)
            print(f"  @{rule.name}  {prov.rule_text(rule)}")
            bound: set = set()
            for step, index in enumerate(order, 1):
                literal = rule.body[index]
                atom = literal.atom if isinstance(literal, (Goal, NegGoal)) else None
                if atom is not None:
                    fixed = sum(
                        1 for a in atom.args
                        if isinstance(a, Const) or (isinstance(a, Var) and a.name in bound)
                    )
                    note = f"{fixed} bound, {sizes.get(atom.pred, 0):,} rows"
                else:
                    note = "filter"
                print(f"    {step}. {str(literal):<34} {note}")
                bound = _advance(literal, bound)
        print()
    return EXIT_OK


# --------------------------------------------------------------------------
# bench / demo
# --------------------------------------------------------------------------


def cmd_bench(args: argparse.Namespace) -> int:
    measurements = bench.run(args.suite)
    total = bench.totals(measurements)
    incremental = (
        [bench.incremental(), bench.incremental(middle=True)] if args.incremental else None
    )

    if args.json:
        payload = {
            "suite": args.suite,
            "instances": [m.as_dict() for m in measurements],
            "totals": {k: round(v, 6) if isinstance(v, float) else v for k, v in total.items()},
        }
        if incremental is not None:
            payload["incremental"] = [m.as_dict() for m in incremental]
        _json(payload)
        return EXIT_OK

    rows = [
        [m.name, f"{m.given:,}", f"{m.total:,}", f"{m.derived:,}",
         f"{m.rounds:,}", f"{m.firings:,}", render.seconds(m.seconds)]
        for m in measurements
    ]
    print(render.table(
        ["instance", "given", "facts", "derived", "rounds", "firings", "time"],
        rows,
        aligns=["left", "right", "right", "right", "right", "right", "right"],
    ))
    print()
    print(
        f"{total['instances']} instances, {int(total['facts']):,} facts, "
        f"{int(total['firings']):,} firings, {render.seconds(total['seconds'])} total"
    )
    if incremental is not None:
        print()
        for m in incremental:
            print(
                f"{m.name}: retract {m.fact} -> {m.retracted:,} facts gone in "
                f"{render.seconds(m.incremental_seconds)}, "
                f"rebuild {render.seconds(m.full_seconds)}, "
                f"{m.full_seconds / m.incremental_seconds:.1f}x, "
                f"identical={m.identical}"
            )
    return EXIT_OK


def cmd_demo(args: argparse.Namespace) -> int:
    if args.name is None:
        print("demos:")
        for name in demos.DEMO_NAMES:
            print(f"  {name}")
        return EXIT_OK
    demo = demos.get(args.name)
    print(demo.title)
    print("─" * render.display_width(demo.title))
    print(demo.body)
    return EXIT_OK


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mendel",
        description="A Datalog engine that shows its work.",
    )
    parser.add_argument("--version", action="version", version=f"mendel {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="evaluate a program")
    run.add_argument("file")
    run.add_argument("--goal", help="print every fact matching this goal")
    run.add_argument("--add", action="append", metavar="FACT", help="insert a fact before reporting")
    run.add_argument("--remove", action="append", metavar="FACT", help="retract a fact before reporting")
    run.add_argument("--stats", action="store_true", help="per-predicate table")
    run.add_argument("--json", action="store_true")
    run.set_defaults(func=cmd_run)

    explain = sub.add_parser("explain", help="show the derivation of a fact")
    explain.add_argument("file")
    explain.add_argument("fact")
    explain.add_argument("--all", action="store_true", help="search for alternative proofs")
    explain.add_argument("--limit", type=int, default=8)
    explain.add_argument("--depth", type=int, default=64)
    explain.add_argument("--json", action="store_true")
    explain.set_defaults(func=cmd_explain)

    why = sub.add_parser("why-not", help="explain why a fact is missing")
    why.add_argument("file")
    why.add_argument("fact")
    why.add_argument("--json", action="store_true")
    why.set_defaults(func=cmd_why_not)

    impact = sub.add_parser("impact", help="what a retraction would remove")
    impact.add_argument("file")
    impact.add_argument("fact")
    impact.add_argument("--exact", action="store_true", help="rebuild instead of using recorded supports")
    impact.add_argument("--json", action="store_true")
    impact.set_defaults(func=cmd_impact)

    plan = sub.add_parser("plan", help="show strata and join order")
    plan.add_argument("file")
    plan.add_argument("--json", action="store_true")
    plan.set_defaults(func=cmd_plan)

    bench_cmd = sub.add_parser("bench", help="run a benchmark suite")
    bench_cmd.add_argument("suite", nargs="?", default="quick", choices=bench.suite_names())
    bench_cmd.add_argument("--incremental", action="store_true")
    bench_cmd.add_argument("--json", action="store_true")
    bench_cmd.set_defaults(func=cmd_bench)

    demo = sub.add_parser("demo", help="run a self-checking demo")
    demo.add_argument("name", nargs="?", choices=list(demos.DEMO_NAMES))
    demo.set_defaults(func=cmd_demo)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    render.use_utf8_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except LoadError as exc:
        print(exc.message, file=sys.stderr)
        return EXIT_ERROR
    except MendelError as exc:
        source = getattr(args, "file", None) or "<program>"
        print(exc.located(source), file=sys.stderr)
        return EXIT_USAGE
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    except BrokenPipeError:  # pragma: no cover - piping into head
        return EXIT_OK
