"""Level of Traffic Stress classification — pure functions only.

Deliberately holds no I/O and no pandas: every function here takes scalars and
returns scalars, so the golden-corridor tests can call them directly without
fixtures. This is the module that replaces the old ``compute_lts`` and
``compute_residential_lts``.

Scale is 0-4:
    0  bikes legally prohibited (Interstate / Parkway)
    1  Relaxed
    2  Comfortable for most adults
    3  Busy
    4  Stressful

There is no LTS 5. Furth/Mekuria define 1-4; the old code's LTS 5 conflated
"illegal to ride" with "legal but unpleasant".

Not yet implemented — see task #2.
"""

from __future__ import annotations

PROHIBITED = 0
LOW_STRESS_DEFAULT = 2
