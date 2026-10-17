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
    else:
        report = run_width_diagnostics_case(
            args.input, args.case_id, args.repaired_plan, args.output, POC_CONFIG
        )
        print(json.dumps(report, indent=2))
        return 0 if report["review_state"] == "READY_FOR_USER_TEST" else 1
    print(json.dumps({"fixture_id": report["fixture_id"], "status": report["status"]}, indent=2))
    return 0 if report["status"] == "READY_FOR_USER_TEST" else 1


if __name__ == "__main__":
    raise SystemExit(main())

