"""Provenance queries: derivation trees, alternative proofs, why-not.

A stored :class:`~mendel.engine.Support` only records *one* derivation.  That is
enough to explain a fact and to drive incremental deletion, but not enough to
show that a proof is the only one.  ``derivations`` therefore re-searches the
rules backwards, while ``explain`` walks the recorded supports.  Keeping the two
separate means the common path stays cheap and the exhaustive path stays honest:
if it cannot find a second proof within its limits, it says so rather than
claiming uniqueness.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

from .engine import Engine, Fact, Support
from .syntax import (
    Atom,
    Compare,
    Const,
    EvaluationError,
    Goal,
    Literal,
    NegGoal,
    Rule,
    Value,
    Var,
    format_value,
)

#: How many candidate environments a diagnostic will explore before giving up.
ENV_CAP = 400


def _relation(engine: Engine, pred: str):
    try:
        return engine.relations[pred]
    except KeyError:
        raise EvaluationError(f"unknown predicate {pred!r}") from None


def fact_text(fact: Fact) -> str:
    pred, args = fact
    return f"{pred}({', '.join(format_value(a) for a in args)})"


def atom_text(atom: Atom) -> str:
    return str(atom)


# --------------------------------------------------------------------------
# Derivation trees
# --------------------------------------------------------------------------


@dataclass
class Node:
    """One fact in a proof tree, plus how it was derived."""

    fact: Fact
    support: Optional[Support] = None
    children: List["Node"] = field(default_factory=list)
    absences: List[Fact] = field(default_factory=list)
    truncated: bool = False

    @property
    def given(self) -> bool:
        return self.support is None

    def __str__(self) -> str:
        return fact_text(self.fact)


def build_tree(engine: Engine, fact: Fact, max_depth: int = 64) -> Optional[Node]:
    """Build the recorded derivation of ``fact`` iteratively.

    Iterative because a chain graph produces one proof level per edge, and a
    300-edge chain would otherwise hit Python's recursion limit -- exactly the
    kind of input a reachability engine is expected to handle.
    """
    if fact[1] not in engine.relations[fact[0]].rows:
        return None
    root = Node(fact)
    stack: List[Tuple[Node, int]] = [(root, 0)]
    while stack:
        node, depth = stack.pop()
        support = engine.support_of(node.fact)
        if support is None:
            continue
        node.support = support
        node.absences = list(support.absences)
        if depth >= max_depth:
            node.truncated = True
            continue
        for premise in support.facts:
            child = Node(premise)
            node.children.append(child)
            stack.append((child, depth + 1))
    return root


def rule_text(rule: Rule) -> str:
    text = str(rule)
    prefix = f"@{rule.name} "
    return text[len(prefix) :] if text.startswith(prefix) else text


def format_tree(node: Node) -> List[str]:
    """Render a proof tree as indented lines with rule labels on the edges."""
    rule_here = node.support.rule.name if node.support else None
    head = fact_text(node.fact)
    if rule_here:
        head += f"   [@{rule_here}]"
    else:
        head += "   (given)"
    lines = [head]

    def walk(current: Node, prefix: str) -> None:
        entries: List[Tuple[Optional[Node], str, str]] = []
        for child in current.children:
            label = f"[@{child.support.rule.name}] " if child.support else ""
            suffix = "   (given)" if child.support is None else ""
            if child.truncated:
                suffix += "   (depth limit)"
            entries.append((child, label + fact_text(child.fact) + suffix, "   "))
        for absence in current.absences:
            entries.append((None, f"not {fact_text(absence)}   (absent)", "   "))
        total = len(entries)
        for index, (child, text, _) in enumerate(entries):
            last = index == total - 1
            connector = "└─ " if last else "├─ "
            lines.append(prefix + connector + text)
            if child is not None:
                walk(child, prefix + ("   " if last else "│  "))

    walk(node, "")
    return lines


# --------------------------------------------------------------------------
# Alternative derivations
# --------------------------------------------------------------------------


def _match_head(atom: Atom, args: Sequence[Value]) -> Optional[Dict[str, Value]]:
    env: Dict[str, Value] = {}
    for term, value in zip(atom.args, args):
        if isinstance(term, Const):
            if term.value != value:
                return None
        else:
            assert isinstance(term, Var)
            if term.name in env and env[term.name] != value:
                return None
            env[term.name] = value
    return env


def _substitute(atom: Atom, env: Dict[str, Value]) -> Optional[Tuple[Value, ...]]:
    out: List[Value] = []
    for term in atom.args:
        if isinstance(term, Const):
            out.append(term.value)
        elif term.name in env:
            out.append(env[term.name])
        else:
            return None
    return tuple(out)


def derivations(
    engine: Engine,
    fact: Fact,
    limit: int = 8,
    max_depth: int = 64,
) -> List[Node]:
    """Enumerate distinct proofs of ``fact``, up to ``limit``.

    A proof is identified by the rule and the premises used at the top level;
    deeper alternatives are explored depth-first, taking the first proof of
    each premise.  This is a readability tool, not a proof counter.
    """
    if fact[1] not in engine.relations[fact[0]].rows:
        return []
    results: List[Node] = []
    seen: set = set()

    def prove(goal: Fact, stack: frozenset, depth: int) -> Iterator[Node]:
        if depth > max_depth or goal in stack:
            return
        if goal[1] not in engine.relations[goal[0]].rows:
            return
        if engine.support_of(goal) is None:
            # A given fact is a leaf proof.  Without this, a premise with no
            # rules of its own would end the branch and make every proof that
            # touches the EDB disappear.
            yield Node(goal)
            return
        inner = stack | {goal}
        for rule in engine.program.rules_for(goal[0]):
            env = _match_head(rule.head, goal[1])
            if env is None:
                continue
            for env2, premises, absences in engine.body_matches(rule, env):
                if _substitute(rule.head, env2) != goal[1]:
                    continue
                key = (rule.name, premises)
                if key in seen:
                    continue
                children: List[Node] = []
                ok = True
                for premise in premises:
                    child = next(prove(premise, inner, depth + 1), None)
                    if child is None:
                        ok = False
                        break
                    children.append(child)
                if not ok:
                    continue
                seen.add(key)
                node = Node(goal, Support(rule, premises, absences))
                node.children = children
                node.absences = list(absences)
                yield node
                if len(results) >= limit:
                    return

    for node in prove(fact, frozenset(), 0):
        results.append(node)
        if len(results) >= limit:
            break
    return results


# --------------------------------------------------------------------------
# Why not
# --------------------------------------------------------------------------


@dataclass
class Relaxation:
    """What happens if one bound argument of a failing goal is set free."""

    position: int
    count: int
    examples: List[Fact]


@dataclass
class Attempt:
    rule: Rule
    blocked: Optional[Literal]
    satisfied: int
    inputs: int
    relaxations: List[Relaxation] = field(default_factory=list)
    note: str = ""
    blocked_text: str = ""


@dataclass
class WhyNot:
    fact: Fact
    matches: int
    attempts: List[Attempt]
    has_rules: bool
    relation_size: int = 0

    @property
    def exists(self) -> bool:
        return self.matches > 0


def why_not(engine: Engine, fact: Fact) -> WhyNot:
    """Explain why a ground fact is absent.

    Each rule with a matching head is walked in join order.  The walk stops at
    the first literal that produces no extension; that literal, and how close
    the rule got before it, is the explanation.
    """
    pred, args = fact
    relation = _relation(engine, pred)
    matches = 1 if args in relation.rows else 0
    rules = engine.program.rules_for(pred)
    if matches:
        return WhyNot(fact, matches, [], bool(rules), len(relation))

    attempts: List[Attempt] = []
    for rule in rules:
        env = _match_head(rule.head, args)
        if env is None:
            continue
        order = engine.program.plan_indices(
            rule, {p: len(r) for p, r in engine.relations.items()}, initial=tuple(env)
        )
        envs: List[Dict[str, Value]] = [env]
        satisfied = 0
        blocked: Optional[Literal] = None
        inputs = 1
        for index in order:
            literal = rule.body[index]
            extended: List[Dict[str, Value]] = []
            for current in envs:
                for nxt, _ in engine.expand(literal, current, rule, count=False):
                    extended.append(nxt)
                    if len(extended) >= ENV_CAP:
                        break
                if len(extended) >= ENV_CAP:
                    break
            if not extended:
                blocked = literal
                inputs = len(envs)
                break
            envs = extended
            if isinstance(literal, Goal):
                satisfied += 1
        if blocked is None:
            # The body is satisfiable but the head is already fixed to the
            # goal, so this would contradict ``matches == 0``.  Reaching it
            # means the search hit ENV_CAP; report it as inconclusive.
            attempts.append(
                Attempt(rule, None, satisfied, len(envs), note="search limit reached")
            )
            continue
        relaxations: List[Relaxation] = []
        note = ""
        blocked_text = ""
        if blocked is not None:
            blocked_text = _literal_under(blocked, envs[0] if envs else env)
        if isinstance(blocked, Goal):
            relaxations = _relax(engine, blocked.atom, envs[0] if envs else env)
            if len(relation) == 0:
                note = f"{pred}/{len(args)} is empty"
        elif isinstance(blocked, NegGoal):
            note = "the negated goal is satisfied, which blocks this rule"
        elif isinstance(blocked, Compare):
            note = "the comparison fails for every match found so far"
        attempts.append(
            Attempt(rule, blocked, satisfied, inputs, relaxations, note, blocked_text)
        )

    attempts.sort(key=lambda a: (-a.satisfied, a.rule.line))
    return WhyNot(fact, matches, attempts, bool(rules), len(relation))


def _term_under(term, env: Dict[str, Value]) -> str:
    if isinstance(term, Const):
        return format_value(term.value)
    assert isinstance(term, Var)
    if term.name in env:
        return format_value(env[term.name])
    return "?" + term.name


def _literal_under(literal: Literal, env: Dict[str, Value]) -> str:
    """Show a body literal with the bindings known at the point it failed."""
    if isinstance(literal, (Goal, NegGoal)):
        text = (
            f"{literal.atom.pred}("
            + ", ".join(_term_under(arg, env) for arg in literal.atom.args)
            + ")"
        )
        return f"not {text}" if isinstance(literal, NegGoal) else text
    assert isinstance(literal, Compare)
    return f"{_term_under(literal.left, env)} {literal.op} {_term_under(literal.right, env)}"


def _relax(engine: Engine, atom: Atom, env: Dict[str, Value]) -> List[Relaxation]:
    """Count matches when each bound column is dropped, one at a time."""
    from .engine import _bound_key

    positions, key = _bound_key(atom, env)
    relation = engine.relations[atom.pred]
    out: List[Relaxation] = []
    for drop in range(len(positions)):
        sub_positions = positions[:drop] + positions[drop + 1 :]
        sub_key = key[:drop] + key[drop + 1 :]
        rows = list(relation.match(sub_positions, sub_key))
        if not rows:
            continue
        rows.sort()
        examples = [(atom.pred, row) for row in rows[:3]]
        out.append(Relaxation(positions[drop], len(rows), examples))
    out.sort(key=lambda r: -r.count)
    return out


def format_why_not(report: WhyNot) -> List[str]:
    if report.exists:
        return [
            f"{fact_text(report.fact)} is derivable.",
            "use 'mendel explain' to see the derivation.",
        ]
    lines = [f"no fact matches {fact_text(report.fact)}."]
    pred, args = report.fact

    if not report.has_rules:
        lines.append(f"  no rule has {pred}/{len(args)} in its head, so nothing can derive it.")
        return lines
    if not report.attempts:
        lines.append("  every rule with this head contradicts the goal on its constants.")
        return lines

    best = report.attempts[0]
    lines.append("")
    lines.append(f"closest attempt: @{best.rule.name}")
    lines.append(f"  {rule_text(best.rule)}")
    goals = sum(1 for literal in best.rule.body if isinstance(literal, Goal))
    lines.append(f"  matched {best.satisfied} of {goals} goals before getting stuck.")
    lines.append(
        f"  {pred}/{len(args)} holds {report.relation_size} fact"
        f"{'' if report.relation_size == 1 else 's'}, none of them the goal."
    )
    if best.blocked is not None:
        lines.append(f"  stuck at: {best.blocked_text}")
    if best.note:
        lines.append(f"  {best.note}")
    for relaxation in best.relaxations[:2]:
        lines.append(
            f"  relaxing argument {relaxation.position + 1} leaves "
            f"{relaxation.count} candidate{'s' if relaxation.count != 1 else ''}:"
        )
        for example in relaxation.examples:
            lines.append(f"    {fact_text(example)}")
    return lines
