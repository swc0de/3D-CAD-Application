"""Command-line entry point: ``python -m pymodeler [command] ...``.

Commands:
    (none)      launch the desktop app
    build       run a build script; save, export, preview, report
    validate    check build scripts without building
    render      preview PNGs of a .pym, .obj or .json file
    export      convert a .pym, .obj or .json file to .pym/.obj/.stl/.glb/.gltf
    info        sizes and contents of a model or script
    ops         list every build-script operation
    docs        regenerate the JSON Schema and the reference docs
    watch       rebuild scripts in a folder whenever they change, writing previews
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from pymodeler import __version__


class CliError(Exception):
    """A user-facing error: printed without a traceback, exit code 1."""


def build_parser() -> argparse.ArgumentParser:
    """Create the argument parser."""
    parser = argparse.ArgumentParser(
        prog="pymodeler",
        description="SketchUp-style 3D modeler, scriptable with JSON build scripts.",
    )
    parser.add_argument("--version", action="version", version=f"pymodeler {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="command")

    b = sub.add_parser("build", help="run a JSON build script")
    b.add_argument("script", help="build script (.json)")
    b.add_argument("-o", "--output", help="save the model (.pym, or any export format)")
    b.add_argument("--export", action="append", default=[], metavar="FILE",
                   help="also export to FILE (.obj/.stl/.glb/.gltf/.pym); repeatable")
    _preview_args(b)
    b.add_argument("--report", nargs="?", const="-", metavar="FILE",
                   help="write a JSON report of every named object's size (to FILE or stdout)")
    b.add_argument("-q", "--quiet", action="store_true", help="only print errors")

    v = sub.add_parser("validate", help="check build scripts without building them")
    v.add_argument("scripts", nargs="+", help="build scripts (.json)")

    r = sub.add_parser("render", help="render preview PNGs of a model or script")
    r.add_argument("model", help=".pym, .obj or .json build script")
    _preview_args(r, required=True)

    e = sub.add_parser("export", help="convert a model or script to other formats")
    e.add_argument("model", help=".pym, .obj or .json build script")
    e.add_argument("outputs", nargs="+", help="output files (.pym/.obj/.stl/.glb/.gltf)")

    i = sub.add_parser("info", help="show the size and contents of a model or script")
    i.add_argument("model", help=".pym, .obj or .json build script")
    i.add_argument("--json", action="store_true", help="print machine-readable JSON")

    sub.add_parser("ops", help="list every build-script operation")

    d = sub.add_parser("docs", help="regenerate the JSON Schema and docs/BUILD_SCRIPT_REFERENCE.md")
    d.add_argument("--check", action="store_true", help="fail if the files are out of date")

    w = sub.add_parser("watch", help="rebuild build scripts whenever they change, writing previews")
    w.add_argument("folder", nargs="?", default="scripts", help="folder of build scripts (default: scripts)")
    w.add_argument("--out", default="out", help="folder for previews and reports (default: out)")
    w.add_argument("--once", action="store_true", help="build every script once, then exit")
    w.add_argument("--interval", type=float, default=0.5, help="seconds between checks (default: 0.5)")
    w.add_argument("--no-preview", action="store_true", help="only build and report, no PNGs")
    w.add_argument("--views", default="iso,front,top,right", help="comma-separated preview views")
    w.add_argument("--size", default="800x600", help="size of each view, e.g. 800x600")
    w.add_argument("--renderer", default="auto", choices=("auto", "gl", "software"), help="preview renderer")

    g = sub.add_parser("gui", help="launch the desktop app (the default)")
    g.add_argument("file", nargs="?", help="model or build script to open")
    g.add_argument("--watch", metavar="FOLDER", nargs="?", const="scripts",
                   help="rebuild scripts saved in FOLDER live (default when launched with no file: "
                        "./scripts if it exists)")
    g.add_argument("--no-watch", action="store_true", help="do not watch a scripts folder")
    return parser


def _preview_args(parser: argparse.ArgumentParser, required: bool = False) -> None:
    parser.add_argument("--preview", required=required, metavar="PNG",
                        help="write a contact sheet PNG plus one PNG per view")
    parser.add_argument("--views", default="iso,front,top,right",
                        help="comma-separated views (iso, front, back, left, right, top, bottom)")
    parser.add_argument("--size", default="800x600", help="size of each view, e.g. 800x600")
    parser.add_argument("--renderer", default="auto", choices=("auto", "gl", "software"),
                        help="preview renderer (auto uses OpenGL when available)")


COMMANDS = ("build", "validate", "render", "export", "info", "ops", "docs", "watch", "gui")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in COMMANDS and not argv[0].startswith("-"):
        argv = ["gui", *argv]  # `python -m pymodeler model.pym` opens the file in the app
    args = build_parser().parse_args(argv)
    handlers = {
        "build": _cmd_build, "validate": _cmd_validate, "render": _cmd_render, "export": _cmd_export,
        "info": _cmd_info, "ops": _cmd_ops, "docs": _cmd_docs, "watch": _cmd_watch, "gui": _cmd_gui,
        None: _cmd_gui,
    }
    try:
        return handlers[args.command](args)
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # pragma: no cover
        return 130


# ---------------------------------------------------------------------- loading


def _load(path_text: str) -> tuple[Any, Any]:
    """Load a .json script (built), .pym or .obj; returns (model, build_result_or_None)."""
    from pymodeler.io import UnsupportedFormatError, load_model_file
    from pymodeler.io.native import ModelFormatError
    from pymodeler.io.obj_import import ObjImportError
    from pymodeler.script.engine import build_file
    from pymodeler.script.errors import ScriptError

    path = Path(path_text)
    if not path.exists():
        raise CliError(f"{path}: no such file")
    if path.suffix.lower() == ".json":
        try:
            result = build_file(path)
        except ScriptError as exc:
            raise CliError(f"{path.name}: {exc}") from None
        return result.model, result
    try:
        return load_model_file(path), None
    except (UnsupportedFormatError, ModelFormatError, ObjImportError, OSError) as exc:
        raise CliError(f"{path.name}: {exc}") from None


def compact_json(data: Any) -> str:
    """Indented JSON with short lists of numbers kept on one line."""
    import re

    text = json.dumps(data, indent=2)
    return re.sub(
        r"\[\s+([-0-9.eE,\s]+?)\s+\]",
        lambda m: "[" + ", ".join(x.strip() for x in m.group(1).split(",")) + "]",
        text,
    )


def _size(text: str) -> tuple[int, int]:
    try:
        w, h = (int(v) for v in text.lower().split("x"))
    except ValueError:
        raise CliError(f"bad --size {text!r} (use e.g. 800x600)") from None
    if not (16 <= w <= 8192 and 16 <= h <= 8192):
        raise CliError("--size must be between 16 and 8192 pixels")
    return w, h


def _preview(model: Any, args: argparse.Namespace, quiet: bool = False) -> None:
    from pymodeler.render.offscreen import render_previews

    views = [v.strip() for v in args.views.split(",") if v.strip()]
    try:
        result = render_previews(model, args.preview, views, _size(args.size), args.renderer)
    except ValueError as exc:
        raise CliError(str(exc)) from None
    if not quiet:
        names = ", ".join(p.name for p in result.views.values())
        print(f"preview: {result.sheet} ({names}; {result.renderer} renderer)")


def _export(model: Any, path: str, source: dict | None = None) -> None:
    from pymodeler.io import UnsupportedFormatError, export_model

    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        export_model(model, path, source)
    except (UnsupportedFormatError, OSError) as exc:
        raise CliError(str(exc)) from None


def _summary(model: Any) -> str:
    from pymodeler.core.units import format_length

    stats = model.stats()
    box = model.bounds()
    size = "empty"
    if box is not None:
        dims = box[1] - box[0]
        size = " x ".join(format_length(float(v), model.units) for v in dims)
    return (f"{stats['faces']} faces, {stats['edges']} edges, {stats['instances']} instances, "
            f"{stats['definitions']} definitions; size {size} (W x D x H)")


# ---------------------------------------------------------------------- commands


def _cmd_build(args: argparse.Namespace) -> int:
    from pymodeler.script.report import build_report

    model, result = _load(args.script)
    if Path(args.script).suffix.lower() != ".json":
        raise CliError("build expects a .json build script (use 'render' or 'export' for models)")
    if not args.quiet:
        print(f"built {Path(args.script).name}: {_summary(model)}")
    for out in [args.output, *args.export]:
        if out:
            _export(model, out, result.script if out.lower().endswith(".pym") else None)
            if not args.quiet:
                print(f"wrote {out}")
    if args.preview:
        _preview(model, args, args.quiet)
    if args.report:
        text = compact_json(build_report(result))
        if args.report == "-":
            print(text)
        else:
            Path(args.report).write_text(text + "\n", encoding="utf-8")
            if not args.quiet:
                print(f"report: {args.report}")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    from pymodeler.script.engine import load_script
    from pymodeler.script.errors import ScriptError
    from pymodeler.script.validate import validate_script

    failures = 0
    for path in args.scripts:
        try:
            data = load_script(path)
        except ScriptError as exc:
            print(f"FAIL {path}: {exc}")
            failures += 1
            continue
        problems = validate_script(data)
        if problems:
            failures += 1
            print(f"FAIL {path}: {len(problems)} problem(s)")
            for p in problems:
                print(f"  - {p}")
        else:
            print(f"OK   {path} ({_count_steps(data.get('steps', []))} steps)")
    return 1 if failures else 0


def _count_steps(steps: Any) -> int:
    if not isinstance(steps, list):
        return 0
    return sum(1 + _count_steps(s.get("steps")) for s in steps if isinstance(s, dict))


def _cmd_render(args: argparse.Namespace) -> int:
    model, _ = _load(args.model)
    _preview(model, args)
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    model, result = _load(args.model)
    for out in args.outputs:
        _export(model, out, result.script if result and out.lower().endswith(".pym") else None)
        print(f"wrote {out}")
    return 0


def _cmd_info(args: argparse.Namespace) -> int:
    from pymodeler.script.report import build_report
    from pymodeler.script.engine import BuildResult

    model, result = _load(args.model)
    report = build_report(result if result is not None else BuildResult(model=model))
    if args.json:
        print(compact_json(report))
        return 0
    print(f"{Path(args.model).name}: {_summary(model)}")
    if model.materials:
        print("materials: " + ", ".join(model.materials))
    for name, obj in report["objects"].items():
        box = obj["bounds"]
        if box is None:
            continue
        size = " x ".join(f"{v:g}" for v in box["size"])
        at = ", ".join(f"{v:g}" for v in box["min"])
        solid = "solid" if obj["solid"] else "open"
        print(f"  {name:<20} size {size} {model.units} at [{at}] ({solid}, {obj['parts']} part(s))")
    return 0


def _cmd_ops(args: argparse.Namespace) -> int:
    from pymodeler.ops.registry import all_ops

    for spec in all_ops():
        params = " ".join(p.name + ("*" if p.required else "") for p in spec.params)
        print(f"{spec.name:<15} {spec.summary}\n{'':<15} params: {params}")
    print("\n* = required. Full reference: docs/BUILD_SCRIPT_REFERENCE.md")
    return 0


def _cmd_docs(args: argparse.Namespace) -> int:
    from pymodeler.script.docs import (
        CLAUDE_PATH,
        DOCS_PATH,
        generate_docs,
        updated_claude_md,
        write_claude_md,
        write_docs,
    )
    from pymodeler.script.schema import SCHEMA_PATH, schema_text, write_schema

    if args.check:
        stale = []
        if not SCHEMA_PATH.exists() or SCHEMA_PATH.read_text(encoding="utf-8") != schema_text():
            stale.append(str(SCHEMA_PATH))
        if not DOCS_PATH.exists() or DOCS_PATH.read_text(encoding="utf-8") != generate_docs():
            stale.append(str(DOCS_PATH))
        if CLAUDE_PATH.exists():
            text = CLAUDE_PATH.read_text(encoding="utf-8")
            if updated_claude_md(text) != text:
                stale.append(str(CLAUDE_PATH))
        if stale:
            raise CliError("out of date (run 'python -m pymodeler docs'): " + ", ".join(stale))
        print("schema and docs are up to date")
        return 0
    print(f"wrote {write_schema()}")
    print(f"wrote {write_docs()}")
    if CLAUDE_PATH.exists():
        print(f"updated {write_claude_md()}")
    return 0


def _cmd_watch(args: argparse.Namespace, cycles: int | None = None) -> int:
    """Build every script now, then each one again whenever it is saved (until Ctrl+C).

    ``cycles`` limits the number of checks (for tests).
    """
    import time

    from pymodeler.script.watch import FolderWatcher

    folder = Path(args.folder)
    if not folder.is_dir():
        raise CliError(f"{folder}: no such folder")
    _size(args.size)  # fail early on a bad size
    watcher = FolderWatcher(folder, report_existing=True, settle=False)
    failures = sum(not _watch_build(p, args) for p in watcher.poll())
    if args.once:
        return 1 if failures else 0
    watcher.settle = True
    print(f"watching {folder}/ for changes (Ctrl+C to stop)", flush=True)
    count = 0
    while cycles is None or count < cycles:
        time.sleep(args.interval)
        for path in watcher.poll():
            _watch_build(path, args)
        count += 1
    return 0


def _watch_build(path: Path, args: argparse.Namespace) -> bool:
    """Build one watched script, writing ``<out>/<name>.png`` and ``<out>/<name>_report.json``."""
    import time

    from pymodeler.render.offscreen import render_previews
    from pymodeler.script.engine import build_file
    from pymodeler.script.errors import ScriptError
    from pymodeler.script.report import build_report

    stamp = time.strftime("%H:%M:%S")
    try:
        result = build_file(path)
    except ScriptError as exc:
        print(f"[{stamp}] FAIL {path.name}: {exc}", flush=True)
        return False
    except Exception as exc:  # noqa: BLE001 - a broken script must never stop the watcher
        print(f"[{stamp}] FAIL {path.name}: internal error: {exc!r}", flush=True)
        return False
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{path.stem}_report.json").write_text(compact_json(build_report(result)) + "\n", encoding="utf-8")
    line = f"[{stamp}] built {path.name}: {_summary(result.model)}"
    if not args.no_preview:
        views = [v.strip() for v in args.views.split(",") if v.strip()]
        try:
            preview = render_previews(result.model, out / f"{path.stem}.png", views, _size(args.size), args.renderer)
        except ValueError as exc:
            raise CliError(str(exc)) from None
        line += f"; preview {preview.sheet}"
    print(line, flush=True)
    return True


def _cmd_gui(args: argparse.Namespace) -> int:
    from pymodeler.ui.app import run_app

    file = getattr(args, "file", None)
    watch = getattr(args, "watch", None)
    if watch is None and file is None and not getattr(args, "no_watch", False) and Path("scripts").is_dir():
        watch = "scripts"
    if getattr(args, "no_watch", False):
        watch = None
    return run_app(file, watch=watch)
