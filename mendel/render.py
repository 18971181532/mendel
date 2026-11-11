"""Terminal rendering helpers: width-aware padding, tables and numbers.

CJK characters occupy two columns in a terminal, so ``len()`` is the wrong
ruler for any table that contains Chinese text.  Everything here measures
display width instead, and the table renderer strips trailing spaces so the
output survives ``diff`` and copy-paste.
"""

from __future__ import annotations

import shutil
import sys
import unicodedata
from typing import Iterable, List, Optional, Sequence

_UNICODE_OK = True


def display_width(text: str) -> int:
    width = 0
    for char in text:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in "WF" else 1
    return width


def pad(text: str, width: int, align: str = "left") -> str:
    space = max(0, width - display_width(text))
    if align == "right":
        return " " * space + text
    if align == "center":
        left = space // 2
        return " " * left + text + " " * (space - left)
    return text + " " * space


def table(
    headers: Sequence[str],
    rows: Iterable[Sequence[str]],
    aligns: Optional[Sequence[str]] = None,
    indent: str = "",
) -> str:
    body: List[List[str]] = [[str(cell) for cell in row] for row in rows]
    columns = len(headers)
    widths = [display_width(h) for h in headers]
    for row in body:
        if len(row) != columns:  # pragma: no cover - programming error
            raise ValueError(f"row has {len(row)} cells, expected {columns}")
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], display_width(cell))
    aligns = list(aligns) if aligns else ["left"] * columns

    def render(cells: Sequence[str]) -> str:
        return indent + "  ".join(
            pad(cell, widths[i], aligns[i]) for i, cell in enumerate(cells)
        ).rstrip()

    lines = [render(list(headers))]
    lines.append(indent + "  ".join("─" * w for w in widths))
    for row in body:
        lines.append(render(row))
    return "\n".join(lines)


def number(value: int) -> str:
    return f"{value:,}"


def seconds(value: float) -> str:
    if value < 1e-3:
        return f"{value * 1e6:.0f} µs"
    if value < 1:
        return f"{value * 1e3:.1f} ms"
    return f"{value:.2f} s"


def bar(value: float, maximum: float, width: int = 28) -> str:
    if maximum <= 0:
        return ""
    filled = min(width, max(0, round(width * value / maximum)))
    return "█" * filled + "·" * (width - filled)


def heading(text: str) -> str:
    return f"{text}\n{'─' * display_width(text)}"


def wrap(text: str, width: int = 88, indent: str = "") -> List[str]:
    words = text.split()
    lines: List[str] = []
    current = indent.rstrip()
    for word in words:
        candidate = f"{current} {word}" if current else indent + word
        if display_width(candidate) > width and current.strip():
            lines.append(current.rstrip())
            current = indent + word
        else:
            current = candidate
    if current.strip():
        lines.append(current.rstrip())
    return lines


def use_utf8_stdout() -> None:
    """Make box drawing and µ render on a Windows console."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # pragma: no cover - unusual console
                pass


def glyph(text: str) -> str:
    """ASCII fallback for terminals that cannot render the Unicode set."""
    if _UNICODE_OK:
        return text
    return (
        text.replace("─", "-")
        .replace("│", "|")
        .replace("├", "+")
        .replace("└", "+")
        .replace("█", "#")
        .replace("·", ".")
        .replace("µ", "u")
        .replace("✓", "ok")
        .replace("✗", "x")
    )


def terminal_width(default: int = 88) -> int:
    return shutil.get_terminal_size((default, 24)).columns
