"""Optional Pydantic models and fields for protocol input files.

Install ``lab-compiler[config]`` to use these types. Quantities become Decimal-
backed Pint objects in Python and unit-bearing strings in JSON. ConfigModel
rejects unknown fields and freezes attribute assignment. Protocol operations
continue to consume Lab objects; configuration models describe their inputs.
"""

from decimal import Decimal
from functools import partial
from typing import Annotated, Literal

from pint import PintError, Unit
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    WithJsonSchema,
    field_validator,
)

from lab.liquid_classes import HamiltonLiquidClass
from lab.plates.design import PlateDesign, well_target
from lab.units import Quantity, parse_quantity, units

# All quantities are finite. Concentrations can be molar or mass/volume;
# conversion between those dimensions requires information this module cannot infer.
_CONCENTRATION_UNITS = ("mole/liter", "gram/liter")
_QuantityText = Annotated[
    Quantity, PlainSerializer(str, return_type=str), WithJsonSchema({"type": "string"})
]
Volume = Annotated[_QuantityText, BeforeValidator(partial(parse_quantity, unit="microliter"))]
NonnegativeVolume = Annotated[
    _QuantityText, BeforeValidator(partial(parse_quantity, unit="microliter", positive=False))
]
FlowRate = Annotated[
    _QuantityText, BeforeValidator(partial(parse_quantity, unit="microliter/second"))
]
Concentration = Annotated[
    _QuantityText,
    BeforeValidator(partial(parse_quantity, unit=_CONCENTRATION_UNITS, positive=False)),
]
VolumeFraction = Annotated[Decimal, Field(ge=0, le=1, allow_inf_nan=False)]
NonnegativeDecimal = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
NonemptyString = Annotated[str, Field(strict=True, min_length=1, pattern=r"\S")]


def _concentration_unit(value: str | Unit) -> Unit:
    """Validate a separate units column without changing the numeric column's scale."""
    try:
        unit = units.Unit(value)
        parse_quantity(1 * unit, unit=_CONCENTRATION_UNITS)
        return unit
    except (PintError, TypeError) as exc:
        raise ValueError(str(exc)) from exc


ConcentrationUnit = Annotated[
    Unit,
    BeforeValidator(_concentration_unit),
    PlainSerializer(str, return_type=str),
    WithJsonSchema({"type": "string"}),
]


class ConfigModel(BaseModel):
    """Base for explicit input schemas using Lab's quantity fields.

    Parse at the application boundary with model_validate(); serialize with
    model_dump_json(). Nested models retain their fields and named references.
    Like Pydantic's frozen models, contained dictionaries are not deeply frozen.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True, frozen=True)


class PortableLiquidClassConfig(ConfigModel):
    """Portable aspiration/dispense rates, keyed by a name in the caller's config."""

    kind: Literal["portable"]
    aspiration_flow_rate: FlowRate
    dispense_flow_rate: FlowRate


class HamiltonLiquidClassConfig(ConfigModel):
    """Native STAR symbol; the installed catalogue is checked during compilation."""

    kind: Literal["hamilton"]
    name: NonemptyString

    @field_validator("name")
    @classmethod
    def public_symbol(cls, name: str) -> str:
        HamiltonLiquidClass(name)
        return name


LiquidClassConfig = Annotated[
    PortableLiquidClassConfig | HamiltonLiquidClassConfig, Field(discriminator="kind")
]


class MixingConfig(ConfigModel):
    """Mixing volume, positive integer cycles, and a caller-defined liquid-class name."""

    volume: Volume
    cycles: Annotated[int, Field(strict=True, gt=0)]
    liquid_class: NonemptyString


WellName = Annotated[str, Field(strict=True, pattern=r"^[A-Z](?:[1-9]|[1-9][0-9])$")]


class WellTargetConfig(ConfigModel):
    """One well of a plate design: an optional material and solute concentrations."""

    well: WellName
    material: NonemptyString | None = None
    concentrations: dict[NonemptyString, Concentration]


class PlateDesignConfig(ConfigModel):
    """A plate design as JSON or TOML; ``to_design()`` builds the Lab object."""

    name: NonemptyString
    shape: tuple[Annotated[int, Field(ge=1, le=26)], Annotated[int, Field(ge=1)]] = (8, 12)
    final_volume: Volume
    wells: Annotated[tuple[WellTargetConfig, ...], Field(min_length=1)]

    def to_design(self) -> PlateDesign:
        return PlateDesign.build(
            self.name,
            shape=self.shape,
            final_volume=self.final_volume,
            wells=(
                well_target(w.well, material=w.material, concentrations=w.concentrations)
                for w in self.wells
            ),
        )


class PreparationConfig(ConfigModel):
    """How a design is turned into operations; mirrors ``lab.plates.recipe.Settings``."""

    strategy: Literal["direct", "serial", "auto"] = "auto"
    concentrate: Annotated[Decimal, Field(ge=1, allow_inf_nan=False)] = Decimal(2)
    serial_min_steps: Annotated[int, Field(strict=True, ge=2)] = 4
    premix_min_group: Annotated[int, Field(strict=True, ge=2)] | None = None
    premix_overage: Annotated[Decimal, Field(ge=1, allow_inf_nan=False)] = Decimal("1.1")
    warn_below: Volume | None = None
    resolution: Volume | None = None
    tip_policy: Literal["fresh_per_step", "reuse_per_source", "reuse_when_contained"] = (
        "fresh_per_step"
    )
