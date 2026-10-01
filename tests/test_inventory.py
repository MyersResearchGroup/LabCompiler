from dataclasses import replace

import pytest

from lab import uL
from lab.inventory import CountedStock, Inventory, MaterialForm, Stock
from lab.provenance import Component, Document, EvidenceState, Implementation
from lab.provenance.vocabulary import DNA


def test_inventory_round_trip_preserves_quantities_and_rejects_planned_stock(tmp_path):
    document = Document(namespace="https://example.org/inventory")
    design = Component(identity=document.iri("design"), types=(DNA,))
    liquid = Implementation(
        identity=document.iri("liquid"),
        derived_from=(design.ref,),
        evidence_state=EvidenceState.RECORDED,
    )
    counted = replace(liquid, identity=document.iri("counted"))
    document.add(design, liquid, counted)
    stocks = (
        Stock(
            identity=document.iri("aliquot"),
            design=design.ref,
            implementation=liquid.ref,
            form=MaterialForm.DNA,
            quantity=10 * uL,
        ),
        CountedStock(
            identity=document.iri("stab"),
            design=design.ref,
            implementation=counted.ref,
            form=MaterialForm.BACTERIAL_STAB,
            count=2,
        ),
    )
    inventory = Inventory(identity=document.iri("inventory"), stocks=stocks)
    snapshot = document.freeze()
    inventory.validate(snapshot)
    assert (
        Inventory.read(inventory.write(tmp_path / "inventory.json"), document=snapshot) == inventory
    )
    assert inventory.stocks[0].amount == 10 and inventory.stocks[1].amount == 2
    with pytest.raises(ValueError, match="double-count"):
        replace(
            inventory, stocks=(stocks[1], replace(stocks[1], identity=document.iri("duplicate")))
        )
    document.replace(replace(liquid, evidence_state=EvidenceState.PLANNED))
    with pytest.raises(ValueError, match="recorded"):
        inventory.validate(document.freeze())
