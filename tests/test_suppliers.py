import io
import json
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from lab.inventory import MaterialForm
from lab.provenance import Ref
from lab.suppliers import (
    AddgeneClient,
    Catalog,
    CatalogEntry,
    SequenceSource,
    parse_plasmid,
)

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
    snapshot = catalog(Ref("https://example.org/catalog/design"))
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


def test_addgene_is_preferred_without_automatic_sequence_selection():
    entry = catalog(Ref("https://example.org/catalog/design")).entries[0]
    alternative = replace(
        entry,
        item=replace(
            entry.item, supplier="Example", identity="https://example.org/catalog/supplier"
        ),
    )
    snapshot = Catalog(entries=(alternative, entry))
    assert snapshot.candidates(entry.design) == (entry, alternative)
