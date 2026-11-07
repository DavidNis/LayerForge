"""Performance Step 5 check: timings and output hashes of the three clean-ups on one golden kit.

    python scripts/perf_step5_check.py <out.json> <golden kit dir>

Read-only use of the kit (``stack/`` and ``cad/stl``).  Records, for the current code:

* ``downsample``: ``cad._downsample`` on real 4x supersampled rasters of every sheet (hash of each result);
* ``trace``: ``cad.mask_to_geometry`` of every sheet (WKB hash of each outline);
* ``kit_sequence``: the print-kit order of the steps that trace or validate a stack —
  ``validate_stack``, one trace per sheet (as ``export_stack``), ``revalidate_cad`` on the kit's STL
  files, ``assess_printability``, then ``validate_stack`` of a reloaded copy (as the next kit build);
  hashes of every report, plus the kit's own ``cad_revalidation.json`` / ``printability`` for comparison.

Caches added in Step 5 are cleared before each section, so a section measures one cold kit build.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import shapely

from layerforge_l1.core import cad, validation
from layerforge_l1.core.model import load_stack
from layerforge_l1.core.printkit import PrintProcess, assess_printability


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def jsha(obj) -> str:
    return sha(json.dumps(obj, sort_keys=True, default=str).encode())


def clear_caches() -> None:
    for module in (cad, validation):
        for name in ("clear_cache",):
            if hasattr(module, name):
                getattr(module, name)()


def without_seconds(obj):
    if isinstance(obj, dict):
        return {k: without_seconds(v) for k, v in obj.items() if "seconds" not in k}
    if isinstance(obj, list):
        return [without_seconds(v) for v in obj]
    return obj


def supersampled(geometry, grid) -> np.ndarray:
    n = grid.outer_pixels * cad.SUPERSAMPLE
    image = np.zeros((n, n), np.uint8)
    for poly in cad.polygons(geometry):
        cv2.fillPoly(image, [cad._to_subpixels(poly.exterior.coords, grid)], 1, shift=4)
        for ring in poly.interiors:
            cv2.fillPoly(image, [cad._to_subpixels(ring.coords, grid)], 0, shift=4)
    return image


def main() -> None:
    out, kit = Path(sys.argv[1]), Path(sys.argv[2])
    stack = load_stack(kit / "stack")
    grid = stack.grid
    result: dict = {"kit": kit.as_posix(), "sheets": len(stack.sheets), "outer_pixels": grid.outer_pixels}

    warm = np.zeros((grid.outer_pixels,) * 2, bool)
    warm[:10, :10] = True
    cad.mask_to_geometry(warm, grid.mm_per_pixel, cad.DEFAULT_SIMPLIFY_MM)  # one-off imports (CadQuery)
    clear_caches()
    t = time.perf_counter()
    outlines = [cad.mask_to_geometry(s.retained, grid.mm_per_pixel, cad.DEFAULT_SIMPLIFY_MM) for s in stack.sheets]
    result["trace"] = {"seconds": round(time.perf_counter() - t, 3),
                       "wkb_sha256": [sha(shapely.to_wkb(g)) for g in outlines]}

    images = [supersampled(g, grid) for g in outlines]
    times, hashes = [], []
    for image in images:
        best = None
        for _ in range(3):
            t = time.perf_counter()
            down = cad._downsample(image, grid)
            best = min(best or 1e9, time.perf_counter() - t)
        times.append(best)
        hashes.append(sha(np.packbits(down).tobytes() + str(down.shape).encode() + str(down.dtype).encode()))
    result["downsample"] = {"calls": len(images), "best_of_3_seconds_total": round(sum(times), 4),
                            "per_call_ms": round(1000 * sum(times) / len(times), 1), "sha256": hashes}
    del images

    clear_caches()
    steps: dict = {}
    t = time.perf_counter()
    first = validation.validate_stack(stack)
    steps["validate_stack"] = time.perf_counter() - t
    t = time.perf_counter()
    export_geoms = [cad.sheet_geometry(stack, i) for i in range(len(stack.sheets))]
    steps["trace_for_export"] = time.perf_counter() - t
    t = time.perf_counter()
    check, stl_masks = cad.revalidate_cad(stack, kit / "cad")
    steps["revalidate_cad"] = time.perf_counter() - t
    manifest = json.loads((kit / "cad" / "cad_manifest.json").read_text(encoding="utf-8"))
    t = time.perf_counter()
    printability = assess_printability(stack, PrintProcess(), manifest["layers"])
    steps["assess_printability"] = time.perf_counter() - t
    t = time.perf_counter()
    second = validation.validate_stack(load_stack(kit / "stack"))
    steps["validate_stack_again_reloaded"] = time.perf_counter() - t
    golden_kit = json.loads((kit / "print_kit.json").read_text(encoding="utf-8"))
    golden_check = json.loads((kit / "cad" / "cad_revalidation.json").read_text(encoding="utf-8"))
    printability.get("features", []) and [f.pop("_geometry", None) for f in printability["features"]]
    result["kit_sequence"] = {
        "seconds": {k: round(v, 3) for k, v in steps.items()},
        "total_seconds": round(sum(steps.values()), 3),
        "validation_sha256": jsha(first),
        "validation_again_identical": jsha(first) == jsha(second),
        "export_geometry_wkb_sha256": [sha(shapely.to_wkb(g)) for g in export_geoms],
        "revalidation_sha256": jsha(check),
        "revalidation_equals_kit_file": without_seconds(check) == without_seconds(golden_check),
        "stl_masks_sha256": [sha(np.packbits(m).tobytes()) for m in stl_masks],
        "printability_sha256": jsha(without_seconds(printability)),
        "printability_equals_kit_file": jsha(without_seconds(printability))
        == jsha(without_seconds(golden_kit["printability"])),
    }
    out.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k not in ("trace", "downsample", "kit_sequence")}
                     | {"trace_s": result["trace"]["seconds"], "downsample": {k: v for k, v in result["downsample"].items() if k != "sha256"},
                        "kit_sequence": {k: v for k, v in result["kit_sequence"].items() if not k.endswith("sha256")}}, indent=1))


if __name__ == "__main__":
    main()
