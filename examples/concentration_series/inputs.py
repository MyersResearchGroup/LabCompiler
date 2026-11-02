"""LMSF fitness CSV and protocol configuration models, followed by dose calculation.

The models mirror the CSV columns and config.toml. prepare_wells() checks the
inputs together and resolves sources and doses before protocol construction.
"""

from dataclasses import dataclass
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from lab import Quantity, uL
from lab.config import (
    Concentration,
    ConcentrationUnit,
    ConfigModel,
    LiquidClassConfig,
    MixingConfig,
    NonemptyString,
    NonnegativeDecimal,
    NonnegativeVolume,
    Volume,
    VolumeFraction,
)
from lab.deck import Deck
from lab.units import dilution_volume, magnitude

WellName = Annotated[str, Field(strict=True, pattern=r"^[A-H](?:[1-9]|1[0-2])$")]


class SourceConfig(ConfigModel):
    well: WellName
    volume: Volume
    liquid_class: NonemptyString  # A key in ProtocolConfig.liquid_classes.


class NamedSourceConfig(SourceConfig):
    name: NonemptyString


class MediumConfig(NamedSourceConfig):
    well: Literal["A1"]  # The development deck binds reservoir A1.
    dead_volume: NonnegativeVolume


class StockConfig(SourceConfig):
    solute: NonemptyString
    concentration: Concentration
    max_target: Concentration
    vehicle_fraction: VolumeFraction

    @model_validator(mode="after")
    def feasible_target(self) -> Self:
        dilution_volume(
            stock_concentration=self.concentration,
            target_concentration=self.max_target,
            final_volume=1 * uL,
        )
        return self


class CultureConfig(SourceConfig):
    strain: NonemptyString
    plasmid: NonemptyString


class ProtocolConfig(ConfigModel):
    """The preparation TOML schema; target concentrations apply after inoculation."""

    wells: Annotated[tuple[WellName, ...], Field(min_length=1)] | None = None
    final_volume: Volume
    inoculum_volume: Volume
    vehicle_fraction: VolumeFraction
    source_dead_volume: NonnegativeVolume
    solute_and_vehicle_free_medium_and_cultures: Annotated[bool, Field(strict=True)]
    liquid_classes: dict[NonemptyString, LiquidClassConfig]
    medium: MediumConfig
    vehicle: NamedSourceConfig
    stocks: tuple[StockConfig, ...]
    cultures: tuple[CultureConfig, ...]
    source_mix: MixingConfig
    destination_mix: MixingConfig
    deck: Deck

    @field_validator("solute_and_vehicle_free_medium_and_cultures")
    @classmethod
    def known_absence(cls, value: bool) -> bool:
        if not value:
            raise ValueError(
                "This recipe requires solute- and vehicle-free medium and cultures; "
                "carryover needs an explicit composition balance"
            )
        return value

    @field_validator("wells")
    @classmethod
    def distinct_wells(cls, wells: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if wells is not None and len(set(wells)) != len(wells):
            raise ValueError("Select wells without repeats")
        return wells

    @model_validator(mode="after")
    def compatible_inputs(self) -> Self:
        if self.inoculum_volume >= self.final_volume:
            raise ValueError("Inoculum volume must be less than final volume")
        if len({(c.strain, c.plasmid) for c in self.cultures}) != len(self.cultures):
            raise ValueError("Each strain/plasmid pair must have one explicit source")
        # Validate working-stock bounds once, independently of the selected CSV rows.
        for solute in {stock.solute for stock in self.stocks}:
            targets = [stock.max_target for stock in self.stocks if stock.solute == solute]
            limits = [
                magnitude(target, str(targets[0].units), positive=False) for target in targets
            ]
            if len(set(limits)) != len(limits):
                raise ValueError(f"{solute}: stock target limits must be distinct")
        sources: tuple[SourceConfig, ...] = (self.vehicle, *self.stocks, *self.cultures)
        if len({source.well for source in sources}) != len(sources):
            raise ValueError("A source well is assigned more than once")
        uses: list[tuple[str, SourceConfig | MixingConfig]] = [
            ("medium", self.medium),
            ("vehicle", self.vehicle),
            ("source_mix", self.source_mix),
            ("destination_mix", self.destination_mix),
        ]
        uses.extend((f"stocks[{i}]", stock) for i, stock in enumerate(self.stocks))
        uses.extend((f"cultures[{i}]", culture) for i, culture in enumerate(self.cultures))
        for path, source in uses:
            if source.liquid_class not in self.liquid_classes:
                raise ValueError(f"{path}.liquid_class: unknown class {source.liquid_class!r}")
        return self


class Condition(ConfigModel):
    """One fitness CSV record; aliases preserve the source column names."""

    position: Annotated[int, Field(ge=1, le=96)]
    well: WellName
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


class PlateLayout(ConfigModel):
    """Validated fitness CSV records, retaining each physical well occurrence."""

    conditions: Annotated[tuple[Condition, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def distinct_wells(self) -> Self:
        if len({condition.well for condition in self.conditions}) != len(self.conditions):
            raise ValueError("Repeated CSV destination")
        return self


@dataclass(frozen=True)
class PreparedWell:
    """A CSV condition with its source and doses resolved before recording operations."""

    well: str
    culture: CultureConfig
    concentrations: dict[str, Quantity]
    stock_volumes: tuple[tuple[StockConfig, Quantity], ...]
    vehicle_volume: Quantity
    medium_volume: Quantity


def prepare_wells(layout: PlateLayout, config: ProtocolConfig) -> list[PreparedWell]:
    """Check the two inputs together and calculate each well's additions once."""
    conditions = {condition.well: condition for condition in layout.conditions}
    selected_wells = config.wells if config.wells is not None else tuple(conditions)
    if missing := set(selected_wells) - conditions.keys():
        raise ValueError(f"Selected wells absent from CSV: {sorted(missing)}")
    cultures = {(culture.strain, culture.plasmid): culture for culture in config.cultures}
    prepared_wells = []
    for well in selected_wells:
        condition = conditions[well]
        culture = cultures.get((condition.strain, condition.plasmid))
        if culture is None:
            raise ValueError(f"{well}: no source for {condition.strain} / {condition.plasmid}")
        # The CSV model has already parsed the numbers and checked the units.
        targets = {
            condition.inducer1_id: condition.inducer1_concentration * condition.inducer1_units,
            condition.inducer2_id: condition.inducer2_concentration * condition.inducer2_units,
            condition.selector_id: condition.selector_concentration * condition.selector_units,
        }
        doses: list[tuple[StockConfig, Quantity]] = []
        for solute, target in targets.items():
            if target.magnitude == 0:
                continue
            candidates = sorted(
                (stock for stock in config.stocks if stock.solute == solute),
                key=lambda stock: magnitude(stock.max_target, str(target.units), positive=False),
            )
            selected = next((stock for stock in candidates if target <= stock.max_target), None)
            if selected is None:
                raise ValueError(f"{well}: no stock covers {solute} target {target}")
            doses.append(
                (
                    selected,
                    dilution_volume(
                        stock_concentration=selected.concentration,
                        target_concentration=target,
                        final_volume=config.final_volume,
                    ),
                )
            )
        vehicle_volume: Quantity = config.vehicle_fraction * config.final_volume - sum(
            (stock.vehicle_fraction * dose for stock, dose in doses), 0 * uL
        )
        medium_volume: Quantity = (
            config.final_volume
            - config.inoculum_volume
            - vehicle_volume
            - sum((dose for _, dose in doses), 0 * uL)
        )
        if vehicle_volume.magnitude < 0 or medium_volume.magnitude < 0:
            raise ValueError(
                f"{well}: stock/vehicle/inoculum additions exceed the final composition"
            )
        prepared_wells.append(
            PreparedWell(
                well=well,
                culture=culture,
                concentrations=targets,
                stock_volumes=tuple(doses),
                vehicle_volume=vehicle_volume,
                medium_volume=medium_volume,
            )
        )
    return prepared_wells
