"""Headless PNG previews: standard views plus a labelled 2x2 contact sheet.

``render_previews(model, "house.png")`` writes ``house_iso.png``, ``house_front.png``,
``house_top.png``, ``house_right.png`` and the contact sheet ``house.png`` showing all
four with the model's overall dimensions, so one image is enough to check a build.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from pymodeler.core.model import Model
from pymodeler.core.units import format_length
from pymodeler.render.camera import STANDARD_VIEWS, Camera
from pymodeler.render.scene import SceneData, build_scene
from pymodeler.render.software import render_software

DEFAULT_VIEWS = ("iso", "front", "top", "right")
RENDERERS = ("auto", "gl", "software")


@dataclass
class PreviewResult:
    """Files written by :func:`render_previews`."""

    sheet: Path
    views: dict[str, Path] = field(default_factory=dict)
    renderer: str = "software"


def render_view(
    scene: SceneData,
    view: str,
    width: int = 800,
    height: int = 600,
    renderer: str = "auto",
    perspective: bool | None = None,
) -> tuple[np.ndarray, str]:
    """Render one standard view.

    Returns:
        ``(image, renderer_used)`` where the image is (height, width, 3) uint8.

    Raises:
        ValueError: on an unknown view or renderer, or if ``renderer="gl"`` is
            requested but no OpenGL context can be created.
    """
    if renderer not in RENDERERS:
        raise ValueError(f"unknown renderer {renderer!r} (use {', '.join(RENDERERS)})")
    camera = Camera.standard(view, scene.bounds, width / height, perspective)
    if renderer in ("auto", "gl"):
        from pymodeler.render.gl_renderer import render_gl_offscreen, standalone_context

        image = render_gl_offscreen(scene, camera, width, height) if standalone_context() else None
        if image is not None:
            return image, "gl"
        if renderer == "gl":
            raise ValueError("no OpenGL context available; use --renderer software")
    return render_software(scene, camera, width, height), "software"


def render_previews(
    model: Model,
    out_path: str | Path,
    views: Sequence[str] = DEFAULT_VIEWS,
    size: tuple[int, int] = (800, 600),
    renderer: str = "auto",
    title: str | None = None,
) -> PreviewResult:
    """Render standard views to PNG files plus a contact sheet at ``out_path``.

    Raises:
        ValueError: on unknown view names or renderer.
    """
    unknown = [v for v in views if v not in STANDARD_VIEWS]
    if unknown:
        raise ValueError(f"unknown view(s) {', '.join(unknown)} (use {', '.join(STANDARD_VIEWS)})")
    out = Path(out_path)
    if out.suffix.lower() != ".png":
        out = out.with_suffix(".png")
    out.parent.mkdir(parents=True, exist_ok=True)
    scene = build_scene(model)
    width, height = size
    result = PreviewResult(sheet=out)
    tiles: list[tuple[str, Image.Image]] = []
    for view in views:
        image, used = render_view(scene, view, width, height, renderer)
        result.renderer = used
        tile = Image.fromarray(image)
        path = out.with_name(f"{out.stem}_{view}.png")
        tile.save(path)
        result.views[view] = path
        tiles.append((view, tile))
    contact_sheet(tiles, scene, model.units, title or out.stem).save(out)
    return result


def contact_sheet(tiles: list[tuple[str, Image.Image]], scene: SceneData, units: str, title: str) -> Image.Image:
    """Arrange labelled tiles in a grid with a header showing the model's size."""
    if not tiles:
        raise ValueError("no views to arrange")
    tw, th = tiles[0][1].size
    cols = 2 if len(tiles) > 1 else 1
    rows = (len(tiles) + cols - 1) // cols
    header = 34
    sheet = Image.new("RGB", (cols * tw + (cols - 1) * 4, header + rows * th + (rows - 1) * 4), (60, 64, 70))
    draw = ImageDraw.Draw(sheet)
    font = _font(17)
    small = _font(15)
    draw.text((10, 8), f"{title}   {dimension_text(scene, units)}", fill=(255, 255, 255), font=font)
    for i, (name, tile) in enumerate(tiles):
        x = (i % cols) * (tw + 4)
        y = header + (i // cols) * (th + 4)
        sheet.paste(tile, (x, y))
        label = f"{name.upper()}" + ("" if name == "iso" else "  (parallel)")
        box = draw.textbbox((x + 8, y + 6), label, font=small)
        draw.rectangle((box[0] - 4, box[1] - 3, box[2] + 4, box[3] + 3), fill=(255, 255, 255))
        draw.text((x + 8, y + 6), label, fill=(30, 30, 30), font=small)
    return sheet


def dimension_text(scene: SceneData, units: str) -> str:
    """``"W 4m x D 3m x H 2.4m  |  grid 500mm"`` for the scene bounds."""
    if scene.bounds is None:
        return "(empty model)"
    size = scene.bounds[1] - scene.bounds[0]
    dims = " x ".join(f"{axis} {format_length(float(v), units)}" for axis, v in zip("WDH", size, strict=True))
    grid = f"  |  grid {format_length(scene.grid_spacing, units)}" if scene.grid_spacing else ""
    return f"{dims}{grid}"


def _font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    """Pillow's bundled font at ``size`` (falls back to the bitmap default)."""
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()
