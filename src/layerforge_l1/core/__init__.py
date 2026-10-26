"""LayerForge L0 reusable geometry core (public API).

Typical use::

    from layerforge_l1 import core

    stack, report = core.plan_stack(target_rgb, core.PlanningConfig(...))
    core.save_stack(stack, project_dir)
    stack = core.load_stack(project_dir)
    validation = core.validate_stack(stack)
    cad = core.export_stack(stack, cad_dir)
    cad_check, stl_masks = core.revalidate_cad(stack, cad_dir)
    previews = core.generate_previews(stack, preview_dir, stl_masks, "reopened STL")

    # L2 review: explicit edits on a loaded stack, logged in a project directory
    edited, change = core.apply_edit(stack, {"op": "recolor", "sheet": "<id>", "color": "#112233"})
    core.review.start_review(stack_dir, project_dir)
    core.review.apply_to_project(project_dir, {"op": "subtract", "sheet": "<id>", "shape": {...}})

    # L3 print kit: export, reopen, compare, estimate FDM printability (geometry unchanged)
    kit = core.printkit.build_print_kit(project_dir, kit_dir, core.printkit.PrintProcess.from_json(process_json))

The layer-generation algorithm itself is ``layerforge_l1.reveal_planner``.
"""

from . import printkit, review
from .cad import export_stack, mask_to_geometry, rasterize_geometry, rasterize_stl, revalidate_cad, stl_footprint
from .model import (
    SCHEMA,
    SCHEMA_VERSION,
    ManifestError,
    Sheet,
    SheetStack,
    StackError,
    StackGrid,
    import_planner_output,
    load_stack,
    save_stack,
)
from .editing import EDIT_OPS, EditError, apply_edit
from .planning import PlanningConfig, PlanningError, plan_stack
from .preview import generate_previews
from .validation import validate_stack

__all__ = [
    "EDIT_OPS",
    "EditError",
    "SCHEMA",
    "SCHEMA_VERSION",
    "ManifestError",
    "PlanningConfig",
    "PlanningError",
    "Sheet",
    "SheetStack",
    "StackError",
    "StackGrid",
    "apply_edit",
    "export_stack",
    "generate_previews",
    "import_planner_output",
    "load_stack",
    "mask_to_geometry",
    "plan_stack",
    "printkit",
    "rasterize_geometry",
    "rasterize_stl",
    "revalidate_cad",
    "review",
    "save_stack",
    "stl_footprint",
    "validate_stack",
]
