"""Groups and components in the GUI, the Paint Bucket and the side panels."""

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QContextMenuEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from pymodeler.core.components import ComponentInstance
from pymodeler.core.entities import Face
from pymodeler.core.materials import Material
from pymodeler.core.transform import translation
from pymodeler.ops import draw
from pymodeler.ops.organize import make_group
from pymodeler.ops.transform import copy_entities, transform_entities

from .test_draw_tools import at, click, hover


@pytest.fixture
def top(window):
    vp = window.viewport
    vp.set_perspective(False)
    vp.set_view("top")
    vp.camera.ortho_height = 6000.0
    vp.camera.target = np.array([1500.0, 1000.0, 0.0])
    vp.camera.eye = np.array([1500.0, 1000.0, 10000.0])
    return window


def root(window):
    return window.document.model.entities


def grouped_square(window, offset=(2000.0, 0.0, 0.0)) -> ComponentInstance:
    """A 1 m square on the ground, grouped and moved by ``offset``."""
    model = window.document.model

    def setup() -> ComponentInstance:
        drawn = draw.rectangle(model.entities, (0, 0, 0), 1000, 1000)
        inst = make_group(model, model.entities, drawn.faces + drawn.edges)
        transform_entities(model.entities, [inst], translation(offset))
        return inst

    return window.document.perform("Setup", setup)


def add_material(window, name: str, color=(0.6, 0.3, 0.2)) -> None:
    model = window.document.model
    window.document.perform("Material", lambda: model.add_material(Material(name, color)))


# ---------------------------------------------------------------------------- group editing
def test_make_group_explode_and_undo(top) -> None:
    model = top.document.model
    top.document.perform("Setup", lambda: draw.rectangle(model.entities, (0, 0, 0), 1000, 1000))
    top.select_all()
    inst = top.make_group()
    assert isinstance(inst, ComponentInstance) and inst.is_group
    assert not root(top).faces and len(root(top).instances) == 1
    assert top.viewport.selection.items() == [inst]
    assert len(inst.definition.entities.faces) == 1
    top.explode_selection()
    assert len(root(top).faces) == 1 and not root(top).instances
    assert len(top.viewport.selection) == 5, "exploded geometry stays selected"
    top.undo()
    assert len(root(top).instances) == 1 and not root(top).faces
    top.undo()
    assert len(root(top).faces) == 1 and not root(top).instances


def test_make_component_and_make_unique(top) -> None:
    model = top.document.model
    top.document.perform("Setup", lambda: draw.rectangle(model.entities, (0, 0, 0), 500, 500))
    top.select_all()
    top.ask_text = lambda title, label, default="": "Tile"
    inst = top.make_component()
    assert inst is not None and not inst.is_group and inst.definition.name == "Tile"
    copy = top.document.perform("Copy", lambda: copy_entities(root(top), [inst], translation((1000, 0, 0))))
    second = copy.instances[0]
    assert second.definition is inst.definition, "component copies share their definition"
    top.viewport.selection.set([inst, second])
    assert top.actions_by_name["make_unique"].isEnabled()
    top.make_unique_selection()
    assert inst.definition is not second.definition
    assert {inst.definition.name, second.definition.name} == {"Tile", "Tile#2"}


def test_draw_inside_a_group_uses_its_coordinates(top) -> None:
    inst = grouped_square(top)
    top.activate_tool("select")
    QTest.mouseDClick(top.viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                      at(top, (2500, 500, 0)))
    assert top.document.edit_path == [inst]
    assert top.actions_by_name["close_group"].isEnabled()
    top.activate_tool("rectangle")
    click(top, (2200, 200, 0))
    hover(top, (2400, 400, 0))
    click(top, (2400, 400, 0))
    inner = inst.definition.entities
    assert len(inner.faces) == 2, "the rectangle splits the group's face"
    assert not root(top).faces, "nothing was drawn outside the group"
    xs = sorted({round(v.position[0]) for v in inner.vertices.values()})
    assert np.allclose(xs, [0, 200, 400, 1000], atol=15), "vertices are stored in the group's own coordinates"
    top.undo()
    assert top.document.edit_path, "undo keeps the group open"
    assert len(top.document.edit_path[0].definition.entities.faces) == 1
    top.activate_tool("select")
    QTest.keyClick(top.viewport, Qt.Key.Key_Escape)
    assert top.document.edit_path == []


def test_click_outside_closes_group_and_menu_actions(top) -> None:
    inst = grouped_square(top)
    top.viewport.selection.set([inst])
    top.edit_group()
    assert top.document.edit_path == [inst]
    top.activate_tool("select")
    click(top, (-1500, 1800, 0))
    assert top.document.edit_path == []
    top.viewport.selection.set([inst])
    top.edit_group()
    top.close_group()
    assert top.document.edit_path == [] and top.viewport.selection.items() == [inst]


def test_opening_a_copied_group_makes_it_unique(top) -> None:
    inst = grouped_square(top)
    copy = top.document.perform("Copy", lambda: copy_entities(root(top), [inst], translation((0, 1500, 0))))
    other = copy.instances[0]
    assert other.definition is inst.definition
    top.document.enter(other)
    assert other.definition is not inst.definition
    top.document.perform("Draw", lambda: draw.rectangle(other.definition.entities, (100, 100, 0), 200, 200))
    assert len(other.definition.entities.faces) == 2
    assert len(inst.definition.entities.faces) == 1, "the original group is untouched"


def test_hide_and_unhide_all(top) -> None:
    inst = grouped_square(top)
    top.viewport.selection.set([inst])
    top.hide_selection()
    assert inst.hidden and not top.viewport.selection.items()
    top.unhide_all()
    assert not inst.hidden


def test_context_menu_selects_and_lists_actions(top) -> None:
    inst = grouped_square(top)
    shown = []
    top.exec_menu = lambda menu, pos: shown.append([a.text() for a in menu.actions() if not a.isSeparator()])
    top.activate_tool("select")
    pos = at(top, (2500, 500, 0))
    QApplication.sendEvent(top.viewport, QContextMenuEvent(QContextMenuEvent.Reason.Mouse, pos,
                                                           top.viewport.mapToGlobal(pos)))
    assert top.viewport.selection.items() == [inst]
    assert shown and "E&xplode" in shown[0] and "&Edit Group/Component" in shown[0]
    assert "Make &Unique" not in shown[0]


def test_right_click_cancels_a_drawing_operation(top) -> None:
    top.activate_tool("line")
    click(top, (0, 0, 0))
    assert top.viewport.tool.busy()
    top.exec_menu = lambda menu, pos: pytest.fail("no menu while drawing")
    pos = at(top, (500, 0, 0))
    QApplication.sendEvent(top.viewport, QContextMenuEvent(QContextMenuEvent.Reason.Mouse, pos,
                                                           top.viewport.mapToGlobal(pos)))
    assert not top.viewport.tool.busy()


# ---------------------------------------------------------------------------- paint bucket
def test_paint_bucket_paints_the_visible_side(top) -> None:
    model = top.document.model
    top.document.perform("Setup", lambda: draw.rectangle(model.entities, (0, 0, 0), 1000, 1000))
    add_material(top, "Brick")
    face = next(iter(root(top).faces.values()))
    assert face.normal[2] > 0
    top.viewport.current_material = "Brick"
    top.activate_tool("paint")
    click(top, (500, 500, 0))
    assert face.material == "Brick" and face.back_material is None
    top.viewport.set_view("bottom")
    top.viewport.zoom_extents()
    top.viewport.current_material = None
    click(top, (500, 500, 0))
    assert face.material == "Brick" and face.back_material is None, "from below, the back is painted"
    add_material(top, "Tile")
    top.viewport.current_material = "Tile"
    click(top, (500, 500, 0))
    assert face.back_material == "Tile"
    top.undo()
    assert next(iter(root(top).faces.values())).back_material is None


def test_paint_bucket_groups_selection_and_sampling(top) -> None:
    inst = grouped_square(top)
    model = top.document.model
    top.document.perform("Setup", lambda: draw.rectangle(model.entities, (0, 0, 0), 1000, 1000))
    add_material(top, "Oak")
    top.viewport.current_material = "Oak"
    top.activate_tool("paint")
    click(top, (2500, 500, 0))
    assert inst.material == "Oak", "clicking a group paints the whole group"
    face = next(iter(root(top).faces.values()))
    top.viewport.selection.set([face, inst])
    add_material(top, "Stone")
    top.viewport.current_material = "Stone"
    click(top, (500, 500, 0))
    assert face.material == "Stone" and inst.material == "Stone", "clicking a selected face paints the selection"
    top.viewport.current_material = None
    sampled = []
    top.viewport.on_material_sampled = sampled.append
    QTest.mouseClick(top.viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.AltModifier, at(top, (2500, 500, 0)))
    assert top.viewport.current_material == "Stone" and sampled == ["Stone"]


# ---------------------------------------------------------------------------- panels
def test_panels_are_docked_and_toggleable(window) -> None:
    assert set(window.panels) == {"entityinfo", "materials", "tags", "outliner"}
    titles = [a.text() for a in window.panels_menu.actions()]
    assert titles == ["Entity Info", "Materials", "Tags", "Outliner"]


def test_materials_panel_create_pick_edit_delete(window) -> None:
    panel = window.panels["materials"]
    panel.ask_text = lambda *a, **k: "Oak"
    panel.ask_color = lambda initial: QColor(150, 100, 50)
    panel.new_material()
    model = window.document.model
    assert "Oak" in model.materials and window.viewport.current_material == "Oak"
    assert np.allclose(model.materials["Oak"].color, (150 / 255, 100 / 255, 50 / 255))
    panel.pick(None)
    assert window.viewport.current_material is None
    panel.pick("Oak")
    assert window.viewport.tool is window.tools["paint"], "picking a material switches to the Paint Bucket"
    panel.ask_color = lambda initial: QColor(255, 0, 0)
    panel.ask_number = lambda *a: 0.5
    panel.edit_material()
    assert model.materials["Oak"].opacity == 0.5 and model.materials["Oak"].color == (1.0, 0.0, 0.0)
    window.document.perform("Setup", lambda: draw.rectangle(model.entities, (0, 0, 0), 100, 100))
    face = next(iter(model.entities.faces.values()))
    window.document.perform("Paint", lambda: setattr(face, "material", "Oak"))
    panel.pick("Oak")
    panel.delete_material()
    assert "Oak" not in model.materials and face.material is None
    window.undo()
    assert "Oak" in window.document.model.materials


def test_tags_panel_add_toggle_delete(window) -> None:
    panel = window.panels["tags"]
    panel.ask_text = lambda *a, **k: "Walls"
    panel.add_tag()
    model = window.document.model
    assert "Walls" in model.tags
    item = next(panel.list.item(i) for i in range(panel.list.count()) if panel.list.item(i).text() == "Walls")
    item.setCheckState(Qt.CheckState.Unchecked)
    assert not window.document.model.tags["Walls"].visible
    window.undo()
    assert window.document.model.tags["Walls"].visible
    item = next(panel.list.item(i) for i in range(panel.list.count()) if panel.list.item(i).text() == "Walls")
    panel.list.setCurrentItem(item)
    panel.delete_tag()
    assert "Walls" not in window.document.model.tags


def test_outliner_lists_selects_and_opens_groups(top) -> None:
    inst = grouped_square(top)
    model = top.document.model
    nested = top.document.perform("Nest", lambda: make_group(
        model, inst.definition.entities, list(inst.definition.entities.faces.values())))
    panel = top.panels["outliner"]
    assert panel.item_for(inst) is not None and panel.item_for(nested) is not None
    top.viewport.selection.set([inst])
    assert panel.tree.currentItem() is panel.item_for(inst)
    panel.select_path((inst, nested))
    assert top.document.edit_path == [inst]
    assert top.viewport.selection.items() == [nested]
    panel.select_path((inst,))
    assert top.document.edit_path == [] and top.viewport.selection.items() == [inst]
    panel.open_path((inst, nested))
    assert top.document.edit_path == [inst, nested]


def test_entity_info_edits_are_undoable(top) -> None:
    inst = grouped_square(top)
    add_material(top, "Brick")
    top.document.perform("Tag", lambda: top.document.model.add_tag("Walls"))
    panel = top.panels["entityinfo"]
    top.viewport.selection.set([inst])
    assert "1 group selected" in panel.summary.text()
    panel.name_edit.setText("Shed")
    panel._name_changed()
    assert inst.name == "Shed"
    panel.material_combo.setCurrentText("Brick")
    panel._material_changed()
    assert inst.material == "Brick"
    panel.tag_combo.setCurrentText("Walls")
    panel._tag_changed()
    assert inst.tag == "Walls"
    top.undo()
    restored = next(iter(root(top).instances.values()))
    assert restored.tag is None and restored.material == "Brick" and restored.name == "Shed"
    face = next(iter(restored.definition.entities.faces.values()))
    top.document.enter(restored)
    top.viewport.selection.set([face])
    assert panel.measure_label.text() == "Area 1,000,000 mm² (1 m²)"
    edge = next(iter(restored.definition.entities.edges.values()))
    top.viewport.selection.set([edge])
    assert panel.measure_label.text().startswith("Length 1000")
    panel.soft_check.setChecked(True)
    panel._soft_changed()
    assert edge.soft
    assert isinstance(face, Face)


def test_panels_follow_undo_of_a_new_model(window) -> None:
    window.document.perform("Material", lambda: window.document.model.add_material(Material("Glass", (0.5, 0.7, 0.9), 0.4)))
    labels = [window.panels["materials"].list.item(i).text() for i in range(window.panels["materials"].list.count())]
    assert labels == ["Default", "Glass  (40%)"]
    window.undo()
    assert window.panels["materials"].list.count() == 1

