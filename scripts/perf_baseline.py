"""Performance baseline and golden-output check for the optimization steps.

    python scripts/perf_baseline.py run <projects.json> <out dir> [project ...]
    python scripts/perf_baseline.py compare <golden dir> <new dir> [project ...]

``run`` executes, per project and in a fresh process each, the same calls as the app: the L1
proposal (``core.propose.propose`` with the project's settings) and a print kit of the selected
candidate (``core.printkit.build_print_kit``).  It writes the outputs, ``timing.json`` (wall time and
peak working set per stage, from timers around existing functions; no code is changed) and
``fingerprint.json``: a content hash of every output file.  Things that legitimately differ between
runs are normalized: timings, timestamps, absolute output paths and the STEP header time stamp.
PNG files are hashed by their decoded pixels, STEP files without the header time stamp and OCCT's
per-process file counter; reopened STL files are also re-rasterized with the
validator's own 4x raster (``cad.rasterize_stl``) so geometric equivalence is checked separately from
byte equality.

``run ... --resume-kit`` reuses a completed proposal already in the output folder (its
``proposal.json`` is written last, after every candidate, the CAD checks and the previews) and builds
only the missing print kit; ``timing.json`` then marks the proposal times as taken from
``proposal.json`` / ``cad_manifest.json`` (no per-stage memory for the proposal).

``run ... --export-step`` runs the proposal with ``ProposalConfig(export_step=True)``, the
proposal-time STEP export used before performance Step 2 (for before/after benchmarks).

``compare`` reports every file whose fingerprint differs (and, for JSON, the differing keys).
"""

from __future__ import annotations

import collections
import ctypes
import ctypes.wintypes as wt
import functools
import hashlib
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

# ---- timing / memory -----------------------------------------------------------------------------


class _PMC(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t)] + [(f"f{i}", ctypes.c_size_t) for i in range(6)]


def _working_set() -> tuple[int, int]:
    c = _PMC()
    c.cb = ctypes.sizeof(_PMC)
    k32, psapi = ctypes.WinDLL("kernel32"), ctypes.WinDLL("psapi")
    k32.GetCurrentProcess.restype = wt.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.POINTER(_PMC), wt.DWORD]
    psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb)
    return c.WorkingSetSize, c.PeakWorkingSetSize


class Stages:
    """Wall time and peak working set of named stages (outermost activation only)."""

    def __init__(self) -> None:
        self.current: list[str] = []
        self.seconds: dict[str, float] = collections.defaultdict(float)
        self.calls: collections.Counter = collections.Counter()
        self.peak_mb: dict[str, int] = collections.defaultdict(int)
        self._stop = threading.Event()
        threading.Thread(target=self._sample, daemon=True).start()

    def _sample(self) -> None:
        while not self._stop.is_set():
            ws = _working_set()[0] >> 20
            for name in self.current:
                self.peak_mb[name] = max(self.peak_mb[name], ws)
            time.sleep(0.05)

    def wrap(self, owner: Any, attr: str, name: str) -> None:
        fn = getattr(owner, attr)

        @functools.wraps(fn)
        def inner(*a: Any, **k: Any) -> Any:
            if name in self.current:
                return fn(*a, **k)
            self.current.append(name)
            t = time.perf_counter()
            try:
                return fn(*a, **k)
            finally:
                self.seconds[name] += time.perf_counter() - t
                self.calls[name] += 1
                self.current.remove(name)

        setattr(owner, attr, inner)

    def report(self) -> dict[str, Any]:
        self._stop.set()
        return {name: {"seconds": round(self.seconds[name], 2), "calls": self.calls[name],
                       "peak_working_set_mb": self.peak_mb.get(name)} for name in self.seconds}


def _run_project(spec: dict[str, Any], out: Path) -> None:
    """Child process: proposal + kit for one project, with stage timers."""

    import layerforge_l1.core.cad as cad
    import layerforge_l1.core.intake as intake
    import layerforge_l1.core.preview as preview
    import layerforge_l1.core.printkit as printkit
    import layerforge_l1.core.propose as propose
    import layerforge_l1.core.validation as validation
    from layerforge_l1.core.intake import IntakeConfig
    from layerforge_l1.core.planning import PlanningConfig
    from layerforge_l1.core.propose import ProposalConfig

    stages = Stages()
    for owner, attr, name in [
        (propose, "load_image", "load_image"), (propose, "prepare_target", "intake"),
        (propose, "plan_stack", "planning (all candidates)"), (propose, "validate_stack", "validation (all candidates)"),
        (propose, "save_stack", "save candidate stacks"),
        (cad, "export_stack", "CAD export + read-back"), (cad, "revalidate_cad", "CAD revalidation from STL"),
        (preview, "generate_previews", "previews"),
        (printkit, "validate_stack", "kit: validation"), (printkit, "assess_printability", "kit: printability"),
        (printkit, "printability_maps", "kit: printability maps"), (printkit, "side_section", "kit: side section"),
    ]:
        stages.wrap(owner, attr, name)
    del intake, validation
    out.mkdir(parents=True, exist_ok=True)
    timing: dict[str, Any] = {"project": spec["name"]}
    t0 = time.perf_counter()
    if spec.get("resume_kit"):
        result = json.loads((out / "proposal" / "proposal.json").read_text(encoding="utf-8"))
        if result.get("status") != propose.READY or not result.get("selected"):
            raise SystemExit(f"cannot resume: proposal status {result.get('status')}, selected {result.get('selected')}")
        if (out / "kit").exists():
            raise SystemExit(f"cannot resume: {out / 'kit'} exists; move the interrupted kit aside first")
        cad_manifest = json.loads((out / "proposal" / "selected" / "cad" / "cad_manifest.json").read_text(encoding="utf-8"))
        timing["proposal_seconds"] = result["seconds"]
        timing["resumed"] = {"proposal_reused": True, "proposal_timing_source": "proposal.json seconds, "
                             "candidates planning_seconds, cad_manifest total_seconds (no per-stage memory)",
                             "proposal_cad_seconds": cad_manifest["total_seconds"]}
    else:
        planning = PlanningConfig(outer_sheet_mm=spec["outer_mm"], scale=spec["scale"], thickness_mm=spec["thickness_mm"])
        result = propose.propose(Path(spec["image"]), out / "proposal", IntakeConfig(max_colors=spec["max_colors"]),
                                 ProposalConfig(planning=planning, **({"export_step": True} if spec.get("export_step") else {})))
        timing["proposal_seconds"] = round(time.perf_counter() - t0, 1)
    timing["candidates"] = [{k: c.get(k) for k in ("name", "status", "sheet_count", "planning_seconds")}
                            for c in result["candidates"]]
    timing["selected"] = result["selected"]
    timing["proposal_export_step"] = bool(spec.get("export_step")) or None
    if result["selected"]:
        t1 = time.perf_counter()
        printkit.build_print_kit(out / "proposal" / "candidates" / result["selected"] / "stack", out / "kit")
        timing["kit_seconds"] = round(time.perf_counter() - t1, 1)
    timing["total_seconds"] = round(time.perf_counter() - t0, 1) + (timing["proposal_seconds"] if spec.get("resume_kit") else 0)
    timing["stages"] = stages.report()
    timing["process_peak_working_set_mb"] = _working_set()[1] >> 20
    (out / "timing.json").write_text(json.dumps(timing, indent=1), encoding="utf-8")
    (out / "fingerprint.json").write_text(json.dumps(fingerprint(out), indent=1, sort_keys=True), encoding="utf-8")


# ---- fingerprints --------------------------------------------------------------------------------

# ``manifest_sha256`` hashes a stack manifest that records planning times; that manifest is
# itself fingerprinted (normalized), so its raw hash is not compared.
_VOLATILE_KEY = re.compile(r"seconds|_utc$|^created|^checked|elapsed|^manifest_sha256$")
_STEP_TIME = re.compile(rb"^FILE_NAME\(.*?\);", re.S | re.M)
# OCCT numbers the PRODUCT name of each STEP file it writes within one process ("Open CASCADE STEP
# translator 7.8 <n>"): it depends on how many files the process wrote before, not on the geometry.
_STEP_COUNTER = re.compile(rb"(Open CASCADE STEP translator [0-9.]+ )\d+")


def _normalize(value: Any, roots: tuple[str, ...]) -> Any:
    if isinstance(value, dict):
        return {k: _normalize(v, roots) for k, v in value.items() if not _VOLATILE_KEY.search(str(k))}
    if isinstance(value, list):
        return [_normalize(v, roots) for v in value]
    if isinstance(value, str):
        for root in roots:
            value = value.replace(root, "<OUT>")
    return value


def _checksum_lines(text: str) -> list[str]:
    """``checksums.sha256`` of a kit: STEP files carry a time stamp and JSON files timings; both are
    fingerprinted on their own (normalized), so only their names are kept here."""

    rows = []
    for line in text.splitlines():
        digest, _, name = line.partition("  ")
        rows.append(name if name.endswith((".step", ".json")) else line)
    return rows


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fingerprint(out: Path) -> dict[str, Any]:
    from PIL import Image

    roots = (out.resolve().as_posix(), out.as_posix())
    files: dict[str, Any] = {}
    for path in sorted(p for p in out.rglob("*") if p.is_file() and p.name not in ("timing.json", "fingerprint.json")):
        rel = path.relative_to(out).as_posix()
        suffix = path.suffix.lower()
        if suffix == ".json":
            data = _normalize(json.loads(path.read_text(encoding="utf-8")), roots)
            files[rel] = {"sha256": _sha(json.dumps(data, sort_keys=True).encode()), "json": data}
        elif suffix == ".png":
            image = np.asarray(Image.open(path))
            files[rel] = {"pixels_sha256": _sha(image.tobytes() + str(image.shape).encode())}
        elif path.name == "checksums.sha256":
            lines = _checksum_lines(path.read_text(encoding="utf-8"))
            files[rel] = {"sha256_normalized": _sha("\n".join(lines).encode())}
        elif suffix in (".step", ".stp"):
            data = _STEP_COUNTER.sub(rb"\1<n>", _STEP_TIME.sub(b"FILE_NAME(<time>);", path.read_bytes(), 1))
            files[rel] = {"sha256_without_header_time_and_counter": _sha(data)}
        else:
            files[rel] = {"sha256": _sha(path.read_bytes())}
    # Geometric equivalence of every reopened STL, independent of its bytes: the validator's raster.
    from layerforge_l1.core.cad import rasterize_stl
    from layerforge_l1.core.model import StackGrid

    for cad_dir in sorted(p.parent for p in out.rglob("cad_manifest.json")):
        stack_dir = (out / "kit" / "stack") if cad_dir.parent.name == "kit" else None
        if stack_dir is None:
            selected = json.loads((out / "proposal" / "proposal.json").read_text(encoding="utf-8"))["selected"]
            stack_dir = out / "proposal" / "candidates" / selected / "stack"
        manifest = json.loads((stack_dir / "sheet_stack.json").read_text(encoding="utf-8"))
        grid = StackGrid.from_json(manifest["grid"])
        for stl in sorted((cad_dir / "stl").glob("*.stl")):
            mask = rasterize_stl(stl, grid)
            files[stl.relative_to(out).as_posix()]["raster_4x_sha256"] = _sha(np.packbits(mask).tobytes())
    return files


def compare(a: Path, b: Path) -> list[str]:
    fa = json.loads((a / "fingerprint.json").read_text(encoding="utf-8"))
    fb = json.loads((b / "fingerprint.json").read_text(encoding="utf-8"))
    problems = [f"only in {a.name}: {k}" for k in sorted(set(fa) - set(fb))]
    problems += [f"only in {b.name}: {k}" for k in sorted(set(fb) - set(fa))]
    for key in sorted(set(fa) & set(fb)):
        x, y = fa[key], fb[key]
        for field in sorted(set(x) | set(y)):
            if field == "json":
                continue
            if x.get(field) != y.get(field):
                detail = ""
                if field == "sha256" and "json" in x:
                    detail = " keys: " + ", ".join(_json_diff(x["json"], y["json"])[:8])
                problems.append(f"{key}: {field} differs{detail}")
    return problems


def _json_diff(x: Any, y: Any, path: str = "") -> list[str]:
    if isinstance(x, dict) and isinstance(y, dict):
        return [d for k in sorted(set(x) | set(y)) for d in _json_diff(x.get(k), y.get(k), f"{path}.{k}")]
    if isinstance(x, list) and isinstance(y, list) and len(x) == len(y):
        return [d for i, (u, v) in enumerate(zip(x, y)) for d in _json_diff(u, v, f"{path}[{i}]")]
    return [] if x == y else [path or "<root>"]


def main() -> None:
    mode = sys.argv[1]
    if mode == "refingerprint":  # after a change to the normalization only
        for out in sys.argv[2:]:
            Path(out, "fingerprint.json").write_text(json.dumps(fingerprint(Path(out)), indent=1, sort_keys=True),
                                                     encoding="utf-8")
        return
    if mode == "child":
        _run_project(json.loads(sys.argv[2]), Path(sys.argv[3]))
    elif mode == "run":
        specs = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        resume, export_step = "--resume-kit" in sys.argv[4:], "--export-step" in sys.argv[4:]
        out, only = Path(sys.argv[3]), set(sys.argv[4:]) - {"--resume-kit", "--export-step"}
        for spec in specs:
            if only and spec["name"] not in only:
                continue
            print(f"[perf] {spec['name']} ...", flush=True)
            started = time.perf_counter()
            spec = {**spec, "resume_kit": True} if resume else spec
            spec = {**spec, "export_step": True} if export_step else spec  # proposal-time STEP (before Step 2)
            subprocess.run([sys.executable, __file__, "child", json.dumps(spec), str(out / spec["name"])], check=True)
            timing = json.loads((out / spec["name"] / "timing.json").read_text(encoding="utf-8"))
            print(f"[perf] {spec['name']} proposal {timing['proposal_seconds']} s, kit {timing.get('kit_seconds')} s, "
                  f"process {round(time.perf_counter() - started, 1)} s", flush=True)
    elif mode == "compare":
        a, b, only = Path(sys.argv[2]), Path(sys.argv[3]), set(sys.argv[4:])
        names = sorted(p.name for p in a.iterdir() if (p / "fingerprint.json").is_file() and (not only or p.name in only))
        failed = 0
        for name in names:
            problems = compare(a / name, b / name)
            failed += bool(problems)
            print(f"{name}: {'IDENTICAL' if not problems else f'{len(problems)} DIFFERENCES'}")
            for p in problems[:40]:
                print("   ", p)
        sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
