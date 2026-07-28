"""Parameter loading.

All tunable values live in ``params.toml``. This module loads them, applies
``--set`` overrides for sensitivity runs, and computes a digest so any published
figure can be traced back to the exact ruleset that produced it.
"""

from __future__ import annotations

import copy
import hashlib
import json
import tomllib
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "params.toml"


class ParamsError(Exception):
    """Raised for a malformed params file or an unusable override."""


class Params:
    """Dotted-path read-only view over the params tree."""

    def __init__(self, tree: dict[str, Any], source: Path | None = None):
        self._tree = tree
        self.source = source

    def __getitem__(self, dotted: str) -> Any:
        node: Any = self._tree
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                raise KeyError(f"no such parameter: {dotted!r}")
            node = node[part]
        return node

    def get(self, dotted: str, default: Any = None) -> Any:
        try:
            return self[dotted]
        except KeyError:
            return default

    @property
    def tree(self) -> dict[str, Any]:
        """A deep copy, so callers cannot mutate the loaded params."""
        return copy.deepcopy(self._tree)

    @property
    def digest(self) -> str:
        """Stable 12-char hash of the effective ruleset.

        Sort keys so an insignificant reordering of params.toml does not appear
        to be a rule change.
        """
        blob = json.dumps(self._tree, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:12]


def _coerce(raw: str) -> Any:
    """Interpret an override value using TOML's own scalar rules.

    Reusing the TOML parser means ``--set x=1``, ``x=1.5``, ``x=true`` and
    ``x="lane"`` all behave exactly as they would in the file itself, rather
    than going through a hand-rolled ladder of try/except casts.
    """
    try:
        return tomllib.loads(f"v = {raw}")["v"]
    except tomllib.TOMLDecodeError:
        return raw  # bare string, e.g. --set facility.default=lane


def _apply_override(tree: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = tree
    for part in parts[:-1]:
        if part not in node or not isinstance(node[part], dict):
            raise ParamsError(
                f"--set {dotted}: {part!r} is not a table in params.toml"
            )
        node = node[part]
    leaf = parts[-1]
    if leaf not in node:
        # Refuse to invent parameters. A typo in a sensitivity sweep would
        # otherwise run silently and report a misleading "no change" result.
        raise ParamsError(
            f"--set {dotted}: no such parameter in {DEFAULT_PATH.name}. "
            "Overrides may only change existing values."
        )
    node[leaf] = value


def load(path: Path | None = None, overrides: list[str] | None = None) -> Params:
    """Load params.toml, applying ``key.path=value`` overrides in order."""
    path = path or DEFAULT_PATH
    if not path.exists():
        raise ParamsError(f"params file not found: {path}")

    with path.open("rb") as fh:
        tree = tomllib.load(fh)

    for item in overrides or []:
        if "=" not in item:
            raise ParamsError(f"malformed override {item!r}; expected key.path=value")
        dotted, raw = item.split("=", 1)
        _apply_override(tree, dotted.strip(), _coerce(raw.strip()))

    return Params(tree, source=path)
