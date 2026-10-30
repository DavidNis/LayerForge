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
STACK_COMMANDS = ("stack-plan", "stack-import", "stack-validate", "stack-export", "stack-preview", "stack-reference",
                  "stack-propose", "stack-edit", "stack-kit")


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

    pro = commands.add_parser("stack-propose", help="L1: propose a sheet stack for a new flat-color PNG/JPEG (no manifest)")
    pro.add_argument("--image", type=Path, required=True)
    pro.add_argument("--output", type=Path, required=True)
    pro.add_argument("--outer-mm", type=float, default=225.0)
    pro.add_argument("--scale", type=int, default=2)
    pro.add_argument("--tile-mm", type=float, default=PlanningConfig.cell_tile_mm)
    pro.add_argument("--beam", type=int, default=PlanningConfig.beam_width)
    pro.add_argument("--thickness-mm", type=float, default=PlanningConfig.thickness_mm)
    pro.add_argument("--max-colors", type=int, default=12)
    pro.add_argument("--min-color-mm2", type=float, default=2.0)
    pro.add_argument("--no-cad", action="store_true", help="skip STEP/STL export of the selected candidate")

    edit = commands.add_parser("stack-edit", help="L2: review and edit a saved sheet stack (project directory)")
    steps = edit.add_subparsers(dest="edit_command", required=True)
    init = steps.add_parser("init", help="start a review project from a saved stack (e.g. an L1 candidate)")
    init.add_argument("--from", dest="source", type=Path, required=True)
    init.add_argument("--project", type=Path, required=True)
    for name, text in (("inspect", "list sheets, islands, edits and status"),
                       ("validate", "run the L0 validators on the current draft"),
                       ("verify", "reload and replay the edit log from the base; must reproduce the geometry")):
        step = steps.add_parser(name, help=text)
        step.add_argument("--project", type=Path, required=True)
        step.add_argument("--report", type=Path, default=None)
    app = steps.add_parser("apply", help="apply one edit: OP key=value ... (values are JSON when they parse)")
    app.add_argument("--project", type=Path, required=True)
    app.add_argument("--op-file", type=Path, default=None, help="JSON file with one edit dict")
    app.add_argument("op", nargs="?", default=None)
    app.add_argument("params", nargs="*", default=[])
    und = steps.add_parser("undo", help="drop the last edit(s) by replaying the rest from the base")
    und.add_argument("--project", type=Path, required=True)
    und.add_argument("--steps", type=int, default=1)
    rev = steps.add_parser("revert", help="keep only the first N edits (0 = the unedited base)")
    rev.add_argument("--project", type=Path, required=True)
    rev.add_argument("--to", type=int, required=True)
    prv = steps.add_parser("preview", help="previews of the draft plus a visible/hidden change map")
    prv.add_argument("--project", type=Path, required=True)
    prv.add_argument("--output", type=Path, required=True)
    prv.add_argument("--against", choices=["base", "previous"], default="base")
    out = steps.add_parser("export", help="export STEP/STL with the L0 pipeline; refused for invalid drafts")
    out.add_argument("--project", type=Path, required=True)
    out.add_argument("--output", type=Path, required=True)

    kit = commands.add_parser("stack-kit", help="L3: checksummed print kit with CAD read-back and an FDM estimate")
    kit.add_argument("--stack", type=Path, required=True, help="saved stack or L2 review project")
    kit.add_argument("--output", type=Path, required=True, help="new, empty kit directory")
    kit.add_argument("--process", type=Path, default=None, help="printer/process JSON (default: generic assumption)")
    kit.add_argument("--bambu-printer", default=None, metavar="PRESET",
                     help='also write a Bambu Studio multi-plate project, e.g. "Bambu Lab P1S 0.4 nozzle"')


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
        if stack.review is not None:
            # An edited draft is exported only when it validates (L2); plain stacks as before.
            validation = validate_stack(stack)
            if validation["status"] != "PASS":
                print(json.dumps({"status": "REFUSED_INVALID_DRAFT", "failures": validation["failures"]}, indent=2))
                return 1
        _, check, _ = _export_and_check(stack, args.output, progress, args.simplify_mm)
        progress("STACK_EXPORTED", output=args.output.as_posix(), cad_revalidation=check["pass"])
        return 0 if check["pass"] else 1

    if args.command == "stack-preview":
        stack = load_stack(args.stack)
        masks, source = None, "planned masks"
        if args.cad:
            from .cad import rasterize_stl

            masks = [rasterize_stl(args.cad / "stl" / f"{name}.stl", stack.grid) for name in stack.export_names()]
            source = f"reopened STL in {args.cad.as_posix()}"
        report = generate_previews(stack, args.output, masks, source)
        _write(args.output / "preview_report.json", report)
        progress("STACK_PREVIEWED", output=args.output.as_posix(), assembled_px_diff=report["assembled_px_diff_from_planned"])
        return 0 if report["assembled_px_diff_from_planned"] == 0 else 1

    if args.command == "stack-reference":
        return _reference(args, progress)

    if args.command == "stack-propose":
        from .intake import IntakeConfig
        from .propose import READY, ProposalConfig, propose

        planning = PlanningConfig(outer_sheet_mm=args.outer_mm, scale=args.scale, cell_tile_mm=args.tile_mm,
                                  beam_width=args.beam, thickness_mm=args.thickness_mm)
        intake = IntakeConfig(max_colors=args.max_colors, min_color_area_mm2=args.min_color_mm2)
        result = propose(args.image, args.output, intake, ProposalConfig(planning=planning, export_cad=not args.no_cad),
                         progress)
        print(json.dumps({"status": result["status"], "selected": result["selected"], "palette": result["palette"],
                          "flags": [f"{f['severity']}: {f['code']}" for f in result["flags"]]}, indent=2))
        return 0 if result["status"] == READY else 1

    if args.command == "stack-edit":
        return _edit(args, progress)

    if args.command == "stack-kit":
        from .printkit import PrintProcess, build_print_kit

        process = PrintProcess.from_json(args.process) if args.process else PrintProcess()
        kit = build_print_kit(args.stack, args.output, process, progress, bambu_printer=args.bambu_printer)
        bambu = kit.get("bambu_3mf")
        print(json.dumps({"status": kit["status"], "cad_problems": kit["cad_problems"],
                          **({"bambu_3mf": {"status": bambu["status"], "file": bambu.get("file"),
                                            "validation": bambu.get("validation", {}).get("level"),
                                            "problems": bambu.get("problems", [])}} if bambu else {}),
                          "printability_counts": kit["printability"]["counts"],
                          "findings": [f"{f['severity']}: {f['file']} {f['kind']} {f.get('width_mm', '')}"
                                       for f in kit["printability"]["features"] if f["severity"] != "INFO"]}, indent=2))
        bambu_ok = bambu is None or (bambu["status"] == "WRITTEN" and bambu["validation"]["level"] != "FAILED")
        return 0 if (kit["status"]["cad"] == "CAD_VALID" and kit["status"]["fdm_estimate"] != "UNSUPPORTED_FEATURES"
                     and bambu_ok) else 1
    raise StackError(f"unknown stack command {args.command}")


def _edit_op(args: argparse.Namespace) -> dict[str, Any]:
    if args.op_file:
        return json.loads(args.op_file.read_text(encoding="utf-8"))
    if not args.op:
        raise StackError("apply needs an OP (e.g. recolor sheet=<id> color=#RRGGBB) or --op-file")
    op: dict[str, Any] = {"op": args.op}
    for item in args.params:
        key, sep, value = item.partition("=")
        if not sep:
            raise StackError(f"edit parameter {item!r} is not key=value")
        try:
            op[key] = json.loads(value)
        except json.JSONDecodeError:
            op[key] = value
    return op


def _edit(args: argparse.Namespace, progress: Callable[..., None]) -> int:
    from . import review

    command = args.edit_command
    if command == "init":
        stack, validation = review.start_review(args.source, args.project)
        progress("REVIEW_STARTED", project=args.project.as_posix(), sheets=len(stack.sheets),
                 base_validation=validation["status"])
        return 0
    if command == "inspect":
        report = review.inspect_project(args.project)
    elif command == "validate":
        report = validate_stack(review.load_project(args.project))
    elif command == "verify":
        report = review.verify_project(args.project)
    elif command == "apply":
        stack, entry = review.apply_to_project(args.project, _edit_op(args))
        report = {"edit": entry, "status": stack.review["current"]}
    elif command in ("undo", "revert"):
        keep = None if command == "undo" else args.to
        stack, _ = review.undo(args.project, args.steps) if keep is None else review.revert(args.project, keep)
        report = {"edits": len(stack.review["edits"]), "status": stack.review["current"]}
    elif command == "preview":
        report = review.preview_project(args.project, args.output, args.against)
    elif command == "export":
        report = review.export_project(args.project, args.output, progress)
    else:
        raise StackError(f"unknown stack-edit command {command}")
    if getattr(args, "report", None):
        _write(args.report, report)
    if command == "validate":
        print(json.dumps({k: report[k] for k in ("status", "failures", "sheet_count", "reconstruction")}, indent=2))
        return 0 if report["status"] == "PASS" else 1
    shown = {k: v for k, v in report.items() if k != "fingerprint"}
    print(json.dumps(shown, indent=2, default=_json_default))
    if command == "verify":
        return 0 if report["replay_identical"] else 1
    if command == "export":
        return 0 if report["pass"] else 1
    if command in ("apply", "undo", "revert"):
        return 0 if report["status"]["printable"] else 1
    if command == "preview":
        return 0 if report["review_status"]["printable"] else 1
    return 0 if report["status"]["printable"] else 1


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
