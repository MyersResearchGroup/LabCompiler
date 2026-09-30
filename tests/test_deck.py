"""Logical labware and deck invariants, without requiring a robot SDK."""

from dataclasses import FrozenInstanceError, replace
from decimal import Decimal

import pytest

from lab import CompileError, Protocol, compile, seconds
from lab.deck import Container, Deck, DeckSite
from lab.labware import COLD_BLOCK_24, PCR_PLATE_96, TUBE_RACK_24, ContainerSpec
from lab.targets import LiquidHandler
from lab.targets.lower import lower_deck


def test_specifications_are_reusable_and_immutable():
    first = Container(id="first", labware=PCR_PLATE_96, site=DeckSite.PLATES)
    second = Container(id="second", labware=PCR_PLATE_96, site=DeckSite.PLATES)
    deck = Deck(containers=(first, second))
    assert first.labware is second.labware
    with pytest.raises(FrozenInstanceError):
        first.labware.capacity_ul = Decimal(1)
    with pytest.raises(FrozenInstanceError):
        first.site = DeckSite.RESERVOIR
    with pytest.raises(FrozenInstanceError):
        deck.containers = ()


@pytest.mark.parametrize("kind", ["pcr_plate", "typo", DeckSite.PLATES])
def test_labware_rejects_raw_strings_and_unrelated_enums(kind):
    with pytest.raises(TypeError, match="LabwareKind"):
        replace(PCR_PLATE_96, kind=kind)


@pytest.mark.parametrize("site", ["plates", "typo", PCR_PLATE_96.kind])
def test_placement_rejects_raw_strings_and_unrelated_enums(site):
    with pytest.raises(TypeError, match="DeckSite"):
        Container(id="samples", labware=PCR_PLATE_96, site=site)


@pytest.mark.parametrize("rows, columns", [(0, 12), (27, 12), (True, 12), (8, 0), (8, 1.5)])
def test_labware_rejects_invalid_geometry(rows, columns):
    with pytest.raises(ValueError, match="Rows and columns"):
        replace(PCR_PLATE_96, rows=rows, columns=columns)


@pytest.mark.parametrize(
    "capacity", [Decimal(0), Decimal(-1), Decimal("NaN"), Decimal("Infinity"), 100]
)
def test_labware_rejects_invalid_capacity(capacity):
    with pytest.raises(ValueError, match="positive finite Decimal"):
        replace(PCR_PLATE_96, capacity_ul=capacity)


@pytest.mark.parametrize("identifier", ["", "  ", None])
def test_named_containers_validate_identity_before_placement(identifier):
    with pytest.raises(ValueError, match="nonempty text"):
        ContainerSpec(id=identifier, labware=PCR_PLATE_96)
    with pytest.raises(ValueError, match="nonempty text"):
        Container(id=identifier, labware=PCR_PLATE_96, site=DeckSite.PLATES)


def test_named_container_requires_a_labware_specification():
    with pytest.raises(TypeError, match="LabwareSpec"):
        Container(id="samples", labware="pcr_plate", site=DeckSite.PLATES)


@pytest.mark.parametrize(
    "labware, site",
    [
        (PCR_PLATE_96, DeckSite.TUBE_RACK),
        (TUBE_RACK_24, DeckSite.THERMOCYCLER),
        (COLD_BLOCK_24, DeckSite.PLATES),
    ],
)
def test_placement_rejects_incompatible_labware(labware, site):
    with pytest.raises(ValueError, match="cannot sit on"):
        Container(id="samples", labware=labware, site=site)


def test_deck_accepts_shared_container_specs_in_an_immutable_collection():
    placed = Container(id="samples", labware=PCR_PLATE_96, site=DeckSite.PLATES)
    with pytest.raises(TypeError, match="tuple of ContainerSpec"):
        Deck(containers=[placed])
    unplaced = ContainerSpec(id="samples", labware=PCR_PLATE_96)
    assert Deck(containers=(unplaced,)).containers == (unplaced,)
    with pytest.raises(ValueError, match="at least one"):
        Deck(containers=())


def test_deck_rejects_duplicate_ids_even_at_different_sites():
    placed = Container(id="samples", labware=PCR_PLATE_96, site=DeckSite.PLATES)
    with pytest.raises(ValueError, match="unique"):
        Deck(containers=(placed, replace(placed, site=DeckSite.MORE_PLATES)))


@pytest.mark.parametrize(
    "labware, site",
    [(COLD_BLOCK_24, DeckSite.TEMPERATURE_MODULE), (PCR_PLATE_96, DeckSite.THERMOCYCLER)],
)
def test_shared_deck_does_not_assume_one_thermal_device(labware, site):
    deck = Deck(
        containers=(
            Container(id="first", labware=labware, site=site),
            Container(id="second", labware=labware, site=site),
        )
    )
    assert len(deck.containers) == 2


@pytest.mark.parametrize("entrypoint", ["lower", "compile"])
def test_star_cold_block_preset_requires_lab_equipment_configuration(entrypoint):
    deck = Deck(
        containers=(
            Container(id="samples", labware=COLD_BLOCK_24, site=DeckSite.TEMPERATURE_MODULE),
        )
    )
    protocol = Protocol("Deck configuration check")
    protocol.wait(1 * seconds)
    with pytest.raises(CompileError, match="No STAR preset.*Lab DeckLayout"):
        if entrypoint == "lower":
            lower_deck(deck, LiquidHandler.STAR)
        else:
            compile(protocol, deck=deck, liquid_handler=LiquidHandler.STAR)
