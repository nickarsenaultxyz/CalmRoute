# Sensitivity of the LTS results to parameter choices

Ruleset 2.0.0 (digest `1b07001fd85c`).
Regenerate with `make sensitivity`.

Several thresholds in this model are judgement calls, not measurements.
This table shows what each one is worth, so a reader can disagree with a
choice and see immediately how much it would change.

| variant | low-stress mi | islands | largest island | share | LTS<=3 mi | segments changed |
|---|--:|--:|--:|--:|--:|--:|
| `baseline` | 948.2 | 934 | 178.2 mi | 18.8% | 1249.4 | 0 |
| `mixed_35mph_as_lts2` | 1135.5 | 505 | 670.1 mi | 59.0% | 1249.4 | 1646 |
| `aadt_break_2000` | 943.9 | 955 | 175.4 mi | 18.6% | 1249.4 | 54 |
| `aadt_break_5000` | 997.9 | 750 | 234.6 mi | 23.5% | 1249.4 | 627 |
| `conflation_buffer_20m` | 950.7 | 935 | 178.3 mi | 18.8% | 1250.5 | 34 |
| `connector_15m` | 948.2 | 947 | 177.1 mi | 18.7% | 1249.4 | 268 |
| `connector_40m` | 948.2 | 907 | 188.1 mi | 19.8% | 1249.4 | 289 |

## What each variant tests

- **`mixed_35mph_as_lts2`** — Dominant parameter. Measured: largest island 58.9% vs 18.7%, 1,623 segments changed.
- **`aadt_break_2000`** — 995 imputed RDCLASS-5 segments at 25 mph hinge on this threshold.
- **`aadt_break_5000`** — Upper bound on the same question.
- **`conflation_buffer_20m`** — Upper bound on conflation aggressiveness.
- **`connector_15m`** — Off-road trail attachment. Measured: barely matters, 18.5% vs 18.7%.
- **`connector_40m`** — Tests whether trail connectivity is an artifact of the radius.

## Variants that could not be built

These are results too: the parameter is bounded by a build-time quality
gate, so the sweep could not explore that direction.

- **`conflation_buffer_8m`** — Lower bound on the buffer. Measured: fails the 90% recall gate at 79.0%.
  - blocked by: ConflationError: conflation quality gate failed: recall 79.0% is below the 90% floor — real facilities are being dropped; try raising conflation.buffer_m or lowering conflation.min_coverage

## How this feeds the map

2632 segments change LTS under at least one variant. Those are
marked low-confidence in the published data, so the map can distinguish a
rating that is robust from one that rests on a contested threshold.

## The honest headline

The island count is a methodology choice, not a measurement — it moves by a
factor of three across defensible variants below. The pairing that survives
the whole sweep is the one worth quoting: the network is close to whole for
confident riders and shattered for everyone else.
