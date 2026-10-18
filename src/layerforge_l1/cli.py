"""Minimal command-line entry point for the L-1 proof."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .auto_pipeline import DEFAULT_CONFIG, apply_corrections, run_auto_case
from .connectivity_repair import run_connectivity_repair_case
from .pipeline import DEFAULT_MANIFEST, DEFAULT_OUTPUT, build, verify
from .layer_optimization import run_optimization_case
from .subtractive_poc import DEFAULT_CONFIG as POC_CONFIG
from .subtractive_poc import run_poc_case
from .width_diagnostics import run_width_diagnostics_case
from .width_aware_planner import run_width_aware_case
from .suitability import run_suitability_case
from .shadowbox_simplification import run_controlled_simplification, run_direct_case
from .object_simplification_poc import run_full_review as run_object_simplification_review
from .flat_color_workflow import run_flat_color_review


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="layerforge-l1")
    commands = result.add_subparsers(dest="command", required=True)
    build_command = commands.add_parser("build", help="build and validate the controlled L-1 fixture")
    build_command.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    build_command.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    verify_command = commands.add_parser("verify", help="verify the required artifact tree exists")
    verify_command.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    auto_command = commands.add_parser(
        "auto", help="propose, export, and validate layer sheets from an unannotated PNG"
    )
    auto_command.add_argument("--input", type=Path, required=True)
    auto_command.add_argument("--case-id", required=True)
    auto_command.add_argument("--output", type=Path, required=True)
    correction_command = commands.add_parser(
        "apply-corrections", help="derive a reviewed manifest without altering its automatic source"
    )
    correction_command.add_argument("--automatic-manifest", type=Path, required=True)
    correction_command.add_argument("--corrections", type=Path, required=True)
    correction_command.add_argument("--output", type=Path, required=True)
    poc_command = commands.add_parser(
        "poc2d", help="build the layer-first subtractive 2D proof without CAD"
    )
    poc_command.add_argument("--input", type=Path, required=True)
    poc_command.add_argument("--case-id", required=True)
    poc_command.add_argument("--output", type=Path, required=True)
    optimize_command = commands.add_parser(
        "optimize2d", help="optimize an existing subtractive 2D layer proposal without CAD"
    )
    optimize_command.add_argument("--input", type=Path, required=True)
    optimize_command.add_argument("--case-id", required=True)
    optimize_command.add_argument("--baseline", type=Path, required=True)
    optimize_command.add_argument("--output", type=Path, required=True)
    repair_command = commands.add_parser(
        "repair-connectivity2d",
        help="audit corner contacts and build an edge-connected subtractive 2D plan",
    )
    repair_command.add_argument("--input", type=Path, required=True)
    repair_command.add_argument("--case-id", required=True)
    repair_command.add_argument("--failed-plan", type=Path, required=True)
    repair_command.add_argument("--output", type=Path, required=True)
    width_command = commands.add_parser(
        "diagnose-width2d",
        help="correct structural-width diagnostics for a repaired 2D mask plan",
    )
    width_command.add_argument("--input", type=Path, required=True)
    width_command.add_argument("--case-id", required=True)
    width_command.add_argument("--repaired-plan", type=Path, required=True)
    width_command.add_argument("--output", type=Path, required=True)
    width_plan_command = commands.add_parser(
        "plan-width-aware2d",
        help="plan subtractive sheets using actual cumulative-mask width checks",
    )
    width_plan_command.add_argument("--input", type=Path, required=True)
    width_plan_command.add_argument("--case-id", required=True)
    width_plan_command.add_argument("--baseline", type=Path, required=True)
    width_plan_command.add_argument("--output", type=Path, required=True)
    width_plan_command.add_argument("--prefix-layers", type=int)
    width_plan_command.add_argument("--window-start", type=int)
    width_plan_command.add_argument("--window-end", type=int)
    suitability_command = commands.add_parser(
        "assess-suitability2d",
        help="classify an illustration and create an auditable simplification candidate when needed",
    )
    suitability_command.add_argument("--input", type=Path, required=True)
    suitability_command.add_argument("--case-id", required=True)
    suitability_command.add_argument("--output", type=Path, required=True)
    controlled_command = commands.add_parser(
        "simplify-shadowbox2d",
        help="generate protected LIGHT/BALANCED/AGGRESSIVE candidates and validate each with the 2D planner",
    )
    controlled_command.add_argument("--input", type=Path, required=True)
    controlled_command.add_argument("--case-id", required=True)
    controlled_command.add_argument("--output", type=Path, required=True)
    direct_command = commands.add_parser(
        "validate-direct2d",
        help="validate a DIRECT illustration with the same bounded 2D subtractive planner",
    )
    direct_command.add_argument("--input", type=Path, required=True)
    direct_command.add_argument("--case-id", required=True)
    direct_command.add_argument("--output", type=Path, required=True)
    object_command = commands.add_parser(
        "object-simplification2d",
        help="run one structurally motivated secondary-object edit proof",
    )
    object_command.add_argument("--fox-input", type=Path, required=True)
    object_command.add_argument("--bear-input", type=Path, required=True)
    object_command.add_argument("--historical-plan", type=Path, required=True)
    object_command.add_argument("--output", type=Path, required=True)
    flat_color_command = commands.add_parser(
        "flat-color-workflow2d",
        help="generate and validate flat-color and shadow-box-optimized artwork candidates",
    )
    flat_color_command.add_argument("--input", type=Path, required=True)
    flat_color_command.add_argument("--reference", type=Path, required=True)
    flat_color_command.add_argument("--output", type=Path, required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    if args.command == "build":
        report = build(args.manifest, args.output)
    elif args.command == "verify":
        report = verify(args.output)
    elif args.command == "auto":
        report = run_auto_case(args.input, args.case_id, args.output, DEFAULT_CONFIG)
    elif args.command == "apply-corrections":
        report = apply_corrections(args.automatic_manifest, args.corrections, args.output)
        print(json.dumps(report, indent=2))
        return 0
    elif args.command == "poc2d":
        report = run_poc_case(args.input, args.case_id, args.output, POC_CONFIG)
        print(json.dumps({"case_id": report["case_id"], "status": report["status"]}, indent=2))
        return 0 if report["status"] == "PASS" else 1
    elif args.command == "optimize2d":
        report = run_optimization_case(
            args.input, args.case_id, args.baseline, args.output, POC_CONFIG
        )
        print(json.dumps({"case_id": report["case_id"], "status": report["status"]}, indent=2))
        return 0 if report["status"] == "READY_FOR_USER_TEST" else 1
    elif args.command == "repair-connectivity2d":
        report = run_connectivity_repair_case(
            args.input, args.case_id, args.failed_plan, args.output, POC_CONFIG
        )
        print(
            json.dumps(
                {
                    "case_id": report["case_id"],
                    "review_state": report["review_state"],
                    "validation_status": report["validation_status"],
                },
                indent=2,
            )
        )
        return 0 if report["review_state"] == "READY_FOR_USER_TEST" else 1
    elif args.command == "diagnose-width2d":
        report = run_width_diagnostics_case(
            args.input, args.case_id, args.repaired_plan, args.output, POC_CONFIG
        )
        print(json.dumps(report, indent=2))
        return 0 if report["review_state"] == "READY_FOR_USER_TEST" else 1
    elif args.command == "assess-suitability2d":
        report = run_suitability_case(args.input, args.case_id, args.output, POC_CONFIG)
        print(
            json.dumps(
                {
                    "case_id": report["case_id"],
                    "classification": report["assessment"]["classification"],
                    "ready_to_proceed": report["ready_to_proceed"],
                },
                indent=2,
            )
        )
        return 0
    elif args.command == "simplify-shadowbox2d":
        report = run_controlled_simplification(
            args.input, args.case_id, args.output, POC_CONFIG
        )
        print(
            json.dumps(
                {
                    "case_id": report["case_id"],
                    "review_state": report["review_state"],
                    "candidate_count": len(report["candidates"]),
                },
                indent=2,
            )
        )
        return 0
    elif args.command == "validate-direct2d":
        report = run_direct_case(args.input, args.case_id, args.output, POC_CONFIG)
        print(
            json.dumps(
                {
                    "case_id": report["case_id"],
                    "classification": report["classification"],
                    "planner_status": report["planner"]["planner_status"],
                },
                indent=2,
            )
        )
        return 0
    elif args.command == "object-simplification2d":
        report = run_object_simplification_review(
            args.fox_input,
            args.bear_input,
            args.output,
            args.historical_plan,
        )
        print(json.dumps(report, indent=2))
        return 0
    elif args.command == "flat-color-workflow2d":
        report = run_flat_color_review(
            args.input,
            args.reference,
            args.output,
            POC_CONFIG,
        )
        print(
            json.dumps(
                {
                    "review_state": report["review_state"],
                    "flat_color_valid": report["candidate_validity"]["flat_color"],
                    "shadow_box_optimized_valid": report["candidate_validity"][
                        "shadow_box_optimized"
                    ],
                    "automatic_candidate_selection": report[
                        "automatic_candidate_selection"
                    ],
                },
                indent=2,
            )
        )
        return 0
    else:
        window = None
        if args.window_start is not None or args.window_end is not None:
            if args.window_start is None or args.window_end is None:
                raise SystemExit("--window-start and --window-end must be supplied together")
            window = (args.window_start, args.window_end)
        report = run_width_aware_case(
            args.input,
            args.case_id,
            args.baseline,
            args.output,
            POC_CONFIG,
            prefix_layers=args.prefix_layers,
            window=window,
        )
        print(
            json.dumps(
                {
                    "case_id": report["case_id"],
                    "review_state": report["review_state"],
                    "width_failed_layers": report["width_aware"][
                        "width_failed_layers"
                    ],
                },
                indent=2,
            )
        )
        return 0 if report["review_state"] == "READY_FOR_USER_TEST" else 1
    print(json.dumps({"fixture_id": report["fixture_id"], "status": report["status"]}, indent=2))
    return 0 if report["status"] == "READY_FOR_USER_TEST" else 1


if __name__ == "__main__":
    raise SystemExit(main())

