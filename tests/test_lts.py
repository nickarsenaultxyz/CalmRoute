"""Classifier unit tests.

These test the *table*, not the data — golden-corridor tests against real
segments live in test_golden_corridors.py. Several cases here exist specifically
to pin down defects in the previous implementation; those name the defect so a
future change cannot quietly reintroduce it.
"""

from __future__ import annotations

import pytest

from lexbike import params as params_mod
from lexbike.lts import (
    MAX_LTS,
    PROHIBITED,
    Ruleset,
    RulesetError,
    is_low_stress,
    lane_surrogate,
    lts_for_segment,
    resolve_facility,
    round_to_posted_speed,
)


@pytest.fixture(scope="module")
def rules() -> Ruleset:
    return Ruleset.from_params(params_mod.load())


def classify(rules, fac="none", rdclass=6, lanes=2, speed=25, aadt=500):
    return lts_for_segment(fac, rdclass, lanes, speed, aadt, rules)


# ---------------------------------------------------------------------------
#  Prohibited roads
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rdclass", [1, 2])
def test_interstates_and_parkways_are_prohibited_not_lts5(rules, rdclass):
    """The old pipeline gave these 'LTS 5', conflating illegal with unpleasant."""
    assert classify(rules, rdclass=rdclass, speed=55, lanes=6) == PROHIBITED


def test_prohibited_beats_any_facility(rules):
    """A sidepath stripe on an interstate does not make it legal to ride."""
    assert classify(rules, fac="lane", rdclass=1) == PROHIBITED


def test_arterial_without_facility_is_honest_lts4_not_a_separate_class(rules):
    assert classify(rules, rdclass=3, lanes=5, speed=45, aadt=27_575) == 4


def test_prohibited_is_not_low_stress(rules):
    """LTS 0 must not be swept into the low-stress network by a <= comparison."""
    assert not is_low_stress(PROHIBITED, rules)
    assert is_low_stress(1, rules)
    assert is_low_stress(2, rules)
    assert not is_low_stress(3, rules)


# ---------------------------------------------------------------------------
#  Off-stream facilities
# ---------------------------------------------------------------------------

def test_path_is_lts1_regardless_of_adjacent_road(rules):
    """A trail's rating must not depend on the road it happens to parallel."""
    assert classify(rules, fac="path", rdclass=3, speed=55, aadt=30_000, lanes=6) == 1


def test_connector_is_low_stress(rules):
    assert is_low_stress(classify(rules, fac="connector"), rules)


# ---------------------------------------------------------------------------
#  Residential / mixed traffic — the table that shapes the network
# ---------------------------------------------------------------------------

def test_quiet_narrow_residential_street_can_reach_lts1(rules):
    """Previously impossible: compute_residential_lts could only return 2 or 3,
    so LTS 1 meant 'shared-use path' and nothing else."""
    assert classify(rules, rdclass=6, lanes=2, speed=25, aadt=500) == 1


def test_lts1_requires_a_narrow_street(rules):
    """25 mph and quiet is not enough if the road is wide enough for 3 lanes."""
    assert classify(rules, rdclass=6, lanes=3, speed=25, aadt=500) == 2


def test_alleys_and_service_roads_are_rated_not_hardcoded(rules):
    """RDCLASS 7 and 8 previously fell through to a hardcoded `return 3`,
    which excluded 200 segments from the low-stress network by accident."""
    assert classify(rules, rdclass=8, lanes=1, speed=15, aadt=100) == 1
    assert classify(rules, rdclass=7, lanes=2, speed=25, aadt=200) == 1


@pytest.mark.parametrize(
    "speed,aadt,expected",
    [
        (25, 500, 1),
        (25, 2_000, 2),
        (25, 5_000, 3),
        (30, 2_000, 2),
        (30, 5_000, 3),
        (35, 500, 3),      # locked decision
        (35, 20_000, 3),
        (40, 500, 4),
        (45, 500, 4),
    ],
)
def test_mixed_traffic_table(rules, speed, aadt, expected):
    assert classify(rules, rdclass=5, lanes=2, speed=speed, aadt=aadt) == expected


def test_35mph_collector_is_lts3_the_locked_decision(rules):
    """1,104 minor collectors are posted at 35 mph and are the stitching of the
    low-stress grid. Faithful to Mekuria/Furth Table 8; flipping this to 2 moves
    the largest island from ~12% to ~42%, so it must not change by accident.
    The alternative is published in docs/sensitivity.md."""
    assert classify(rules, rdclass=5, lanes=2, speed=35, aadt=500) == 3
    assert not is_low_stress(classify(rules, rdclass=5, speed=35), rules)


def test_sharrow_is_treated_as_mixed_traffic(rules):
    """Paint on a shared lane confers no protection, so it must rate exactly
    as the same street with no facility."""
    for speed in (25, 30, 35, 45):
        assert classify(rules, fac="sharrow", speed=speed, aadt=2_000) == classify(
            rules, fac="none", speed=speed, aadt=2_000
        )


def test_multilane_mixed_traffic(rules):
    assert classify(rules, rdclass=3, lanes=4, speed=30, aadt=15_000) == 3
    assert classify(rules, rdclass=3, lanes=4, speed=35, aadt=15_000) == 4
    assert classify(rules, rdclass=3, lanes=6, speed=25, aadt=15_000) == 4


# ---------------------------------------------------------------------------
#  Striped facilities
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "speed,lanes,expected",
    [(30, 2, 2), (35, 2, 3), (45, 2, 4), (30, 4, 3), (35, 4, 4), (45, 4, 4)],
)
def test_bike_lane_table_with_wide_road_penalty(rules, speed, lanes, expected):
    assert classify(rules, fac="lane", rdclass=3, lanes=lanes, speed=speed, aadt=8_000) == expected


def test_wide_road_penalty_is_capped(rules):
    assert classify(rules, fac="lane", lanes=6, speed=55) == MAX_LTS


def test_buffered_lane_beats_a_plain_lane(rules):
    for speed in (30, 35, 45):
        buffered = classify(rules, fac="buffered", lanes=2, speed=speed)
        plain = classify(rules, fac="lane", lanes=2, speed=speed)
        assert buffered <= plain


def test_shoulder_is_not_a_bike_lane(rules):
    """9 road shoulders were previously rated as mixed traffic; the plan's
    original design would have promoted 7 of them to LTS 1 paths via
    AltType_Facility. Neither is right."""
    assert classify(rules, fac="shoulder", rdclass=4, speed=35) == 3
    assert classify(rules, fac="shoulder", rdclass=3, speed=45) == 4


def test_more_protection_never_raises_stress(rules):
    """Monotonicity: on identical road conditions, a better facility must never
    rate worse. Catches threshold typos that cross the tables."""
    order = ["none", "sharrow", "shoulder", "lane", "buffered", "protected", "path"]
    for rdclass, lanes, speed, aadt in [
        (6, 2, 25, 500), (5, 2, 35, 4_000), (3, 4, 45, 20_000), (4, 3, 30, 7_000),
    ]:
        scores = [
            lts_for_segment(f, rdclass, lanes, speed, aadt, rules) for f in order
        ]
        assert scores == sorted(scores, reverse=True), dict(zip(order, scores))


# ---------------------------------------------------------------------------
#  Missing data — the asymmetry the old code got backwards both ways
# ---------------------------------------------------------------------------

def test_unknown_speed_does_not_condemn_a_bike_lane(rules):
    """62 striped bike lanes were rated LTS 4 purely because no speed matched:
    every branch was gated on speed_known, so an unknown failed every test."""
    assert classify(rules, fac="lane", speed=None, aadt=5_000) == 2


def test_unknown_volume_cannot_reach_lts1(rules):
    """An unknown must never buy the best rating. The old code's
    `(not aadt_known or aadt <= X)` let an unknown volume pass every gate."""
    assert classify(rules, fac="none", speed=25, aadt=None) == 2
    assert classify(rules, fac="none", speed=25, aadt=500) == 1


def test_unknown_speed_and_volume_together(rules):
    assert classify(rules, fac="none", speed=None, aadt=None) == 2


# ---------------------------------------------------------------------------
#  Inputs
# ---------------------------------------------------------------------------

def test_speed_rounding_ties_favour_the_lower_value(rules):
    """The old round_to_common_mph turned a 27.5 median into 30, pushing
    segments across the 25 mph threshold the wrong way."""
    assert round_to_posted_speed(27.5) == 25.0
    assert round_to_posted_speed(32.5) == 30.0
    assert round_to_posted_speed(26) == 25.0
    assert round_to_posted_speed(29) == 30.0
    assert round_to_posted_speed(None) is None
    assert round_to_posted_speed(0) is None


def test_lane_surrogate_reads_oneway_by_class(rules):
    """ONEWAY != 'B' means MORE lanes on a divided arterial and FEWER on a
    downtown local street. A single global rule gets one of them wrong."""
    rl, dflt = rules.lane_rules, rules.lane_default
    assert lane_surrogate(3, True, 3, rl, dflt) == 3      # half a divided arterial
    assert lane_surrogate(3, False, 2, rl, dflt) == 5     # major undivided
    assert lane_surrogate(3, False, 3, rl, dflt) == 3
    assert lane_surrogate(6, True, 4, rl, dflt) == 1      # genuine one-way street
    assert lane_surrogate(6, False, 4, rl, dflt) == 2
    assert lane_surrogate(4, True, 3, rl, dflt) == 2
    assert lane_surrogate(4, False, 3, rl, dflt) == 3


def test_lane_surrogate_falls_back_for_unmapped_input(rules):
    assert lane_surrogate(99, False, None, rules.lane_rules, rules.lane_default) == 2
    # cartoclass is required by the arterial rule; None must not match it
    assert lane_surrogate(3, False, None, rules.lane_rules, rules.lane_default) == 3


def test_resolve_facility_takes_the_most_protective(rules):
    rank = rules.facility_rank
    assert resolve_facility(["none", "lane"], rank) == "lane"
    assert resolve_facility(["lane", "path"], rank) == "path"
    assert resolve_facility(["sharrow", "none"], rank) == "sharrow"
    assert resolve_facility([], rank) == "none"


def test_resolve_facility_rejects_an_unranked_category(rules):
    with pytest.raises(RulesetError, match="no rank"):
        resolve_facility(["bike-highway"], rules.facility_rank)


def test_unknown_facility_raises(rules):
    with pytest.raises(RulesetError, match="unknown facility"):
        classify(rules, fac="cycle-superhighway")


# ---------------------------------------------------------------------------
#  Range
# ---------------------------------------------------------------------------

def test_every_result_is_in_range(rules):
    for fac in ["none", "sharrow", "shoulder", "lane", "buffered", "protected", "path"]:
        for rdclass in [1, 2, 3, 4, 5, 6, 7, 8]:
            for lanes in [1, 2, 3, 4, 5, 6]:
                for speed in [None, 15, 25, 30, 35, 45, 55, 70]:
                    for aadt in [None, 0.0, 500, 3_000, 30_000]:
                        got = lts_for_segment(fac, rdclass, lanes, speed, aadt, rules)
                        assert got == PROHIBITED or 1 <= got <= MAX_LTS
