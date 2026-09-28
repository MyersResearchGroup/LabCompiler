"""A mutable builder with explicit ownership; ordinary Python composes steps."""

import inspect
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from lab.model import RecordedProtocol
from lab.operations import (
    Distribute,
    ExternalPreparation,
    Fill,
    Hold,
    ManualInstruction,
    MaterialPort,
    Mix,
    Origin,
    Resource,
    SetTemperature,
    Step,
    Thermocycle,
    Transfer,
    Wait,
)
from lab.samples import Location, Sample, SamplePlacement
from lab.units import magnitude, temperature


@dataclass(frozen=True)
class Well:
    resource: str
    name: str
    _owner: object = field(repr=False)


@dataclass(frozen=True)
class Plate:
    name: str
    shape: tuple[int, int]
    _owner: object = field(repr=False)

    def __getitem__(self, well: str) -> Well:
        if well not in self.wells:
            raise KeyError(f"{well!r} is not a well in {self.name!r}")
        return Well(self.name, well, self._owner)

    @property
    def wells(self) -> tuple[str, ...]:
        rows, columns = self.shape
        return tuple(f"{chr(65 + row)}{col + 1}" for row in range(rows) for col in range(columns))

    def row(self, row: str) -> tuple[Well, ...]:
        if len(row) != 1 or not 0 <= ord(row) - 65 < self.shape[0]:
            raise KeyError(f"Unknown row {row!r}")
        return tuple(self[f"{row}{col + 1}"] for col in range(self.shape[1]))


def _origin() -> Origin:
    frame = inspect.currentframe()
    try:
        # Find the authoring caller, including calls from notebooks.
        while frame is not None:
            if Path(frame.f_code.co_filename).resolve() != Path(__file__).resolve():
                return Origin(frame.f_code.co_filename, frame.f_lineno)
            frame = frame.f_back
    finally:
        del frame
    return Origin("<unknown>", 0)


def _text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty text")
    return value


class Protocol:
    """Record sequential work. Construction and compilation never operate hardware."""

    def __init__(self, name: str, *, description: str = "", identity: str | None = None) -> None:
        self.name = _text(name, "Protocol name")
        self.description = description
        self.identity = identity
        self._owner = object()
        self._resources: dict[str, Resource] = {}
        self._steps: list[Step] = []
        self._samples: dict[str, Sample] = {}
        self._placements: list[SamplePlacement] = []
        self._input_sample_ids: list[str] = []
        self._output_sample_ids: list[str] = []

    @property
    def steps(self) -> tuple[Step, ...]:
        return tuple(self._steps)

    def plate(
        self,
        name: str,
        *,
        shape: tuple[int, int] = (8, 12),
        capacity: Any,
        dead_volume: Any = None,
    ) -> Plate:
        """Declare empty wells with a working capacity and optional residual volume."""
        _text(name, "Resource name")
        if name in self._resources:
            raise ValueError(f"Resource {name!r} already exists")
        if len(shape) != 2 or any(type(n) is not int or n < 1 for n in shape):
            raise ValueError("Shape must contain two positive integers")
        if shape[0] > 26:
            raise ValueError("At most 26 named rows are supported")
        cap = magnitude(capacity, "microliter")
        dead = (
            magnitude(dead_volume, "microliter", positive=False)
            if dead_volume is not None
            else Decimal(0)
        )
        if dead >= cap:
            raise ValueError("Dead volume must be smaller than capacity")
        self._resources[name] = Resource(name, *shape, cap, dead)
        return Plate(name, shape, self._owner)

    def container(
        self,
        name: str,
        *,
        capacity: Any,
        contents: str | None = None,
        volume: Any = None,
        dead_volume: Any = None,
    ) -> Well:
        """Declare one logical well, optionally with explicitly loaded material."""
        if (contents is None) != (volume is None):
            raise ValueError("Initial contents and volume must be supplied together")
        if contents is not None:
            _text(contents, "Material")
            if self._steps:
                raise ValueError("Declare initial contents before recording steps")
            if magnitude(volume, "microliter") > magnitude(capacity, "microliter"):
                raise ValueError(f"Initial volume exceeds the capacity of {name}")
        well = self.plate(name, shape=(1, 1), capacity=capacity, dead_volume=dead_volume)["A1"]
        if contents is not None:
            self.load(well, contents, volume=volume)
        return well

    def _location(self, well: Well) -> Location:
        if not isinstance(well, Well) or well._owner is not self._owner:
            raise ValueError("A well must belong to this protocol")
        resource = self._resources.get(well.resource)
        if resource is None or well.name not in resource.wells:
            raise ValueError("Unknown well")
        return Location(well.resource, well.name)

    def external_preparation(
        self,
        *,
        procedure: str,
        instructions: str,
        inputs: tuple[MaterialPort, ...],
        outputs: tuple[MaterialPort, ...],
    ) -> None:
        """Specify operator work and its expected material balance, without executing it."""
        for port in (*inputs, *outputs):
            resource = self._resources.get(port.location.resource)
            if resource is None or port.location.well not in resource.wells:
                raise ValueError("External material ports must reference this protocol's wells")
        self._steps.append(ExternalPreparation(procedure, instructions, inputs, outputs, _origin()))

    def load(self, well: Well, material: str, *, volume: Any) -> None:
        """Declare initial contents before recording any steps; this is not a transfer."""
        if self._steps:
            raise ValueError("Declare initial contents before recording steps")
        location = self._location(well)
        resource = self._resources[location.resource]
        if any(fill.well == location.well for fill in resource.fills):
            raise ValueError(f"{location} already has initial contents")
        amount = magnitude(volume, "microliter")
        if amount > resource.capacity:
            raise ValueError(f"Initial volume exceeds the capacity of {location}")
        fill = Fill(location.well, _text(material, "Material"), amount)
        self._resources[location.resource] = replace(resource, fills=(*resource.fills, fill))

    def add_sample(
        self,
        sample: Sample,
        *,
        at: Well,
        is_input: bool = False,
        is_output: bool = False,
    ) -> None:
        """Declare identity and lineage at a logical well; volumes belong to loads and steps.

        Parents reference samples in this protocol. Imported samples retain their
        upstream identity through ``source_protocol_id`` and ``source_sample_id``.
        """
        if not isinstance(sample, Sample):
            raise TypeError("Pass a Sample.")
        location = self._location(at)
        if sample.id in self._samples:
            raise ValueError(f"Sample {sample.id!r} already exists.")
        if any(placement.location == location for placement in self._placements):
            raise ValueError(f"A sample is already declared at {location}.")
        self._samples[sample.id] = sample
        self._placements.append(SamplePlacement(sample_id=sample.id, location=location))
        if is_input:
            self._input_sample_ids.append(sample.id)
        if is_output:
            self._output_sample_ids.append(sample.id)

    def transfer(
        self, source: Well, destination: Well, *, volume: Any, destination_height: Any = None
    ) -> None:
        start, end = self._location(source), self._location(destination)
        if start == end:
            raise ValueError("Transfer source and destination must differ")
        height = (
            None
            if destination_height is None
            else magnitude(destination_height, "millimeter", positive=False)
        )
        self._steps.append(Transfer(start, end, magnitude(volume, "microliter"), _origin(), height))

    def distribute(
        self,
        source: Well,
        destinations: Iterable[Well],
        *,
        volume: Any,
        air_gap: Any = None,
    ) -> None:
        """Move the same volume from one source to each destination, using one tip."""
        start = self._location(source)
        ends = tuple(self._location(destination) for destination in destinations)
        if not ends:
            raise ValueError("Distribute needs at least one destination")
        if any(end == start for end in ends):
            raise ValueError("Distribute source and destination must differ")
        gap = magnitude(air_gap, "microliter") if air_gap is not None else None
        self._steps.append(Distribute(start, ends, magnitude(volume, "microliter"), gap, _origin()))

    def mix(self, well: Well, *, volume: Any, cycles: int) -> None:
        if type(cycles) is not int or cycles < 1:
            raise ValueError("Mix cycles must be a positive integer")
        self._steps.append(
            Mix(self._location(well), magnitude(volume, "microliter"), cycles, _origin())
        )

    def wait(self, duration: Any) -> None:
        self._steps.append(Wait(magnitude(duration, "second"), _origin()))

    def thermocycle(
        self,
        plate: Plate,
        profile: Iterable[tuple[Any, Any]],
        *,
        cycles: int = 1,
        lid_temperature: Any = None,
        block_volume: Any = None,
    ) -> None:
        """Apply (temperature, duration) holds to a whole plate, then release it.

        ``block_volume`` overrides the block's reported liquid volume for instruments
        that schedule ramps from that number. The ledger still uses the modeled volumes.
        """
        if not isinstance(plate, Plate) or plate._owner is not self._owner:
            raise ValueError("A plate must belong to this protocol")
        if plate.name not in self._resources or plate.shape != (
            self._resources[plate.name].rows,
            self._resources[plate.name].columns,
        ):
            raise ValueError("Unknown plate")
        if type(cycles) is not int or cycles < 1:
            raise ValueError("Thermal cycles must be a positive integer")
        holds = tuple(Hold(temperature(t), magnitude(d, "second")) for t, d in profile)
        if not holds:
            raise ValueError("A thermal profile needs at least one hold")
        lid = temperature(lid_temperature) if lid_temperature is not None else None
        block = magnitude(block_volume, "microliter") if block_volume is not None else None
        self._steps.append(Thermocycle(plate.name, holds, cycles, lid, _origin(), block))

    def set_temperature(self, plate: Plate, value: Any) -> None:
        """Hold a plate at one temperature without ending the hold when the call returns."""
        if not isinstance(plate, Plate) or plate._owner is not self._owner:
            raise ValueError("A plate must belong to this protocol")
        if plate.name not in self._resources or plate.shape != (
            self._resources[plate.name].rows,
            self._resources[plate.name].columns,
        ):
            raise ValueError("Unknown plate")
        self._steps.append(SetTemperature(plate.name, temperature(value), _origin()))

    def manual(self, instruction: str) -> None:
        """An explicit operator pause, with no unmodeled changes to material state."""
        self._steps.append(ManualInstruction(_text(instruction, "Instruction"), _origin()))

    def snapshot(self) -> RecordedProtocol:
        return RecordedProtocol(
            self.name,
            self.description,
            tuple(self._resources.values()),
            tuple(self._steps),
            tuple(self._samples.values()),
            tuple(self._placements),
            tuple(self._input_sample_ids),
            tuple(self._output_sample_ids),
            self.identity,
        )
