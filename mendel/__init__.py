"""Mendel -- a Datalog engine that shows its work.

Public surface:

>>> from mendel import Program, Engine
>>> program = Program.parse('parent("a", "b"). ancestor(?x, ?y) :- parent(?x, ?y).')
>>> engine = Engine(program)
>>> sorted(engine.facts_of("ancestor"))
[('a', 'b')]

Everything else -- provenance, benchmarks, the CLI -- is reachable from its
own module, so importing the package stays cheap.
"""

from .engine import Engine, Fact, Stats, Support
from .program import Program, Stratum
from .syntax import (
    Atom,
    Compare,
    Const,
    EvaluationError,
    Goal,
    MendelError,
    NegGoal,
    ParseError,
    Rule,
    SafetyError,
    StratificationError,
    Var,
    parse_atom,
    parse_program,
)

__version__ = "1.0.0"

__all__ = [
    "Atom",
    "Compare",
    "Const",
    "Engine",
    "EvaluationError",
    "Fact",
    "Goal",
    "MendelError",
    "NegGoal",
    "ParseError",
    "Program",
    "Rule",
    "SafetyError",
    "Stats",
    "StratificationError",
    "Stratum",
    "Support",
    "Var",
    "parse_atom",
    "parse_program",
    "__version__",
]
