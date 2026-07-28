# Sensitivity of the LTS results to parameter choices

Ruleset 2.0.0 (digest `1612b0859fe3`).
Regenerate with `make sensitivity`.

Several thresholds in this model are judgement calls, not measurements.
This table shows what each one is worth, so a reader can disagree with a
choice and see immediately how much it would change.

| variant | low-stress mi | islands | largest island | share | LTS<=3 mi | segments changed |
|---|--:|--:|--:|--:|--:|--:|
| `baseline` | 922.1 | 986 | 111.0 mi | 12.0% | 1223.6 | 0 |
| `mixed_35mph_as_lts2` | 1109.4 | 536 | 586.9 mi | 52.9% | 1223.6 | 1591 |
| `aadt_break_2000` | 917.7 | 1007 | 108.2 mi | 11.8% | 1223.6 | 54 |
| `aadt_break_5000` | 1004.3 | 652 | 178.2 mi | 17.7% | 1223.6 | 999 |
| `conflation_buffer_20m` | 924.5 | 987 | 111.2 mi | 12.0% | 1224.7 | 33 |
| `connector_15m` | 921.7 | 985 | 111.0 mi | 12.0% | 1223.2 | 64 |
| `connector_40m` | 922.7 | 986 | 111.1 mi | 12.0% | 1224.1 | 71 |

## What each variant tests

- **`mixed_35mph_as_lts2`** — Dominant parameter. Measured: largest island 52.9% vs 12.0%, 1,591 segments changed.
- **`aadt_break_2000`** — 995 imputed RDCLASS-5 segments at 25 mph hinge on this threshold.
- **`aadt_break_5000`** — Upper bound on the same question.
- **`conflation_buffer_20m`** — Upper bound on conflation aggressiveness.
- **`connector_15m`** — Off-road trail attachment. Measured: barely matters, 12.0% either way.
- **`connector_40m`** — Tests whether trail connectivity is an artifact of the radius.

## Variants that could not be built

These are results too: the parameter is bounded by a build-time quality
gate, so the sweep could not explore that direction.

- **`conflation_buffer_8m`** — Lower bound on the buffer. Measured: fails the 90% recall gate at 78.9%.
  - blocked by: ConflationError: conflation quality gate failed: recall 78.9% is below the 90% floor — real facilities are being dropped; try raising conflation.buffer_m or lowering conflation.min_coverage

## How this feeds the map

2713 segments change LTS under at least one variant. Those are
marked low-confidence in the published data, so the map can distinguish a
rating that is robust from one that rests on a contested threshold.

## The honest headline

The island count is a methodology choice, not a measurement — it moves by a
factor of three across defensible variants below. The pairing that survives
the whole sweep is the one worth quoting: the network is close to whole for
confident riders and shattered for everyone else.
