"""Compile a protocol covering the operations supported by the optional LabOP exporter."""

import argparse
from pathlib import Path

import lab


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("build/labop_export"))
    args = parser.parse_args()
    protocol = lab.Protocol("LabOP operations")
    plate = protocol.plate("operations", capacity=100 * lab.uL)
    protocol.load(plate["A1"], "water", volume=50 * lab.uL)
    protocol.set_temperature(plate, lab.celsius(25))
    protocol.distribute(
        plate["A1"], (plate["A2"], plate["A3"]), volume=2 * lab.uL, air_gap=1 * lab.uL
    )
    protocol.transfer(plate["A1"], plate["A4"], volume=2 * lab.uL)
    protocol.mix(plate["A1"], volume=2 * lab.uL, cycles=2)
    protocol.wait(1 * lab.seconds)
    protocol.manual("Inspect the plate")
    protocol.thermocycle(
        plate,
        ((lab.celsius(25), 1 * lab.seconds),),
        lid_temperature=lab.celsius(40),
        block_volume=10 * lab.uL,
    )
    output = lab.compile(protocol, to=None).write(args.out)
    print(output / "plan.json")


if __name__ == "__main__":
    main()
