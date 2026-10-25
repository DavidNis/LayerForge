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

The layer-generation algorithm itself is ``layerforge_l1.reveal_planner``.
"""

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
from .planning import PlanningConfig, PlanningError, plan_stack
from .preview import generate_previews
from .validation import validate_stack

__all__ = [
    "SCHEMA",
    "SCHEMA_VERSION",
    "ManifestError",
    "PlanningConfig",
    "PlanningError",
    "Sheet",
    "SheetStack",
    "StackError",
    "StackGrid",
    "export_stack",
    "generate_previews",
    "import_planner_output",
    "load_stack",
    "mask_to_geometry",
    "plan_stack",
    "rasterize_geometry",
    "rasterize_stl",
    "revalidate_cad",
    "save_stack",
    "stl_footprint",
    "validate_stack",
]
