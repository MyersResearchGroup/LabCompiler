"""Read and write LMSF destination CSVs as plate designs."""

import csv
from decimal import Decimal
from io import StringIO
from typing import Annotated, Self

from pydantic import Field, ValidationError, field_validator, model_validator

from lab.config import ConcentrationUnit, ConfigModel, NonemptyString, NonnegativeDecimal
from lab.plates.design import PlateDesign, WellTarget, coordinates, well_target
from lab.units import Quantity, number, units

LAYOUT_COLUMNS = (
    "position",
    "well",
    "row",
    "column",
    "inducer1Id",
    "inducer1Concentration",
    "inducer1Units",
    "inducer2Id",
    "inducer2Concentration",
    "inducer2Units",
    "selectorId",
    "selectorConc",
    "selectorUnits",
    "strain",
    "plasmid",
)
_SHOWN_UNITS = {"mole/liter": "umol/L", "gram/liter": "ug/mL"}
_WellName = Annotated[str, Field(strict=True, pattern=r"^[A-H](?:[1-9]|1[0-2])$")]


class Condition(ConfigModel):
    """One LMSF fitness CSV record; aliases preserve the source column names."""

    position: Annotated[int, Field(ge=1, le=96)]
    well: _WellName
    row: Annotated[str, Field(pattern=r"^[A-H]$")]
    column: Annotated[int, Field(ge=1, le=12)]
    strain: NonemptyString
    plasmid: NonemptyString
    inducer1_id: NonemptyString = Field(alias="inducer1Id")
    inducer1_concentration: NonnegativeDecimal = Field(alias="inducer1Concentration")
    inducer1_units: ConcentrationUnit = Field(alias="inducer1Units")
    inducer2_id: NonemptyString = Field(alias="inducer2Id")
    inducer2_concentration: NonnegativeDecimal = Field(alias="inducer2Concentration")
    inducer2_units: ConcentrationUnit = Field(alias="inducer2Units")
    selector_id: NonemptyString = Field(alias="selectorId")
    selector_concentration: NonnegativeDecimal = Field(alias="selectorConc")
    selector_units: ConcentrationUnit = Field(alias="selectorUnits")

    @field_validator("strain", "plasmid", "inducer1_id", "inducer2_id", "selector_id")
    @classmethod
    def assigned_name(cls, value: str) -> str:
        if value == "none":
            raise ValueError("Expected a known culture or solute assignment")
        return value

    @model_validator(mode="after")
    def consistent_record(self) -> Self:
        if (
            self.well != f"{self.row}{self.column}"
            or self.position != 8 * (self.column - 1) + ord(self.row) - 64
        ):
            raise ValueError("Inconsistent position/row/column/well")
        if len({self.inducer1_id, self.inducer2_id, self.selector_id}) != 3:
            raise ValueError("Expected distinct solutes")
        return self

    def to_well_target(self) -> WellTarget:
        return well_target(
            self.well,
            material=material_name(self.strain, self.plasmid),
            concentrations={
                self.inducer1_id: self.inducer1_concentration * self.inducer1_units,
                self.inducer2_id: self.inducer2_concentration * self.inducer2_units,
                self.selector_id: self.selector_concentration * self.selector_units,
            },
        )


def material_name(strain: str, plasmid: str) -> str:
    """The design material text for one strain/plasmid pair."""
    return f"{strain} / {plasmid}"


def split_material(material: str | None) -> tuple[str, str]:
    """Recover (strain, plasmid) from ``material_name`` text."""
    if material is None:
        raise ValueError("LMSF rows need a strain and plasmid for every well")
    strain, separator, plasmid = material.partition(" / ")
    if not separator or not strain or not plasmid:
        raise ValueError(f"{material!r} is not 'strain / plasmid' text")
    return strain, plasmid


def read_layout(text: str, *, name: str, final_volume: Quantity) -> PlateDesign:
    """Parse LMSF CSV text; every row is validated as a ``Condition`` first."""
    reader = csv.DictReader(StringIO(text.lstrip("﻿"), newline=""))
    if tuple(reader.fieldnames or ()) != LAYOUT_COLUMNS:
        raise ValueError("Expected the LMSF fitness CSV schema; cytometry needs a separate recipe")
    conditions = []
    for index, record in enumerate(reader):
        try:
            conditions.append(Condition.model_validate(record))
        except ValidationError as exc:
            raise ValueError(f"CSV row {index + 2}: {exc}") from exc
    if not conditions:
        raise ValueError("The CSV has no destination rows")
    return PlateDesign.build(
        name,
        final_volume=final_volume,
        wells=(condition.to_well_target() for condition in conditions),
    )


def write_layout(design: PlateDesign, *, solute_order: tuple[str, str, str]) -> str:
    """Emit LMSF rows in position order; ``solute_order`` names inducer1, inducer2, selector."""
    if len(set(solute_order)) != 3:
        raise ValueError("solute_order names three distinct solutes")
    if design.shape != (8, 12):
        raise ValueError("LMSF layouts describe 96-well plates")
    out = StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=LAYOUT_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for target in sorted(design.wells, key=lambda w: coordinates(w.well)[::-1]):
        writer.writerow(_row(target, solute_order))
    return out.getvalue()


def _shown(target_magnitude: Decimal, unit: str) -> tuple[str, str]:
    shown = _SHOWN_UNITS[unit]
    converted: Decimal = Decimal(str((target_magnitude * units(unit)).to(shown).magnitude))
    return number(converted), shown


def _row(target: WellTarget, solute_order: tuple[str, str, str]) -> dict[str, str | int]:
    if len(target.targets) != 3:
        raise ValueError(f"{target.well}: LMSF rows carry exactly three solutes")
    strain, plasmid = split_material(target.material)
    row, column = coordinates(target.well)
    values: dict[str, str | int] = {
        "position": 8 * column + row + 1,
        "well": target.well,
        "row": target.row,
        "column": target.column,
        "strain": strain,
        "plasmid": plasmid,
    }
    columns = (
        ("inducer1Id", "inducer1Concentration", "inducer1Units"),
        ("inducer2Id", "inducer2Concentration", "inducer2Units"),
        ("selectorId", "selectorConc", "selectorUnits"),
    )
    for (id_key, value_key, unit_key), solute in zip(columns, solute_order, strict=True):
        found = target.concentration(solute)
        if found is None:
            raise ValueError(f"{target.well}: no target for {solute}")
        shown, unit = _shown(found.magnitude, found.unit)
        values[id_key], values[value_key], values[unit_key] = solute, shown, unit
    return values
