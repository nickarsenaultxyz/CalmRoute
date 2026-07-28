"""Level of Traffic Stress classification — pure functions only.

Deliberately holds no I/O and no pandas: every function takes scalars and
returns scalars, so the golden-corridor tests call them directly without
fixtures. This module replaces the old ``compute_lts`` and
``compute_residential_lts``.

Scale is 0-4:
    0  bikes legally prohibited (Interstate / Parkway)
    1  Relaxed
    2  Comfortable for most adults
    3  Busy
    4  Stressful

There is no LTS 5. Furth/Mekuria define 1-4; the old code's LTS 5 conflated
"illegal to ride" with "legal but unpleasant", which are different facts a
rider needs to distinguish.

Missing-data policy, stated once and applied uniformly:

    An unknown value never improves a rating and never silently ruins one.

The old code got this backwards in both directions inside a single function:
every AADT test read ``(not aadt_known or aadt <= X)``, so an unknown volume
*passed* the gate, while every branch was gated on ``speed_known``, so an
unknown speed *failed* every test and fell through to LTS 4 — which is how 62
striped bike lanes were rated high-stress. Here, volume is always resolved
before classification (measured or imputed, with provenance recorded), and an
unknown speed is treated as the class-typical speed rather than as a defect.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PROHIBITED = 0
MIN_LTS = 1
MAX_LTS = 4

#: Facility categories the mixed-traffic table applies to. A sharrow is paint
#: on a shared lane; the literature treats it as equivalent to no facility.
MIXED_FACILITIES = frozenset({"none", "sharrow"})

#: Speeds we snap to when a source value is off-scale. Applied *before*
#: classification, and never across a threshold in the unfavourable direction —
#: see :func:`round_to_posted_speed`.
COMMON_SPEEDS = (10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 70)


class RulesetError(Exception):
    """The params tree is missing something the classifier needs."""


@dataclass(frozen=True)
class Ruleset:
    """Thresholds resolved once, so the hot loop does no dotted lookups.

    Construct via :meth:`from_params`. Tests may build one directly to probe a
    single threshold without editing ``params.toml``.
    """

    prohibited_rdclass: frozenset[int]
    low_stress_max: int
    facility_rank: dict[str, int]

    # mixed traffic
    mixed_aadt_low: float
    mixed_aadt_mid: float
    mixed_25_low_lts: int
    mixed_25_mid_lts: int
    mixed_25_else_lts: int
    mixed_30_mid_lts: int
    mixed_30_else_lts: int
    mixed_35_lts: int
    mixed_40_plus_lts: int
    mixed_lanes_4_5_speed_30_lts: int
    mixed_lanes_4_5_else_lts: int
    mixed_lanes_6_plus_lts: int

    path_lts: int
    protected_lts: int
    connector_lts: int

    buffered_30_lanes2_lts: int
    buffered_35_lts: int
    buffered_else_lts: int

    lane_30_lts: int
    lane_35_lts: int
    lane_else_lts: int
    lane_wide_penalty: int
    lane_wide_lanes: int

    shoulder_35_lts: int
    shoulder_else_lts: int

    lane_rules: tuple[dict[str, Any], ...]
    lane_default: int

    @classmethod
    def from_params(cls, params: Any) -> "Ruleset":
        try:
            return cls(
                prohibited_rdclass=frozenset(int(x) for x in params["rdclass.prohibited"]),
                low_stress_max=int(params["lts.low_stress_max"]),
                facility_rank=dict(params["facility.rank"]),
                mixed_aadt_low=float(params["lts.mixed.aadt_break_low"]),
                mixed_aadt_mid=float(params["lts.mixed.aadt_break_mid"]),
                mixed_25_low_lts=int(params["lts.mixed.speed_25_aadt_750_lts"]),
                mixed_25_mid_lts=int(params["lts.mixed.speed_25_aadt_3000_lts"]),
                mixed_25_else_lts=int(params["lts.mixed.speed_25_else_lts"]),
                mixed_30_mid_lts=int(params["lts.mixed.speed_30_aadt_3000_lts"]),
                mixed_30_else_lts=int(params["lts.mixed.speed_30_else_lts"]),
                mixed_35_lts=int(params["lts.mixed.speed_35_lts"]),
                mixed_40_plus_lts=int(params["lts.mixed.speed_40_plus_lts"]),
                mixed_lanes_4_5_speed_30_lts=int(params["lts.mixed.lanes_4_5_speed_30_lts"]),
                mixed_lanes_4_5_else_lts=int(params["lts.mixed.lanes_4_5_else_lts"]),
                mixed_lanes_6_plus_lts=int(params["lts.mixed.lanes_6_plus_lts"]),
                path_lts=int(params["lts.path.lts"]),
                protected_lts=int(params["lts.protected.lts"]),
                connector_lts=int(params["lts.connector.lts"]),
                buffered_30_lanes2_lts=int(params["lts.buffered.speed_30_lanes_2_lts"]),
                buffered_35_lts=int(params["lts.buffered.speed_35_lts"]),
                buffered_else_lts=int(params["lts.buffered.else_lts"]),
                lane_30_lts=int(params["lts.lane.speed_30_lts"]),
                lane_35_lts=int(params["lts.lane.speed_35_lts"]),
                lane_else_lts=int(params["lts.lane.else_lts"]),
                lane_wide_penalty=int(params["lts.lane.wide_road_penalty"]),
                lane_wide_lanes=int(params["lts.lane.wide_road_lanes"]),
                shoulder_35_lts=int(params["lts.shoulder.speed_35_lts"]),
                shoulder_else_lts=int(params["lts.shoulder.else_lts"]),
                lane_rules=tuple(params["lanes.rules"]),
                lane_default=int(params["lanes.default"]),
            )
        except KeyError as exc:
            raise RulesetError(f"params.toml is missing {exc}") from exc


# ---------------------------------------------------------------------------
#  Inputs
# ---------------------------------------------------------------------------

def round_to_posted_speed(value: float | None) -> float | None:
    """Snap an off-scale speed to the nearest plausible posted value.

    Ties round *down*, toward the lower-stress outcome. The old code snapped to
    the nearest value unconditionally, so a median of 27.5 became 30 and pushed
    segments across the 25 mph LTS-2 threshold in the unfavourable direction.
    Averaging speeds across a whole road is no longer done at all — speed comes
    from the specific centreline — but the guard is cheap and documents intent.
    """
    if value is None:
        return None
    v = float(value)
    if v <= 0:
        return None
    best = min(COMMON_SPEEDS, key=lambda c: (abs(c - v), c))
    return float(best)


def lane_surrogate(
    rdclass: int,
    directional: bool,
    cartoclass: int | None,
    rules: tuple[dict[str, Any], ...],
    default: int,
) -> int:
    """Estimated through-lane count.

    No lane count exists in any source, so this is an explicit surrogate,
    shipped to the browser as a property (``ln``) so a reader can audit it.

    The subtlety it encodes: ``ONEWAY != 'B'`` means opposite things by class.
    On arterials it is 817 of 1,091 segments — divided roads digitized as two
    centrelines, i.e. *more* lanes. On local streets it is 810 of 11,420 —
    genuine downtown one-ways, i.e. *fewer* lanes.

    First matching rule wins, so order in ``params.toml`` is significant:
    narrower conditions must precede broader ones.
    """
    want = "directional" if directional else "both"
    for rule in rules:
        if rdclass not in rule["rdclass"]:
            continue
        if rule.get("oneway") not in (None, want):
            continue
        allowed = rule.get("cartoclass")
        if allowed is not None and (cartoclass is None or cartoclass not in allowed):
            continue
        return int(rule["lanes"])
    return default


def resolve_facility(categories: list[str], rank: dict[str, int]) -> str:
    """Most protective facility among those conflated onto one centreline.

    Only ``Type_Facility`` feeds this — ``AltType_Facility`` is a recommended
    upgrade rather than infrastructure that exists. See the ``[facility]``
    comment in ``params.toml``.
    """
    if not categories:
        return "none"
    unknown = [c for c in categories if c not in rank]
    if unknown:
        raise RulesetError(f"facility category has no rank: {sorted(set(unknown))}")
    return max(categories, key=lambda c: rank[c])


# ---------------------------------------------------------------------------
#  Classification
# ---------------------------------------------------------------------------

def _mixed_lts(rules: Ruleset, lanes: int, speed: float, aadt: float) -> int:
    """Mixed traffic: no facility, or sharrows.

    This is the table that decides the shape of the whole low-stress network,
    because ~9,000 of 13,775 segments have no bike facility at all.
    """
    if lanes >= 6:
        return rules.mixed_lanes_6_plus_lts
    if lanes >= 4:
        return (
            rules.mixed_lanes_4_5_speed_30_lts
            if speed <= 30
            else rules.mixed_lanes_4_5_else_lts
        )

    if speed <= 25:
        # LTS 1 additionally requires a genuinely narrow street: a 25 mph road
        # wide enough for three through lanes is not a quiet residential street.
        if aadt <= rules.mixed_aadt_low and lanes <= 2:
            return rules.mixed_25_low_lts
        if aadt <= rules.mixed_aadt_mid:
            return rules.mixed_25_mid_lts
        return rules.mixed_25_else_lts
    if speed <= 30:
        return rules.mixed_30_mid_lts if aadt <= rules.mixed_aadt_mid else rules.mixed_30_else_lts
    if speed <= 35:
        # The single highest-leverage parameter in the model: 1,104 minor
        # collectors are posted at 35 mph and they are the stitching of the
        # low-stress grid. Locked to 3 per Mekuria/Furth Table 8; the
        # alternative is published in docs/sensitivity.md rather than buried.
        return rules.mixed_35_lts
    return rules.mixed_40_plus_lts


def lts_for_segment(
    fac: str,
    rdclass: int,
    lanes: int,
    speed: float | None,
    aadt: float | None,
    rules: Ruleset,
) -> int:
    """Classify one segment. Returns 0 (prohibited) or 1-4.

    ``speed`` and ``aadt`` may be ``None``; both are then treated as the
    class-typical case rather than as a defect. Callers should normally resolve
    volume beforehand (measured or imputed) and record provenance so the map can
    say which it was.
    """
    if rdclass in rules.prohibited_rdclass:
        return PROHIBITED

    if fac not in rules.facility_rank:
        raise RulesetError(
            f"unknown facility category {fac!r}; "
            f"expected one of {sorted(rules.facility_rank)}"
        )

    # A shared-use path or a protected lane is off the traffic stream, so
    # neither speed nor volume applies.
    if fac == "path":
        return rules.path_lts
    if fac == "protected":
        return rules.protected_lts
    if fac == "connector":
        return rules.connector_lts

    # 25 mph is the modal posted speed on Lexington's local streets and the
    # statutory urban default; using it for an unknown is the class-typical
    # assumption, not an optimistic one.
    sp = 25.0 if speed is None else float(speed)
    # An unresolved volume falls at the middle break rather than below the
    # lowest, so an unknown can reach LTS 2 but never LTS 1.
    ad = rules.mixed_aadt_mid if aadt is None else float(aadt)

    mixed = _mixed_lts(rules, lanes, sp, ad)
    if fac in MIXED_FACILITIES:
        return mixed

    if fac == "buffered":
        if sp <= 30 and lanes <= 2:
            score = rules.buffered_30_lanes2_lts
        elif sp <= 35:
            score = rules.buffered_35_lts
        else:
            score = rules.buffered_else_lts

    elif fac == "lane":
        if sp <= 30:
            score = rules.lane_30_lts
        elif sp <= 35:
            score = rules.lane_35_lts
        else:
            score = rules.lane_else_lts
        # A bike lane beside four or more through lanes is a worse place to ride
        # than the same stripe on a two-lane street: more conflict points, more
        # turning movements, higher effective exposure.
        if lanes >= rules.lane_wide_lanes:
            score = min(MAX_LTS, score + rules.lane_wide_penalty)

    elif fac == "shoulder":
        score = rules.shoulder_35_lts if sp <= 35 else rules.shoulder_else_lts

    else:
        raise RulesetError(f"no rule for facility category {fac!r}")

    # Floor an on-street facility at the rating the same segment would get with
    # no facility at all. The Furth/Mekuria bike-lane and shoulder tables are
    # keyed on speed and lane count only, because they were written for streets
    # that need a bike lane; applied literally to a 25 mph cul-de-sac they
    # return LTS 2 (lane) or LTS 3 (shoulder) where the bare street is LTS 1,
    # so striping would appear to *raise* stress. Adding infrastructure can help
    # or be neutral, never hurt.
    return min(score, mixed)


def is_low_stress(lts: int, rules: Ruleset) -> bool:
    """Whether a segment counts toward the connected low-stress network.

    LTS 0 is excluded: bikes are prohibited there, so it is not low-stress
    however quiet the road may be.
    """
    return MIN_LTS <= lts <= rules.low_stress_max
