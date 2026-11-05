"""The entire recorded protocol vocabulary. No callbacks or device objects."""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any

from lab.limits import PipettingLimits
from lab.liquid_classes import HamiltonLiquidClass, LiquidClass, LiquidClassSpec
from lab.samples import Location, OutputManifest, Sample, SamplePlacement
from lab.units import number


@dataclass(frozen=True)
class Origin:
    file: str
    line: int


@dataclass(frozen=True)
class Fill:
    well: str
    material: str
    volume: Decimal


@dataclass(frozen=True)
class Resource:
    name: str
    rows: int
    columns: int
    capacity: Decimal
    dead_volume: Decimal
    fills: tuple[Fill, ...] = ()

    @property
    def wells(self) -> tuple[str, ...]:
        return tuple(
            f"{chr(65 + row)}{column + 1}"
            for row in range(self.rows)
            for column in range(self.columns)
        )


@dataclass(frozen=True)
class Transfer:
    source: Location
    destination: Location
    volume: Decimal
    origin: Origin
    liquid_class: LiquidClassSpec | None = None


@dataclass(frozen=True)
class Distribute:
    """One aspiration shared across destinations. ``air_gap`` is air, not liquid volume."""

    source: Location
    destinations: tuple[Location, ...]
    volume: Decimal
    air_gap: Decimal | None
    origin: Origin
    liquid_class: LiquidClassSpec | None = None


@dataclass(frozen=True)
class Mix:
    location: Location
    volume: Decimal
    cycles: int
    origin: Origin
    liquid_class: LiquidClassSpec | None = None


@dataclass(frozen=True)
class Wait:
    seconds: Decimal
    origin: Origin


@dataclass(frozen=True)
class Hold:
    celsius: Decimal
    seconds: Decimal


@dataclass(frozen=True)
class Thermocycle:
    resource: str
    profile: tuple[Hold, ...]
    cycles: int
    lid_celsius: Decimal | None
    origin: Origin
    block_volume: Decimal | None = None


@dataclass(frozen=True)
class SetTemperature:
    """Hold a plate's controlling module at one temperature. Liquid handling may continue."""

    resource: str
    celsius: Decimal
    origin: Origin


@dataclass(frozen=True)
class ManualInstruction:
    text: str
    origin: Origin


Step = Transfer | Distribute | Mix | Wait | Thermocycle | SetTemperature | ManualInstruction


@dataclass(frozen=True)
class RecordedProtocol:
    name: str
    description: str
    resources: tuple[Resource, ...]
    steps: tuple[Step, ...]
    samples: tuple[Sample, ...] = ()
    placements: tuple[SamplePlacement, ...] = ()
    input_sample_ids: tuple[str, ...] = ()
    output_sample_ids: tuple[str, ...] = ()

    def output_manifest(self) -> OutputManifest:
        """Project declared outputs from this snapshot using logical locations."""
        samples = {sample.id: sample for sample in self.samples}
        placements = {placement.sample_id: placement for placement in self.placements}
        return OutputManifest(
            protocol_id=self.name,
            samples=tuple(samples[sample_id] for sample_id in self.output_sample_ids),
            placements=tuple(placements[sample_id] for sample_id in self.output_sample_ids),
        )


@dataclass(frozen=True)
class Binding:
    """One exact physical location and its usable constraints, in microlitres."""

    location: Location
    physical: str
    capacity: Decimal
    dead_volume: Decimal


@dataclass(frozen=True)
class TargetPlan:
    """A target's bindings, configuration, generated source, setup notes, and pipetting heads."""

    name: str
    bindings: tuple[Binding, ...]
    configuration_json: str
    source: str | None = None
    setup: tuple[str, ...] = ()
    limits: tuple[PipettingLimits, ...] = ()


def encode(value: Any) -> Any:
    """JSON-compatible data for the small, closed vocabulary."""
    if isinstance(value, Decimal):
        return number(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (tuple, list)):
        return [encode(item) for item in value]
    if isinstance(value, dict):
        return {key: encode(item) for key, item in value.items()}
    if isinstance(
        value,
        (
            Transfer,
            Distribute,
            Mix,
            Wait,
            Thermocycle,
            SetTemperature,
            ManualInstruction,
            LiquidClass,
            HamiltonLiquidClass,
        ),
    ):
        return {
            "kind": type(value).__name__,
            **{name: encode(getattr(value, name)) for name in value.__dataclass_fields__},
        }
    if hasattr(value, "__dataclass_fields__"):
        # Do not recursively use asdict here: it would erase step discriminants.
        return {name: encode(getattr(value, name)) for name in value.__dataclass_fields__}
    return value
