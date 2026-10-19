from __future__ import annotations

from layerforge_l1.width_aware_planner import (
    WidthSearchRecord,
    WidthStateMetrics,
    _ContinuationProfile,
    _diverse_state_rank,
    _select_diverse_beam,
)


def _record(sequence: tuple[int, ...], area: int) -> WidthSearchRecord:
    return WidthSearchRecord(
        sequence=sequence,
        groups=tuple(1 << index for index in range(len(sequence))),
        neighbour_bits=0,
        assigned_area=area,
        retained_pixel_layers=area,
        failed_prefixes=0,
        cumulative_orphan_components=0,
        cumulative_orphan_pixels=0,
        worst_prefix_orphan_components=0,
    )


def _profile(
    color_bits: int,
    candidate_bits: int,
    next_states: int = 2,
) -> _ContinuationProfile:
    return _ContinuationProfile(
        valid_next_states=next_states,
        valid_colors=color_bits.bit_count(),
        valid_color_bits=color_bits,
        candidate_region_bits=candidate_bits,
        reachable_region_bits=candidate_bits,
        largest_group_regions=max(1, candidate_bits.bit_count()),
        largest_group_area=max(1, candidate_bits.bit_count() * 10),
    )


class _CleanEvaluator:
    def metrics(self, state: int) -> WidthStateMetrics:
        return WidthStateMetrics(0, 0, 1, state.bit_count())


def test_continuation_viability_outranks_larger_dead_end_prefix() -> None:
    graph = {"color_bits": (0b0011, 0b1100)}
    evaluator = _CleanEvaluator()
    large_dead_end = _record((0,), 1000)
    smaller_extendable = _record((1,), 100)
    dead_profile = _profile(0, 0, next_states=0)
    viable_profile = _profile(0b10, 0b1100, next_states=2)

    dead_rank = _diverse_state_rank(
        0b0011,
        large_dead_end,
        dead_profile,
        1,
        4,
        graph,
        evaluator,
        0b1111,
    )
    viable_rank = _diverse_state_rank(
        0b0001,
        smaller_extendable,
        viable_profile,
        1,
        4,
        graph,
        evaluator,
        0b1111,
    )

    assert viable_rank < dead_rank


def test_diverse_beam_reserves_distinct_continuation_signatures() -> None:
    ranked = [
        (0b111100, _record((0,), 400)),
        (0b111000, _record((0,), 390)),
        (0b110000, _record((0,), 380)),
        (0b001100, _record((1,), 200)),
        (0b000011, _record((2,), 100)),
        (0b101000, _record((0,), 370)),
    ]
    profiles = {
        0b111100: _profile(0b001, 0b000011),
        0b111000: _profile(0b001, 0b000011),
        0b110000: _profile(0b001, 0b000011),
        0b001100: _profile(0b010, 0b110000),
        0b000011: _profile(0b100, 0b001100),
        0b101000: _profile(0b001, 0b000011),
    }

    kept = _select_diverse_beam(ranked, profiles, max_states=4)
    kept_states = {state for state, _ in kept}

    assert 0b111100 in kept_states
    assert 0b001100 in kept_states
    assert 0b000011 in kept_states
    assert len(kept_states) == 4
