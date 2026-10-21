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


class RevealPlanner:
    """Front-to-back sheet planner over a fixed cell decomposition."""

    def __init__(self, labels: np.ndarray, config: RevealConfig = RevealConfig()):
        self.config = config
        self.labels = labels
        self.grid = physical_grid(config)
        self.kernel, self.width_parameters = width_kernel(
            self.grid, margin_pixels=config.planning_margin_pixels
        )
        _, self.perimeter = _outer_and_perimeter(self.grid)
        native_cells, self.cell_color, self.cell_region = build_cells(labels, config)
        self.native_cells = native_cells
        self.cell_map = embed(native_cells, self.grid, -1)
        self.cell_count = len(self.cell_color)
        self.cell_area = np.bincount(native_cells.ravel(), minlength=self.cell_count)
        self.nub_steps = max(
            1, int(round(config.nub_tolerance_mm / self.width_parameters["mm_per_pixel"]))
        )
        self.colors = sorted(int(value) for value in np.unique(self.cell_color))

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
                    covered = np.zeros(self.cell_count, dtype=bool)
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

        covered = np.zeros(self.cell_count, dtype=bool)
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

    def beam_search(self) -> dict[str, Any]:
        """Shortest color sequence of maximal reveals, bounded beam."""

        start = np.zeros(self.cell_count, dtype=bool)
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

        covered = np.zeros(self.cell_count, dtype=bool)
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
