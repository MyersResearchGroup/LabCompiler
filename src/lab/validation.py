"""Ordered accounting over the exact resources in a recorded protocol."""

from decimal import Decimal

from lab.model import (
    Binding,
    Distribute,
    Mix,
    MultiMix,
    MultiTransfer,
    RecordedProtocol,
    SetTemperature,
    Step,
    Thermocycle,
    Transfer,
)
from lab.samples import Location
from lab.units import number


class CompileError(ValueError):
    """A protocol or hardware binding that cannot be compiled."""


def step_error(index: int, step: Step, message: str) -> CompileError:
    return CompileError(f"{step.origin.file}:{step.origin.line}\nStep {index + 1}: {message}")


def validate(protocol: RecordedProtocol, bindings: tuple[Binding, ...]) -> dict[Location, Decimal]:
    validate_samples(protocol)
    return volume_trace(protocol, bindings)[-1]


def validate_samples(protocol: RecordedProtocol) -> None:
    """Check declarations against the recorded resources, independently of any target."""
    samples = {sample.id: sample for sample in protocol.samples}
    if len(samples) != len(protocol.samples):
        raise CompileError("Sample ids must be unique within a protocol.")
    for label, ids in (
        ("inputs", protocol.input_sample_ids),
        ("outputs", protocol.output_sample_ids),
    ):
        if len(set(ids)) != len(ids) or not set(ids) <= samples.keys():
            raise CompileError(f"Protocol {label} must reference unique declared samples.")
    placed = {placement.sample_id for placement in protocol.placements}
    if len(protocol.placements) != len(samples) or placed != samples.keys():
        raise CompileError("Every sample needs exactly one placement.")
    locations = {
        Location(resource.name, well) for resource in protocol.resources for well in resource.wells
    }
    occupied: set[Location] = set()
    for placement in protocol.placements:
        location = placement.location
        if location not in locations:
            raise CompileError(f"Unknown sample location {location}.")
        if location in occupied:
            raise CompileError(f"Different samples cannot share {location}.")
        occupied.add(location)
    for sample in protocol.samples:
        if not set(sample.parent_ids) <= samples.keys():
            raise CompileError(f"Unknown parent for sample {sample.id}.")
    pending = set(samples)
    while pending:
        ready = {
            sample_id for sample_id in pending if not set(samples[sample_id].parent_ids) & pending
        }
        if not ready:
            raise CompileError("Sample lineage must not contain cycles.")
        pending -= ready


def volume_trace(
    protocol: RecordedProtocol, bindings: tuple[Binding, ...]
) -> tuple[dict[Location, Decimal], ...]:
    """Initial state followed by the volume state after each step."""
    if not protocol.steps:
        raise CompileError("A protocol needs at least one step")
    expected = {
        Location(resource.name, well): resource
        for resource in protocol.resources
        for well in resource.wells
    }
    bound = {binding.location: binding for binding in bindings}
    if len(bound) != len(bindings) or bound.keys() != expected.keys():
        raise CompileError("Every logical well needs exactly one binding")
    physical = [binding.physical for binding in bindings]
    if len(set(physical)) != len(physical):
        raise CompileError("Different logical wells cannot share one physical well")
    capacities, dead, volumes = {}, {}, dict.fromkeys(expected, Decimal(0))
    for location, resource in expected.items():
        binding = bound[location]
        if (
            not binding.capacity.is_finite()
            or not binding.dead_volume.is_finite()
            or binding.capacity <= 0
            or not 0 <= binding.dead_volume < binding.capacity
        ):
            raise CompileError(f"Invalid physical limits for {location}")
        capacities[location] = min(resource.capacity, binding.capacity)
        dead[location] = max(resource.dead_volume, binding.dead_volume)
    for resource in protocol.resources:
        for fill in resource.fills:
            location = Location(resource.name, fill.well)
            if fill.volume > capacities[location]:
                raise CompileError(f"Initial volume exceeds the bound capacity of {location}")
            volumes[location] = fill.volume
    states = [volumes.copy()]
    for index, step in enumerate(protocol.steps):
        if isinstance(step, (Transfer, Mix)):
            source = step.source if isinstance(step, Transfer) else step.location
            if source not in volumes:
                raise step_error(index, step, f"Unknown source {source}")
            available = max(Decimal(0), volumes[source] - dead[source])
            if step.volume > available:
                raise step_error(
                    index,
                    step,
                    f"{source} needs {number(step.volume)} µL; only "
                    f"{number(available)} µL is available above its dead volume",
                )
            if isinstance(step, Transfer):
                if step.destination not in volumes:
                    raise step_error(index, step, f"Unknown destination {step.destination}")
                if volumes[step.destination] + step.volume > capacities[step.destination]:
                    raise step_error(index, step, f"Transfer overflows {step.destination}")
                volumes[source] -= step.volume
                volumes[step.destination] += step.volume
        elif isinstance(step, (MultiTransfer, MultiMix)):
            sources = step.sources if isinstance(step, MultiTransfer) else step.locations
            required: dict[Location, Decimal] = {}
            for source in sources:
                if source not in volumes:
                    raise step_error(index, step, f"Unknown source {source}")
                required[source] = required.get(source, Decimal(0)) + step.volume
            for source, total in required.items():
                available = max(Decimal(0), volumes[source] - dead[source])
                if total > available:
                    raise step_error(
                        index,
                        step,
                        f"{source} needs {number(total)} µL; only "
                        f"{number(available)} µL is available above its dead volume",
                    )
            if isinstance(step, MultiTransfer):
                for destination in step.destinations:
                    if destination not in volumes:
                        raise step_error(index, step, f"Unknown destination {destination}")
                    if volumes[destination] + step.volume > capacities[destination]:
                        raise step_error(index, step, f"Transfer overflows {destination}")
                for source, destination in zip(step.sources, step.destinations, strict=True):
                    volumes[source] -= step.volume
                    volumes[destination] += step.volume
        elif isinstance(step, Distribute):
            if step.source not in volumes:
                raise step_error(index, step, f"Unknown source {step.source}")
            needed = step.volume * len(step.destinations)
            available = max(Decimal(0), volumes[step.source] - dead[step.source])
            if needed > available:
                raise step_error(
                    index,
                    step,
                    f"{step.source} needs {number(needed)} µL; only "
                    f"{number(available)} µL is available above its dead volume",
                )
            for destination in step.destinations:
                if destination not in volumes:
                    raise step_error(index, step, f"Unknown destination {destination}")
                if volumes[destination] + step.volume > capacities[destination]:
                    raise step_error(index, step, f"Distribute overflows {destination}")
            volumes[step.source] -= needed
            for destination in step.destinations:
                volumes[destination] += step.volume
        elif isinstance(step, Thermocycle):
            contents = [v for loc, v in volumes.items() if loc.resource == step.resource]
            if not contents:
                raise step_error(index, step, f"Unknown thermal resource {step.resource}")
            if not any(contents):
                raise step_error(index, step, "Cannot thermocycle an empty plate")
        elif isinstance(step, SetTemperature):
            if not any(loc.resource == step.resource for loc in volumes):
                raise step_error(index, step, f"Unknown thermal resource {step.resource}")
        states.append(volumes.copy())
    return tuple(states)


def logical_bindings(protocol: RecordedProtocol) -> tuple[Binding, ...]:
    return tuple(
        Binding(
            Location(resource.name, well),
            f"{resource.name}:{well}",
            resource.capacity,
            resource.dead_volume,
        )
        for resource in protocol.resources
        for well in resource.wells
    )
