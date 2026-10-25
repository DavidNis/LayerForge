from __future__ import annotations

from layerforge_l1.connectivity_repair import _width_parameters
from layerforge_l1.layer_optimization import DEFAULT_OPTIMIZATION_CONFIG
from layerforge_l1.subtractive_poc import DEFAULT_CONFIG


# Report-reading tests for the deleted artifacts/l1_width_diagnostics_v2 tree
# were removed in L0; see tests/REMOVED_TESTS.md.


def test_physical_width_maps_to_a_centered_seven_pixel_kernel() -> None:
    parameters = _width_parameters(DEFAULT_CONFIG, DEFAULT_OPTIMIZATION_CONFIG)
    assert parameters["enforced_connection_width_mm"] == 1.2
    assert 6.9 < parameters["requested_connection_width_pixels"] < 7.0
    assert parameters["enforced_connection_width_pixels"] == 7
    assert parameters["morphology_kernel_size_pixels"] == 7
    assert parameters["morphology_kernel_radius_pixels"] == 3
