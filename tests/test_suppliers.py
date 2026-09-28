import io
import json
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from lab import uL
from lab.experiments.cloning.planning import RequirementKind, plan
from lab.inventory import Inventory, MaterialForm
from lab.provenance import Agent, Document, EvidenceState
from lab.suppliers import (
    AddgeneClient,
    Catalog,
    CatalogEntry,
    OrderReference,
    Receipt,
    SequenceSource,
    parse_plasmid,
)
from tests.planning_fixture import NS, planning_case

TIME = datetime(2026, 9, 27, tzinfo=UTC)


def response():
    # Synthetic neutral bases, with the field names of Addgene's retrieve schema.
    return json.dumps(
        {
            "id": 42,
            "name": "Contract fixture",
            "url": "https://www.addgene.org/42/",
            "sequences": {
                "public_user_full_sequences": [
                    {
                        "sequence_id": 17,
                        "sequence_description": "Depositor assertion",
                        "sequence": "ACGT",
                        "length": 4,
                        "genbank_url": "",
                        "genbank_api_url": "https://example.org/sequence.gb",
                    }
                ],
                "public_addgene_partial_sequences": [
                    {
                        "sequence_id": 18,
                        "sequence_description": "Partial trace",
                        "sequence": "AC",
                        "length": 2,
                        "genbank_api_url": "",
                    }
                ],
            },
        }
    )


def catalog(design):
    item = parse_plasmid(response(), retrieved_at=TIME)
    return Catalog(
        entries=(CatalogEntry(item=item, design=design, form=MaterialForm.BACTERIAL_STAB),)
    )


def test_catalog_preserves_source_completeness_and_original_metadata(tmp_path):
    request, _ = planning_case()
    snapshot = catalog(request.targets[0].design)
    item = snapshot.entries[0].item
    assert item.sequences[0].source is SequenceSource.DEPOSITOR
    assert item.sequences[0].complete
    assert item.sequences[1].source is SequenceSource.ADDGENE
    assert not item.sequences[1].complete
    assert json.loads(item.metadata_json) == json.loads(response())
    path = snapshot.write(tmp_path / "catalog.json")
    assert Catalog.read(path) == snapshot


def test_catalog_http_request_matches_documented_auth_and_endpoint():
    client = AddgeneClient(token="secret-example")
    assert "secret-example" not in repr(client)
    with patch("lab.suppliers.addgene.build_opener") as opener:
        opener.return_value.open.return_value = io.BytesIO(response().encode())
        result = client.plasmid(42)
        request = opener.return_value.open.call_args.args[0]
        assert request.full_url == (
            "https://api.developers.addgene.org/catalog/plasmid-with-sequences/42/"
        )
        assert request.method == "GET"
        assert request.headers["Authorization"] == "Token secret-example"
        assert result.catalog_id == "42"
    with pytest.raises(ValueError, match="positive integer"):
        client.plasmid(True)
    with patch("lab.suppliers.addgene.build_opener") as opener:
        opener.return_value.open.return_value = io.BytesIO(response().encode())
        with pytest.raises(ValueError, match="different plasmid"):
            client.plasmid(43)


def test_catalog_candidate_does_not_count_as_inventory():
    request, inputs = planning_case()
    missing = next(
        stock for stock in inputs["inventory"].stocks if stock.design.identity == NS + "/vector"
    )
    inventory = replace(
        inputs["inventory"],
        stocks=tuple(stock for stock in inputs["inventory"].stocks if stock != missing),
    )
    build = plan(request, **{**inputs, "inventory": inventory}, catalog=catalog(missing.design))
    assert not build.ready
    assert build.acquisitions[0].candidates[0].form is MaterialForm.BACTERIAL_STAB
    assert build.acquisitions[0].required_form is MaterialForm.DNA
    assert build.acquisitions[0].volume_ul == 1


def test_receipt_records_physical_arrival_without_claiming_sequence_verification():
    request, inputs = planning_case()
    design = inputs["system"].recipes[0].fragments[0].component
    person = Agent(identity=NS + "/person")
    doc = Document.from_snapshot(inputs["document"])
    doc.add(person)
    item = catalog(design).entries[0].item
    order = OrderReference(
        identity=NS + "/order",
        acquisition=NS + "/acquisition",
        item=item.identity,
        reference="external-order-42",
        placed_at=TIME,
    )
    receipt = Receipt(
        identity=NS + "/receipt",
        order=order,
        design=design,
        form=MaterialForm.BACTERIAL_STAB,
        received_at=TIME,
        received_by=person.ref,
        packages=1,
    )
    recorded = receipt.record(doc.freeze())
    assert recorded.resolve(receipt.implementation).built is None
    assert recorded.resolve(receipt.implementation).evidence_state is EvidenceState.RECORDED
    counted = receipt.counted_stock(identity=NS + "/counted_stock", count=1)
    Inventory(identity=NS + "/counted_inventory", stocks=(counted,)).validate(recorded)
    assert counted.count == 1 and counted.implementation == receipt.implementation
    with pytest.raises(ValueError, match="preparation"):
        receipt.stock(identity=NS + "/stock", quantity=1 * uL)
    inventory = replace(
        inputs["inventory"],
        stocks=tuple(stock for stock in inputs["inventory"].stocks if stock.design != design),
    )
    build = plan(
        request, **{**inputs, "document": recorded, "inventory": inventory}, receipts=(receipt,)
    )
    assert any(item.kind is RequirementKind.PREPARATION for item in build.requirements)
    assert not build.acquisitions

    liquid = replace(receipt, identity=NS + "/liquid_receipt", form=MaterialForm.DNA)
    stock = liquid.stock(identity=NS + "/received_stock", quantity=10 * uL)
    Inventory(identity=NS + "/received_inventory", stocks=(stock,)).validate(
        liquid.record(doc.freeze())
    )


def test_addgene_is_preferred_without_automatic_sequence_selection():
    request, _ = planning_case()
    entry = catalog(request.targets[0].design).entries[0]
    alternative = replace(
        entry, item=replace(entry.item, supplier="Example", identity=NS + "/supplier")
    )
    snapshot = Catalog(entries=(alternative, entry))
    assert snapshot.candidates(entry.design) == (entry, alternative)
