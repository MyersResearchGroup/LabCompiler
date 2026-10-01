"""Explicit material assertions and immutable inventory snapshots."""

import json
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from lab.artifacts import canonical_json, digest, write_bundle
from lab.provenance import Component, DocumentSnapshot, EvidenceState, Implementation, Ref
from lab.provenance.types import require_iri
from lab.units import magnitude, uL, units


class MaterialForm(StrEnum):
    DNA = "dna"
    COMPETENT_CELLS = "competent_cells"
    CULTURE = "culture"
    BACTERIAL_STAB = "bacterial_stab"
    PLATED_SAMPLE = "plated_sample"
    REAGENT = "reagent"

    @property
    def counted(self) -> bool:
        return self in (MaterialForm.BACTERIAL_STAB, MaterialForm.PLATED_SAMPLE)


@dataclass(frozen=True, slots=True)
class StockLocation:
    container: str
    position: str

    def __post_init__(self) -> None:
        if not self.container.strip() or not self.position.strip():
            raise ValueError("Stock locations need a container and position")


@dataclass(frozen=True, kw_only=True, init=False)
class Stock:
    identity: str
    implementation: Ref[Implementation]
    design: Ref[Component]
    form: MaterialForm
    quantity_ul: Decimal
    concentration_ng_ul: Decimal | None
    location: StockLocation | None
    supplier_item: str | None

    def __init__(
        self,
        *,
        identity: str,
        implementation: Ref[Implementation],
        design: Ref[Component],
        form: MaterialForm,
        quantity: object,
        concentration: object = None,
        location: StockLocation | None = None,
        supplier_item: str | None = None,
    ) -> None:
        require_iri(identity)
        if not isinstance(implementation, Ref) or not isinstance(design, Ref):
            raise TypeError("Stock implementation and design must be references")
        if not isinstance(form, MaterialForm):
            raise TypeError("Pass a MaterialForm")
        if form.counted:
            raise ValueError("Use CountedStock or counted Receipts, not liquid-volume stocks")
        if location is not None and not isinstance(location, StockLocation):
            raise TypeError("Pass a StockLocation")
        if supplier_item is not None:
            require_iri(supplier_item)
        values = {
            "identity": identity,
            "implementation": implementation,
            "design": design,
            "form": form,
            "quantity_ul": magnitude(quantity, "microliter", positive=False),
            "concentration_ng_ul": None
            if concentration is None
            else magnitude(concentration, "nanogram/microliter"),
            "location": location,
            "supplier_item": supplier_item,
        }
        for name, value in values.items():
            object.__setattr__(self, name, value)

    @property
    def amount(self) -> Decimal:
        return self.quantity_ul


@dataclass(frozen=True, kw_only=True)
class CountedStock:
    """Available whole material units, without an inferred liquid volume."""

    identity: str
    implementation: Ref[Implementation]
    design: Ref[Component]
    form: MaterialForm
    count: int
    location: StockLocation | None = None
    supplier_item: str | None = None

    def __post_init__(self) -> None:
        require_iri(self.identity)
        if not isinstance(self.implementation, Ref) or not isinstance(self.design, Ref):
            raise TypeError("Stock implementation and design must be references")
        if not isinstance(self.form, MaterialForm) or not self.form.counted:
            raise ValueError("Counted stocks require a counted material form")
        if type(self.count) is not int or self.count < 0:
            raise ValueError("Available count must be a nonnegative integer")
        if self.location is not None and not isinstance(self.location, StockLocation):
            raise TypeError("Pass a StockLocation")
        if self.supplier_item is not None:
            require_iri(self.supplier_item)

    @property
    def amount(self) -> Decimal:
        return Decimal(self.count)


@dataclass(frozen=True, kw_only=True)
class Inventory:
    """Available amounts, never a live database or an automatically depleted ledger.

    ``design`` is an explicit inventory assertion. It does not mean the stock's
    sequence was verified. Liquid volumes and whole-unit counts are distinct.
    """

    identity: str
    stocks: tuple[Stock | CountedStock, ...] = ()

    def __post_init__(self) -> None:
        require_iri(self.identity)
        if not isinstance(self.stocks, tuple) or not all(
            isinstance(stock, (Stock, CountedStock)) for stock in self.stocks
        ):
            raise TypeError("Inventory stocks must be a tuple of Stock objects")
        if len({stock.identity for stock in self.stocks}) != len(self.stocks):
            raise ValueError("Stock identities must be unique")
        if len({stock.implementation.identity for stock in self.stocks}) != len(self.stocks):
            raise ValueError(
                "Each stock needs its own implementation; "
                "duplicate lots would double-count material"
            )
        occupied = [stock.location for stock in self.stocks if stock.location is not None]
        if len(set(occupied)) != len(occupied):
            raise ValueError("Inventory locations must be unique")
        object.__setattr__(
            self, "stocks", tuple(sorted(self.stocks, key=lambda stock: stock.identity))
        )

    def validate(self, document: DocumentSnapshot) -> None:
        for stock in self.stocks:
            implementation = document.get(stock.implementation.identity, Implementation)
            document.get(stock.design.identity, Component)
            if implementation.evidence_state is not EvidenceState.RECORDED:
                raise ValueError(
                    f"Inventory stock {stock.identity} must reference a recorded implementation"
                )
            if implementation.built is not None and implementation.built != stock.design:
                raise ValueError(f"Stock {stock.identity} conflicts with its realized design")
            if implementation.built is None and stock.design not in implementation.derived_from:
                raise ValueError(
                    f"Stock {stock.identity} must identify its intended design in provenance"
                )

    @property
    def digest(self) -> str:
        return digest(self)

    def write(self, path: str | Path) -> Path:
        path = Path(path)
        write_bundle(
            path.parent,
            {path.name: canonical_json({"format": "lab.inventory.v1", "inventory": self})},
        )
        return path

    @classmethod
    def read(cls, path: str | Path, *, document: DocumentSnapshot) -> "Inventory":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("format") != "lab.inventory.v1":
            raise ValueError("Expected lab.inventory.v1")
        item = data["inventory"]
        stocks = tuple(
            CountedStock(
                identity=row["identity"],
                implementation=Ref(row["implementation"]["identity"]),
                design=Ref(row["design"]["identity"]),
                form=MaterialForm(row["form"]),
                count=row["count"],
                location=None if row["location"] is None else StockLocation(**row["location"]),
                supplier_item=row["supplier_item"],
            )
            if MaterialForm(row["form"]).counted
            else Stock(
                identity=row["identity"],
                implementation=Ref(row["implementation"]["identity"]),
                design=Ref(row["design"]["identity"]),
                form=MaterialForm(row["form"]),
                quantity=Decimal(row["quantity_ul"]) * uL,
                concentration=None
                if row["concentration_ng_ul"] is None
                else Decimal(row["concentration_ng_ul"]) * units.nanogram / uL,
                location=None if row["location"] is None else StockLocation(**row["location"]),
                supplier_item=row["supplier_item"],
            )
            for row in item["stocks"]
        )
        result = cls(identity=item["identity"], stocks=stocks)
        result.validate(document)
        return result
