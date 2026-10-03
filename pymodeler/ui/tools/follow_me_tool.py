"""The Follow Me tool: sweep a profile face along a path of edges."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt

from pymodeler.core.entities import Edge, Face
from pymodeler.core.vec import GeometryError
from pymodeler.ops.extrude import edge_chain, follow_me, path_from_edges, start_path_near
from pymodeler.ui.tools.edit_tools import _EditTool

if TYPE_CHECKING:
    from PySide6.QtGui import QMouseEvent


def face_loop_edges(face: Face) -> list[Edge]:
    """The edges of a face's outer boundary."""
    loop = face.outer_loop
    assert face.parent is not None
    edges = [face.parent.edge_between(loop[i], loop[(i + 1) % len(loop)]) for i in range(len(loop))]
    return [e for e in edges if e is not None]


class FollowMeTool(_EditTool):
    """SketchUp's Follow Me.

    Either select the path first (edges, or one face whose perimeter is the path) and
    click the profile face; or click the profile face, then click an edge of the path.
    A clicked edge that borders a face (other than the profile) uses that face's
    perimeter; otherwise the chain of edges running through it.
    """

    name = "Follow Me"
    shortcut = ""
    hint = "Select a path (edges or a face), then click the profile face. Or click the profile, then the path."

    def __init__(self, viewport) -> None:  # type: ignore[no-untyped-def]
        super().__init__(viewport)
        self.profile: Face | None = None
        self.path: list[Edge] = []

    def activate(self) -> None:
        super().activate()
        self.profile = None
        self.path = self._selected_path()

    def reset(self) -> None:
        super().reset()
        self.profile = None
        self.path = []

    def busy(self) -> bool:
        return self.profile is not None

    def _selected_path(self) -> list[Edge]:
        items = [e for e in self.viewport.selection.items() if e.parent is self.active]
        edges = [e for e in items if isinstance(e, Edge)]
        faces = [e for e in items if isinstance(e, Face)]
        if len(faces) == 1 and all(e in faces[0].edges() for e in edges):
            return face_loop_edges(faces[0])  # a selected face (with or without its edges)
        return edges

    def path_for_edge(self, edge: Edge, profile: Face) -> list[Edge]:
        """The path a clicked edge stands for (see the class docstring)."""
        profile_edges = set(profile.edges())
        for face in edge.faces:
            if face is not profile:
                return face_loop_edges(face)
        return edge_chain(edge, exclude=profile_edges)

    def mouse_move(self, event: "QMouseEvent") -> bool:
        self.cursor = event.position()
        x, y = self.cursor.x(), self.cursor.y()
        if self.profile is None:
            entity = self.entity_under(x, y, prefer_faces=True)
            self.viewport.set_hover([entity] if isinstance(entity, Face) else [])
        else:
            entity = self.entity_under(x, y)
            hover: list = [self.profile]
            if isinstance(entity, Edge) and entity not in self.profile.edges():
                hover += self.path_for_edge(entity, self.profile)
            self.viewport.set_hover(hover)
        return True

    def mouse_press(self, event: "QMouseEvent") -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        self.cursor = event.position()
        x, y = self.cursor.x(), self.cursor.y()
        if self.profile is None:
            face = self.entity_under(x, y, prefer_faces=True)
            if not isinstance(face, Face):
                self.message = "Click a face to use as the profile"
                return True
            if self.path:
                self.sweep(face, self.path)
            else:
                self.profile = face
                self.message = ""
            return True
        edge = self.entity_under(x, y)
        if isinstance(edge, Edge) and edge not in self.profile.edges():
            self.sweep(self.profile, self.path_for_edge(edge, self.profile))
        else:
            self.message = "Click an edge of the path"
        return True

    def sweep(self, profile: Face, path: list[Edge]) -> bool:
        """Run Follow Me as one undoable command."""
        path = [e for e in path if e.alive and e not in profile.edges()]
        try:
            points = start_path_near(path_from_edges(path), profile)
        except GeometryError as exc:
            self.message = f"Cannot follow this path: {exc}"
            return False
        active = self.active
        ok = self.commit("Follow Me", lambda: follow_me(active, profile, points))
        if ok:
            self.viewport.selection.clear()
            self.profile = None
            self.path = []
            self.viewport.set_hover([])
        else:
            self.message = self.message.replace("Could not draw", "Follow Me failed")
        return ok

    def status(self) -> str:
        if self.message:
            return self.message
        if self.path:
            return f"Path: {len(self.path)} edge(s). Click the profile face to sweep it along the path."
        if self.profile is not None:
            return "Click an edge of the path (an edge on a face uses the face's perimeter). Esc cancels."
        return self.hint
