#!/usr/bin/env python3
"""Run every demo in order; each one checks its own claims."""

from __future__ import annotations

import sys

from mendel import demos, render


def main() -> int:
    render.use_utf8_stdout()
    for index, demo in enumerate(demos.all_demos()):
        if index:
            print()
        print(demo.title)
        print("─" * render.display_width(demo.title))
        print(demo.body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
