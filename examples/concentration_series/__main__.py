"""Prepare an LMSF fitness CSV layout using explicit configuration.

Run with --csv and --config (see config.toml). Stocks are supplied already
prepared. Every CSV row is prepared unless config.wells selects a subset.
The example ends with an inoculated culture plate.
"""

import argparse
import csv
import hashlib
import tomllib
from io import StringIO
from pathlib import Path

from examples.concentration_series.inputs import (
    Condition,
    PlateLayout,
    ProtocolConfig,
    prepare_wells,
)
from examples.concentration_series.protocol import protocol
from lab import compile
from lab.equipment import LiquidHandler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, type=Path, help="LMSF fitness destination CSV")
    parser.add_argument("--config", required=True, type=Path, help="Protocol configuration TOML")
    parser.add_argument("--target", choices=("manual", "ot2", "star"), default="manual")
    parser.add_argument("--out")
    args = parser.parse_args()
    try:
        with args.config.open("rb") as stream:
            config = ProtocolConfig.model_validate(tomllib.load(stream))
        csv_bytes = args.csv.read_bytes()
        reader = csv.DictReader(StringIO(csv_bytes.decode("utf-8-sig"), newline=""))
        headers = {field.alias or name for name, field in Condition.model_fields.items()}
        if set(reader.fieldnames or ()) != headers or len(reader.fieldnames or ()) != len(headers):
            raise ValueError(
                "Expected the LMSF fitness CSV schema; cytometry needs a separate recipe"
            )
        layout = PlateLayout.model_validate({"conditions": list(reader)})
        prepared_wells = prepare_wells(layout, config)
        p = protocol(prepared_wells, config)
        summary, _, notes = p.description.partition("\n\n")
        p.description = (
            f"{summary}\n\n"
            f"Targets from {args.csv.name} (SHA256 {hashlib.sha256(csv_bytes).hexdigest()}).\n\n"
            f"{notes}"
        )
        handler = None if args.target == "manual" else LiquidHandler(args.target)
        bundle = compile(
            p,
            deck=config.deck if handler else None,
            liquid_handler=handler,
            to=args.out or f"build/concentration_series/csv/{args.target}",
        )
        print(bundle.directory)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
