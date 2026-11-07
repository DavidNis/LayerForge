"""The STEP validity check used by ``export_stack`` (performance Step 3): the parallel OCCT check gives the
same verdicts as the serial ``cq.Shape.isValid`` it replaced, which stays available as a fallback."""

from __future__ import annotations

import cadquery as cq
import pytest
from OCP.BRep import BRep_Builder
from OCP.TopoDS import TopoDS_Shell, TopoDS_Solid

from layerforge_l1.core import cad


def _bowtie() -> cq.Face:
    return cq.Face.makeFromWires(cq.Wire.makePolygon(
        [cq.Vector(0, 0, 0), cq.Vector(10, 10, 0), cq.Vector(10, 0, 0), cq.Vector(0, 10, 0)], close=True))


def _open_shell_solid() -> cq.Shape:
    builder, shell, solid = BRep_Builder(), TopoDS_Shell(), TopoDS_Solid()
    builder.MakeShell(shell)
    for face in cq.Solid.makeBox(10, 10, 2).Faces()[:-1]:
        builder.Add(shell, face.wrapped)
    builder.MakeSolid(solid)
    builder.Add(solid, shell)
    return cq.Shape.cast(solid)


def _sheet_with_holes() -> cq.Shape:
    from shapely.geometry import Polygon

    from layerforge_l1.geometry import extrude_geometry

    holes = [[(x, y), (x + 4, y), (x + 4, y + 3), (x, y + 3)] for x in range(10, 80, 12) for y in range(10, 80, 9)]
    return extrude_geometry(Polygon([(0, 0), (100, 0), (100, 100), (0, 100)], holes), 1.6)


@pytest.mark.parametrize("parallel", [True, False])
def test_step_check_matches_serial_is_valid(parallel, monkeypatch) -> None:
    monkeypatch.setattr(cad, "STEP_CHECK_PARALLEL", parallel)
    for shape, expected in ((_sheet_with_holes(), True), (_bowtie(), False),
                            (cq.Solid.extrudeLinear(_bowtie(), cq.Vector(0, 0, 1.6)), False), (_open_shell_solid(), False)):
        assert shape.isValid() is expected
        assert cad.step_is_valid(shape) is expected


def test_export_stack_uses_the_parallel_check_by_default(tmp_path, monkeypatch) -> None:
    from layerforge_l1.core.model import Sheet, SheetStack, StackGrid

    import numpy as np

    assert cad.STEP_CHECK_PARALLEL is True
    calls = []
    original = cad.step_is_valid
    monkeypatch.setattr(cad, "step_is_valid", lambda shape: calls.append(1) or original(shape))
    grid = StackGrid.for_planner(225.0, 1)
    retained = np.ones((grid.outer_pixels, grid.outer_pixels), dtype=bool)
    retained[200:260, 200:300] = False
    stack = SheetStack(name="t", grid=grid, sheets=[Sheet(id="layer_00_ffffff", color="#FFFFFF", retained=retained,
                                                          visible=~grid.perimeter_mask() & retained, thickness_mm=1.6)])
    manifest = cad.export_stack(stack, tmp_path)
    assert calls == [1]
    row = manifest["layers"][0]
    assert row["step_valid"] is True and row["step_solids"] == 1 and row["holes"] == 1
