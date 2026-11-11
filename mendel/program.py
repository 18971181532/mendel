"""Programs: validation, join planning and stratification.

Two static analyses live here, and both are load-bearing:

* **Safety** -- every variable must be bound by a positive goal before it is
  used.  The planner doubles as the checker: if it cannot schedule a literal,
  that literal is unsafe.
* **Stratification** -- negation may not occur inside a strongly connected
  component of the predicate dependency graph.  Without this the least model
  need not exist (``p(x) :- not p(x)``), and evaluation could oscillate instead
  of terminating.

The join planner is greedy rather than cost-based.  At each step it picks the
goal with the most already-bound arguments, breaking ties by the current size
of the relation.  It is not a query optimiser; it is a way to keep the nested
loops from starting with a full cross product, and its choices are printed by
``mendel plan`` so they can be argued with.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .syntax import (
    Atom,
    Compare,
    Const,
    EvaluationError,
    Goal,
    Literal,
    NegGoal,
    ParseError,
    Rule,
    SafetyError,
    StratificationError,
    Var,
    literal_vars,
    parse_program,
    term_vars,
)


@dataclass
class Stratum:
    """One strongly connected component of the predicate graph."""

    index: int
    preds: Tuple[str, ...]
    rules: Tuple[Rule, ...]

    @property
    def pred_set(self) -> frozenset:
        return frozenset(self.preds)

    def __str__(self) -> str:
        return f"stratum {self.index}: {', '.join(self.preds)}"


class Program:
    """A validated Datalog program.

    Construction is the validation pass: arity consistency, rule safety and
    stratification all happen in ``__init__`` so that an engine can never be
    built around a program that has no well-defined least model.
    """

    def __init__(
        self,
        facts: Sequence[Atom],
        rules: Sequence[Rule],
        name: str = "<program>",
    ):
        self.name = name
        self.facts: List[Atom] = []
        self.rules: List[Rule] = []
        self.arity: Dict[str, int] = {}
        self.strata: List[Stratum] = []
        self._stratum_of: Dict[str, int] = {}
        self._rules_by_head: Dict[str, List[Rule]] = {}

        for fact in facts:
            self._check_atom(fact, None)
            self.facts.append(fact)
        for rule in rules:
            self._check_atom(rule.head, rule)
            for literal in rule.body:
                if isinstance(literal, (Goal, NegGoal)):
                    self._check_atom(literal.atom, rule)
            self.rules.append(rule)

        self._assign_names()
        self._build_graph()
        self._stratify()
        for rule in self.rules:
            self._rules_by_head.setdefault(rule.pred, []).append(rule)
        # Planning without sizes is the static safety check.
        for rule in self.rules:
            self.plan_indices(rule)

    # -- construction helpers --------------------------------------------
    @classmethod
    def parse(cls, text: str, source: str = "<program>") -> "Program":
        facts, rules = parse_program(text, source)
        return cls(facts, rules, name=source)

    def _check_atom(self, atom: Atom, rule: Optional[Rule]) -> None:
        expected = self.arity.get(atom.pred)
        where = f"rule @{rule.name or '?'} (line {rule.line})" if rule else "a fact"
        if expected is None:
            self.arity[atom.pred] = len(atom.args)
        elif expected != len(atom.args):
            raise ParseError(
                f"predicate {atom.pred!r} is used with arity {len(atom.args)} in {where}, "
                f"but arity {expected} elsewhere",
                rule.line if rule else None,
            )

    def _assign_names(self) -> None:
        used: Set[str] = set()
        counters: Dict[str, int] = {}
        for rule in self.rules:
            if rule.name:
                if rule.name in used:
                    raise ParseError(f"duplicate rule label @{rule.name}", rule.line)
                used.add(rule.name)
                continue
            counters[rule.pred] = counters.get(rule.pred, 0) + 1
            candidate = f"{rule.pred}#{counters[rule.pred]}"
            while candidate in used:
                counters[rule.pred] += 1
                candidate = f"{rule.pred}#{counters[rule.pred]}"
            rule.name = candidate
            used.add(candidate)

    def _build_graph(self) -> None:
        self._edges: Dict[str, Set[str]] = {p: set() for p in self.arity}
        self._neg_edges: List[Tuple[str, str, Rule]] = []
        for rule in self.rules:
            for literal in rule.body:
                if isinstance(literal, (Goal, NegGoal)):
                    dep = literal.atom.pred
                    self._edges[rule.pred].add(dep)
                    if isinstance(literal, NegGoal):
                        self._neg_edges.append((rule.pred, dep, rule))

    def _stratify(self) -> None:
        components = _tarjan(sorted(self._edges), self._edges)
        for index, component in enumerate(components):
            for pred in component:
                self._stratum_of[pred] = index
        for head, dep, rule in self._neg_edges:
            if self._stratum_of[head] == self._stratum_of[dep]:
                path = _shortest_path(dep, head, self._edges)
                cycle = " -> ".join(path) if path else dep
                raise StratificationError(
                    f"negation cycle in rule @{rule.name}: {head} depends negatively on "
                    f"{dep}, but {cycle} leads back to {head}",
                    rule.line,
                )
        for index, component in enumerate(components):
            members = set(component)
            rules = tuple(r for r in self.rules if r.pred in members)
            self.strata.append(Stratum(index, tuple(sorted(component)), rules))

    # -- queries ----------------------------------------------------------
    def stratum_of(self, pred: str) -> int:
        try:
            return self._stratum_of[pred]
        except KeyError:
            raise EvaluationError(f"unknown predicate {pred!r}", None) from None

    def rules_for(self, pred: str) -> Tuple[Rule, ...]:
        return tuple(self._rules_by_head.get(pred, ()))

    def edb_preds(self) -> Set[str]:
        return {fact.pred for fact in self.facts}

    def idb_preds(self) -> Set[str]:
        return {rule.pred for rule in self.rules}

    def check_goal(self, atom: Atom) -> None:
        """Validate a user-supplied goal against the program's signature."""
        if atom.pred not in self.arity:
            close = difflib.get_close_matches(atom.pred, sorted(self.arity), n=1)
            hint = f" (did you mean {close[0]!r}?)" if close else ""
            raise EvaluationError(f"unknown predicate {atom.pred!r}{hint}")
        if len(atom.args) != self.arity[atom.pred]:
            raise EvaluationError(
                f"predicate {atom.pred!r} takes {self.arity[atom.pred]} arguments, "
                f"the goal gives {len(atom.args)}"
            )

    # -- planning ---------------------------------------------------------
    def plan_indices(
        self,
        rule: Rule,
        sizes: Optional[Dict[str, int]] = None,
        initial: Iterable[str] = (),
    ) -> List[int]:
        """Order the body of ``rule`` for nested-loop evaluation.

        Goals are chosen greedily by the number of bound arguments, then by
        the relation's current size.  Negations and comparisons are emitted as
        soon as their variables are bound, because they cannot bind anything
        themselves.  ``?x = ?y`` is the one exception: with one side bound it
        acts as an assignment and is what makes the literal safe.

        ``initial`` names variables already bound from outside the body -- a
        ground head during backward search, for instance.  Evaluation passes
        nothing, because a head variable is an output there.
        """
        sizes = sizes or {}
        bound: Set[str] = set(initial)
        remaining = list(range(len(rule.body)))
        order: List[int] = []

        def is_ready(index: int) -> bool:
            literal = rule.body[index]
            if isinstance(literal, Goal):
                return True
            if isinstance(literal, NegGoal):
                return all(v in bound for v in literal.atom.variables())
            assert isinstance(literal, Compare)
            left = set(term_vars(literal.left))
            right = set(term_vars(literal.right))
            if literal.op != "=":
                return left <= bound and right <= bound
            if left <= bound and right <= bound:
                return True
            # Assignment forms: ?x = <bound term> or <bound term> = ?x.
            if left <= bound and isinstance(literal.right, Var) and literal.right.name not in bound:
                return True
            if right <= bound and isinstance(literal.left, Var) and literal.left.name not in bound:
                return True
            return False

        def bind_from(index: int) -> None:
            literal = rule.body[index]
            if isinstance(literal, Goal):
                bound.update(literal.atom.variables())
                return
            if isinstance(literal, Compare) and literal.op == "=":
                left, right = literal.left, literal.right
                if (
                    isinstance(left, Var)
                    and left.name not in bound
                    and set(term_vars(right)) <= bound
                ):
                    bound.add(left.name)
                elif (
                    isinstance(right, Var)
                    and right.name not in bound
                    and set(term_vars(left)) <= bound
                ):
                    bound.add(right.name)

        while remaining:
            goals = [i for i in remaining if isinstance(rule.body[i], Goal)]
            if goals:

                def score(index: int) -> Tuple[int, int, int]:
                    atom = rule.body[index].atom
                    fixed = sum(
                        1
                        for arg in atom.args
                        if isinstance(arg, Const) or (isinstance(arg, Var) and arg.name in bound)
                    )
                    return (-fixed, sizes.get(atom.pred, 0), index)

                pick = min(goals, key=score)
            else:
                ready = [i for i in remaining if is_ready(i)]
                if not ready:
                    stuck = rule.body[remaining[0]]
                    raise SafetyError(
                        f"rule @{rule.name} (line {rule.line}) cannot bind the variables of "
                        f"{stuck}; every variable needs a positive goal first",
                        rule.line,
                    )
                pick = ready[0]
            order.append(pick)
            remaining.remove(pick)
            bind_from(pick)
            # Drain every literal that just became ready, so a comparison is
            # never scheduled after a goal that does not need it.
            changed = True
            while changed:
                changed = False
                for index in list(remaining):
                    if not isinstance(rule.body[index], Goal) and is_ready(index):
                        order.append(index)
                        remaining.remove(index)
                        bind_from(index)
                        changed = True

        head_vars = set(rule.head.variables())
        unbound = head_vars - bound
        if unbound:
            names = ", ".join("?" + v for v in sorted(unbound))
            raise SafetyError(
                f"rule @{rule.name} (line {rule.line}) has {names} in the head but never "
                f"binds them in the body",
                rule.line,
            )
        return order


# --------------------------------------------------------------------------
# Graph helpers
# --------------------------------------------------------------------------


def _tarjan(nodes: Sequence[str], edges: Dict[str, Set[str]]) -> List[List[str]]:
    """Strongly connected components, dependencies first.

    Tarjan emits a component only after everything reachable from it, which is
    exactly the evaluation order we need for strata.
    """
    index: Dict[str, int] = {}
    low: Dict[str, int] = {}
    on_stack: Set[str] = set()
    stack: List[str] = []
    components: List[List[str]] = []
    counter = 0

    def strong(node: str) -> None:
        nonlocal counter
        index[node] = low[node] = counter
        counter += 1
        stack.append(node)
        on_stack.add(node)
        for dep in edges.get(node, ()):  # type: ignore[union-attr]
            if dep not in index:
                strong(dep)
                low[node] = min(low[node], low[dep])
            elif dep in on_stack:
                low[node] = min(low[node], index[dep])
        if low[node] == index[node]:
            component: List[str] = []
            while True:
                member = stack.pop()
                on_stack.discard(member)
                component.append(member)
                if member == node:
                    break
            components.append(sorted(component))

    for node in nodes:
        if node not in index:
            strong(node)
    return components


def _shortest_path(
    start: str, goal: str, edges: Dict[str, Set[str]]
) -> Optional[List[str]]:
    """Shortest dependency path from ``start`` to ``goal`` (BFS)."""
    if start == goal:
        return [start]
    queue: List[List[str]] = [[start]]
    seen = {start}
    while queue:
        path = queue.pop(0)
        for dep in sorted(edges.get(path[-1], ())):  # type: ignore[union-attr]
            if dep == goal:
                return path + [dep]
            if dep not in seen:
                seen.add(dep)
                queue.append(path + [dep])
    return None
