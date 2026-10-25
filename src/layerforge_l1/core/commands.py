"""``stack-*`` subcommands of ``python -m layerforge_l1`` (see ``cli.py``)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image

from .. import PROJECT_ROOT
from .model import StackError, import_planner_output, load_stack, save_stack
from .planning import PlanningConfig, PlanningError, plan_stack
from .validation import validate_stack

DEFAULT_REGISTRY = PROJECT_ROOT / "fixtures" / "reference_stacks.json"
STACK_COMMANDS = ("stack-plan", "stack-import", "stack-validate", "stack-export", "stack-preview", "stack-reference")


def add_parsers(commands: Any) -> None:
    plan = commands.add_parser("stack-plan", help="plan a sheet stack from a flat-color target with the approved planner")
    plan.add_argument("--target", type=Path, required=True, help="square exact-palette RGB PNG")
    plan.add_argument("--output", type=Path, required=True)
    plan.add_argument("--name", default=None)
    plan.add_argument("--outer-mm", type=float, default=PlanningConfig.outer_sheet_mm)
    plan.add_argument("--scale", type=int, default=PlanningConfig.scale)
    plan.add_argument("--tile-mm", type=float, default=PlanningConfig.cell_tile_mm)
    plan.add_argument("--beam", type=int, default=PlanningConfig.beam_width)
    plan.add_argument("--score", choices=["area", "colors"], default=PlanningConfig.beam_score)
    plan.add_argument("--margin", type=int, default=PlanningConfig.planning_margin_pixels)
    plan.add_argument("--frame-hex", default=None, help="target color that marks frame padding")
    plan.add_argument("--thickness-mm", type=float, default=PlanningConfig.thickness_mm)

    imp = commands.add_parser("stack-import", help="migrate a pre-L0 planner layers/ directory to a versioned stack")
    imp.add_argument("--layers", type=Path, required=True)
    imp.add_argument("--output", type=Path, required=True)
    imp.add_argument("--outer-mm", type=float, default=225.0)
    imp.add_argument("--thickness-mm", type=float, default=1.6)
    imp.add_argument("--target", type=Path, default=None)
    imp.add_argument("--frame-hex", default=None)
    imp.add_argument("--name", default=None)

    val = commands.add_parser("stack-validate", help="validate a saved sheet stack")
    val.add_argument("--stack", type=Path, required=True)
    val.add_argument("--report", type=Path, default=None)

    exp = commands.add_parser("stack-export", help="export STEP/STL per sheet and re-validate the reopened files")
    exp.add_argument("--stack", type=Path, required=True)
    exp.add_argument("--output", type=Path, required=True)
    exp.add_argument("--simplify-mm", type=float, default=0.03)

    pre = commands.add_parser("stack-preview", help="sheet, assembled, exploded and source-comparison previews")
    pre.add_argument("--stack", type=Path, required=True)
    pre.add_argument("--output", type=Path, required=True)
    pre.add_argument("--cad", type=Path, default=None, help="export directory; previews then use the reopened STL")

    ref = commands.add_parser("stack-reference", help="regenerate an accepted reference stack and compare to its baseline")
    ref.add_argument("--name", default="van_gogh_optB")
    ref.add_argument("--output", type=Path, required=True)
    ref.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    ref.add_argument("--skip-cad", action="store_true")
    ref.add_argument(
        "--from-accepted", action="store_true",
        help="load the committed accepted masks instead of re-planning (reveal planner itself is not exercised)",
    )


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, default=_json_default) + "\n", encoding="utf-8")


def _json_default(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"not JSON serializable: {type(value)}")


def _read_rgb(path: Path) -> np.ndarray:
    if not Path(path).is_file():
        raise StackError(f"target image not found: {path}")
    return np.asarray(Image.open(path).convert("RGB"))


def _export_and_check(stack: Any, output: Path, progress: Callable, simplify_mm: float = 0.03) -> tuple[dict, dict, list]:
    from .cad import export_stack, revalidate_cad

    manifest = export_stack(stack, output, simplify_mm=simplify_mm, progress=progress)
    check, stl_masks = revalidate_cad(stack, output, simplify_mm=simplify_mm, progress=progress)
    _write(output / "cad_revalidation.json", check)
    return manifest, check, stl_masks


def run(args: argparse.Namespace, progress: Callable[..., None]) -> int:
    try:
        return _run(args, progress)
    except (StackError, FileNotFoundError, KeyError) as error:
        if isinstance(error, PlanningError):
            print(json.dumps(error.report, indent=2, default=_json_default), file=sys.stderr)
        print(f"error: {error}", file=sys.stderr)
        return 2


def _run(args: argparse.Namespace, progress: Callable[..., None]) -> int:
    from .preview import generate_previews

    if args.command == "stack-plan":
        config = PlanningConfig(
            outer_sheet_mm=args.outer_mm, scale=args.scale, cell_tile_mm=args.tile_mm, beam_width=args.beam,
            beam_score=args.score, planning_margin_pixels=args.margin, frame_color=args.frame_hex,
            thickness_mm=args.thickness_mm,
        )
        stack, report = plan_stack(_read_rgb(args.target), config, name=args.name or args.target.stem)
        stack.provenance["target_file"] = args.target.as_posix()
        manifest = save_stack(stack, args.output)
        validation = validate_stack(stack)
        _write(args.output / "validation.json", validation)
        progress("STACK_PLANNED", manifest=manifest.as_posix(), sheets=report["sheet_count"],
                 front_to_back=report["front_to_back_colors"], validation=validation["status"])
        return 0 if validation["status"] == "PASS" else 1

    if args.command == "stack-import":
        stack = import_planner_output(
            args.layers, outer_sheet_mm=args.outer_mm, thickness_mm=args.thickness_mm,
            target_image=args.target, frame_color=args.frame_hex, name=args.name,
        )
        manifest = save_stack(stack, args.output)
        progress("STACK_IMPORTED", manifest=manifest.as_posix(), sheets=len(stack.sheets))
        return 0

    if args.command == "stack-validate":
        validation = validate_stack(load_stack(args.stack))
        if args.report:
            _write(args.report, validation)
        print(json.dumps({k: validation[k] for k in ("status", "failures", "sheet_count", "reconstruction")}, indent=2))
        return 0 if validation["status"] == "PASS" else 1

    if args.command == "stack-export":
        stack = load_stack(args.stack)
        _, check, _ = _export_and_check(stack, args.output, progress, args.simplify_mm)
        progress("STACK_EXPORTED", output=args.output.as_posix(), cad_revalidation=check["pass"])
        return 0 if check["pass"] else 1

    if args.command == "stack-preview":
        stack = load_stack(args.stack)
        masks, source = None, "planned masks"
        if args.cad:
            from .cad import rasterize_stl

            masks = [rasterize_stl(args.cad / "stl" / f"{s.id}.stl", stack.grid) for s in stack.sheets]
            source = f"reopened STL in {args.cad.as_posix()}"
        report = generate_previews(stack, args.output, masks, source)
        _write(args.output / "preview_report.json", report)
        progress("STACK_PREVIEWED", output=args.output.as_posix(), assembled_px_diff=report["assembled_px_diff_from_planned"])
        return 0 if report["assembled_px_diff_from_planned"] == 0 else 1

    if args.command == "stack-reference":
        return _reference(args, progress)
    raise StackError(f"unknown stack command {args.command}")


def _reference(args: argparse.Namespace, progress: Callable[..., None]) -> int:
    from .preview import generate_previews
    from .reference import compare_cad_to_accepted, compare_masks_to_accepted, compare_stack_to_baseline, load_reference

    case = load_reference(args.registry, args.name)
    root = PROJECT_ROOT
    out = args.output
    baseline = json.loads((root / case["baseline"]).read_text(encoding="utf-8"))
    if args.from_accepted:
        config = case["planning_config"]
        stack = import_planner_output(
            root / case["accepted_layers"], outer_sheet_mm=config.outer_sheet_mm, thickness_mm=config.thickness_mm,
            target_image=root / case["target"], frame_color=config.frame_color, name=args.name,
        )
        report = {
            "mode": "ACCEPTED_MASKS_NO_REPLANNING",
            "note": "reveal planner not re-run; accepted_mask_comparison is therefore identical by construction",
            "sheet_count": len(stack.sheets),
            "planning_seconds": None,
        }
        progress("REFERENCE_ACCEPTED_LOADED", name=args.name, sheets=len(stack.sheets))
    else:
        progress("REFERENCE_PLANNING_START", name=args.name, config=case["planning"])
        stack, report = plan_stack(_read_rgb(root / case["target"]), case["planning_config"], name=args.name)
        progress("REFERENCE_PLANNED", sheets=report["sheet_count"], seconds=report["planning_seconds"])
    stack.provenance.update(target_file=case["target"], rights=case["rights"], manual_steps=case["manual_steps"])
    save_stack(stack, out / "stack")

    reloaded = load_stack(out / "stack")
    validation = validate_stack(reloaded)
    _write(out / "validation.json", validation)
    summary: dict[str, Any] = {
        "reference": args.name,
        "planning": report,
        "validation_status": validation["status"],
        "validation_failures": validation["failures"],
        "baseline_comparison": compare_stack_to_baseline(reloaded, baseline, validation),
        "accepted_mask_comparison": compare_masks_to_accepted(reloaded, root / case["accepted_layers"]),
    }
    progress("REFERENCE_COMPARED", baseline_identical=summary["baseline_comparison"]["identical"],
             masks_identical=summary["accepted_mask_comparison"]["identical"])
    masks, source = None, "planned masks (CAD skipped)"
    if not args.skip_cad:
        manifest, check, masks = _export_and_check(reloaded, out / "cad", progress)
        source = "reopened STL in cad/stl"
        summary["cad_revalidation_pass"] = check["pass"]
        summary["cad_stack_mismatch_px"] = check["stack_mismatch_px"]
        summary["cad_reconstruction_from_stl"] = check["reconstruction_from_stl"]
        summary["cad_all_step_valid_single_solid"] = all(r["step_valid"] and r["step_solids"] == 1 for r in manifest["layers"])
        summary["cad_all_stl_watertight_single_component"] = all(
            r["stl_watertight"] and r["stl_components"] == 1 for r in manifest["layers"]
        )
        summary["cad_comparison"] = compare_cad_to_accepted(
            reloaded, manifest, out / "cad", baseline, root / case["accepted_cad"]
        )
    previews = generate_previews(reloaded, out / "previews", masks, source)
    _write(out / "previews" / "preview_report.json", previews)
    summary["previews"] = previews
    passed = (
        validation["status"] == "PASS"
        and summary["baseline_comparison"]["identical"]
        and summary["accepted_mask_comparison"]["identical"]
        and previews["assembled_px_diff_from_planned"] == 0
        and (args.skip_cad or (summary["cad_revalidation_pass"] and summary["cad_comparison"]["identical"]
                               and summary["cad_all_step_valid_single_solid"]
                               and summary["cad_all_stl_watertight_single_component"]))
    )
    summary["regression_status"] = "PASS" if passed else "FAIL"
    _write(out / "reference_regression.json", summary)
    progress("REFERENCE_DONE", regression_status=summary["regression_status"])
    return 0 if passed else 1
