"""Surface syntax: values, terms, atoms, literals, rules, and the parser.

The language is deliberately small.  Everything a rule can say is one of:

* a positive goal ``p(?x, "a")``
* a negated goal ``not p(?x)``
* a comparison ``?x < 3`` (``=``, ``!=``, ``<``, ``<=``, ``>``, ``>=``)

There are no function symbols and no aggregates.  That is what keeps the
Herbrand universe finite, evaluation guaranteed to terminate, and the
provenance graph acyclic -- three properties the rest of the engine leans on.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple, Union

Value = Union[int, str]

#: Comparison operators, longest first so the lexer cannot split ``<=``.
OPERATORS = ("<=", ">=", "!=", "=", "<", ">")


class MendelError(Exception):
    """Base class for every error a user can trigger with a program.

    Carries an optional source position so the CLI can print ``file:line``
    without the caller having to string-match the message.
    """

    def __init__(self, message: str, line: Optional[int] = None, column: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.line = line
        self.column = column

    def located(self, source: str) -> str:
        if self.line is None:
            return f"{source}: {self.message}"
        if self.column is None:
            return f"{source}:{self.line}: {self.message}"
        return f"{source}:{self.line}:{self.column}: {self.message}"


class ParseError(MendelError):
    """The text is not a well-formed program."""


class SafetyError(MendelError):
    """A rule uses a variable it never binds."""


class StratificationError(MendelError):
    """Negation forms a cycle, so the program has no unique least model."""


class EvaluationError(MendelError):
    """Evaluation could not proceed (currently: unknown predicate in a goal)."""


# --------------------------------------------------------------------------
# Terms
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Var:
    """A logic variable.  ``name`` never carries the leading ``?``."""

    name: str

    def __str__(self) -> str:
        return "?" + self.name


@dataclass(frozen=True)
class Const:
    """An integer or string constant."""

    value: Value

    def __str__(self) -> str:
        return format_value(self.value)


Term = Union[Var, Const]


def format_value(value: Value) -> str:
    """Render a constant the way the parser would read it back."""
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def term_vars(term: Term) -> Tuple[str, ...]:
    return (term.name,) if isinstance(term, Var) else ()


# --------------------------------------------------------------------------
# Atoms and literals
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Atom:
    pred: str
    args: Tuple[Term, ...]

    def __str__(self) -> str:
        inner = ", ".join(str(a) for a in self.args)
        return f"{self.pred}({inner})"

    def variables(self) -> Tuple[str, ...]:
        out: List[str] = []
        for arg in self.args:
            if isinstance(arg, Var) and arg.name not in out:
                out.append(arg.name)
        return tuple(out)

    def is_ground(self) -> bool:
        return all(isinstance(a, Const) for a in self.args)

    def as_fact(self) -> Tuple[str, Tuple[Value, ...]]:
        """Turn a ground atom into a relation row.  Raises if not ground."""
        if not self.is_ground():
            raise ValueError(f"{self} is not ground")
        return (self.pred, tuple(a.value for a in self.args))  # type: ignore[union-attr]


@dataclass(frozen=True)
class Goal:
    atom: Atom

    def __str__(self) -> str:
        return str(self.atom)


@dataclass(frozen=True)
class NegGoal:
    atom: Atom

    def __str__(self) -> str:
        return f"not {self.atom}"


@dataclass(frozen=True)
class Compare:
    op: str
    left: Term
    right: Term

    def __str__(self) -> str:
        return f"{self.left} {self.op} {self.right}"

    def evaluate(self, left: Value, right: Value) -> bool:
        if self.op == "=":
            return left == right
        if self.op == "!=":
            return left != right
        # Mixed int/str ordering is a programming error, not a false comparison;
        # the engine rejects it with a located runtime message instead of
        # letting Python raise ``TypeError`` from deep inside a join.
        if self.op == "<":
            return left < right  # type: ignore[operator]
        if self.op == "<=":
            return left <= right  # type: ignore[operator]
        if self.op == ">":
            return left > right  # type: ignore[operator]
        if self.op == ">=":
            return left >= right  # type: ignore[operator]
        raise ValueError(f"unknown operator {self.op!r}")


Literal = Union[Goal, NegGoal, Compare]


def literal_vars(literal: Literal) -> Tuple[str, ...]:
    if isinstance(literal, Goal):
        return literal.atom.variables()
    if isinstance(literal, NegGoal):
        return literal.atom.variables()
    return tuple(dict.fromkeys(term_vars(literal.left) + term_vars(literal.right)))


def contains_only_constants_or_bound(literal: Literal, bound: Iterable[str]) -> bool:
    return all(v in bound for v in literal_vars(literal))


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------


@dataclass
class Rule:
    head: Atom
    body: Tuple[Literal, ...]
    name: Optional[str] = None
    line: int = 0

    @property
    def pred(self) -> str:
        return self.head.pred

    def __str__(self) -> str:
        prefix = f"@{self.name} " if self.name else ""
        if not self.body:
            return f"{prefix}{self.head}."
        body = ", ".join(str(lit) for lit in self.body)
        return f"{prefix}{self.head} :- {body}."


# --------------------------------------------------------------------------
# Lexer
# --------------------------------------------------------------------------

_TOKEN_RE = re.compile(
    r"""
      (?P<space>\s+)
    | (?P<comment>\#[^\n]*|%[^\n]*)
    | (?P<var>\?[A-Za-z_][A-Za-z0-9_]*)
    | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
    | (?P<int>-?[0-9]+)
    | (?P<string>"(?:[^"\\]|\\.)*")
    | (?P<op>:-|<=|>=|!=|=|<|>)
    | (?P<punct>[(),.@])
    """,
    re.VERBOSE,
)

KEYWORDS = frozenset({"not"})

Token = Tuple[str, str, int, int]  # (kind, text, line, column)


def tokenize(text: str, source: str = "<string>") -> List[Token]:
    tokens: List[Token] = []
    pos = 0
    line = 1
    line_start = 0
    while pos < len(text):
        match = _TOKEN_RE.match(text, pos)
        if match is None:
            column = pos - line_start + 1
            snippet = text[pos : pos + 20].splitlines()[0]
            raise ParseError(f"unexpected character {text[pos]!r} in {snippet!r}", line, column)
        kind = match.lastgroup or ""
        value = match.group()
        column = pos - line_start + 1
        if kind not in ("space", "comment"):
            tokens.append((kind, value, line, column))
        newlines = value.count("\n")
        if newlines:
            line += newlines
            line_start = pos + value.rfind("\n") + 1
        pos = match.end()
    return tokens


class Parser:
    """Recursive descent over the token list.

    The grammar is small enough that a table-driven parser would be more
    machinery than language, so this stays hand-written and readable.
    """

    def __init__(self, tokens: Sequence[Token], source: str = "<string>"):
        self.tokens = list(tokens)
        self.pos = 0
        self.source = source

    # -- token helpers ----------------------------------------------------
    def peek(self, offset: int = 0) -> Optional[Token]:
        index = self.pos + offset
        if index >= len(self.tokens):
            return None
        return self.tokens[index]

    def at(self, kind: str, text: Optional[str] = None) -> bool:
        token = self.peek()
        if token is None:
            return False
        if token[0] != kind:
            return False
        return text is None or token[1] == text

    def next(self) -> Token:
        token = self.peek()
        if token is None:
            raise ParseError("unexpected end of input", self._last_line())
        self.pos += 1
        return token

    def expect(self, kind: str, text: Optional[str] = None) -> Token:
        token = self.peek()
        if token is None:
            raise ParseError(
                f"expected {text or kind!r} but reached end of input", self._last_line()
            )
        if token[0] != kind or (text is not None and token[1] != text):
            raise ParseError(
                f"expected {text or kind!r}, found {token[1]!r}", token[2], token[3]
            )
        self.pos += 1
        return token

    def _last_line(self) -> Optional[int]:
        return self.tokens[-1][2] if self.tokens else None

    # -- grammar ----------------------------------------------------------
    def parse_program(self) -> Tuple[List[Atom], List[Rule]]:
        facts: List[Atom] = []
        rules: List[Rule] = []
        while self.peek() is not None:
            statement = self.parse_statement()
            if statement is None:
                continue
            if isinstance(statement, Atom):
                if not statement.is_ground():
                    raise ParseError(
                        f"fact {statement} has variables; add a body with ':-'",
                        self._last_line(),
                    )
                facts.append(statement)
            else:
                rules.append(statement)
        return facts, rules

    def parse_statement(self) -> Optional[Union[Atom, Rule]]:
        label: Optional[str] = None
        line = self.peek()[2] if self.peek() else 0
        if self.at("punct", "@"):
            self.next()
            label = self.expect("ident")[1]
        head = self.parse_atom()
        if self.at("punct", "."):
            self.next()
            if label is not None:
                raise ParseError("@label is only allowed on rules, not on facts", line)
            return head
        self.expect("op", ":-")
        body = self.parse_body()
        self.expect("punct", ".")
        if not body:
            raise ParseError("a rule needs at least one body literal", line)
        return Rule(head=head, body=tuple(body), name=label, line=line)

    def parse_body(self) -> List[Literal]:
        items = [self.parse_literal()]
        while self.at("punct", ","):
            self.next()
            items.append(self.parse_literal())
        return items

    def parse_literal(self) -> Literal:
        if self.at("ident", "not"):
            self.next()
            return NegGoal(self.parse_atom())
        # A goal starts with ``ident (``; anything else is a comparison.
        token = self.peek()
        if token is not None and token[0] == "ident" and self.peek(1) is not None and self.peek(1)[1] == "(":
            return Goal(self.parse_atom())
        left = self.parse_term()
        op = self.expect("op")[1]
        right = self.parse_term()
        return Compare(op=op, left=left, right=right)

    def parse_atom(self) -> Atom:
        name = self.expect("ident")[1]
        self.expect("punct", "(")
        args: List[Term] = []
        if not self.at("punct", ")"):
            args.append(self.parse_term())
            while self.at("punct", ","):
                self.next()
                args.append(self.parse_term())
        self.expect("punct", ")")
        return Atom(pred=name, args=tuple(args))

    def parse_term(self) -> Term:
        token = self.peek()
        if token is None:
            raise ParseError("expected a term, found end of input", self._last_line())
        kind, text, line, column = token
        if kind == "var":
            self.next()
            return Var(text[1:])
        if kind == "int":
            self.next()
            return Const(int(text))
        if kind == "string":
            self.next()
            try:
                return Const(json.loads(text))
            except json.JSONDecodeError as exc:  # pragma: no cover - lexer guards this
                raise ParseError(f"bad string literal {text}: {exc}", line, column) from None
        raise ParseError(f"expected a variable, integer or string, found {text!r}", line, column)


def parse_program(text: str, source: str = "<string>") -> Tuple[List[Atom], List[Rule]]:
    return Parser(tokenize(text, source), source).parse_program()


def parse_atom(text: str, source: str = "<goal>") -> Atom:
    parser = Parser(tokenize(text, source), source)
    atom = parser.parse_atom()
    if parser.peek() is not None:
        token = parser.peek()
        raise ParseError(f"trailing input after goal: {token[1]!r}", token[2], token[3])
    return atom


def parse_fact(text: str, source: str = "<fact>") -> Tuple[str, Tuple[Value, ...]]:
    atom = parse_atom(text, source)
    if not atom.is_ground():
        raise ParseError(f"{atom} is not ground", 1)
    return atom.as_fact()
