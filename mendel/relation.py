"""Relation storage with lazily built, incrementally maintained indexes.

A relation is a set of tuples plus a dictionary per "index shape".  An index
shape is a tuple of argument positions, e.g. ``(0,)`` for "look up by first
argument".  Buckets are plain lists, which keeps insertion O(1); deletion is
O(bucket), and deletions only happen in the incremental path.

The engine asks for an index shape whenever a join has some arguments bound.
Rebuilding on every insert would dominate the join cost, so each index is
updated in place instead.
"""

from __future__ import annotations

from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

from .syntax import Value

Row = Tuple[Value, ...]


class Relation:
    __slots__ = ("pred", "arity", "rows", "_indexes")

    def __init__(self, pred: str, arity: int):
        self.pred = pred
        self.arity = arity
        self.rows: Set[Row] = set()
        self._indexes: Dict[Tuple[int, ...], Dict[Tuple[Value, ...], List[Row]]] = {}

    # -- basic set operations ---------------------------------------------
    def __len__(self) -> int:
        return len(self.rows)

    def __contains__(self, row: Row) -> bool:
        return row in self.rows

    def __iter__(self) -> Iterator[Row]:
        return iter(self.rows)

    def add(self, row: Row) -> bool:
        if row in self.rows:
            return False
        if len(row) != self.arity:  # pragma: no cover - guarded by the engine
            raise ValueError(f"{self.pred}: expected {self.arity} columns, got {len(row)}")
        self.rows.add(row)
        for positions, index in self._indexes.items():
            key = tuple(row[i] for i in positions)
            index.setdefault(key, []).append(row)
        return True

    def discard(self, row: Row) -> bool:
        if row not in self.rows:
            return False
        self.rows.discard(row)
        for positions, index in self._indexes.items():
            key = tuple(row[i] for i in positions)
            bucket = index.get(key)
            if bucket is not None:
                try:
                    bucket.remove(row)
                except ValueError:  # pragma: no cover - index and set agree by design
                    pass
                if not bucket:
                    del index[key]
        return True

    def clear(self) -> None:
        self.rows.clear()
        self._indexes.clear()

    # -- index access ------------------------------------------------------
    def index(self, positions: Sequence[int]) -> Dict[Tuple[Value, ...], List[Row]]:
        positions = tuple(positions)
        index = self._indexes.get(positions)
        if index is None:
            index = {}
            for row in self.rows:
                key = tuple(row[i] for i in positions)
                index.setdefault(key, []).append(row)
            self._indexes[positions] = index
        return index

    def match(self, positions: Sequence[int], key: Sequence[Value]) -> Iterable[Row]:
        """Rows whose columns at ``positions`` equal ``key``.

        An empty shape means "everything", and is answered with the live set
        rather than a copy -- callers iterate it immediately.
        """
        if not positions:
            return self.rows
        return self.index(positions).get(tuple(key), ())

    def named(self) -> Set[Row]:  # pragma: no cover - convenience for callers
        return set(self.rows)
