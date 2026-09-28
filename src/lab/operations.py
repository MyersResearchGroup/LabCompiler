"""Immutable semantic operations with persistent step identities."""

from dataclasses import dataclass
from decimal import Decimal

from lab.provenance.types import require_iri
from lab.samples import Location


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


@dataclass(frozen=True, kw_only=True)
class Operation:
    identity: str | None = None


@dataclass(frozen=True)
class Transfer(Operation):
    source: Location
    destination: Location
    volume: Decimal
    origin: Origin
    destination_height_mm: Decimal | None = None

    def __post_init__(self) -> None:
        if self.destination_height_mm is not None and (
            not isinstance(self.destination_height_mm, Decimal)
            or not self.destination_height_mm.is_finite()
            or self.destination_height_mm < 0
        ):
            raise ValueError("Destination height must be a nonnegative finite Decimal")


@dataclass(frozen=True)
class Distribute(Operation):
    """Reuse one tip across destinations, splitting aspirations to fit the target.

    ``air_gap`` is air, not liquid volume.
    """

    source: Location
    destinations: tuple[Location, ...]
    volume: Decimal
    air_gap: Decimal | None
    origin: Origin


@dataclass(frozen=True)
class Mix(Operation):
    location: Location
    volume: Decimal
    cycles: int
    origin: Origin


@dataclass(frozen=True)
class Wait(Operation):
    seconds: Decimal
    origin: Origin


@dataclass(frozen=True)
class Hold:
    celsius: Decimal
    seconds: Decimal


@dataclass(frozen=True)
class Thermocycle(Operation):
    resource: str
    profile: tuple[Hold, ...]
    cycles: int
    lid_celsius: Decimal | None
    origin: Origin
    block_volume: Decimal | None = None


@dataclass(frozen=True)
class SetTemperature(Operation):
    """Hold a plate's controlling module at one temperature. Liquid handling may continue."""

    resource: str
    celsius: Decimal
    origin: Origin


@dataclass(frozen=True)
class ManualInstruction(Operation):
    text: str
    origin: Origin


@dataclass(frozen=True, kw_only=True)
class MaterialPort:
    location: Location
    volume_ul: Decimal = Decimal(0)
    count: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.location, Location):
            raise TypeError("A material port needs a logical Location")
        if (
            not isinstance(self.volume_ul, Decimal)
            or not self.volume_ul.is_finite()
            or self.volume_ul < 0
            or type(self.count) is not int
            or self.count < 0
            or (self.volume_ul > 0) == (self.count > 0)
        ):
            raise ValueError("A material port specifies either positive volume or count")


@dataclass(frozen=True)
class ExternalPreparation(Operation):
    procedure: str
    instructions: str
    inputs: tuple[MaterialPort, ...]
    outputs: tuple[MaterialPort, ...]
    origin: Origin

    def __post_init__(self) -> None:
        require_iri(self.procedure)
        if not isinstance(self.instructions, str) or not self.instructions.strip():
            raise ValueError("Supply external procedure instructions")
        for ports in (self.inputs, self.outputs):
            if (
                not isinstance(ports, tuple)
                or not ports
                or not all(isinstance(p, MaterialPort) for p in ports)
            ):
                raise TypeError(
                    "External inputs and outputs must be nonempty tuples of material ports"
                )
            if len({port.location for port in ports}) != len(ports):
                raise ValueError("Combine quantities for repeated material ports")
        if {p.location for p in self.inputs} & {p.location for p in self.outputs}:
            raise ValueError("External preparation outputs require distinct locations")


Step = (
    Transfer
    | Distribute
    | Mix
    | Wait
    | Thermocycle
    | SetTemperature
    | ManualInstruction
    | ExternalPreparation
)
