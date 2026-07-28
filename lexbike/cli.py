"""Command-line entry point.

    python -m lexbike build
    python -m lexbike build --set lts.mixed.speed_35_lts=2 --out data/sens_a
    python -m lexbike validate
    python -m lexbike sensitivity
    python -m lexbike stats
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import params as params_mod

DEFAULT_OUT = Path("data")


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--params",
        type=Path,
        default=None,
        help="path to params.toml (default: repository root)",
    )
    p.add_argument(
        "--set",
        dest="overrides",
        action="append",
        metavar="KEY.PATH=VALUE",
        default=[],
        help="override a params value; repeatable (e.g. --set lts.mixed.speed_35_lts=2)",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m lexbike",
        description="Compute bicycle Level of Traffic Stress for Lexington, KY.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help="run the pipeline and write data artifacts")
    p_build.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output directory")
    p_build.add_argument(
        "--skip-size-check",
        action="store_true",
        help="do not fail when an artifact exceeds its size budget",
    )
    _add_common(p_build)

    p_validate = sub.add_parser(
        "validate", help="check artifacts against golden corridors and baseline stats"
    )
    p_validate.add_argument("--out", type=Path, default=DEFAULT_OUT)
    _add_common(p_validate)

    p_sens = sub.add_parser(
        "sensitivity", help="re-run the build per [[sensitivity.runs]] and write docs/sensitivity.md"
    )
    p_sens.add_argument("--out", type=Path, default=Path("data/_sensitivity"))
    p_sens.add_argument("--doc", type=Path, default=Path("docs/sensitivity.md"))
    _add_common(p_sens)

    p_stats = sub.add_parser("stats", help="print the current build's summary figures")
    p_stats.add_argument("--out", type=Path, default=DEFAULT_OUT)
    _add_common(p_stats)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(levelname)-7s %(message)s",
        stream=sys.stderr,
    )
    log = logging.getLogger("lexbike")

    try:
        params = params_mod.load(args.params, args.overrides)
    except params_mod.ParamsError as exc:
        log.error("%s", exc)
        return 2

    log.info("ruleset %s (digest %s)", params["meta.ruleset_version"], params.digest)

    try:
        if args.command == "build":
            from .pipeline import run_build

            run_build(params, args.out, skip_size_check=args.skip_size_check)
        elif args.command == "validate":
            from .validate import run_validate

            return 0 if run_validate(params, args.out) else 1
        elif args.command == "sensitivity":
            from .pipeline import run_sensitivity

            run_sensitivity(params, args.out, args.doc)
        elif args.command == "stats":
            from .export import print_stats

            print_stats(args.out)
    except NotImplementedError as exc:
        log.error("not implemented yet: %s", exc)
        return 3
    except Exception as exc:  # surface the message, keep the traceback for -v
        log.error("%s: %s", type(exc).__name__, exc)
        if getattr(args, "verbose", False):
            raise
        return 1

    return 0
