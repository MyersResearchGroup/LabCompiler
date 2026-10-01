"""Write the Golden Gate, heat-shock, and plating workflow."""

import argparse
from pathlib import Path

import sbol3

from lab import compile
from lab.experiments.cloning import (
    BSAI,
    Assembly,
    Transformation,
    assembly_deck,
    build_assembly,
    build_plating,
    build_transformation,
    plating_deck,
    transformation_deck,
)
from lab.targets import LiquidHandler

PSB1C3 = sbol3.Component("https://sbolcanvas.org/pSB1C3", sbol3.SBO_DNA)
J23101 = sbol3.Component("https://sbolcanvas.org/J23101", sbol3.SBO_DNA)
J23106 = sbol3.Component("https://sbolcanvas.org/J23106", sbol3.SBO_DNA)
B0034 = sbol3.Component("https://sbolcanvas.org/B0034", sbol3.SBO_DNA)
GFP = sbol3.Component("https://sbolcanvas.org/GFP", sbol3.SBO_DNA)
RFP = sbol3.Component("https://sbolcanvas.org/RFP", sbol3.SBO_DNA)
B0015 = sbol3.Component("https://sbolcanvas.org/B0015", sbol3.SBO_DNA)
DH5ALPHA = sbol3.Component("https://sbolcanvas.org/DH5alpha", sbol3.SBO_FUNCTIONAL_ENTITY)
BL21 = sbol3.Component("https://sbolcanvas.org/BL21", sbol3.SBO_FUNCTIONAL_ENTITY)
PLASMID_1 = sbol3.Component("https://SBOL2Build.org/composite_plasmid_1", sbol3.SBO_DNA)
PLASMID_2 = sbol3.Component("https://SBOL2Build.org/composite_plasmid_2", sbol3.SBO_DNA)
STRAIN_1 = sbol3.Component("https://SBOL2Build.org/composite_strain_1", sbol3.SBO_FUNCTIONAL_ENTITY)
STRAIN_2 = sbol3.Component("https://SBOL2Build.org/composite_strain_2", sbol3.SBO_FUNCTIONAL_ENTITY)
STRAIN_3 = sbol3.Component("https://SBOL2Build.org/composite_strain_3", sbol3.SBO_FUNCTIONAL_ENTITY)
STRAIN_4 = sbol3.Component("https://SBOL2Build.org/composite_strain_4", sbol3.SBO_FUNCTIONAL_ENTITY)

designs = sbol3.Document()
designs.add(
    [
        BSAI,
        PSB1C3,
        J23101,
        J23106,
        B0034,
        GFP,
        RFP,
        B0015,
        DH5ALPHA,
        BL21,
        PLASMID_1,
        PLASMID_2,
        STRAIN_1,
        STRAIN_2,
        STRAIN_3,
        STRAIN_4,
    ]
)

ASSEMBLIES = (
    Assembly(
        product=PLASMID_1,
        backbone=PSB1C3,
        parts=[J23101, B0034, GFP, B0015],
        restriction_enzyme=BSAI,
    ),
    Assembly(
        product=PLASMID_2,
        backbone=PSB1C3,
        parts=[J23106, B0034, RFP, B0015],
        restriction_enzyme=BSAI,
    ),
)

STRAINS = (
    Transformation(
        strain=STRAIN_1,
        chassis=DH5ALPHA,
        plasmids=[PLASMID_1],
    ),
    Transformation(
        strain=STRAIN_2,
        chassis=DH5ALPHA,
        plasmids=[PLASMID_2],
    ),
    Transformation(
        strain=STRAIN_3,
        chassis=BL21,
        plasmids=[PLASMID_1],
    ),
    Transformation(
        strain=STRAIN_4,
        chassis=BL21,
        plasmids=[PLASMID_2],
    ),
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        choices=("manual", LiquidHandler.OT2.value, LiquidHandler.FLEX.value),
        default="manual",
        help="Use a preset or manual plan. See examples.deck_layouts for layouts across handlers.",
    )
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    liquid_handler = None if args.target == "manual" else LiquidHandler(args.target)
    protocol = build_assembly(ASSEMBLIES, name="sbol-loop-assembly")
    assembled = compile(
        protocol,
        deck=None if liquid_handler is None else assembly_deck(),
        liquid_handler=liquid_handler,
        to=None,
    )
    protocol = build_transformation(STRAINS, inputs=assembled.manifest, name="heat-shock")
    transformed = compile(
        protocol,
        deck=None if liquid_handler is None else transformation_deck(),
        liquid_handler=liquid_handler,
        to=None,
    )
    protocol = build_plating(transformed.manifest)
    plated = compile(
        protocol,
        deck=None if liquid_handler is None else plating_deck(),
        liquid_handler=liquid_handler,
        to=None,
    )
    out = Path(args.out or f"build/cloning/{args.target}")
    out.mkdir(parents=True, exist_ok=True)
    assert not designs.validate().errors
    designs.write(str(out / "designs.ttl"), sbol3.TURTLE)
    for name, compiled in (
        ("assembly", assembled),
        ("transformation", transformed),
        ("plating", plated),
    ):
        path = compiled.write(out / name)
        print(f"{name}: {path}")


if __name__ == "__main__":
    main()
