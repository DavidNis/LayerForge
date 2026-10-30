"""Experimental monotone reveal-closure planner (per-cell depth).

Each source region is split into cells, and depth is assigned per cell, so one
connected color region may appear on several physical sheets.  Sheets are
planned front to back.  Every sheet carries all structurally reachable material
behind the sheets in front of it (``structural_support``), plus its own visible
cells.

The key property is monotonicity: ``structural_support`` only grows when the
covered set grows.  For a fixed covered set and color there is therefore one
largest feasible reveal, computed as a greatest fixpoint by deleting
unsupported cells until stable.  Revealing more never makes a later sheet
harder.  Cells that can never be supported are returned as an explicit
obstruction set; the planner does not export them.

Width semantics are deliberately identical to
``connectivity_repair.audit_connection_width``: one odd elliptical erosion
footprint, structural cores must be 4-connected to the shared perimeter.  The
existing validator is reused unchanged to check the planner's output.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
from scipy import ndimage

from .connectivity_repair import _width_parameters
from .hidden_width_repair import WidthRepairConfig, validation_poc_config
from .layer_optimization import DEFAULT_OPTIMIZATION_CONFIG, OptimizationConfig
from .subtractive_poc import DEFAULT_CONFIG, PocConfig, _outer_and_perimeter


@dataclass(frozen=True)
class RevealConfig:
    """Physical scale, cell size and search controls."""

    # 512 px artwork = 200 mm; the 32 px perimeter makes the sheet 225 mm.
    outer_sheet_mm: float = 225.0
    scale: int = 4
    # Visible pixels may lie outside the 1.2 mm structural set only as short
    # nubs (convex corners, stroke ends) within this geodesic distance of it.
    nub_tolerance_mm: float = 0.5
    # Cells are region pieces inside square tiles of this size.
    cell_tile_mm: float = 3.9
    # Fragments smaller than this merge into a same-region neighbor cell.
    min_cell_mm2: float = 0.9
    split_regions: bool = True
    # Label of artwork-grid pixels that belong to the frame (e.g. side strips of
    # a portrait image on the square grid): solid on every sheet, never visible.
    frame_label: int | None = None
    # The planner's footprint exceeds the validator's by this many pixels so a
    # one-pixel diagonal step in the eroded core (which the 4-connected
    # validator treats as a break) cannot sit exactly at the threshold.
    planning_margin_pixels: int = 0
    beam_width: int = 6
    # "area": most covered area first; "colors": fewest unfinished colors first.
    beam_score: str = "area"
    max_sheets: int = 24


def physical_grid(
    config: RevealConfig, native: PocConfig = DEFAULT_CONFIG
) -> PocConfig:
    return validation_poc_config(
        native,
        WidthRepairConfig(physical_size_mm=config.outer_sheet_mm, validation_scale=config.scale),
    )


def width_kernel(
    grid: PocConfig,
    optimization: OptimizationConfig = DEFAULT_OPTIMIZATION_CONFIG,
    margin_pixels: int = 0,
) -> tuple[np.ndarray, dict[str, Any]]:
    parameters = _width_parameters(grid, optimization)
    size = int(parameters["morphology_kernel_size_pixels"]) + 2 * (margin_pixels // 2)
    parameters["planning_kernel_size_pixels"] = size
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size)), parameters


def build_cells(
    labels: np.ndarray, config: RevealConfig
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (cell map, cell color, cell source region) at native resolution.

    Regions are 4-connected same-color components.  With ``split_regions``
    each region is cut by a square tile grid; tiny fragments are merged into an
    adjacent cell of the same region so cuts do not create slivers.
    """

    height, width = labels.shape
    region_map = np.full(labels.shape, -1, dtype=np.int32)
    region_color: list[int] = []
    for color in np.unique(labels):
        components, count = ndimage.label(labels == color)
        region_map[components > 0] = components[components > 0] - 1 + len(region_color)
        region_color.extend([int(color)] * count)

    if not config.split_regions:
        return (
            region_map,
            np.asarray(region_color, dtype=np.int32),
            np.arange(len(region_color), dtype=np.int32),
        )

    # Tile and fragment sizes are physical, so cells match at any resolution.
    mm_per_pixel = config.outer_sheet_mm * DEFAULT_CONFIG.analysis_pixels / DEFAULT_CONFIG.outer_pixels / width
    tile = max(2, int(round(config.cell_tile_mm / mm_per_pixel)))
    min_pixels = max(1, int(round(config.min_cell_mm2 / mm_per_pixel**2)))
    cell_map = np.full(labels.shape, -1, dtype=np.int32)
    cell_region: list[int] = []
    for y in range(0, height, tile):
        for x in range(0, width, tile):
            block = region_map[y : y + tile, x : x + tile]
            out = cell_map[y : y + tile, x : x + tile]
            for region in np.unique(block):
                components, count = ndimage.label(block == region)
                out[components > 0] = components[components > 0] - 1 + len(cell_region)
                cell_region.extend([int(region)] * count)

    cell_map, cell_region = _merge_small_cells(cell_map, cell_region, min_pixels)
    cell_region_array = np.asarray(cell_region, dtype=np.int32)
    return cell_map, np.asarray(region_color, dtype=np.int32)[cell_region_array], cell_region_array


def _merge_small_cells(
    cell_map: np.ndarray, cell_region: list[int], min_pixels: int
) -> tuple[np.ndarray, list[int]]:
    changed = True
    while changed:
        changed = False
        areas = np.bincount(cell_map.ravel(), minlength=len(cell_region))
        boxes = ndimage.find_objects(cell_map + 1)
        for cell in np.flatnonzero((areas > 0) & (areas < min_pixels)):
            box = boxes[cell]
            ys = slice(max(box[0].start - 1, 0), box[0].stop + 1)
            xs = slice(max(box[1].start - 1, 0), box[1].stop + 1)
            window = cell_map[ys, xs]
            mask = window == cell
            if not mask.any():
                continue  # already merged this pass
            ring = ndimage.binary_dilation(mask, np.ones((3, 3), bool)) & ~mask
            neighbors = [
                int(value)
                for value in np.unique(window[ring])
                if value >= 0 and cell_region[int(value)] == cell_region[int(cell)]
            ]
            if neighbors:
                window[mask] = max(neighbors, key=lambda value: areas[value])
                changed = True
    used = np.unique(cell_map)
    remap = np.full(len(cell_region), -1, dtype=np.int32)
    remap[used] = np.arange(len(used), dtype=np.int32)
    return remap[cell_map], [cell_region[int(value)] for value in used]


def embed(native: np.ndarray, grid: PocConfig, fill: int) -> np.ndarray:
    """Nearest-upsample an artwork-sized array into the full physical grid."""

    scale = grid.analysis_pixels // native.shape[0]
    p = grid.perimeter_pixels
    out = np.full((grid.outer_pixels, grid.outer_pixels), fill, dtype=native.dtype)
    out[p:-p, p:-p] = np.repeat(np.repeat(native, scale, axis=0), scale, axis=1)
    return out


def structural_support(
    covered: np.ndarray, perimeter: np.ndarray, kernel: np.ndarray
) -> np.ndarray:
    """Material swept by the width footprint sliding in from the perimeter.

    On a raster, eroding the dilated core can yield stray core pixels that touch
    the anchored core only diagonally; the 4-connected validator would count
    them as orphan cores.  Each stray is joined through a 4-neighbor whose
    footprint fits in ``covered`` by adding that footprint (covered material
    only, so the composite never changes).
    """

    covered_core = cv2.erode(covered.astype(np.uint8), kernel).astype(bool)
    anchored = _anchored(covered_core, perimeter)
    support = cv2.dilate(anchored.astype(np.uint8), kernel).astype(bool) & covered
    for _ in range(8):
        core = cv2.erode(support.astype(np.uint8), kernel).astype(bool)
        orphan = core & ~_anchored(core, perimeter)
        if not orphan.any():
            break
        bridge = (
            cv2.dilate(orphan.astype(np.uint8), _CROSS).astype(bool) & covered_core & ~core
        )
        if not bridge.any():
            break
        support |= cv2.dilate(bridge.astype(np.uint8), kernel).astype(bool)
    # Where no bridge fits, cut the anchored core next to each orphan so the
    # material it implied is dropped (hidden) or reported unsupported (visible).
    for attempt in range(16):
        core = cv2.erode(support.astype(np.uint8), kernel).astype(bool)
        orphan = core & ~_anchored(core, perimeter)
        if not orphan.any():
            break
        size = 5 + 4 * attempt  # grow the cut if the same orphan persists
        cut = cv2.dilate(orphan.astype(np.uint8), np.ones((size, size), np.uint8)).astype(bool)
        anchored = _anchored(anchored & ~cut, perimeter)
        support = cv2.dilate(anchored.astype(np.uint8), kernel).astype(bool) & covered
    return support


def _orphan_cores(material: np.ndarray, perimeter: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    core = cv2.erode(material.astype(np.uint8), kernel).astype(bool)
    return core & ~_anchored(core, perimeter)


def _anchored(core: np.ndarray, perimeter: np.ndarray) -> np.ndarray:
    count, components = cv2.connectedComponents(core.astype(np.uint8), connectivity=4)
    keep = np.zeros(count, dtype=bool)
    keep[np.unique(components[perimeter & core])] = True
    keep[0] = False
    return keep[components]


_CROSS = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))


def _geodesic_reach(seed: np.ndarray, domain: np.ndarray, steps: int) -> np.ndarray:
    reach = seed & domain
    for _ in range(steps):
        grown = cv2.dilate(reach.astype(np.uint8), _CROSS).astype(bool) & domain
        if np.array_equal(grown, reach):
            break
        reach = grown
    return reach


def refine_cells(
    native_cells: np.ndarray, cell_region: np.ndarray, targets: np.ndarray, tile: int, min_pixels: int
) -> tuple[np.ndarray, np.ndarray]:
    """Re-cut the cells in ``targets`` by a finer square tile grid.

    Only the listed cells change; every other cell keeps its pixels.  Each new
    piece keeps its parent's source region, and pieces smaller than
    ``min_pixels`` merge into the largest adjacent piece of the *same parent*
    (never into an unrefined neighbor, which would undo the refinement).
    Returns the compacted cell map and per-cell source region.
    """

    cells = native_cells.copy()
    regions = list(int(r) for r in cell_region)
    boxes = ndimage.find_objects(native_cells + 1)
    for parent in np.flatnonzero(targets):
        box = boxes[parent]
        if box is None:
            continue
        window = cells[box]
        mask = native_cells[box] == parent
        y0, x0 = box[0].start, box[1].start
        pieces: list[int] = []
        rows, cols = np.nonzero(mask)
        for ty in np.unique((rows + y0) // tile):
            for tx in np.unique((cols + x0) // tile):
                ys = slice(max(ty * tile - y0, 0), max((ty + 1) * tile - y0, 0))
                xs = slice(max(tx * tile - x0, 0), max((tx + 1) * tile - x0, 0))
                components, count = ndimage.label(mask[ys, xs])
                for index in range(1, count + 1):
                    piece = int(parent) if not pieces else len(regions)
                    if pieces:
                        regions.append(regions[parent])
                    window[ys, xs][components == index] = piece
                    pieces.append(piece)
        changed = True
        while changed and len(pieces) > 1:
            changed = False
            for piece in list(pieces):
                own = window == piece
                if 0 < own.sum() < min_pixels:
                    ring = ndimage.binary_dilation(own, np.ones((3, 3), bool)) & ~own
                    near = [int(v) for v in np.unique(window[ring]) if int(v) in pieces and int(v) != piece]
                    if near:
                        window[own] = max(near, key=lambda v: int((window == v).sum()))
                        pieces.remove(piece)
                        changed = True
    used = np.unique(cells)
    remap = np.full(len(regions), -1, dtype=np.int32)
    remap[used] = np.arange(len(used), dtype=np.int32)
    return remap[cells], np.asarray([regions[int(v)] for v in used], dtype=np.int32)


# Bounded local refinement after an infeasible closure: (tile mm, min cell mm2, neighbor rings).
# Rings are added only when the blocked cells alone do not resolve.
REFINEMENT_ROUNDS: tuple[tuple[float, float, int], ...] = ((1.2, 0.3, 0), (1.2, 0.3, 1), (0.8, 0.1, 1), (0.8, 0.1, 2))


class RevealPlanner:
    """Front-to-back sheet planner over a fixed cell decomposition."""

    def __init__(self, labels: np.ndarray, config: RevealConfig = RevealConfig(),
                 cells: tuple[np.ndarray, np.ndarray] | None = None):
        self.config = config
        self.labels = labels
        self.grid = physical_grid(config)
        self.kernel, self.width_parameters = width_kernel(
            self.grid, margin_pixels=config.planning_margin_pixels
        )
        _, self.perimeter = _outer_and_perimeter(self.grid)
        if cells is None:
            native_cells, self.cell_color, self.cell_region = build_cells(labels, config)
        else:  # a refined decomposition (``refine_cells``); colors follow from the labels
            native_cells, self.cell_region = cells
            self.cell_color = np.zeros(int(native_cells.max()) + 1, dtype=np.int32)
            self.cell_color[native_cells.ravel()] = labels.ravel()
        self.native_cells = native_cells
        self.cell_map = embed(native_cells, self.grid, -1)
        self.cell_count = len(self.cell_color)
        self.cell_area = np.bincount(native_cells.ravel(), minlength=self.cell_count)
        self.nub_steps = max(
            1, int(round(config.nub_tolerance_mm / self.width_parameters["mm_per_pixel"]))
        )
        self.start_covered = (
            self.cell_color == config.frame_label
            if config.frame_label is not None
            else np.zeros(self.cell_count, dtype=bool)
        )
        self.perimeter = self.perimeter | self.cells_mask(self.start_covered)
        self.colors = sorted(int(v) for v in np.unique(self.cell_color[~self.start_covered]))

    def cells_mask(self, cells: np.ndarray) -> np.ndarray:
        lookup = np.zeros(self.cell_count + 1, dtype=bool)
        lookup[np.flatnonzero(cells)] = True
        # cell_map uses -1 for the perimeter; index -1 hits the trailing False.
        return lookup[self.cell_map]

    def covered_mask(self, covered_cells: np.ndarray) -> np.ndarray:
        return self.perimeter | self.cells_mask(covered_cells)

    def unsupported_pixels(
        self, covered_cells: np.ndarray, reveal: np.ndarray
    ) -> np.ndarray:
        """Visible pixels of ``reveal`` that the sheet cannot support."""

        visible = self.cells_mask(reveal)
        covered = self.covered_mask(covered_cells) | visible
        support = structural_support(covered, self.perimeter, self.kernel)
        reach = _geodesic_reach(support, support | visible, self.nub_steps)
        bad = visible & ~reach
        if not bad.any():
            # Nubs can rebuild a sub-width band the support had cut; check
            # the finished sheet exactly as the validator will.
            orphan = _orphan_cores(support | visible, self.perimeter, self.kernel)
            bad = visible & cv2.dilate(orphan.astype(np.uint8), self.kernel).astype(bool)
        return bad

    def maximal_reveal(
        self, covered_cells: np.ndarray, color: int
    ) -> tuple[np.ndarray, int]:
        """Greatest fixpoint of supportable uncovered cells of one color."""

        candidate = (~covered_cells) & (self.cell_color == color)
        iterations = 0
        while candidate.any():
            iterations += 1
            bad = self.unsupported_pixels(covered_cells, candidate)
            if not bad.any():
                break
            bad_cells = np.unique(self.cell_map[bad])
            candidate[bad_cells[bad_cells >= 0]] = False
        return candidate, iterations

    def eliminate_sheets(
        self, colors: list[int], reveals: list[np.ndarray]
    ) -> tuple[list[int], list[np.ndarray], list[dict[str, Any]]]:
        """Delete sheets by moving their cells to a later sheet of the same color.

        Every sheet between the two loses those cells from its covered set, so
        each one is re-checked; a move is kept only if all of them stay valid.
        """

        colors, reveals = list(colors), [r.copy() for r in reveals]
        log: list[dict[str, Any]] = []
        changed = True
        while changed:
            changed = False
            order = sorted(range(len(reveals)), key=lambda j: int(self.cell_area[reveals[j]].sum()))
            for source in order:
                for target in range(source + 1, len(reveals)):
                    if colors[target] != colors[source]:
                        continue
                    trial = [r.copy() for r in reveals]
                    trial[target] |= trial[source]
                    trial[source][:] = False
                    covered = self.start_covered.copy()
                    valid = True
                    for depth in range(len(trial)):
                        if depth >= source and trial[depth].any():
                            if self.unsupported_pixels(covered, trial[depth]).any():
                                valid = False
                                break
                        covered |= trial[depth]
                    if valid:
                        log.append({"removed_front_to_back_depth": source, "merged_into": target,
                                    "color": colors[source], "cells_moved": int(reveals[source].sum())})
                        del trial[source]
                        del colors[source]
                        reveals = trial
                        changed = True
                        break
                if changed:
                    break
        return colors, reveals, log

    def sheet_mask(self, covered_cells: np.ndarray, visible_cells: np.ndarray) -> np.ndarray:
        covered = self.covered_mask(covered_cells | visible_cells)
        visible = self.cells_mask(visible_cells)
        return structural_support(covered, self.perimeter, self.kernel) | visible

    def closure(self) -> dict[str, Any]:
        """Order-independent feasibility decision (round-robin maximal reveals)."""

        covered = self.start_covered.copy()
        sequence: list[int] = []
        progress = True
        while progress and not covered.all():
            progress = False
            for color in self.colors:
                reveal, _ = self.maximal_reveal(covered, color)
                if reveal.any():
                    covered |= reveal
                    sequence.append(color)
                    progress = True
        return {
            "feasible": bool(covered.all()),
            "round_robin_sheet_count": len(sequence),
            "obstruction_cells": np.flatnonzero(~covered).tolist(),
            "covered_cells": covered,
        }

    def refined(self, targets: np.ndarray, tile_mm: float, min_cell_mm2: float) -> "RevealPlanner":
        """A planner whose ``targets`` cells are cut finer; physics and every other cell unchanged."""

        mm_per_pixel = (self.config.outer_sheet_mm * DEFAULT_CONFIG.analysis_pixels / DEFAULT_CONFIG.outer_pixels
                        / self.native_cells.shape[1])
        tile = max(2, int(round(tile_mm / mm_per_pixel)))
        min_pixels = max(1, int(round(min_cell_mm2 / mm_per_pixel**2)))
        cells = refine_cells(self.native_cells, self.cell_region, targets, tile, min_pixels)
        return RevealPlanner(self.labels, self.config, cells)

    def neighbor_cells(self, cells: np.ndarray, rings: int) -> np.ndarray:
        """``cells`` plus the cells within ``rings`` adjacency steps."""

        out = cells.copy()
        for _ in range(rings):
            mask = cv2.dilate(out[self.native_cells].astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
            out[np.unique(self.native_cells[mask])] = True
        return out

    def closure_with_refinement(self, extra_rings: int = 0) -> tuple["RevealPlanner", dict[str, Any], list[dict[str, Any]]]:
        """``closure``; if infeasible, re-cut only the blocked cells (then their neighbors) finer.

        The physical rules (width footprint, perimeter anchoring, nub tolerance)
        are not touched: refinement only lets one source region take more than
        one depth where a coarse cell mixes a wide part that must lie in front of
        a neighbor with a sub-width sliver that must lie behind it.  Rounds are
        bounded (``REFINEMENT_ROUNDS``); a feasible first closure is returned
        unchanged, so images that already plan keep their cells.

        ``extra_rings`` (only used when refinement was needed) widens the refined
        area around the originally blocked cells by that many neighbor rings
        (-1: every cell) at the first level, to compare sheet count and seams of
        a wider refinement; if that closure fails the minimal result is kept.
        """

        planner, closure, log = self, self.closure(), []
        first_blocked = np.zeros(self.cell_count, dtype=bool)
        first_blocked[closure["obstruction_cells"]] = True
        for tile_mm, min_cell_mm2, rings in REFINEMENT_ROUNDS:
            if closure["feasible"]:
                break
            started = time.perf_counter()
            stuck = np.zeros(planner.cell_count, dtype=bool)
            stuck[closure["obstruction_cells"]] = True
            targets = planner.neighbor_cells(stuck, rings)
            entry = {
                "tile_mm": tile_mm, "min_cell_mm2": min_cell_mm2, "neighbor_rings": rings,
                "blocked_cells_before": int(stuck.sum()), "blocked_pixels_before": int(stuck[planner.native_cells].sum()),
                "cells_refined": int(targets.sum()), "pixels_refined": int(targets[planner.native_cells].sum()),
            }
            planner = planner.refined(targets, tile_mm, min_cell_mm2)
            closure = planner.closure()
            entry.update(cells_after=planner.cell_count, feasible_after=closure["feasible"],
                         blocked_cells_after=len(closure["obstruction_cells"]),
                         seconds=round(time.perf_counter() - started, 1))
            log.append(entry)
        if log and closure["feasible"] and extra_rings:
            started = time.perf_counter()
            tile_mm, min_cell_mm2, _ = REFINEMENT_ROUNDS[0]
            targets = (np.ones(self.cell_count, dtype=bool) if extra_rings < 0
                       else self.neighbor_cells(first_blocked, extra_rings))
            wider = self.refined(targets, tile_mm, min_cell_mm2)
            wider_closure = wider.closure()
            log.append({"tile_mm": tile_mm, "min_cell_mm2": min_cell_mm2,
                        "neighbor_rings": "all" if extra_rings < 0 else extra_rings, "widened": True,
                        "cells_refined": int(targets.sum()), "pixels_refined": int(targets[self.native_cells].sum()),
                        "cells_after": wider.cell_count, "feasible_after": wider_closure["feasible"],
                        "used": wider_closure["feasible"], "seconds": round(time.perf_counter() - started, 1)})
            if wider_closure["feasible"]:
                planner, closure = wider, wider_closure
        return planner, closure, log

    def beam_search(self) -> dict[str, Any]:
        """Shortest color sequence of maximal reveals, bounded beam."""

        start = self.start_covered.copy()
        beam: list[tuple[np.ndarray, list[int], list[np.ndarray]]] = [(start, [], [])]
        evaluations = 0
        for _ in range(self.config.max_sheets):
            expanded: dict[bytes, tuple[np.ndarray, list[int], list[np.ndarray]]] = {}
            for covered, colors, reveals in beam:
                for color in self.colors:
                    if colors and colors[-1] == color:
                        continue  # a repeat would have been revealed already
                    reveal, _ = self.maximal_reveal(covered, color)
                    evaluations += 1
                    if not reveal.any():
                        continue
                    nxt = covered | reveal
                    key = np.packbits(nxt).tobytes()
                    if key not in expanded:
                        expanded[key] = (nxt, colors + [color], reveals + [reveal])
            if not expanded:
                break
            complete = [state for state in expanded.values() if state[0].all()]
            if complete:
                covered, colors, reveals = complete[0]
                return {
                    "status": "COMPLETE",
                    "front_to_back_colors": colors,
                    "reveals": reveals,
                    "evaluations": evaluations,
                }
            beam = sorted(
                expanded.values(),
                key=self._beam_key,
            )[: self.config.beam_width]
        best = beam[0]
        return {
            "status": "INCOMPLETE",
            "front_to_back_colors": best[1],
            "reveals": best[2],
            "evaluations": evaluations,
            "uncovered_cells": np.flatnonzero(~best[0]).tolist(),
        }

    def _beam_key(self, state: tuple) -> tuple[int, int]:
        area = -int(self.cell_area[state[0]].sum())
        unfinished = len({int(c) for c in self.cell_color[~state[0]]})
        return (unfinished, area) if self.config.beam_score == "colors" else (area, unfinished)

    def materialize(self, reveals: list[np.ndarray]) -> list[dict[str, Any]]:
        """Physical sheet masks, front to back, from the reveal sequence."""

        covered = self.start_covered.copy()
        sheets = []
        for reveal in reveals:
            physical = self.sheet_mask(covered, reveal)
            sheets.append(
                {
                    "physical_mask": physical,
                    "visible_mask": self.cells_mask(reveal),
                    "visible_cells": reveal.copy(),
                }
            )
            covered |= reveal
        return sheets

    def seam_report(self, reveals: list[np.ndarray]) -> dict[str, Any]:
        """Same-region cell boundaries whose two sides sit on different sheets."""

        depth = np.full(self.cell_count, -1, dtype=np.int32)
        for index, reveal in enumerate(reveals):
            depth[reveal] = index
        cells = self.native_cells
        seam_pixels = 0
        split_regions: set[int] = set()
        for a, b in ((cells[:, :-1], cells[:, 1:]), (cells[:-1, :], cells[1:, :])):
            different = (a != b) & (self.cell_region[a] == self.cell_region[b])
            steps = different & (depth[a] != depth[b])
            seam_pixels += int(steps.sum())
            split_regions.update(int(r) for r in np.unique(self.cell_region[a][steps]))
        regions = len(np.unique(self.cell_region))
        return {
            "seam_edge_length_native_pixels": seam_pixels,
            "seam_edge_length_mm": seam_pixels * self.width_parameters["mm_per_pixel"] * self.grid.analysis_pixels / cells.shape[0],
            "source_regions": regions,
            "source_regions_split_across_sheets": len(split_regions),
            "split_region_ids": sorted(split_regions),
        }
