"""Performance Step 3 check: serial vs parallel OCCT validity check on existing STEP files.

    python scripts/perf_step3_check.py <out.json> <step file or folder> ...

Each STEP file is imported once (``cq.importers.importStep``, as the print kit does) and checked by
``BRepCheck_Analyzer`` both ways: serial (``cq.Shape.isValid``, the current kit check) and parallel
(``theIsParallel=True``; geometric controls on, same as serial).  For each mode it records the verdict,
a signature of *every* status the analyzer reports (each vertex/edge/wire/face/shell/solid, including
statuses in the context of each parent shape), the wall time, and the working-set peak during the
check above the level before it.  The two modes run in alternating order per file.  Two shapes with
known defects (checked in memory, see ``negative_controls``) must be reported invalid by both modes
with identical statuses.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import hashlib
import json
import sys
import threading
import time
from pathlib import Path

import cadquery as cq
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.TopExp import TopExp
from OCP.TopTools import TopTools_IndexedMapOfShape


class _PMC(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t)] + [(f"f{i}", ctypes.c_size_t) for i in range(6)]


_k32, _psapi = ctypes.WinDLL("kernel32"), ctypes.WinDLL("psapi")
_k32.GetCurrentProcess.restype = wt.HANDLE
_psapi.GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.POINTER(_PMC), wt.DWORD]


def working_set_mb() -> float:
    c = _PMC()
    c.cb = ctypes.sizeof(_PMC)
    _psapi.GetProcessMemoryInfo(_k32.GetCurrentProcess(), ctypes.byref(c), c.cb)
    return c.WorkingSetSize / 2**20


def signature(analyzer: BRepCheck_Analyzer, shape) -> tuple[str, int]:
    """Hash of every status of every subshape (own statuses + statuses in each parent's context)."""

    index = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, index)
    rows = []
    for i in range(1, index.Extent() + 1):
        sub = index.FindKey(i)
        result = analyzer.Result(sub)
        if result is None:
            rows.append((i, "no result"))
            continue
        own = [int(s.value) if hasattr(s, "value") else int(s) for s in result.Status()]
        contexts = []
        result.InitContextIterator()
        while result.MoreShapeInContext():
            ctx = result.ContextualShape()
            contexts.append((index.FindIndex(ctx), [int(s.value) if hasattr(s, "value") else int(s)
                                                    for s in result.StatusOnShape()]))
            result.NextShapeInContext()
        rows.append((i, sub.ShapeType().name, own, sorted(contexts)))
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest(), sum(1 for r in rows if r[2:3] and r[2] != [0])


def check(shape, parallel: bool) -> dict:
    base = working_set_mb()
    peak = [base]
    stop = threading.Event()

    def sample() -> None:
        while not stop.is_set():
            peak[0] = max(peak[0], working_set_mb())
            time.sleep(0.02)

    thread = threading.Thread(target=sample, daemon=True)
    thread.start()
    t = time.perf_counter()
    if parallel:
        analyzer = BRepCheck_Analyzer(shape.wrapped, True, True)
        valid = analyzer.IsValid()
    else:
        valid = shape.isValid()  # the current implementation (serial)
        analyzer = BRepCheck_Analyzer(shape.wrapped, True, False)  # same check, kept for the status signature
    seconds = time.perf_counter() - t
    stop.set()
    thread.join()
    sig, faulty = signature(analyzer, shape.wrapped)
    return {"valid": bool(valid), "seconds": round(seconds, 3), "extra_peak_mb": round(peak[0] - base, 1),
            "status_signature": sig, "subshapes_with_errors": faulty}


def negative_controls() -> list[tuple[str, cq.Shape]]:
    """Shapes with known defects, checked in memory: the STEP reader heals these defects when a file
    is reopened (observed: a bow-tie face, an open shell and even a vertex moved 3 mm come back valid),
    so they cannot be stored as STEP files."""

    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Shell, TopoDS_Solid

    bowtie = cq.Face.makeFromWires(cq.Wire.makePolygon(
        [cq.Vector(0, 0, 0), cq.Vector(10, 10, 0), cq.Vector(10, 0, 0), cq.Vector(0, 10, 0)], close=True))
    builder, shell, solid = BRep_Builder(), TopoDS_Shell(), TopoDS_Solid()
    builder.MakeShell(shell)
    for face in cq.Solid.makeBox(10, 10, 2).Faces()[:-1]:  # one face missing: not closed
        builder.Add(shell, face.wrapped)
    builder.MakeSolid(solid)
    builder.Add(solid, shell)
    return [("invalid_bowtie_face", bowtie), ("invalid_bowtie_solid", cq.Solid.extrudeLinear(bowtie, cq.Vector(0, 0, 1.6))),
            ("invalid_open_shell_solid", cq.Shape.cast(solid))]


def main() -> None:
    out = Path(sys.argv[1])
    files = []
    for arg in sys.argv[2:]:
        p = Path(arg)
        files += sorted(p.rglob("*.step")) if p.is_dir() else [p]
    rows = []
    for n, path in enumerate(files + negative_controls()):
        t = time.perf_counter()
        if isinstance(path, tuple):  # in-memory negative control
            path, shape = Path(path[0]), path[1]
        else:
            shape = cq.importers.importStep(str(path)).val()
        import_s = time.perf_counter() - t
        order = (False, True) if n % 2 == 0 else (True, False)
        res = {("parallel" if mode else "serial"): check(shape, mode) for mode in order}
        s, p = res["serial"], res["parallel"]
        same = s["valid"] == p["valid"] and s["status_signature"] == p["status_signature"]
        row = {"file": path.as_posix(), "faces": len(shape.Faces()), "edges": len(shape.Edges()),
               "import_seconds": round(import_s, 2), "first": "parallel" if order[0] else "serial",
               "serial": s, "parallel": p, "identical": same}
        rows.append(row)
        print(f"{n + 1:3d}/{len(files) + 3} {path.name:32s} faces {row['faces']:5d} valid {s['valid']!s:5s}/{p['valid']!s:5s} "
              f"serial {s['seconds']:7.2f}s parallel {p['seconds']:7.2f}s  extra MB {s['extra_peak_mb']:6.1f}/"
              f"{p['extra_peak_mb']:6.1f}  {'IDENTICAL' if same else 'DIFFERENT'}", flush=True)
    golden = [r for r in rows if "invalid_" not in r["file"]]
    summary = {
        "files": len(rows), "golden_layers": len(golden),
        "all_identical": all(r["identical"] for r in rows),
        "golden_all_valid": all(r["serial"]["valid"] for r in golden),
        "negative_controls_invalid_in_both": all(not r["serial"]["valid"] and not r["parallel"]["valid"]
                                                 for r in rows if "invalid_" in r["file"]),
        "serial_seconds": round(sum(r["serial"]["seconds"] for r in golden), 1),
        "parallel_seconds": round(sum(r["parallel"]["seconds"] for r in golden), 1),
        "import_seconds": round(sum(r["import_seconds"] for r in golden), 1),
        "max_extra_peak_mb": {m: max(r[m]["extra_peak_mb"] for r in golden) for m in ("serial", "parallel")},
    }
    summary["speedup"] = round(summary["serial_seconds"] / max(summary["parallel_seconds"], 1e-9), 2)
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
