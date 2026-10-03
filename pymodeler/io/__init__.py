"""Model file formats: native ``.pym``, OBJ/STL/glTF export and OBJ import."""

from __future__ import annotations

from pathlib import Path

from pymodeler.core.model import Model

EXPORT_FORMATS = (".pym", ".obj", ".stl", ".glb", ".gltf")
"""File extensions :func:`export_model` can write."""


class UnsupportedFormatError(ValueError):
    """Raised for file extensions PyModeler cannot read or write."""


def export_model(model: Model, path: str | Path, source: dict | None = None) -> None:
    """Write ``model`` to ``path``, choosing the format from the file extension.

    Raises:
        UnsupportedFormatError: for an unknown extension.
    """
    from pymodeler.io.gltf import export_glb, export_gltf
    from pymodeler.io.native import save_model
    from pymodeler.io.obj import export_obj
    from pymodeler.io.stl import export_stl

    suffix = Path(path).suffix.lower()
    writers = {
        ".pym": lambda: save_model(model, path, source),
        ".obj": lambda: export_obj(model, path),
        ".stl": lambda: export_stl(model, path),
        ".glb": lambda: export_glb(model, path),
        ".gltf": lambda: export_gltf(model, path),
    }
    if suffix not in writers:
        raise UnsupportedFormatError(
            f"cannot export {suffix or 'files without an extension'} (use {', '.join(EXPORT_FORMATS)})"
        )
    writers[suffix]()


def load_model_file(path: str | Path) -> Model:
    """Open a ``.pym`` model or import an ``.obj`` file into a new model.

    Raises:
        UnsupportedFormatError: for other extensions.
    """
    from pymodeler.io.native import load_model
    from pymodeler.io.obj_import import import_obj

    suffix = Path(path).suffix.lower()
    if suffix == ".pym":
        return load_model(path)
    if suffix == ".obj":
        model = Model()
        import_obj(model, path)
        return model
    raise UnsupportedFormatError(f"cannot open {suffix or 'files without an extension'} (use .pym or .obj)")
