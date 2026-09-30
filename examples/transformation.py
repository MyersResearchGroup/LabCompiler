"""Heat-shock two DNAs in triplicate.

Six reactions each receive 20 µL of competent cells, 0.12 mL in total, taken from one 1 mL
aliquot. Replace the DNA and transformant parts below before compiling a real run.
"""

from pathlib import Path

from lab import compile, mL, uL
from lab.experiments.cloning import (
    Transformation,
    TransformationRequest,
    build_transformation,
    transformation_deck,
)
from lab.part import Part
from lab.targets import LiquidHandler

DNA_1 = Part("https://example.org/dna-1/1")
DNA_2 = Part("https://example.org/dna-2/1")
DH5ALPHA = Part("https://sbolcanvas.org/DH5alpha/1")
STRAIN_1 = Part("https://example.org/transformant-1/1")
STRAIN_2 = Part("https://example.org/transformant-2/1")

REPLICATES = 3
CELLS_PER_REACTION = 20 * uL
CELL_ALIQUOT = 1 * mL

TRANSFORMATIONS = (
    Transformation(
        id="transformation-1",
        strain=STRAIN_1,
        chassis=DH5ALPHA,
        plasmids=[DNA_1],
    ),
    Transformation(
        id="transformation-2",
        strain=STRAIN_2,
        chassis=DH5ALPHA,
        plasmids=[DNA_2],
    ),
)

if __name__ == "__main__":
    out = Path(f"build/transformation/{LiquidHandler.OT2.value}")
    compiled = compile(
        build_transformation(
            TransformationRequest(id="transformation", transformations=TRANSFORMATIONS),
            replicates=REPLICATES,
            transfer_volume_competent_cell=CELLS_PER_REACTION,
            tube_volume_competent_cell=CELL_ALIQUOT,
        ),
        deck=transformation_deck(on_module=True),
        liquid_handler=LiquidHandler.OT2,
        to=out,
    )
    used = len(TRANSFORMATIONS) * REPLICATES * CELLS_PER_REACTION
    print(
        f"{compiled.directory}: {used.to(mL):.2f~P} of cells from one {CELL_ALIQUOT:.3g~P} aliquot"
    )
