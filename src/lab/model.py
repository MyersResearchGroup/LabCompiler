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


class TipPolicy(Enum):
    """When a liquid-handling tip is replaced."""

    FRESH_PER_STEP = "fresh_per_step"
    """A new tip for every transfer, distribution, or mix."""
    REUSE_PER_SOURCE = "reuse_per_source"
    """Keep a tip while consecutive steps draw from the same source with the same class."""
    REUSE_WHEN_CONTAINED = "reuse_when_contained"
    """Keep a tip while every destination already contains only what the source contains."""


@dataclass(frozen=True)
class Fill:
    """Initial contents of a well. ``components`` names what the material is made of."""

    well: str
    material: str
    volume: Decimal
    components: tuple[str, ...] = ()


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
class MultiTransfer:
    """Equal-volume lanes moved together; lane ``i`` goes ``sources[i]`` to ``destinations[i]``.

    Sources may repeat, as when one trough feeds a column. A head with enough channels runs
    the lanes in one stroke; otherwise a target runs them one after another.
    """

    sources: tuple[Location, ...]
    destinations: tuple[Location, ...]
    volume: Decimal
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
class MultiMix:
    """The same mixing cycles at several wells together."""

    locations: tuple[Location, ...]
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


Step = (
    Transfer
    | Distribute
    | MultiTransfer
    | Mix
    | MultiMix
    | Wait
    | Thermocycle
    | SetTemperature
    | ManualInstruction
)
LiquidStep = Transfer | Distribute | MultiTransfer | Mix | MultiMix
LIQUID_STEPS = (Transfer, Distribute, MultiTransfer, Mix, MultiMix)


def lanes(step: LiquidStep) -> tuple[tuple[Location, Location | None], ...]:
    """(source, destination) per lane; a mix has no destination, a distribution one source."""
    if isinstance(step, Transfer):
        return ((step.source, step.destination),)
    if isinstance(step, Distribute):
        return tuple((step.source, destination) for destination in step.destinations)
    if isinstance(step, MultiTransfer):
        return tuple(zip(step.sources, step.destinations, strict=True))
    if isinstance(step, Mix):
        return ((step.location, None),)
    return tuple((location, None) for location in step.locations)


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
    tip_policy: TipPolicy = TipPolicy.FRESH_PER_STEP

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
            MultiTransfer,
            Mix,
            MultiMix,
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
