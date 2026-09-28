"""Explicit, read-only Addgene catalog retrieval.

Contract: https://docs.developers.addgene.org/docs/schema/ (e285fff01c).
Credentials stay on the client and are never included in snapshots. Catalog
access requires an approved token with the appropriate retrieve scope.
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import Message
from typing import IO
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from lab.artifacts import canonical_json
from lab.suppliers.types import CatalogSequence, SequenceSource, SupplierItem

BASE_URL = "https://api.developers.addgene.org"


class _NoRedirect(HTTPRedirectHandler):
    # Never forward a credential to a catalog redirect destination.
    def redirect_request(
        self, req: Request, fp: IO[bytes], code: int, msg: str, headers: Message, newurl: str
    ) -> None:
        return None


def parse_plasmid(payload: str, *, retrieved_at: datetime) -> SupplierItem:
    """Parse a saved retrieve response; no network or automatic sequence choice."""
    data = json.loads(payload)
    if not isinstance(data, dict) or type(data.get("id")) is not int or data["id"] < 1:
        raise ValueError("Expected an Addgene plasmid retrieve response")
    plasmid_id = data["id"]
    identity = f"https://www.addgene.org/{plasmid_id}/"
    sequences = []
    groups = data.get("sequences", {})
    if not isinstance(groups, dict):
        raise ValueError("Catalog sequences must be grouped by source and completeness")
    for key, source, complete in (
        ("public_user_full_sequences", SequenceSource.DEPOSITOR, True),
        ("public_addgene_full_sequences", SequenceSource.ADDGENE, True),
        ("public_user_partial_sequences", SequenceSource.DEPOSITOR, False),
        ("public_addgene_partial_sequences", SequenceSource.ADDGENE, False),
    ):
        for row in groups.get(key, []):
            if type(row["sequence_id"]) is not int or not isinstance(row["sequence"], str):
                raise ValueError("Invalid catalog sequence")
            sequences.append(
                CatalogSequence(
                    identity=identity + f"sequence/{row['sequence_id']}",
                    description=row["sequence_description"],
                    elements=row["sequence"],
                    source=source,
                    complete=complete,
                    reported_length=row["length"],
                    genbank_url=row.get("genbank_api_url") or row.get("genbank_url") or None,
                )
            )
    return SupplierItem(
        identity=identity,
        supplier="Addgene",
        catalog_id=str(plasmid_id),
        name=data["name"],
        url=data["url"],
        retrieved_at=retrieved_at,
        sequences=tuple(sequences),
        metadata_json=canonical_json(data),
    )


@dataclass(frozen=True, kw_only=True)
class AddgeneClient:
    token: str = field(repr=False)
    timeout: float = 30

    def __post_init__(self) -> None:
        if not self.token or any(character.isspace() for character in self.token):
            raise ValueError("Pass a nonempty Addgene token without whitespace")
        if not 0 < self.timeout <= 120:
            raise ValueError("Timeout must be between zero and 120 seconds")

    def plasmid(self, plasmid_id: int, *, include_sequences: bool = True) -> SupplierItem:
        if type(plasmid_id) is not int or plasmid_id < 1:
            raise ValueError("Plasmid ID must be a positive integer")
        endpoint = "plasmid-with-sequences" if include_sequences else "plasmid"
        request = Request(
            f"{BASE_URL}/catalog/{endpoint}/{plasmid_id}/",
            headers={"Authorization": f"Token {self.token}", "Accept": "application/json"},
            method="GET",
        )
        try:
            with build_opener(_NoRedirect()).open(request, timeout=self.timeout) as response:
                payload = response.read(16_000_001)
        except HTTPError as error:
            raise ValueError(f"Addgene catalog returned HTTP {error.code}") from None
        if len(payload) > 16_000_000:
            raise ValueError("Catalog response exceeds 16 MB")
        result = parse_plasmid(payload.decode("utf-8"), retrieved_at=datetime.now(UTC))
        if result.catalog_id != str(plasmid_id):
            raise ValueError("Catalog returned a different plasmid ID")
        return result
