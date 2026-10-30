"""L1: automatic sheet-stack proposal for a new flat-color image (no layer manifest).

    load_image -> prepare_target (intake.py)
    -> plan_stack once per beam score     [approved planner, unchanged]
    -> validate_stack per candidate       [existing validators]
    -> export_stack + revalidate_cad for the selected candidate
    -> generate_previews from the reopened STL
    -> proposal.json with flags for every interpretation the user must review

Nothing is edited to make a plan succeed.  When no candidate is valid the
proposal stops at ``NEEDS_USER_DECISION`` with the evidence (e.g. the map of
cells the planner can never support); it does not invent bridges, depth or
color changes.  Every sheet is provenance ``auto-proposed``.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image

from .intake import IntakeConfig, load_image, prepare_target
from .model import StackGrid, save_stack
from .planning import PlanningConfig, PlanningError, plan_stack
from .validation import validate_stack

PROPOSAL_SCHEMA = "layerforge.l1_proposal"
PROPOSAL_VERSION = "1.0.0"
READY = "PROPOSAL_READY_FOR_REVIEW"
NEEDS_DECISION = "NEEDS_USER_DECISION"


@dataclass(frozen=True)
class ProposalConfig:
    planning: PlanningConfig = PlanningConfig(scale=2)
    # Each existing beam score yields one candidate; the planner itself is unchanged.
    beam_scores: tuple[str, ...] = ("area", "colors")
    export_cad: bool = True
    # When planning needed local cell refinement, also plan these wider refinements (extra
    # neighbor rings, -1 = whole artwork) with the first beam score, as extra candidates, so
    # sheet count and seams can be compared; images that plan without refinement skip this.
    refinement_alternatives: tuple[int, ...] = (4, -1)


def _flag(code: str, severity: str, message: str, **evidence: Any) -> dict[str, Any]:
    return {"code": code, "severity": severity, "message": message, "evidence": evidence}


def intake_flags(measurements: dict[str, Any], config: IntakeConfig) -> list[dict[str, Any]]:
    m = measurements
    flags = []
    if m["non_flat_interior_fraction"] > config.max_non_flat_fraction:
        flags.append(_flag(
            "NOT_FLAT_COLOR", "WARN",
            "Interior pixels are far from every palette color (gradients, texture or photo-like shading); "
            "snapping changes the artwork beyond anti-aliasing.",
            non_flat_interior_fraction=m["non_flat_interior_fraction"], limit=config.max_non_flat_fraction))
    if m["unmatched_regions_at_least_min_area"]:
        flags.append(_flag(
            "UNMATCHED_COLOR_REGIONS", "WARN",
            "Regions at least the minimum color area match no palette color; a color may be missing or the "
            "region is shading. See intake/unmatched_regions.png.",
            regions=m["unmatched_regions_at_least_min_area"], area_mm2=m["unmatched_area_mm2"]))
    if m["nearest_palette_pairs"]:
        flags.append(_flag(
            "NEAR_DUPLICATE_COLORS", "WARN",
            "Palette colors closer than the duplicate threshold; confirm they are distinct filaments.",
            pairs=m["nearest_palette_pairs"], threshold=config.near_duplicate_delta))
    small = [c["hex"] for c in m["colors"] if c["area_mm2"] < config.small_color_area_mm2]
    if small:
        flags.append(_flag(
            "SMALL_COLOR", "INFO", "Colors with little total area; each still needs its own filament.",
            colors=small, limit_mm2=config.small_color_area_mm2))
    if m["snap_changed_fraction"] > config.max_snap_changed_fraction:
        flags.append(_flag(
            "SNAP_CHANGED_PIXELS", "WARN",
            "Smoothing/speckle merging relabeled more pixels than expected for anti-aliasing alone.",
            fraction=m["snap_changed_fraction"], limit=config.max_snap_changed_fraction))
    thin = {c["hex"]: c["sub_width_feature_mm2"] for c in m["colors"] if c["sub_width_feature_mm2"] > 0}
    if thin:
        flags.append(_flag(
            "SUB_WIDTH_FEATURES", "INFO",
            f"Visible features narrower than {m['structural_width_mm']} mm; the planner must reveal them through "
            "cutouts in front sheets, or planning fails.", area_mm2_by_color=thin))
    if m["transparency"] != "none":
        flags.append(_flag("TRANSPARENCY", "INFO", "Transparent pixels became the frame (solid, never visible)."))
    return flags


def _save_image(array: np.ndarray, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(array).save(path)
    return path.as_posix()


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, default=lambda v: v.tolist() if hasattr(v, "tolist") else str(v)),
                    encoding="utf-8")


def _color_topology(stack: Any, measurements: dict[str, Any]) -> list[dict[str, Any]]:
    """Source color regions versus the physical sheets that carry each color."""

    by_color: dict[str, list[str]] = {}
    for sheet in stack.sheets:
        by_color.setdefault(sheet.color, []).append(sheet.id)
    return [
        {"color": c["hex"], "source_regions_4": c["source_regions_4"], "sheets": by_color.get(c["hex"], [])}
        for c in measurements["colors"]
    ]


def propose(
    image_path: Path,
    output: Path,
    intake: IntakeConfig = IntakeConfig(),
    config: ProposalConfig = ProposalConfig(),
    progress: Callable[..., None] | None = None,
    detail_decisions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Propose a validated sheet stack for ``image_path`` and write the review package.

    ``detail_decisions`` (detail id -> {"action": "preserve" | "remove" | "adjust", "color"})
    resolve the details intake would otherwise recolor or erase; see ``intake.apply_detail_decisions``.
    """

    progress = progress or (lambda *a, **k: None)
    output = Path(output)
    started = time.perf_counter()
    rgb, opaque, source = load_image(image_path)
    grid = StackGrid.for_planner(config.planning.outer_sheet_mm, config.planning.scale)
    prepared = prepare_target(rgb, opaque, grid, intake, detail_decisions)
    m = prepared["measurements"]
    progress("L1_INTAKE_DONE", palette=m["palette_size"], details=len(prepared["details"]),
             seconds=round(time.perf_counter() - started, 1))

    _save_image(prepared["target_rgb"], output / "intake" / "target.png")
    _save_image((prepared["unmatched_mask"] * 255).astype(np.uint8), output / "intake" / "unmatched_regions.png")
    details = prepared["details"]
    if details:
        _save_image(prepared["detail_overview"], output / "intake" / "details_overview.png")
        for detail in details:
            detail["image"] = f"intake/details/{detail['id']}.png"
            _save_image(prepared["detail_crops"][detail["id"]], output / detail["image"])
    flags = intake_flags(m, intake)
    if details:
        undecided = [d["id"] for d in details if d["action"]["default"]]
        flags.append(_flag(
            "INTAKE_DETAILS_CHANGED", "WARN",
            "Intake would recolor or erase these recognizable details (eye whites, lips, accessories...). Each is "
            "shown original | default intake | as planned. Removed pieces are kept by default; colors missing from "
            "the palette keep the nearest palette color until you choose preserve, remove or adjust.",
            details=len(details), using_default_action=len(undecided),
            recolored=sum(d["kind"] == "color_not_in_palette" for d in details),
            removed_by_cleanup=sum(d["kind"] == "removed_by_cleanup" for d in details)))
    frame = prepared["frame_color"] if prepared["has_frame"] else None
    planning = replace(config.planning, frame_color=frame)

    candidates = []
    stacks = {}
    runs = [(f"beam_{score}", score, 0) for score in config.beam_scores]
    index = 0
    while index < len(runs):
        name, score, extra = runs[index]
        index += 1
        row: dict[str, Any] = {"name": name, "beam_score": score}
        if extra:
            row["refinement_extra_rings"] = extra
        t0 = time.perf_counter()
        try:
            stack, report = plan_stack(prepared["target_rgb"],
                                       replace(planning, beam_score=score, refinement_extra_rings=extra), name=name)
        except PlanningError as error:
            row.update(status="PLANNING_FAILED", reason=str(error),
                       planner_report={k: v for k, v in error.report.items() if k != "reveal_config"})
            if error.obstruction is not None:
                row["obstruction_map"] = _save_image(
                    (error.obstruction * 255).astype(np.uint8), output / "candidates" / name / "obstruction.png")
            candidates.append(row)
            progress("L1_CANDIDATE_FAILED", name=name, reason=str(error))
            if error.report.get("closure_feasible") is False and not extra:
                # Closure does not depend on the beam score: every other candidate fails identically.
                for other_name, other, _ in runs[index:]:
                    candidates.append({"name": other_name, "beam_score": other, "status": "PLANNING_FAILED",
                                       "reason": f"skipped: closure infeasible (independent of beam score), see {name}"})
                break
            continue
        stack.provenance.update(source=source, provenance="auto-proposed", manual_corrections=[])
        validation = validate_stack(stack)
        save_stack(stack, output / "candidates" / name / "stack")
        _write(output / "candidates" / name / "validation.json", validation)
        row.update(
            status="VALID" if validation["status"] == "PASS" else "INVALID",
            validation_failures=validation["failures"],
            sheet_count=len(stack.sheets),
            front_to_back_colors=report["front_to_back_colors"],
            repeated_colors=validation["repeated_colors"],
            source_regions_split_across_sheets=report["seams"]["source_regions_split_across_sheets"],
            seam_length_mm=round(report["seams"]["seam_edge_length_mm"], 1),
            cell_refinement=report.get("cell_refinement"),
            reconstruction_mismatched_pixels=validation["reconstruction"].get("mismatched_pixels"),
            planning_seconds=round(time.perf_counter() - t0, 1),
        )
        candidates.append(row)
        stacks[name] = stack
        progress("L1_CANDIDATE_DONE", name=name, status=row["status"], sheets=row["sheet_count"])
        if row["cell_refinement"] and not extra and index == 1:
            # The first closure needed refinement: compare wider refinements (first beam score only).
            runs[1:1] = [(f"beam_{score}_refine_{'all' if e < 0 else f'{e}rings'}", score, e)
                         for e in config.refinement_alternatives]

    valid = [c for c in candidates if c["status"] == "VALID"]
    selected = min(valid, key=lambda c: (c["sheet_count"], c["source_regions_split_across_sheets"],
                                         c.get("seam_length_mm", 0.0))) if valid else None
    orders = {tuple(c["front_to_back_colors"]) for c in valid}
    if len(orders) > 1:
        flags.append(_flag(
            "DEPTH_ORDER_NOT_UNIQUE", "WARN",
            "Valid candidates stack the colors in different orders; all reproduce the same front view, so depth "
            "cannot be inferred from the image. Choose the intended order.",
            orders={c["name"]: c["front_to_back_colors"] for c in valid}))
    if valid:
        flags.append(_flag(
            "DEPTH_ORDER_IS_STRUCTURAL", "INFO",
            "Sheet order is chosen for printability and exact reproduction, not inferred from scene depth."))
    if selected and selected.get("cell_refinement"):
        flags.append(_flag(
            "LOCAL_CELL_REFINEMENT", "INFO",
            "The first reveal closure was infeasible; only the blocked cells (and, if needed, their neighbors) were "
            "cut finer so a color region can step depth around sub-width details. Physical rules are unchanged.",
            rounds=selected["cell_refinement"]))
    if selected and selected["source_regions_split_across_sheets"]:
        flags.append(_flag(
            "COLOR_REGIONS_SPLIT_ACROSS_SHEETS", "INFO",
            "Some single-color source regions are printed on more than one sheet (seams).",
            regions=selected["source_regions_split_across_sheets"]))
    if not valid:
        flags.append(_flag(
            "NO_VALID_CANDIDATE", "BLOCKER",
            "No candidate produced a valid stack; the artwork needs a user decision (e.g. widen or remove the "
            "features in the obstruction map). No automatic edit was made.",
            candidates={c["name"]: c.get("reason", c.get("validation_failures")) for c in candidates}))

    result: dict[str, Any] = {
        "schema": PROPOSAL_SCHEMA,
        "version": PROPOSAL_VERSION,
        "source": source,
        "provenance": "auto-proposed",
        "manual_layer_manifest_used": False,
        "manual_corrections": [],
        "planning_config": asdict(planning),
        "beam_scores": list(config.beam_scores),
        "intake": m,
        "intake_details": details,
        "detail_decisions": detail_decisions or {},
        "palette": [c["hex"] for c in m["colors"]],
        "frame_color": frame,
        "candidates": candidates,
        "selected": selected["name"] if selected else None,
        "flags": flags,
    }
    if selected:
        stack = stacks[selected["name"]]
        result["color_topology"] = _color_topology(stack, m)
        if config.export_cad:
            from .cad import export_stack, revalidate_cad
            from .preview import generate_previews

            cad_dir = output / "selected" / "cad"
            manifest = export_stack(stack, cad_dir, progress=progress)
            check, stl_masks = revalidate_cad(stack, cad_dir, progress=progress)
            _write(cad_dir / "cad_revalidation.json", check)
            previews = generate_previews(stack, output / "selected" / "previews", stl_masks, "reopened STL")
            result["cad"] = {
                "revalidation_pass": check["pass"],
                "stack_mismatch_px": check["stack_mismatch_px"],
                "all_step_valid_single_solid": all(r["step_valid"] and r["step_solids"] == 1 for r in manifest["layers"]),
                "all_stl_watertight_single_component": all(
                    r["stl_watertight"] and r["stl_components"] == 1 for r in manifest["layers"]),
                "assembled_px_diff_from_planned": previews["assembled_px_diff_from_planned"],
                "source_comparison_mismatched_pixels": previews.get("source_comparison_mismatched_pixels"),
            }
            cad_ok = (result["cad"]["revalidation_pass"] and result["cad"]["all_step_valid_single_solid"]
                      and result["cad"]["all_stl_watertight_single_component"]
                      and result["cad"]["assembled_px_diff_from_planned"] == 0)
            if not cad_ok:
                flags.append(_flag("CAD_CHECK_FAILED", "BLOCKER", "Exported CAD does not reproduce the proposal.",
                                   **result["cad"]))
        else:
            from .preview import generate_previews

            generate_previews(stack, output / "selected" / "previews")
    result["status"] = READY if selected and not any(f["severity"] == "BLOCKER" for f in flags) else NEEDS_DECISION
    result["seconds"] = round(time.perf_counter() - started, 1)
    _write(output / "proposal.json", result)
    progress("L1_PROPOSAL_DONE", status=result["status"], selected=result["selected"], seconds=result["seconds"])
    return result
