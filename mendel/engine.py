"""The evaluation engine: semi-naive fixpoint, provenance capture, DRed.

Evaluation is bottom-up and stratified.  Within a stratum the engine runs the
standard semi-naive loop: in each round at least one body atom is restricted to
the facts discovered in the previous round, so a rule is never re-fired on a
combination it has already seen.

Every fact inserted by a rule gets a :class:`Support` -- the rule that produced
it and the exact premises that matched.  That single record is what makes
``explain``, ``impact`` and incremental deletion possible:

* a derivation tree is just a walk over supports;
* deleting a fact can overdelete everything whose recorded support mentions it;
* ``why-not`` can be answered from the rules alone, since a missing fact has no
  support to walk.

Deletion follows DRed: overdelete the transitive support closure, then
re-saturate from the facts that remain.  Facts with an alternative derivation
come back; facts that only existed because of the retracted input do not.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

from .program import Program
from .relation import Relation
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
)

#: A ground fact: predicate name plus an argument tuple.
Fact = Tuple[str, Tuple[Value, ...]]

_MISSING = object()


@dataclass
class Support:
    """The rule application that first produced a fact.

    ``absences`` records the ground negated goals that were checked and found
    missing.  Without them a proof that depends on negation would look like it
    was derived from its positive premises alone -- the exact omission an
    explanation engine must not make.
    """

    rule: Rule
    facts: Tuple[Fact, ...]
    absences: Tuple[Fact, ...] = ()

    def __str__(self) -> str:
        return f"@{self.rule.name}"


@dataclass
class Stats:
    rounds: int = 0
    firings: int = 0
    derived: int = 0
    retracted: int = 0
    rederived: int = 0
    lookups: int = 0
    seconds: float = 0.0

    def as_dict(self) -> Dict[str, float]:
        return {
            "rounds": self.rounds,
            "firings": self.firings,
            "derived": self.derived,
            "retracted": self.retracted,
            "rederived": self.rederived,
            "lookups": self.lookups,
            "seconds": round(self.seconds, 6),
        }


class Engine:
    """A materialised Datalog database.

    ``Engine(program)`` loads the program's facts and saturates immediately.
    ``add_fact`` and ``remove_fact`` maintain the materialisation incrementally;
    the result is always identical to rebuilding from the current EDB, which is
    the property the test suite hammers on.
    """

    def __init__(self, program: Program, evaluate: bool = True):
        self.program = program
        self.relations: Dict[str, Relation] = {
            pred: Relation(pred, arity) for pred, arity in program.arity.items()
        }
        self.supports: Dict[Fact, Support] = {}
        self.supporters: Dict[Fact, Set[Fact]] = defaultdict(set)
        self.edb: Set[Fact] = set()
        self.stats = Stats()
        self._rederive_orders: Dict[Tuple[str, Tuple[str, ...]], List[int]] = {}

        for atom in program.facts:
            fact = atom.as_fact()
            self.relations[fact[0]].add(fact[1])
            self.edb.add(fact)
        if evaluate:
            self.evaluate()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def evaluate(self) -> None:
        """Saturate the whole program from the current facts."""
        started = time.perf_counter()
        before = self.stats.derived
        self._saturate(full_seed=True)
        self.stats.seconds += time.perf_counter() - started
        del before

    def add_fact(self, pred: str, args: Sequence[Value]) -> bool:
        """Insert an EDB fact and propagate only its consequences."""
        fact = self._coerce(pred, args)
        if fact in self.edb:
            return False
        relation = self.relations[pred]
        if args and fact[1] in relation:
            # The fact was previously derived; it is now given.  Its old
            # support must go, or ``explain`` would claim it was inferred.
            self._detach_support(fact)
        self.edb.add(fact)
        relation.add(fact[1])
        started = time.perf_counter()
        self._saturate(
            seeds={pred: {fact[1]}},
            start=self.program.stratum_of(pred),
            full_seed=False,
        )
        self.stats.seconds += time.perf_counter() - started
        return True

    def remove_fact(self, pred: str, args: Sequence[Value]) -> Set[Fact]:
        """Retract an EDB fact; return every fact that disappeared as a result.

        Raises if the fact is derived rather than given: retracting a conclusion
        is not a Datalog operation, and quietly accepting it would break the
        invariant that the EDB determines the model.
        """
        fact = self._coerce(pred, args)
        if fact[1] not in self.relations[pred].rows:
            raise EvaluationError(f"{_fact_text(fact)} is not in the database")
        if fact not in self.edb:
            raise EvaluationError(
                f"{_fact_text(fact)} is derived, not given; retract one of its premises "
                f"instead (try: mendel impact)"
            )

        started = time.perf_counter()
        affected = self.support_closure(fact)
        candidates = affected - {fact}
        if self.program.rules_for(fact[0]):
            # The fact may have been promoted to EDB earlier, in which case its
            # support was detached and no dependency edge points at it -- but
            # the rules may still derive it.  Retracting an assertion is not the
            # same as proving the fact false.
            candidates.add(fact)
        for doomed in affected:
            self.relations[doomed[0]].discard(doomed[1])
            self.edb.discard(doomed)
            self._detach_support(doomed)
            self.supporters.pop(doomed, None)

        self.stats.retracted += len(affected)
        # DRed rederivation, restricted to the facts that were overdeleted.
        # Re-saturating from every surviving fact would recompute the whole
        # stratum; checking only the candidates keeps the cost proportional to
        # the affected region.  A candidate may need another candidate that is
        # not back yet, so the pass repeats until it makes no progress.
        pending = set(candidates)
        self._rederive_orders.clear()
        rederived = 0
        while pending:
            progressed = False
            for candidate in list(pending):
                if self._try_rederive(candidate):
                    pending.discard(candidate)
                    rederived += 1
                    progressed = True
            if not progressed:
                break
        self.stats.rederived += rederived
        self.stats.seconds += time.perf_counter() - started
        # Report what is actually gone now, not what was overdeleted.
        return {f for f in affected if f[1] not in self.relations[f[0]].rows}

    # -- reading ---------------------------------------------------------
    def facts_of(self, pred: str) -> Set[Tuple[Value, ...]]:
        return set(self.relations[pred].rows)

    def all_facts(self) -> Set[Fact]:
        out: Set[Fact] = set()
        for pred, relation in self.relations.items():
            for row in relation.rows:
                out.add((pred, row))
        return out

    def is_edb(self, fact: Fact) -> bool:
        return fact in self.edb

    def support_of(self, fact: Fact) -> Optional[Support]:
        return self.supports.get(fact)

    def support_closure(self, fact: Fact) -> Set[Fact]:
        """Facts whose recorded derivation depends on ``fact``, plus itself."""
        seen: Set[Fact] = set()
        queue = [fact]
        while queue:
            current = queue.pop()
            if current in seen:
                continue
            seen.add(current)
            for conclusion in self.supporters.get(current, ()):
                if conclusion not in seen:
                    queue.append(conclusion)
        return seen

    def impact(self, fact: Fact) -> List[Fact]:
        """Upper bound on what a retraction would remove, from recorded supports."""
        return sorted(self.support_closure(fact) - {fact})

    def trial_without(self, fact: Fact) -> "Engine":
        """Rebuild without ``fact`` -- the exact (not just recorded) impact."""
        if fact not in self.edb:
            raise EvaluationError(f"{_fact_text(fact)} is not a given fact")
        trimmed = Program(
            [a for a in self.program.facts if a.as_fact() != fact],
            self.program.rules,
            name=self.program.name,
        )
        return Engine(trimmed)

    def counts(self) -> Dict[str, int]:
        return {pred: len(rel) for pred, rel in sorted(self.relations.items()) if len(rel)}

    # ------------------------------------------------------------------
    # Saturation
    # ------------------------------------------------------------------
    def _saturate(
        self,
        seeds: Optional[Dict[str, Set[Tuple[Value, ...]]]] = None,
        start: int = 0,
        stop: Optional[int] = None,
        full_seed: bool = False,
    ) -> None:
        # ``changed`` carries every fact that appeared during this call, not
        # just the ones in the stratum being evaluated.  A new parent edge must
        # drive a rule that joins it with existing ancestor facts, and that rule
        # lives one stratum above the edge.
        changed: Dict[str, Set[Tuple[Value, ...]]] = defaultdict(set)
        for pred, rows in (seeds or {}).items():
            changed[pred] |= set(rows)

        end = len(self.program.strata) - 1 if stop is None else stop
        if end < start:  # pragma: no cover - defensive, callers clamp
            return

        for position in range(start, end + 1):
            stratum = self.program.strata[position]
            if full_seed:
                delta = {p: set(self.relations[p].rows) for p in stratum.preds}
            else:
                delta = {p: set(changed.get(p, ())) for p in stratum.preds}
            lower = {
                p: set(rows)
                for p, rows in changed.items()
                if p not in stratum.pred_set and rows
            }

            sizes = {p: len(r) for p, r in self.relations.items()}
            plans = {r.name: self.program.plan_indices(r, sizes) for r in stratum.rules}
            seeded_rules: List[Rule] = [
                rule
                for rule in stratum.rules
                if not any(
                    isinstance(literal, Goal) and literal.atom.pred in stratum.pred_set
                    for literal in rule.body
                )
            ]

            first = True
            while first or any(delta.values()):
                self.stats.rounds += 1
                new_delta: Dict[str, Set[Tuple[Value, ...]]] = defaultdict(set)
                # Round one is driven by everything that changed below and in
                # this stratum.  Later rounds are driven only by what this
                # stratum just produced: the lower deltas were already joined
                # against the complete relations.
                driver: Dict[str, Set[Tuple[Value, ...]]] = dict(lower)
                for pred, rows in delta.items():
                    if rows:
                        driver[pred] = rows
                if first:
                    # Rules that do not mention this stratum cannot be reached
                    # by the delta loop, so they get exactly one firing.
                    for rule in seeded_rules:
                        self._fire(rule, None, None, plans[rule.name], new_delta)
                for rule in stratum.rules:
                    for index, literal in enumerate(rule.body):
                        if not isinstance(literal, Goal):
                            continue
                        rows = driver.get(literal.atom.pred)
                        if rows:
                            self._fire(rule, index, rows, plans[rule.name], new_delta)
                for pred, rows in new_delta.items():
                    changed[pred] |= rows
                delta = new_delta
                lower = {}
                first = False

    def _fire(
        self,
        rule: Rule,
        delta_index: Optional[int],
        delta_rows: Optional[Set[Tuple[Value, ...]]],
        order: Sequence[int],
        new_delta: Dict[str, Set[Tuple[Value, ...]]],
    ) -> None:
        """Run one rule (or one delta variant) and insert what it derives.

        Insertion happens after the join finishes.  Relations may be iterated
        as candidate sources during the join, and mutating a set mid-iteration
        would corrupt the scan.
        """
        pending: List[Tuple[str, Tuple[Value, ...], Tuple[Fact, ...], Tuple[Fact, ...]]] = []
        seen: Set[Tuple[str, Tuple[Value, ...]]] = set()
        for env, premises, absences in self._join(rule, order, delta_index, delta_rows):
            self.stats.firings += 1
            pred, args = self._head_fact(rule, env)
            key = (pred, args)
            if key in seen or args in self.relations[pred].rows:
                continue
            seen.add(key)
            pending.append((pred, args, premises, absences))
        for pred, args, premises, absences in pending:
            self._derive((pred, args), rule, premises, absences, new_delta)

    def _join(
        self,
        rule: Rule,
        order: Sequence[int],
        delta_index: Optional[int],
        delta_rows: Optional[Set[Tuple[Value, ...]]],
        initial: Optional[Dict[str, Value]] = None,
        count: bool = True,
    ) -> Iterator[Tuple[Dict[str, Value], Tuple[Fact, ...], Tuple[Fact, ...]]]:
        matched: Dict[int, Fact] = {}
        absent: Dict[int, Fact] = {}
        env: Dict[str, Value] = dict(initial) if initial else {}

        def step(
            depth: int, current: Dict[str, Value]
        ) -> Iterator[Tuple[Dict[str, Value], Tuple[Fact, ...], Tuple[Fact, ...]]]:
            if depth == len(order):
                yield (
                    current,
                    tuple(matched[i] for i in sorted(matched)),
                    tuple(absent[i] for i in sorted(absent)),
                )
                return
            index = order[depth]
            literal = rule.body[index]
            rows = delta_rows if index == delta_index else None
            for nxt, fact in self.expand(literal, current, rule, rows=rows, count=count):
                if fact is not None:
                    matched[index] = fact
                    yield from step(depth + 1, nxt)
                    matched.pop(index, None)
                elif isinstance(literal, NegGoal):
                    absent[index] = _ground_atom(literal.atom, current)
                    yield from step(depth + 1, nxt)
                    absent.pop(index, None)
                else:
                    yield from step(depth + 1, nxt)

        yield from step(0, env)

    def expand(
        self,
        literal: Literal,
        env: Dict[str, Value],
        rule: Rule,
        rows: Optional[Iterable[Tuple[Value, ...]]] = None,
        count: bool = True,
    ) -> Iterator[Tuple[Dict[str, Value], Optional[Fact]]]:
        """Match one body literal against the current database.

        ``rows`` restricts a positive goal to a specific candidate set (the
        delta relation).  This is the only join primitive; the evaluator and
        the provenance diagnostics both go through it, so they cannot disagree
        about what counts as a match.
        """
        if isinstance(literal, Goal):
            atom = literal.atom
            if count:
                self.stats.lookups += 1
            if rows is None:
                positions, key = _bound_key(atom, env)
                candidates = self.relations[atom.pred].match(positions, key)
            else:
                candidates = rows
            for row in candidates:
                nxt = _unify(atom, row, env)
                if nxt is not None:
                    yield nxt, (atom.pred, row)
            return
        if isinstance(literal, NegGoal):
            atom = literal.atom
            if count:
                self.stats.lookups += 1
            positions, key = _bound_key(atom, env)
            for _ in self.relations[atom.pred].match(positions, key):
                return
            yield env, None
            return
        assert isinstance(literal, Compare)
        nxt = _compare(literal, env, rule)
        if nxt is not None:
            yield nxt, None

    def body_matches(
        self,
        rule: Rule,
        initial: Optional[Dict[str, Value]] = None,
        count: bool = False,
    ) -> Iterator[Tuple[Dict[str, Value], Tuple[Fact, ...], Tuple[Fact, ...]]]:
        """All body solutions for ``rule`` given a partially bound head."""
        sizes = {p: len(r) for p, r in self.relations.items()}
        order = self.program.plan_indices(rule, sizes, initial=tuple(initial or ()))
        return self._join(rule, order, None, None, initial=initial, count=count)

    def _head_fact(self, rule: Rule, env: Dict[str, Value]) -> Tuple[str, Tuple[Value, ...]]:
        args: List[Value] = []
        for term in rule.head.args:
            if isinstance(term, Const):
                args.append(term.value)
            else:
                assert isinstance(term, Var)
                try:
                    args.append(env[term.name])
                except KeyError:  # pragma: no cover - safety analysis prevents this
                    raise EvaluationError(
                        f"rule @{rule.name} left ?{term.name} unbound at runtime"
                    ) from None
        return rule.head.pred, tuple(args)

    def _derive(
        self,
        fact: Fact,
        rule: Rule,
        premises: Tuple[Fact, ...],
        absences: Tuple[Fact, ...],
        new_delta: Dict[str, Set[Tuple[Value, ...]]],
    ) -> bool:
        pred, args = fact
        relation = self.relations[pred]
        if args in relation.rows:
            return False
        relation.add(args)
        self.supports[fact] = Support(rule=rule, facts=premises, absences=absences)
        for premise in premises:
            self.supporters[premise].add(fact)
        self.stats.derived += 1
        new_delta[pred].add(args)
        return True

    def _detach_support(self, fact: Fact) -> None:
        support = self.supports.pop(fact, None)
        if support is None:
            return
        for premise in support.facts:
            bucket = self.supporters.get(premise)
            if bucket is not None:
                bucket.discard(fact)

    def _try_rederive(self, fact: Fact) -> bool:
        """Reinsert ``fact`` if the current database still supports it."""
        pred, args = fact
        for rule in self.program.rules_for(pred):
            env = _match_head(rule.head, args)
            if env is None:
                continue
            # The join order depends only on which variables are bound, not on
            # their values, and the relation sizes barely move while a handful
            # of candidates come back.  Re-planning per candidate was the
            # dominant cost of a large retraction.
            key = (rule.name, tuple(sorted(env)))
            order = self._rederive_orders.get(key)
            if order is None:
                sizes = {p: len(r) for p, r in self.relations.items()}
                order = self.program.plan_indices(rule, sizes, initial=tuple(env))
                self._rederive_orders[key] = order
            found = next(self._join(rule, order, None, None, initial=env, count=False), None)
            if found is None:
                continue
            env2, premises, absences = found
            if self._head_fact(rule, env2)[1] != args:  # pragma: no cover - defensive
                continue
            self.relations[pred].add(args)
            self.supports[fact] = Support(rule=rule, facts=premises, absences=absences)
            for premise in premises:
                self.supporters[premise].add(fact)
            self.stats.derived += 1
            return True
        return False

    def _coerce(self, pred: str, args: Sequence[Value]) -> Fact:
        if pred not in self.relations:
            raise EvaluationError(f"unknown predicate {pred!r}")
        expected = self.program.arity[pred]
        args = tuple(args)
        if len(args) != expected:
            raise EvaluationError(
                f"predicate {pred!r} takes {expected} arguments, got {len(args)}"
            )
        for value in args:
            if not isinstance(value, (int, str)) or isinstance(value, bool):
                raise EvaluationError(f"unsupported value {value!r}")
        return pred, args


# --------------------------------------------------------------------------
# Join primitives
# --------------------------------------------------------------------------


def _bound_key(atom: Atom, env: Dict[str, Value]) -> Tuple[Tuple[int, ...], Tuple[Value, ...]]:
    """Which argument positions are known, and their values."""
    positions: List[int] = []
    key: List[Value] = []
    for index, arg in enumerate(atom.args):
        if isinstance(arg, Const):
            positions.append(index)
            key.append(arg.value)
        else:
            assert isinstance(arg, Var)
            if arg.name in env:
                positions.append(index)
                key.append(env[arg.name])
    return tuple(positions), tuple(key)


def _ground_atom(atom: Atom, env: Dict[str, Value]) -> Fact:
    values: List[Value] = []
    for arg in atom.args:
        if isinstance(arg, Const):
            values.append(arg.value)
        else:
            assert isinstance(arg, Var)
            values.append(env[arg.name])
    return atom.pred, tuple(values)


def _match_head(atom: Atom, args: Tuple[Value, ...]) -> Optional[Dict[str, Value]]:
    """Bind a rule head to a ground fact, or None if the constants disagree."""
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


def _unify(atom: Atom, row: Tuple[Value, ...], env: Dict[str, Value]) -> Optional[Dict[str, Value]]:
    """Match ``row`` against ``atom``; return an extended environment or None."""
    extended: Optional[Dict[str, Value]] = None
    for term, value in zip(atom.args, row):
        if isinstance(term, Const):
            if term.value != value:
                return None
            continue
        assert isinstance(term, Var)
        if extended is not None and term.name in extended:
            if extended[term.name] != value:
                return None
            continue
        current = env.get(term.name, _MISSING)
        if current is _MISSING:
            if extended is None:
                extended = dict(env)
            extended[term.name] = value
        elif current != value:
            return None
    return env if extended is None else extended


def _compare(
    literal: Compare, env: Dict[str, Value], rule: Rule
) -> Optional[Dict[str, Value]]:
    """Evaluate a comparison, treating ``?x = <bound>`` as an assignment."""
    left, right = literal.left, literal.right
    left_value = left.value if isinstance(left, Const) else env.get(left.name, _MISSING)
    right_value = right.value if isinstance(right, Const) else env.get(right.name, _MISSING)

    if literal.op == "=":
        if left_value is _MISSING and isinstance(left, Var) and right_value is not _MISSING:
            extended = dict(env)
            extended[left.name] = right_value
            return extended
        if right_value is _MISSING and isinstance(right, Var) and left_value is not _MISSING:
            extended = dict(env)
            extended[right.name] = left_value
            return extended

    if left_value is _MISSING or right_value is _MISSING:  # pragma: no cover - safety
        raise EvaluationError(
            f"rule @{rule.name}: {literal} runs before its variables are bound"
        )
    try:
        ok = literal.evaluate(left_value, right_value)  # type: ignore[arg-type]
    except TypeError:
        raise EvaluationError(
            f"rule @{rule.name}: cannot compare {left_value!r} with {right_value!r} "
            f"({type(left_value).__name__} vs {type(right_value).__name__})"
        ) from None
    return env if ok else None


def _fact_text(fact: Fact) -> str:
    from .syntax import format_value

    pred, args = fact
    return f"{pred}({', '.join(format_value(a) for a in args)})"
