"""Performance Step 2 audit: every difference between golden and Step-2 outputs must be an allowed STEP-only difference.

    python scripts/perf_audit_step2.py artifacts/perf_v1 <project ...>   (golden/ vs step2_after/)

Allowed:
  * proposal/selected/cad/step/*.step present only in golden;
  * proposal/proposal.json: .cad.all_step_valid_single_solid (True -> None), .cad.step_verification (added,
    PENDING_PRINT_KIT), .flags (exactly one added INFO flag STEP_VERIFICATION_PENDING, all others unchanged);
  * proposal/selected/cad/cad_manifest.json: per-layer step_solids / step_valid / step_volume_mm3 /
    step_volume_vs_expected_pct -> None, assembly_back_to_front[*].step -> None, step_exported: false added.
Everything else (all kit files, STL bytes + rasters, previews, masks, validation, revalidation) must be identical.
"""
import json
import sys
from pathlib import Path

STEP_LAYER_KEYS = {"step_solids", "step_valid", "step_volume_mm3", "step_volume_vs_expected_pct"}


def leaves(x, path=""):
    if isinstance(x, dict):
        for k, v in x.items():
            yield from leaves(v, f"{path}.{k}")
    elif isinstance(x, list):
        for i, v in enumerate(x):
            yield from leaves(v, f"{path}[{i}]")
    else:
        yield path, x


def audit(golden: Path, new: Path) -> list[str]:
    fg = json.loads((golden / "fingerprint.json").read_text(encoding="utf-8"))
    fn = json.loads((new / "fingerprint.json").read_text(encoding="utf-8"))
    bad = []
    for k in sorted(set(fg) - set(fn)):
        if not (k.startswith("proposal/selected/cad/step/") and k.endswith(".step")):
            bad.append(f"missing: {k}")
    bad += [f"extra: {k}" for k in sorted(set(fn) - set(fg))]
    stats = {"identical_files": 0, "allowed_json_changes": 0}
    for k in sorted(set(fg) & set(fn)):
        a, b = fg[k], fn[k]
        if {f: v for f, v in a.items() if f != "json"} == {f: v for f, v in b.items() if f != "json"}:
            stats["identical_files"] += 1
            continue
        if k == "proposal/proposal.json":
            ja, jb = dict(a["json"]), dict(b["json"])
            ca, cb = dict(ja.pop("cad")), dict(jb.pop("cad"))
            fa, fb = ja.pop("flags"), jb.pop("flags")
            if ja != jb:
                bad.append(f"{k}: keys outside cad/flags differ")
            if cb.pop("step_verification", None) != "PENDING_PRINT_KIT":
                bad.append(f"{k}: step_verification not PENDING_PRINT_KIT")
            if ca.pop("all_step_valid_single_solid") is not True or cb.pop("all_step_valid_single_solid") is not None:
                bad.append(f"{k}: all_step_valid_single_solid not True -> None")
            if ca != cb:
                bad.append(f"{k}: other cad fields differ: {ca} vs {cb}")
            added = [f for f in fb if f not in fa]
            if [f for f in fa if f not in fb] or [f["code"] for f in added] != ["STEP_VERIFICATION_PENDING"] \
                    or [f for f in fb if f in fa] != fa:
                bad.append(f"{k}: flags change is not exactly one added STEP_VERIFICATION_PENDING")
            stats["allowed_json_changes"] += 1
        elif k == "proposal/selected/cad/cad_manifest.json":
            ja, jb = a["json"], b["json"]
            if jb.get("step_exported") is not False:
                bad.append(f"{k}: step_exported false missing")
            la, lb = dict(leaves(ja)), dict(leaves({x: y for x, y in jb.items() if x != "step_exported"}))
            if set(la) != set(lb):
                bad.append(f"{k}: structure differs")
            for path in sorted(set(la) & set(lb)):
                if la[path] == lb[path]:
                    continue
                last = path.rsplit(".", 1)[-1]
                ok_layer = path.startswith(".layers[") and last in STEP_LAYER_KEYS and lb[path] is None
                ok_asm = path.startswith(".assembly_back_to_front[") and last == "step" and lb[path] is None
                if not (ok_layer or ok_asm):
                    bad.append(f"{k}: {path} {la[path]!r} -> {lb[path]!r}")
            stats["allowed_json_changes"] += 1
        else:
            bad.append(f"changed: {k} ({', '.join(f for f in a if f != 'json' and a.get(f) != b.get(f))})")
    print(f"  {stats['identical_files']} files identical, {stats['allowed_json_changes']} allowed JSON changes, "
          f"{len(set(fg) - set(fn))} proposal STEP files no longer written")
    return bad


root = Path(sys.argv[1])
failed = 0
for name in sys.argv[2:]:
    print(name)
    problems = audit(root / "golden" / name, root / "step2_after" / name)
    failed += bool(problems)
    print("  PASS: only STEP-only differences" if not problems else "  FAIL:\n    " + "\n    ".join(problems))
sys.exit(1 if failed else 0)
