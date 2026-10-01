"""Compose caller-supplied cloning designs into a manual plan.

Robot workflows build and compile each stage separately with ``lab.compile`` and
pass the output manifest to the next stage.
"""

from collections.abc import Sequence

from lab.experiments.cloning.addresses import well_at
from lab.experiments.cloning.stages.assembly import layout_assembly, record_assembly
from lab.experiments.cloning.stages.plating import record_plating
from lab.experiments.cloning.stages.transformation import (
    layout_transformation,
    record_transformation,
)
from lab.experiments.cloning.types import Assembly, Transformation
from lab.protocol import Protocol
from lab.units import uL


def golden_gate(
    assemblies: Sequence[Assembly],
    strains: Sequence[Transformation],
) -> Protocol:
    """Assemble, transform, and plate caller-supplied designs. Compile as manual."""
    protocol = Protocol(
        "Golden Gate, heat shock, and plating",
        description=(
            "SBOL loop assembly, heat-shock transformation, and serial-dilution plating. "
            "Wells produced by one stage are the inputs of the next."
        ),
    )
    reagents = protocol.plate("reagents", shape=(4, 6), capacity=1500 * uL, dead_volume=0 * uL)
    assembly_plate = protocol.plate(
        "assemblies", shape=(8, 12), capacity=100 * uL, dead_volume=0 * uL
    )
    tubes = protocol.plate("tubes", shape=(4, 6), capacity=1500 * uL, dead_volume=0 * uL)
    reactions = protocol.plate("reactions", shape=(8, 12), capacity=100 * uL, dead_volume=0 * uL)
    dilutions = protocol.plate("dilutions", shape=(8, 12), capacity=100 * uL, dead_volume=0 * uL)
    agar = protocol.plate("agar", shape=(8, 12), capacity=100 * uL, dead_volume=0 * uL)
    broth = protocol.container(
        "broth",
        contents="liquid_broth",
        volume=10000 * uL,
        capacity=15000 * uL,
        dead_volume=0 * uL,
    )
    assembly = layout_assembly([assembly._inputs() for assembly in assemblies])
    locations = {
        key: [well_at(assembly_plate, index).name for index in indexes]
        for key, indexes in assembly.products.items()
    }
    transformation = layout_transformation([strain._inputs() for strain in strains], locations)
    for index, material, volume in assembly.stocks:
        protocol.load(well_at(reagents, index), material, volume=volume * uL)
    for index, material, volume in (*transformation.cell_stocks, *transformation.media_stocks):
        protocol.load(well_at(tubes, index), material, volume=volume * uL)
    record_assembly(protocol, assembly, reagents, assembly_plate)
    record_transformation(protocol, transformation, assembly_plate, tubes, reactions)
    record_plating(
        protocol,
        [well_at(reactions, move.destination) for move in transformation.cell_moves],
        broth,
        dilutions,
        agar,
        hold_source=False,
    )
    return protocol
