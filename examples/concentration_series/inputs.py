"""Protocol configuration for preparing an LMSF destination layout.

The models mirror config.toml. The CSV itself is read by ``lab.plugins.lmsf`` into a
``PlateDesign``; ``sources()`` resolves the configured stocks and cultures into the Lab
objects that ``lab.plates.assemble`` consumes.
"""

from dataclasses import dataclass
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from lab import HamiltonLiquidClass, LiquidClass, Protocol, Well, uL
from lab.config import (
    Concentration,
    ConfigModel,
    LiquidClassConfig,
    MixingConfig,
    NonemptyString,
    NonnegativeVolume,
    PortableLiquidClassConfig,
    PreparationConfig,
    Volume,
    VolumeFraction,
)
from lab.deck import Deck
from lab.liquid_classes import LiquidClassSpec
from lab.plates import Mixing, Settings, Stock, Strategy
from lab.plugins.lmsf import material_name
from lab.units import dilution_volume, magnitude

WellName = Annotated[str, Field(strict=True, pattern=r"^[A-H](?:[1-9]|1[0-2])$")]


class SourceConfig(ConfigModel):
    well: WellName
    volume: Volume
    liquid_class: NonemptyString  # A key in ProtocolConfig.liquid_classes.


class NamedSourceConfig(SourceConfig):
    name: NonemptyString


class MediumConfig(NamedSourceConfig):
    """The base medium in reservoir A1; ``diluent_volume`` loads A2 for serial dilutions."""

    well: Literal["A1"]
    dead_volume: NonnegativeVolume
    diluent_volume: Volume | None = None


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
    preparation: PreparationConfig = PreparationConfig()
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

    def settings(self, liquid_classes: dict[str, LiquidClassSpec]) -> Settings:
        """Preparation settings for ``lab.plates.assemble`` from this configuration."""
        return Settings(
            final_volume=self.final_volume,
            inoculum_volume=self.inoculum_volume,
            vehicle_fraction=self.vehicle_fraction,
            strategy=Strategy(self.preparation.strategy),
            concentrate=self.preparation.concentrate,
            serial_min_steps=self.preparation.serial_min_steps,
            premix_min_group=self.preparation.premix_min_group,
            premix_overage=self.preparation.premix_overage,
            source_mix=Mixing(
                volume=self.source_mix.volume,
                cycles=self.source_mix.cycles,
                liquid_class=liquid_classes[self.source_mix.liquid_class],
            ),
            destination_mix=Mixing(
                volume=self.destination_mix.volume,
                cycles=self.destination_mix.cycles,
                liquid_class=liquid_classes[self.destination_mix.liquid_class],
            ),
        )


def liquid_classes(config: ProtocolConfig) -> dict[str, LiquidClassSpec]:
    """Resolve named configuration entries into the Lab liquid classes used by the recipe."""
    return {
        name: LiquidClass(
            name,
            aspiration_flow_rate=spec.aspiration_flow_rate,
            dispense_flow_rate=spec.dispense_flow_rate,
        )
        if isinstance(spec, PortableLiquidClassConfig)
        else HamiltonLiquidClass(spec.name)
        for name, spec in config.liquid_classes.items()
    }


@dataclass(frozen=True, slots=True, kw_only=True)
class Sources:
    """Declared and loaded sources, ready for ``lab.plates.assemble``."""

    medium: Well
    diluent: Well | None
    vehicle: Well
    waste: Well
    stocks: tuple[Stock, ...]
    inocula: dict[str, Well]
    culture_wells: dict[str, CultureConfig]


def sources(p: Protocol, config: ProtocolConfig, classes: dict[str, LiquidClassSpec]) -> Sources:
    """Declare the reservoirs and load every configured source into ``p``."""
    reagents = p.plate(
        "reagents", shape=(1, 12), capacity=15000 * uL, dead_volume=config.source_dead_volume
    )
    media = p.plate(
        "medium", shape=(1, 2), capacity=15000 * uL, dead_volume=config.medium.dead_volume
    )
    p.load(media["A1"], config.medium.name, volume=config.medium.volume)
    diluent = None
    if config.medium.diluent_volume is not None:
        p.load(media["A2"], config.medium.name, volume=config.medium.diluent_volume)
        diluent = media["A2"]
    waste = p.container("waste", capacity=15000 * uL)
    p.load(reagents[config.vehicle.well], config.vehicle.name, volume=config.vehicle.volume)
    stocks = []
    for stock in config.stocks:
        p.load(
            reagents[stock.well],
            f"{stock.solute}: {stock.concentration}; "
            f"{config.vehicle.name} {stock.vehicle_fraction} v/v",
            volume=stock.volume,
        )
        stocks.append(
            Stock(
                solute=stock.solute,
                concentration=stock.concentration,
                source=reagents[stock.well],
                max_target=stock.max_target,
                vehicle_fraction=stock.vehicle_fraction,
                liquid_class=classes[stock.liquid_class],
            )
        )
    inocula: dict[str, Well] = {}
    culture_wells: dict[str, CultureConfig] = {}
    for culture in config.cultures:
        material = material_name(culture.strain, culture.plasmid)
        p.load(reagents[culture.well], material, volume=culture.volume)
        inocula[material] = reagents[culture.well]
        culture_wells[material] = culture
    return Sources(
        medium=media["A1"],
        diluent=diluent,
        vehicle=reagents[config.vehicle.well],
        waste=waste,
        stocks=tuple(stocks),
        inocula=inocula,
        culture_wells=culture_wells,
    )
