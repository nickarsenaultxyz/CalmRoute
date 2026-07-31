# Sensitivity of the LTS results to parameter choices

Ruleset 2.0.0 (digest `d7ac61d027de`).
Regenerate with `make sensitivity`.

Several thresholds in this model are judgement calls, not measurements.
This table shows what each one is worth, so a reader can disagree with a
choice and see immediately how much it would change.

| variant | low-stress mi | islands | largest island | share | LTS<=3 mi | segments changed |
|---|--:|--:|--:|--:|--:|--:|
| `baseline` | 953.5 | 928 | 178.7 mi | 18.7% | 1250.5 | 0 |
| `mixed_35mph_as_lts2` | 1136.5 | 502 | 670.5 mi | 59.0% | 1250.5 | 1596 |
| `aadt_break_2000` | 949.1 | 949 | 175.8 mi | 18.5% | 1250.5 | 54 |
| `aadt_break_5000` | 1003.0 | 744 | 235.5 mi | 23.5% | 1250.5 | 624 |
| `conflation_buffer_20m` | 954.1 | 930 | 178.8 mi | 18.7% | 1250.5 | 9 |
| `connector_15m` | 953.5 | 941 | 177.5 mi | 18.6% | 1250.5 | 268 |
| `connector_40m` | 953.5 | 902 | 188.6 mi | 19.8% | 1250.5 | 288 |

## What each variant tests

- **`mixed_35mph_as_lts2`** — Dominant parameter: tests whether unprotected 35 mph streets are LTS 2 or LTS 3.
- **`aadt_break_2000`** — 995 imputed RDCLASS-5 segments at 25 mph hinge on this threshold.
- **`aadt_break_5000`** — Upper bound on the same question.
- **`conflation_buffer_20m`** — Upper bound on conflation aggressiveness.
- **`connector_15m`** — Lower bound on off-road trail attachment reach.
- **`connector_40m`** — Tests whether trail connectivity is an artifact of the radius.

## Variants that could not be built

These are results too: the parameter is bounded by a build-time quality
gate, so the sweep could not explore that direction.

- **`conflation_buffer_8m`** — Lower bound on the ordinary geometric buffer; the recall gate must still pass.
  - blocked by: ConflationError: conflation quality gate failed: recall 85.1% is below the 90% floor — real facilities are being dropped; try raising conflation.buffer_m or lowering conflation.min_coverage

## How this feeds the map

2577 segments change LTS under at least one variant. Those are
marked low-confidence in the published data, so the map can distinguish a
rating that is robust from one that rests on a contested threshold.

## The honest headline

The island count is a methodology choice, not a measurement — it moves by a
factor of three across defensible variants below. The pairing that survives
the whole sweep is the one worth quoting: the network is close to whole for
confident riders and shattered for everyone else.
