"""Prepare a BCA protein assay plate on OT-2, Flex, or STAR using one Lab deck.

These illustrative layouts must be adapted to the configuration of your instrument.
Compilation is offline; direct execution of the generated program uses a software backend.

Standards, protein samples, and BCA working reagent are supplied already prepared.
Plate preparation follows the Pierce BCA Protein Assay microplate method:
https://www.thermofisher.com/TFS-Assets/LSG/manuals/MAN0011430_Pierce_BCA_Protein_Asy_UG.pdf
"""

import argparse
from decimal import Decimal

from lab import Protocol, compile, uL
from lab.deck import (
    Carrier,
    Channel,
    Deck,
    DeckLayout,
    HolderSite,
    Pipette,
    Placement,
    Rail,
    Slot,
    TipRack,
)
from lab.equipment import (
    CarrierModel,
    LabwareModel,
    LiquidHandler,
    Mount,
    PipetteModel,
    TipRackModel,
)
from lab.labware import PLATE_96, ContainerSpec, LabwareKind, LabwareSpec


def protocol() -> Protocol:
    """Describe logical wells and transfers independently of physical placement."""
    p = Protocol("BCA protein assay plate preparation")
    sources = p.plate("sources", shape=(1, 12), capacity=200 * uL)
    assay = p.plate("assay", capacity=360 * uL)
    working_reagent = p.container(
        "working_reagent",
        contents="Prepared BCA working reagent",
        volume=6000 * uL,
        capacity=15000 * uL,
        dead_volume=500 * uL,
    )
    standards = (0, 25, 125, 250, 500, 750, 1000, 1500, 2000)
    materials = (
        *(f"BSA standard, {concentration} ug/mL" for concentration in standards),
        "Purified lysozyme sample 1",
        "Purified lysozyme sample 2",
        "Purified lysozyme sample 3",
    )
    for column, material in enumerate(materials, start=1):
        p.load(sources[f"A{column}"], material, volume=100 * uL)

    # Rows A and B contain duplicate standards (columns 1-9) and samples (10-12).
    for row in ("A", "B"):
        for column in range(1, 13):
            p.transfer(sources[f"A{column}"], assay[f"{row}{column}"], volume=25 * uL)
    for row in ("A", "B"):
        for column in range(1, 13):
            p.transfer(working_reagent, assay[f"{row}{column}"], volume=200 * uL)
    p.manual(
        "Transfer the assay plate to the BCA mixing, incubation, and absorbance-reading "
        "workflow described in the Pierce BCA Protein Assay guide. "
        "Retain the duplicate standard and sample positions in rows A and B."
    )
    return p


def deck() -> Deck:
    """Share container requirements and describe each layout using Lab objects."""
    return Deck(
        containers=(
            ContainerSpec(
                id="sources",
                labware=LabwareSpec(
                    kind=LabwareKind.PLATE, rows=1, columns=12, capacity_ul=Decimal(200)
                ),
            ),
            ContainerSpec(id="assay", labware=PLATE_96),
            ContainerSpec(
                id="working_reagent",
                labware=LabwareSpec(
                    kind=LabwareKind.RESERVOIR, rows=1, columns=1, capacity_ul=Decimal(15000)
                ),
            ),
        ),
        layouts=(
            opentrons_layout(LiquidHandler.OT2),
            opentrons_layout(LiquidHandler.FLEX),
            hamilton_layout(),
        ),
    )


def hamilton_layout() -> DeckLayout:
    """Place plates, a reservoir, and tips on STAR carriers and rails."""
    return DeckLayout(
        liquid_handler=LiquidHandler.STAR,
        carriers=(
            Carrier(id="tip_carrier", model=CarrierModel.HAMILTON_TIP_5, location=Rail(3)),
            Carrier(id="plate_carrier", model=CarrierModel.HAMILTON_PLATE_5, location=Rail(20)),
        ),
        placements=(
            Placement(
                container="sources",
                model=LabwareModel.CORNING_96_360_UL,
                location=HolderSite("plate_carrier", 0),
                wells=tuple(f"B{column}" for column in range(1, 13)),
            ),
            Placement(
                container="assay",
                model=LabwareModel.CORNING_96_360_UL,
                location=HolderSite("plate_carrier", 3),
            ),
            Placement(
                container="working_reagent",
                model=LabwareModel.NEST_12_RESERVOIR_15_ML,
                location=HolderSite("plate_carrier", 4),
                wells=("A1",),
            ),
        ),
        tip_racks=(
            TipRack(
                id="tips", model=TipRackModel.HAMILTON_300_UL, location=HolderSite("tip_carrier", 2)
            ),
        ),
        channels=(
            Channel(
                index=0, min_volume_ul=Decimal(20), max_volume_ul=Decimal(300), tip_racks=("tips",)
            ),
        ),
    )


def opentrons_layout(handler: LiquidHandler) -> DeckLayout:
    """Place plates, a reservoir, and tips in OT-2 or Flex slots."""
    if handler not in (LiquidHandler.OT2, LiquidHandler.FLEX):
        raise ValueError("Choose LiquidHandler.OT2 or LiquidHandler.FLEX.")
    flex = handler == LiquidHandler.FLEX
    return DeckLayout(
        liquid_handler=handler,
        placements=(
            Placement(
                container="sources",
                model=LabwareModel.CORNING_96_360_UL,
                location=Slot("D1" if flex else "1"),
                wells=tuple(f"B{column}" for column in range(1, 13)),
            ),
            Placement(
                container="assay",
                model=LabwareModel.CORNING_96_360_UL,
                location=Slot("D2" if flex else "2"),
            ),
            Placement(
                container="working_reagent",
                model=LabwareModel.NEST_12_RESERVOIR_15_ML,
                location=Slot("C2" if flex else "4"),
                wells=("A1",),
            ),
        ),
        tip_racks=(
            TipRack(
                id="tips",
                model=TipRackModel.FLEX_200_UL if flex else TipRackModel.OPENTRONS_300_UL,
                location=Slot("C1" if flex else "3"),
            ),
        ),
        pipettes=(
            Pipette(
                model=PipetteModel.FLEX_1CHANNEL_1000 if flex else PipetteModel.P300_SINGLE_GEN2,
                mount=Mount.LEFT,
                tip_racks=("tips",),
            ),
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target", choices=tuple(handler.value for handler in LiquidHandler), default="star"
    )
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    bundle = compile(protocol(), deck=deck(), liquid_handler=LiquidHandler(args.target))
    print(bundle.write(args.out or f"build/decks/{args.target}"))


if __name__ == "__main__":
    main()
