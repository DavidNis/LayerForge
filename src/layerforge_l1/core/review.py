"""L2 review project: an edited ``SheetStack`` directory with its base and edit log.

A review project is an ordinary sheet-stack directory (schema 1.1.0, loadable by
every ``stack-*`` command) with three additions::

    sheet_stack.json          current reviewed geometry + "review" block
    layers/ target.png        current masks / reviewed target (as in any stack)
    review/base/              unmodified copy of the stack the review started from
    review/operands/          mask PNGs referenced by edits (named by content hash)

The ``review`` block records the base (source directory, manifest hash, its
provenance), the ordered edit log with each edit's change summary and
validation result, and the current validation status.  The saved masks are the
authoritative geometry; replaying the log from ``review/base`` must reproduce
them bit for bit (``verify_project``), and undo/revert work by replaying a
prefix of the log.  Invalid drafts are saved so they can be inspected and
corrected, but are marked ``DRAFT_INVALID`` and refused by ``export_project``.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Callable

import numpy as np
import shapely

from .editing import EditError, apply_edit, artwork_deviation_px, describe, islands, write_change_image
from .model import MANIFEST_NAME, SheetStack, file_sha256, load_stack, mask_bits_sha256, save_stack
from .validation import validate_stack

REVIEW_SCHEMA = "layerforge.review"
BASE_DIR = Path("review") / "base"
OPERANDS_DIR = Path("review") / "operands"
VALID = "REVIEWED_VALID"
DRAFT = "DRAFT_INVALID"


def _summary(stack: SheetStack, validation: dict[str, Any], base: SheetStack) -> dict[str, Any]:
    found = [row for index in range(len(stack.sheets)) for row in islands(stack, index)]
    return {
        "status": VALID if validation["status"] == "PASS" else DRAFT,
        "printable": validation["status"] == "PASS",
        "validation_status": validation["status"],
        "failures": validation["failures"],
        "reconstruction_vs_reviewed_target_px": validation["reconstruction"].get("mismatched_pixels"),
        "deviation_from_source_artwork_px": artwork_deviation_px(stack, base.target_rgb),
        "floating_islands": len(found),
    }


def _save(stack: SheetStack, project: Path) -> None:
    # Remove masks of sheets that no longer exist (merge) before writing the current set.
    shutil.rmtree(project / "layers", ignore_errors=True)
    (project / "target.png").unlink(missing_ok=True)
    save_stack(stack, project)


def start_review(source: Path, project: Path) -> tuple[SheetStack, dict[str, Any]]:
    """Copy ``source`` as the review base and save it as edit 0 of a new project."""

    project = Path(project)
    if (project / MANIFEST_NAME).exists():
        raise EditError(f"{project} already holds a stack; use a new project directory")
    stack = load_stack(source)
    if stack.review is not None:
        raise EditError(f"{source} is already a review project; open it directly")
    source_manifest = Path(source) / MANIFEST_NAME if Path(source).is_dir() else Path(source)
    save_stack(stack, project / BASE_DIR)
    validation = validate_stack(stack)
    stack.review = {
        "schema": REVIEW_SCHEMA,
        "base": {
            "directory": BASE_DIR.as_posix(),
            "source": source_manifest.parent.as_posix(),
            "source_manifest_sha256": file_sha256(source_manifest),
            "name": stack.name,
            "provenance": stack.provenance,
            "validation_status": validation["status"],
        },
        "edits": [],
    }
    stack.review["current"] = _summary(stack, validation, stack)
    _save(stack, project)
    return stack, validation


def load_project(project: Path) -> SheetStack:
    stack = load_stack(project)
    if stack.review is None or stack.review.get("schema") != REVIEW_SCHEMA:
        raise EditError(f"{project} is not a review project (start one with `stack-edit init`)")
    return stack


def load_base(project: Path) -> SheetStack:
    return load_stack(Path(project) / BASE_DIR)


def _store_operand(op: dict[str, Any], project: Path) -> dict[str, Any]:
    """Copy a mask operand into the project so the log stays replayable."""

    op = json.loads(json.dumps(op))
    holders = [op] + ([op["region"]] if isinstance(op.get("region"), dict) else [])
    for holder in holders:
        if "mask" in holder:
            path = Path(holder["mask"])
            if not path.is_absolute() and not path.is_file():
                path = project / path
            if not path.is_file():
                raise EditError(f"mask operand not found: {holder['mask']}")
            name = OPERANDS_DIR / f"{file_sha256(path)[:16]}.png"
            (project / name).parent.mkdir(parents=True, exist_ok=True)
            if not (project / name).exists():
                shutil.copyfile(path, project / name)
            holder["mask"] = name.as_posix()
    return op


def replay(project: Path, count: int | None = None) -> SheetStack:
    """Re-apply the first ``count`` logged edits (all if None) to the base."""

    project = Path(project)
    edits = load_project(project).review["edits"]
    stack = load_base(project)
    for entry in edits[: len(edits) if count is None else count]:
        stack, _ = apply_edit(stack, entry["op"], project)
    return stack


def with_default_precision(op: dict[str, Any]) -> dict[str, Any]:
    """New shape-based edits (shapes in mm, bridges) are vector unless stated otherwise.

    The precision is written into the log, so replay never depends on this default;
    logged edits without the key (written before vector editing) replay as raster.
    """

    region = op.get("region") if isinstance(op.get("region"), dict) else {}
    shaped = op.get("op") == "bridge" or (op.get("op") in ("add", "subtract") and "shape" in op) or (
        op.get("op") == "split" and "shape" in region)
    return {**op, "precision": op.get("precision", "vector")} if shaped else op


def apply_to_project(project: Path, op: dict[str, Any]) -> tuple[SheetStack, dict[str, Any]]:
    """Apply one edit to the saved project, validate, log and save it (even if invalid)."""

    project = Path(project)
    stack = load_project(project)
    op = _store_operand(with_default_precision(op), project)
    edited, change = apply_edit(stack, op, project)
    validation = validate_stack(edited)
    base = load_base(project)
    entry = {
        "index": len(stack.review["edits"]) + 1,
        "op": op,
        "change": change,
        "validation_status": validation["status"],
        "failures": validation["failures"],
    }
    edited.review = dict(stack.review, edits=stack.review["edits"] + [entry])
    edited.review["current"] = _summary(edited, validation, base)
    _save(edited, project)
    return edited, entry


def revert(project: Path, keep: int) -> tuple[SheetStack, dict[str, Any]]:
    """Keep the first ``keep`` edits (0 = base) by replaying them; drop the rest."""

    project = Path(project)
    current = load_project(project)
    edits = current.review["edits"]
    if not 0 <= keep <= len(edits):
        raise EditError(f"can keep 0..{len(edits)} edits, got {keep}")
    stack = replay(project, keep)
    validation = validate_stack(stack)
    stack.review = dict(current.review, edits=edits[:keep])
    stack.review["reverted"] = current.review.get("reverted", []) + [
        {"dropped": [entry["op"] for entry in edits[keep:]], "kept_edits": keep}
    ]
    stack.review["current"] = _summary(stack, validation, load_base(project))
    _save(stack, project)
    return stack, validation


def undo(project: Path, steps: int = 1) -> tuple[SheetStack, dict[str, Any]]:
    edits = load_project(project).review["edits"]
    if not 0 < steps <= len(edits):
        raise EditError("nothing to undo" if not edits else f"can undo 1..{len(edits)} edits, got {steps}")
    return revert(project, len(edits) - steps)


def geometry_fingerprint(stack: SheetStack) -> dict[str, Any]:
    """Everything that defines the physical result (for save/load and replay checks)."""

    return {
        "grid": stack.grid.to_json(),
        "frame_color": stack.frame_color,
        "target_rgb_sha256": None if stack.target_rgb is None
        else hashlib.sha256(np.ascontiguousarray(stack.target_rgb).tobytes()).hexdigest(),
        "sheets": [
            {"id": s.id, "export_name": name, "color": s.color, "thickness_mm": s.thickness_mm, "rim": s.rim,
             "assembly_z_mm": z, "retained": mask_bits_sha256(s.retained), "visible": mask_bits_sha256(s.visible),
             "geometry_wkb_sha256": None if s.geometry is None
             else hashlib.sha256(shapely.to_wkb(s.geometry)).hexdigest()}
            for s, name, z in zip(stack.sheets, stack.export_names(), stack.assembly_z_mm())
        ],
    }


def verify_project(project: Path) -> dict[str, Any]:
    """Reload the saved project and replay its log from the base; both must agree."""

    saved = load_project(project)
    replayed = replay(project)
    a, b = geometry_fingerprint(saved), geometry_fingerprint(replayed)
    return {
        "edits": len(saved.review["edits"]),
        "replay_identical": a == b,
        "differences": [key for key in a if a[key] != b[key]],
        "fingerprint": a,
    }


def inspect_project(project: Path) -> dict[str, Any]:
    stack = load_project(project)
    return {
        "name": stack.name,
        "status": stack.review["current"],
        "base": {k: stack.review["base"][k] for k in ("source", "source_manifest_sha256", "validation_status")},
        "edits": [{"index": e["index"], "op": e["op"], "kind": e["change"]["kind"],
                   "visible_changed_px": e["change"]["visible_changed_px"],
                   "validation_status": e["validation_status"]} for e in stack.review["edits"]],
        "sheets_back_to_front": describe(stack),
        "islands": [row for index in range(len(stack.sheets)) for row in islands(stack, index)],
        "total_height_mm": round(sum(s.stack_height_mm for s in stack.sheets), 6),
    }


def preview_project(project: Path, output: Path, against: str = "base") -> dict[str, Any]:
    """Previews of the current (possibly invalid) draft plus a change map."""

    from .preview import generate_previews

    project, output = Path(project), Path(output)
    stack = load_project(project)
    edits = stack.review["edits"]
    if against == "base":
        before = load_base(project)
    elif against == "previous":
        before = replay(project, max(len(edits) - 1, 0))
    else:
        raise EditError("against must be 'base' or 'previous'")
    report = generate_previews(stack, output, None, "edited masks (not exported CAD)")
    report["changes_vs_" + against] = write_change_image(before, stack, output / f"changes_vs_{against}.png")
    report["files"].append(f"changes_vs_{against}.png")
    report["review_status"] = stack.review["current"]
    if not stack.review["current"]["printable"]:
        report["notice"] = "DRAFT_INVALID: inspection preview only; not a printable result"
    (output / "preview_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def export_project(project: Path, output: Path, progress: Callable[..., None] | None = None) -> dict[str, Any]:
    """Validate, then export STEP/STL with the L0 pipeline; refuse invalid drafts."""

    from .cad import export_stack, revalidate_cad
    from .preview import generate_previews

    project, output = Path(project), Path(output)
    stack = load_project(project)
    validation = validate_stack(stack)
    if validation["status"] != "PASS":
        raise EditError(f"draft is invalid and is not exported: {validation['failures']}")
    manifest = export_stack(stack, output, progress=progress)
    check, stl_masks = revalidate_cad(stack, output, progress=progress)
    (output / "cad_revalidation.json").write_text(json.dumps(check, indent=2) + "\n", encoding="utf-8")
    previews = generate_previews(stack, output / "previews", stl_masks, f"reopened STL in {output.as_posix()}")
    rows = manifest["layers"]
    summary = {
        "project_manifest_sha256": file_sha256(project / MANIFEST_NAME),
        "edits": len(stack.review["edits"]),
        "validation_status": validation["status"],
        "cad_revalidation_pass": check["pass"],
        "stack_mismatch_px": check["stack_mismatch_px"],
        "reconstruction_from_stl": check["reconstruction_from_stl"],
        "all_step_valid_single_solid": all(r["step_valid"] and r["step_solids"] == 1 for r in rows),
        "all_stl_watertight_single_component": all(r["stl_watertight"] and r["stl_components"] == 1 for r in rows),
        "rim_heights_match": all(abs(r["stl_height_mm"] - r["expected_height_mm"]) < 1e-3 for r in rows if "rim" in r),
        "exported_files": [r["layer"] for r in rows],
        "vector_sheets": {
            r["layer"]: {"sheet_id": r["sheet_id"], "stl_vs_geometry_area_mm2": r["stl_vs_geometry_area_mm2"],
                         "stl_mean_boundary_deviation_mm": r["stl_mean_boundary_deviation_mm"],
                         "vector_checks_on_stl_pass": r["vector_checks_on_stl"]["pass"]}
            for r in check["layers"] if "vector_checks_on_stl" in r
        },
        "assembled_px_diff_from_reviewed": previews["assembled_px_diff_from_planned"],
    }
    summary["pass"] = (
        all(summary[k] for k in ("cad_revalidation_pass", "all_step_valid_single_solid",
                                 "all_stl_watertight_single_component", "rim_heights_match"))
        and summary["stack_mismatch_px"] == 0
        and summary["assembled_px_diff_from_reviewed"] == 0
        and not summary["reconstruction_from_stl"].get("mismatched_pixels")
    )
    (output / "export_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary
