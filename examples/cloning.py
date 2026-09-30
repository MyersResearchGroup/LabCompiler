"""Write the Golden Gate, heat-shock, and plating workflow."""

import argparse
from pathlib import Path

from lab import compile
from lab.experiments.cloning import (
    BSAI,
    Assembly,
    AssemblyRequest,
    PlatingRequest,
    Transformation,
    TransformationRequest,
    assembly_deck,
    plating_deck,
    transformation_deck,
)
from lab.part import Part
from lab.targets import LiquidHandler, Manual

PSB1C3 = Part("https://sbolcanvas.org/pSB1C3/1")
J23101 = Part("https://sbolcanvas.org/J23101/1")
J23106 = Part("https://sbolcanvas.org/J23106/1")
B0034 = Part("https://sbolcanvas.org/B0034/1")
GFP = Part("https://sbolcanvas.org/GFP/1")
RFP = Part("https://sbolcanvas.org/RFP/1")
B0015 = Part("https://sbolcanvas.org/B0015/1")
DH5ALPHA = Part("https://sbolcanvas.org/DH5alpha/1")
BL21 = Part("https://sbolcanvas.org/BL21/1")
PLASMID_1 = Part("https://SBOL2Build.org/composite_plasmid_1/1")
PLASMID_2 = Part("https://SBOL2Build.org/composite_plasmid_2/1")
STRAIN_1 = Part("https://SBOL2Build.org/composite_strain_1/1")
STRAIN_2 = Part("https://SBOL2Build.org/composite_strain_2/1")
STRAIN_3 = Part("https://SBOL2Build.org/composite_strain_3/1")
STRAIN_4 = Part("https://SBOL2Build.org/composite_strain_4/1")

ASSEMBLIES = (
    Assembly(
        id="assembly-1",
        product=PLASMID_1,
        backbone=PSB1C3,
        parts=[J23101, B0034, GFP, B0015],
        restriction_enzyme=BSAI,
    ),
    Assembly(
        id="assembly-2",
        product=PLASMID_2,
        backbone=PSB1C3,
        parts=[J23106, B0034, RFP, B0015],
        restriction_enzyme=BSAI,
    ),
)

STRAINS = (
    Transformation(
        id="transformation-1",
        strain=STRAIN_1,
        chassis=DH5ALPHA,
        plasmids=[PLASMID_1],
    ),
    Transformation(
        id="transformation-2",
        strain=STRAIN_2,
        chassis=DH5ALPHA,
        plasmids=[PLASMID_2],
    ),
    Transformation(
        id="transformation-3",
        strain=STRAIN_3,
        chassis=BL21,
        plasmids=[PLASMID_1],
    ),
    Transformation(
        id="transformation-4",
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
    assembled = compile(
        AssemblyRequest(id="sbol-loop-assembly", assemblies=ASSEMBLIES),
        hardware=Manual() if liquid_handler is None else assembly_deck(),
        liquid_handler=liquid_handler,
    )
    transformed = compile(
        TransformationRequest(id="heat-shock", transformations=STRAINS),
        hardware=Manual() if liquid_handler is None else transformation_deck(),
        liquid_handler=liquid_handler,
        inputs=assembled.manifest,
    )
    plated = compile(
        PlatingRequest(
            id="plating",
            sample_ids=tuple(sample.id for sample in transformed.manifest.samples),
            source_stage_id=transformed.manifest.protocol_id,
        ),
        hardware=Manual() if liquid_handler is None else plating_deck(),
        liquid_handler=liquid_handler,
        inputs=transformed.manifest,
    )
    out = Path(args.out or f"build/cloning/{args.target}")
    for name, compiled in (
        ("assembly", assembled),
        ("transformation", transformed),
        ("plating", plated),
    ):
        path = compiled.write(out / name)
        print(f"{name}: {path}")


if __name__ == "__main__":
    main()
