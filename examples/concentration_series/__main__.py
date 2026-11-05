"""Prepare an LMSF fitness CSV layout using explicit configuration.

Run with --csv and --config (see config.toml). Stocks are supplied already
prepared. Every CSV row is prepared unless config.wells selects a subset.
--strategy chooses direct dosing, serial dilution of detected gradients, or
automatic selection. The example ends with an inoculated culture plate.
"""

import argparse
import hashlib
import sys
import tomllib
from pathlib import Path

from examples.concentration_series.inputs import ProtocolConfig
from examples.concentration_series.protocol import protocol
from lab import compile
from lab.equipment import LiquidHandler
from lab.plates import PlateDesign
from lab.plugins.lmsf import read_layout


def select(design: PlateDesign, wells: tuple[str, ...] | None) -> PlateDesign:
    """Keep the configured subset of wells, in the requested order."""
    if wells is None:
        return design
    if missing := set(wells) - {target.well for target in design.wells}:
        raise ValueError(f"Selected wells absent from CSV: {sorted(missing)}")
    return PlateDesign(
        name=design.name,
        shape=design.shape,
        final_volume_ul=design.final_volume_ul,
        wells=tuple(design[well] for well in wells),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, type=Path, help="LMSF fitness destination CSV")
    parser.add_argument("--config", required=True, type=Path, help="Protocol configuration TOML")
    parser.add_argument("--target", choices=("manual", "ot2", "star"), default="manual")
    parser.add_argument(
        "--strategy",
        choices=("direct", "serial", "auto"),
        help="Override preparation.strategy from the configuration",
    )
    parser.add_argument(
        "--warn-below",
        help='Smallest volume you trust any pipette to deliver, e.g. "2 uL"; '
        "overrides preparation.warn_below",
    )
    parser.add_argument("--out")
    args = parser.parse_args()
    try:
        with args.config.open("rb") as stream:
            raw = tomllib.load(stream)
        if args.strategy is not None:
            raw.setdefault("preparation", {})["strategy"] = args.strategy
        if args.warn_below is not None:
            raw.setdefault("preparation", {})["warn_below"] = args.warn_below
        config = ProtocolConfig.model_validate(raw)
        csv_bytes = args.csv.read_bytes()
        design = select(
            read_layout(
                csv_bytes.decode("utf-8-sig"), name="cultures", final_volume=config.final_volume
            ),
            config.wells,
        )
        p, report = protocol(design, config)
        summary, _, notes = p.description.partition("\n\n")
        gradients = ", ".join(
            f"{plan.candidate.solute} {plan.candidate.steps}-step {plan.candidate.fold}-fold "
            f"across {plan.candidate.width} rows"
            for plan in report.gradients
        )
        p.description = (
            f"{summary}\n\n"
            f"Targets from {args.csv.name} (SHA256 {hashlib.sha256(csv_bytes).hexdigest()}).\n\n"
            + (f"Serial dilutions: {gradients}.\n\n" if gradients else "")
            + notes
        )
        handler = None if args.target == "manual" else LiquidHandler(args.target)
        bundle = compile(
            p,
            deck=config.deck if handler else None,
            liquid_handler=handler,
            to=args.out or f"build/concentration_series/csv/{args.target}",
            warn_below=config.preparation.warn_below,
            resolution=config.preparation.resolution,
        )
        print(bundle.directory)
        for diagnostic in bundle.diagnostics:
            step = (
                f" (step {diagnostic.step_index + 1})" if diagnostic.step_index is not None else ""
            )
            print(f"{diagnostic.severity.value}{step}: {diagnostic.message}", file=sys.stderr)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
