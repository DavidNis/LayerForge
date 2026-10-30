"""Deterministic memory handling for tests of the upscale resource check.

The application refuses work whose estimated peak memory exceeds a share of the memory available
*now* (``core/upscale.resource_check``); that check is never weakened here.  Tests separate two things:

* the decision logic: the available-memory probe is replaced by a fixed value (``fixed_memory``), so a
  test of "fits" or of "refused" gives the same answer on every machine and under any load;
* the real work a test then performs (intake or planning at a finer grid) still needs real memory:
  ``require_free_memory`` skips such a test with a ``RESOURCE:`` reason when the machine has too little
  free memory right now.  A resource skip is reported as a skip with that reason, never as a pass or a
  failure; every other problem fails normally.
"""

from __future__ import annotations

import pytest

from layerforge_l1.core import upscale

GIB = 2**30
PLENTY = 64 * GIB
# Real peak working set measured 2026-10-30 for the eyes fixture previews (150 mm, from scale 1):
# 2x 0.20 GB, 4x 0.55 GB.  The requirement keeps a margin above the measurement.
PREVIEW_INTAKE_GIB = {2: 0.5, 4: 1.0}


def fixed_memory(monkeypatch: pytest.MonkeyPatch, available_bytes: int) -> None:
    """Make the application's available-memory probe report ``available_bytes``."""

    monkeypatch.setattr(upscale, "available_memory_bytes", lambda: available_bytes)


def real_free_gib() -> float | None:
    free = upscale.available_memory_bytes()
    return None if free is None else free / GIB


def require_free_memory(gib: float, what: str) -> None:
    """Skip (reason ``RESOURCE: ...``) when less than ``gib`` GB is free for the real work of a test."""

    free = real_free_gib()
    if free is not None and free < gib:
        pytest.skip(f"RESOURCE: {what} needs about {gib:g} GB of free memory; {free:.2f} GB is free now "
                    "(resource skip, not a test result)")


def require_memory_for_planning(outer_mm: float, scale: int, what: str) -> None:
    """Skip unless the application's own check would let a real run at ``scale`` start now."""

    from layerforge_l1.core.model import StackGrid

    need = upscale.estimate_peak_bytes(StackGrid.for_planner(outer_mm, scale)) / upscale.MEMORY_HEADROOM / GIB
    require_free_memory(need, what)
