"""Lab-owned deck validation and target capability boundaries."""

from dataclasses import replace
from decimal import Decimal

import pytest

from examples.deck_layouts import deck as example_deck
from examples.deck_layouts import protocol as example_protocol
from lab import CompileError, Protocol, celsius, compile, uL
from lab.deck import Carrier, Deck, DeckLayout, HolderSite, Rail, Slot
from lab.equipment import CarrierModel, LabwareModel, LiquidHandler
from lab.labware import COLD_BLOCK_24, PCR_PLATE_96, ContainerSpec
from lab.targets.lower import lower_deck


def star_layout() -> DeckLayout:
    return example_deck().layout_for(LiquidHandler.STAR)


def test_layouts_keep_shared_requirements_and_select_the_requested_handler():
    deck = example_deck()
    assert {layout.liquid_handler for layout in deck.layouts} == set(LiquidHandler)
    assert {container.id for container in deck.containers} == {
        "sources",
        "assay",
        "working_reagent",
    }
    with pytest.raises(ValueError, match="one layout per liquid handler"):
        replace(deck, layouts=(star_layout(), star_layout()))
    with pytest.raises(ValueError, match="exactly the deck's containers"):
        replace(deck, layouts=(replace(star_layout(), placements=star_layout().placements[:1]),))


def test_missing_layout_does_not_fall_back_to_another_handler():
    deck = replace(example_deck(), layouts=(star_layout(),))
    with pytest.raises(CompileError, match="No ot2 layout"):
        compile(example_protocol(), deck=deck, liquid_handler=LiquidHandler.OT2)
    with pytest.raises(CompileError, match="Provide a DeckLayout"):
        lower_deck(Deck(containers=deck.containers), LiquidHandler.STAR)


@pytest.mark.parametrize("location", ["3", 3, None])
def test_layouts_require_typed_locations(location):
    layout = star_layout()
    with pytest.raises(TypeError, match="Slot, Rail, or HolderSite"):
        replace(
            layout, carriers=(replace(layout.carriers[0], location=location), *layout.carriers[1:])
        )


def test_layout_references_occupancy_and_model_types():
    layout = star_layout()
    with pytest.raises(ValueError, match="Unknown holder"):
        replace(
            layout,
            placements=(
                replace(layout.placements[0], location=HolderSite("missing")),
                *layout.placements[1:],
            ),
        )
    with pytest.raises(ValueError, match="occupied more than once"):
        replace(
            layout,
            placements=(
                layout.placements[0],
                replace(layout.placements[1], location=layout.placements[0].location),
                *layout.placements[2:],
            ),
        )
    with pytest.raises(ValueError, match="has 5 sites"):
        replace(
            layout,
            placements=(
                replace(layout.placements[0], location=HolderSite("plate_carrier", 5)),
                *layout.placements[1:],
            ),
        )
    with pytest.raises(TypeError, match="LabwareModel"):
        replace(
            layout,
            placements=(replace(layout.placements[0], model="corning"), *layout.placements[1:]),
        )
    with pytest.raises(ValueError, match="ids must be unique"):
        replace(layout, carriers=(*layout.carriers, layout.carriers[0]))


def test_holder_cycles_and_invalid_positions_are_rejected():
    with pytest.raises(ValueError, match="cycle"):
        DeckLayout(
            liquid_handler=LiquidHandler.STAR,
            placements=(),
            carriers=(
                Carrier(id="one", model=CarrierModel.HAMILTON_PLATE_5, location=HolderSite("two")),
                Carrier(id="two", model=CarrierModel.HAMILTON_PLATE_5, location=HolderSite("one")),
            ),
        )
    with pytest.raises(ValueError, match="positive integer"):
        Rail(0)
    with pytest.raises(ValueError, match="nonnegative integer"):
        HolderSite("carrier", -1)


def test_well_maps_must_cover_the_shared_container():
    deck = example_deck()
    layout = star_layout()
    with pytest.raises(ValueError, match="every logical well"):
        replace(
            deck,
            layouts=(
                replace(
                    layout,
                    placements=(
                        replace(layout.placements[0], wells=("A1", "B1")),
                        *layout.placements[1:],
                    ),
                ),
            ),
        )


def test_cold_blocks_cannot_be_replaced_with_ordinary_plates():
    deck = example_deck()
    layout = star_layout()
    placements = (replace(layout.placements[0], wells=()), *layout.placements[1:])
    deck = replace(
        deck,
        containers=(ContainerSpec(id="sources", labware=COLD_BLOCK_24), *deck.containers[1:]),
        layouts=(replace(layout, placements=placements),),
    )
    with pytest.raises(CompileError, match="does not satisfy.*cold_block"):
        lower_deck(deck, LiquidHandler.STAR)


def test_opentrons_rejects_hamilton_carriers_and_channels():
    layout = replace(star_layout(), liquid_handler=LiquidHandler.OT2)
    with pytest.raises(CompileError, match="does not support carriers"):
        lower_deck(replace(example_deck(), layouts=(layout,)), LiquidHandler.OT2)


def test_requirements_are_checked_before_layout_translation():
    deck = example_deck()
    smaller = replace(deck.containers[1], labware=replace(deck.containers[1].labware, columns=1))
    deck = replace(deck, containers=(deck.containers[0], smaller, *deck.containers[2:]))
    with pytest.raises(ValueError, match="protocol geometry for assay"):
        compile(example_protocol(), deck=deck, liquid_handler=LiquidHandler.STAR)
    deck = example_deck()
    smaller = replace(
        deck.containers[0],
        labware=replace(deck.containers[0].labware, capacity_ul=Decimal(100)),
    )
    with pytest.raises(CompileError, match="capacity exceeds the deck limit for sources"):
        compile(
            example_protocol(),
            deck=replace(deck, containers=(smaller, *deck.containers[1:])),
            liquid_handler=LiquidHandler.STAR,
        )


def test_independent_channels_can_describe_a_shared_tip_rack():
    layout = star_layout()
    expanded = replace(layout, channels=(layout.channels[0], replace(layout.channels[0], index=1)))
    assert expanded.channels[0].tip_racks == expanded.channels[1].tip_racks


@pytest.mark.integration
def test_star_validates_carrier_footprints_and_holder_compatibility():
    pytest.importorskip("pylabrobot")
    layout = star_layout()
    overlapping = replace(
        layout, carriers=(layout.carriers[0], replace(layout.carriers[1], location=Rail(4)))
    )
    with pytest.raises(CompileError, match="Invalid STAR layout"):
        lower_deck(replace(example_deck(), layouts=(overlapping,)), LiquidHandler.STAR)
    wrong_carrier = replace(
        layout,
        placements=(
            replace(layout.placements[0], location=HolderSite("tip_carrier", 0)),
            *layout.placements[1:],
        ),
    )
    with pytest.raises(CompileError, match="requires a hamilton_plate_5"):
        lower_deck(replace(example_deck(), layouts=(wrong_carrier,)), LiquidHandler.STAR)
    unsupported = replace(
        layout,
        placements=(replace(layout.placements[0], location=Slot("1")), *layout.placements[1:]),
    )
    with pytest.raises(CompileError, match="carrier HolderSite"):
        lower_deck(replace(example_deck(), layouts=(unsupported,)), LiquidHandler.STAR)


@pytest.mark.integration
def test_star_reports_unsupported_labware_instead_of_substituting():
    pytest.importorskip("pylabrobot")
    layout = star_layout()
    layout = replace(
        layout,
        placements=(
            layout.placements[0],
            replace(layout.placements[1], model=LabwareModel.NEST_96_PCR_100_UL),
            *layout.placements[2:],
        ),
    )
    with pytest.raises(CompileError, match="does not support labware model"):
        lower_deck(
            replace(
                example_deck(),
                containers=(
                    example_deck().containers[0],
                    ContainerSpec(id="assay", labware=PCR_PLATE_96),
                    example_deck().containers[2],
                ),
                layouts=(layout,),
            ),
            LiquidHandler.STAR,
        )


@pytest.mark.integration
def test_star_thermal_steps_require_a_declared_handoff():
    pytest.importorskip("pylabrobot")
    protocol = Protocol("Thermal capability check")
    protocol.plate("sources", shape=(1, 12), capacity=200 * uL)
    assay = protocol.plate("assay", capacity=360 * uL)
    protocol.container("working_reagent", capacity=15000 * uL)
    protocol.set_temperature(assay, celsius(4))
    with pytest.raises(CompileError, match="declared external thermal handoff"):
        compile(protocol, deck=example_deck(), liquid_handler=LiquidHandler.STAR)
    layout = replace(star_layout(), external_thermal_resources=("assay",))
    bundle = compile(
        protocol,
        deck=replace(example_deck(), layouts=(layout,)),
        liquid_handler=LiquidHandler.STAR,
    )
    assert "Supply an async set_temperature callback" in bundle.files["protocol.py"]
