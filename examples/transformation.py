"""Heat-shock two DNAs in triplicate.

Six reactions each receive 20 µL of competent cells, 0.12 mL in total, taken from one 1 mL
aliquot. Replace the DNA and transformant parts below before compiling a real run.
"""

from pathlib import Path

import sbol3

from lab import compile, mL, uL
from lab.experiments.cloning import (
    Transformation,
    build_transformation,
    transformation_deck,
)
from lab.targets import LiquidHandler

DNA_1 = sbol3.Component("https://example.org/dna_1", sbol3.SBO_DNA)
DNA_2 = sbol3.Component("https://example.org/dna_2", sbol3.SBO_DNA)
DH5ALPHA = sbol3.Component("https://sbolcanvas.org/DH5alpha", sbol3.SBO_FUNCTIONAL_ENTITY)
STRAIN_1 = sbol3.Component("https://example.org/transformant_1", sbol3.SBO_FUNCTIONAL_ENTITY)
STRAIN_2 = sbol3.Component("https://example.org/transformant_2", sbol3.SBO_FUNCTIONAL_ENTITY)

designs = sbol3.Document()
designs.add([DNA_1, DNA_2, DH5ALPHA, STRAIN_1, STRAIN_2])

REPLICATES = 3
CELLS_PER_REACTION = 20 * uL
CELL_ALIQUOT = 1 * mL

TRANSFORMATIONS = (
    Transformation(
        strain=STRAIN_1,
        chassis=DH5ALPHA,
        plasmids=[DNA_1],
    ),
    Transformation(
        strain=STRAIN_2,
        chassis=DH5ALPHA,
        plasmids=[DNA_2],
    ),
)

if __name__ == "__main__":
    out = Path(f"build/transformation/{LiquidHandler.OT2.value}")
    protocol = build_transformation(
        TRANSFORMATIONS,
        name="transformation",
        replicates=REPLICATES,
        transfer_volume_competent_cell=CELLS_PER_REACTION,
        tube_volume_competent_cell=CELL_ALIQUOT,
    )
    compiled = compile(
        protocol,
        deck=transformation_deck(on_module=True),
        liquid_handler=LiquidHandler.OT2,
        to=out,
    )
    assert not designs.validate().errors
    designs.write(str(out / "designs.ttl"), sbol3.TURTLE)
    used = len(TRANSFORMATIONS) * REPLICATES * CELLS_PER_REACTION
    print(
        f"{compiled.directory}: {used.to(mL):.2f~P} of cells from one {CELL_ALIQUOT:.3g~P} aliquot"
    )
