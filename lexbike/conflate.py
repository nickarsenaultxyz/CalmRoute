"""Transfer bike facility attributes onto street centrelines.

The centreline layer is the one canonical network. On-road facilities become
centreline *attributes*; only off-road paths keep their own geometry, attached
to the street network by explicit, visible connector edges.

Not yet implemented — see task #3.
"""

from __future__ import annotations
