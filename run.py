#!/usr/bin/env python3
"""Run the Mendel CLI from a checkout, without installing anything."""

from __future__ import annotations

import sys

from mendel.cli import main

if __name__ == "__main__":
    sys.exit(main())
