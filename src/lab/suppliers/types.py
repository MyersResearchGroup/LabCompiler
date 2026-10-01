"""Immutable catalog evidence and records of externally placed purchases."""

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from lab.artifacts import canonical_json, digest, write_bundle
from lab.inventory import CountedStock, MaterialForm, Stock, StockLocation
from lab.provenance import (
    Activity,
    Agent,
    Association,
    Component,
    Document,
    DocumentSnapshot,
    EvidenceState,
    Implementation,
    Ref,
)
from lab.provenance.types import require_iri
from lab.provenance.vocabulary import LAB


def aware(value: datetime) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Record timestamps must include a timezone")


class SequenceSource(StrEnum):
    DEPOSITOR = "depositor"
    ADDGENE = "addgene"


@dataclass(frozen=True, kw_only=True)
class CatalogSequence:
    identity: str
    description: str
    elements: str
    source: SequenceSource
    complete: bool
    reported_length: int | None
    genbank_url: str | None = None

    def __post_init__(self) -> None:
        require_iri(self.identity)
        if not isinstance(self.source, SequenceSource) or type(self.complete) is not bool:
            raise TypeError("Sequence source and completeness must be explicit")
        if self.reported_length is not None and (
            type(self.reported_length) is not int or self.reported_length < 0
        ):
            raise ValueError("Reported length must be nonnegative")
        if self.genbank_url is not None:
            require_iri(self.genbank_url)


@dataclass(frozen=True, kw_only=True)
class SupplierItem:
    identity: str
    supplier: str
    catalog_id: str
    name: str
    url: str
    retrieved_at: datetime
    sequences: tuple[CatalogSequence, ...] = ()
    metadata_json: str = "{}"

    def __post_init__(self) -> None:
        require_iri(self.identity)
        require_iri(self.url)
        aware(self.retrieved_at)
        if not self.supplier.strip() or not self.catalog_id.strip():
            raise ValueError("Supplier and catalog ID are required")
        if not isinstance(self.sequences, tuple) or not all(
            isinstance(item, CatalogSequence) for item in self.sequences
        ):
            raise TypeError("Catalog sequences must be an immutable tuple")
        if len({item.identity for item in self.sequences}) != len(self.sequences):
            raise ValueError("Catalog sequence identities must be unique")
        metadata = json.loads(self.metadata_json)
        if not isinstance(metadata, dict):
            raise ValueError("Catalog metadata must be a JSON object")
        object.__setattr__(self, "metadata_json", canonical_json(metadata))


@dataclass(frozen=True, kw_only=True)
class CatalogEntry:
    """Caller-reviewed mapping; a catalog name is never a design identity.

    The caller explicitly selects a design and the supplied material form.
    Sequence candidates remain evidence, not an automatic assertion of identity.
    """

    item: SupplierItem
    design: Ref[Component]
    form: MaterialForm

    def __post_init__(self) -> None:
        if not isinstance(self.item, SupplierItem) or not isinstance(self.design, Ref):
            raise TypeError("Catalog entries need an item and a design reference")
        if not isinstance(self.form, MaterialForm):
            raise TypeError("Catalog material form must be explicit")


@dataclass(frozen=True, kw_only=True)
class Catalog:
    entries: tuple[CatalogEntry, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.entries, tuple) or not all(
            isinstance(item, CatalogEntry) for item in self.entries
        ):
            raise TypeError("Catalog entries must be an immutable tuple")
        keys = [(entry.item.identity, entry.design, entry.form) for entry in self.entries]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate catalog mapping")
        object.__setattr__(
            self,
            "entries",
            tuple(
                sorted(
                    self.entries,
                    key=lambda entry: (
                        entry.item.supplier.casefold() != "addgene",
                        entry.item.identity,
                        entry.design.identity,
                        entry.form.value,
                    ),
                )
            ),
        )

    def candidates(self, design: Ref[Component]) -> tuple[CatalogEntry, ...]:
        return tuple(entry for entry in self.entries if entry.design == design)

    @property
    def digest(self) -> str:
        return digest(self)

    def write(self, path: str | Path) -> Path:
        path = Path(path)
        write_bundle(
            path.parent, {path.name: canonical_json({"format": "lab.catalog.v1", "catalog": self})}
        )
        return path

    @classmethod
    def read(cls, path: str | Path) -> "Catalog":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("format") != "lab.catalog.v1":
            raise ValueError("Expected lab.catalog.v1")
        entries = []
        for row in data["catalog"]["entries"]:
            raw = row["item"]
            sequences = tuple(
                CatalogSequence(**{**sequence, "source": SequenceSource(sequence["source"])})
                for sequence in raw["sequences"]
            )
            item = SupplierItem(
                **{
                    **raw,
                    "retrieved_at": datetime.fromisoformat(raw["retrieved_at"]),
                    "sequences": sequences,
                }
            )
            entries.append(
                CatalogEntry(
                    item=item, design=Ref(row["design"]["identity"]), form=MaterialForm(row["form"])
                )
            )
        return cls(entries=tuple(entries))


@dataclass(frozen=True, kw_only=True)
class AcquisitionRequest:
    identity: str
    design: Ref[Component]
    required_form: MaterialForm
    volume_ul: Decimal
    candidates: tuple[CatalogEntry, ...] = ()
    count: int = 0

    def __post_init__(self) -> None:
        require_iri(self.identity)
        if (
            not isinstance(self.volume_ul, Decimal)
            or not self.volume_ul.is_finite()
            or self.volume_ul < 0
            or type(self.count) is not int
            or self.count < 0
            or (self.required_form.counted and (self.volume_ul != 0 or self.count <= 0))
            or (not self.required_form.counted and (self.volume_ul <= 0 or self.count != 0))
        ):
            raise ValueError("Acquisition requires a positive quantity of the declared form")
        if not isinstance(self.candidates, tuple) or any(
            not isinstance(item, CatalogEntry) or item.design != self.design
            for item in self.candidates
        ):
            raise ValueError("Acquisition candidates must refer to the requested design")


@dataclass(frozen=True, kw_only=True)
class QuoteRecord:
    identity: str
    acquisition: str
    item: str
    reference: str
    issued_at: datetime
    attachment: str | None = None

    def __post_init__(self) -> None:
        for value in (self.identity, self.acquisition, self.item):
            require_iri(value)
        aware(self.issued_at)
        if not self.reference.strip():
            raise ValueError("Quote reference is required")
        if self.attachment is not None:
            require_iri(self.attachment)


@dataclass(frozen=True, kw_only=True)
class OrderReference:
    identity: str
    acquisition: str
    item: str
    reference: str
    placed_at: datetime
    quote: str | None = None
    tracking_url: str | None = None

    def __post_init__(self) -> None:
        for value in (self.identity, self.acquisition, self.item):
            require_iri(value)
        aware(self.placed_at)
        if not self.reference.strip():
            raise ValueError("External order reference is required")
        for optional in (self.quote, self.tracking_url):
            if optional is not None:
                require_iri(optional)


@dataclass(frozen=True, kw_only=True)
class Receipt:
    """Observation of receipt, not sequence verification or DNA preparation.

    Counts describe supplied packages. A liquid aliquot enters inventory only
    through an explicit measured quantity and its own implementation identity.
    """

    identity: str
    order: OrderReference
    design: Ref[Component]
    form: MaterialForm
    received_at: datetime
    received_by: Ref[Agent]
    packages: int
    lot: str | None = None

    def __post_init__(self) -> None:
        require_iri(self.identity)
        aware(self.received_at)
        if not isinstance(self.order, OrderReference) or not isinstance(self.form, MaterialForm):
            raise TypeError("A receipt needs an order and an explicit material form")
        if not isinstance(self.design, Ref) or not isinstance(self.received_by, Ref):
            raise TypeError("Receipt design and receiving agent must be references")
        if type(self.packages) is not int or self.packages < 1:
            raise ValueError("Receipt package count must be positive")
        if self.received_at < self.order.placed_at:
            raise ValueError("Receipt precedes its order")

    @property
    def implementation(self) -> Ref[Implementation]:
        return Ref(self.identity + "/material")

    def record(self, document: DocumentSnapshot) -> DocumentSnapshot:
        document.resolve(self.design)
        document.resolve(self.received_by)
        activity = Activity(
            identity=self.identity,
            types=(LAB + "receipt",),
            evidence_state=EvidenceState.RECORDED,
            end_time=self.received_at,
            association=(Association(agent=self.received_by),),
            description=f"Received {self.packages} package(s); order {self.order.reference}; "
            f"item {self.order.item}; form {self.form.value}; lot {self.lot or 'unrecorded'}.",
        )
        material = Implementation(
            identity=self.implementation.identity,
            derived_from=(self.design,),
            generated_by=(activity.ref,),
            evidence_state=EvidenceState.RECORDED,
        )
        result = Document.from_snapshot(document)
        result.add(activity, material)
        return result.freeze()

    def counted_stock(
        self, *, identity: str, count: int, location: StockLocation | None = None
    ) -> CountedStock:
        """Record an explicitly assessed count; package count is not a material count."""
        return CountedStock(
            identity=identity,
            implementation=self.implementation,
            design=self.design,
            form=self.form,
            count=count,
            location=location,
            supplier_item=self.order.item,
        )

    def stock(
        self,
        *,
        identity: str,
        quantity: object,
        location: StockLocation | None = None,
        concentration: object = None,
    ) -> Stock:
        if self.form.counted:
            raise ValueError(
                "A bacterial stab requires explicit preparation before liquid inventory"
            )
        if self.packages != 1:
            raise ValueError("Record each package separately before measuring an inventory aliquot")
        return Stock(
            identity=identity,
            implementation=self.implementation,
            design=self.design,
            form=self.form,
            quantity=quantity,
            concentration=concentration,
            location=location,
            supplier_item=self.order.item,
        )
