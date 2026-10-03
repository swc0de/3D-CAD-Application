"""Command-line entry point for PyModeler."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from pymodeler import __version__


def build_parser() -> argparse.ArgumentParser:
    """Create the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="pymodeler",
        description="SketchUp-style 3D modeler, scriptable with JSON build scripts.",
    )
    parser.add_argument("--version", action="version", version=f"pymodeler {__version__}")
    parser.add_subparsers(dest="command")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        print(
            "PyModeler {0}: the geometry kernel is ready; the GUI and build-script "
            "commands arrive in later phases (see PLAN.md).".format(__version__)
        )
        return 0
    parser.print_help(sys.stderr)
    return 2
