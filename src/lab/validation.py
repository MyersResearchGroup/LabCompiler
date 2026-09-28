"""Ordered accounting over the exact resources in a recorded protocol."""

from decimal import Decimal

from lab.inventory import MaterialForm
from lab.model import RecordedProtocol
from lab.operations import (
    Distribute,
    ExternalPreparation,
    Mix,
    SetTemperature,
    Step,
    Thermocycle,
    Transfer,
)
from lab.samples import Location
from lab.target import Binding
from lab.units import number


class CompileError(ValueError):
    """A protocol or hardware binding that cannot be compiled."""


def step_error(index: int, step: Step, message: str) -> CompileError:
    return CompileError(f"{step.origin.file}:{step.origin.line}\nStep {index + 1}: {message}")


def validate(protocol: RecordedProtocol, bindings: tuple[Binding, ...]) -> dict[Location, Decimal]:
    validate_samples(protocol)
    count_trace(protocol)
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
    samples = {sample.id: sample for sample in protocol.samples}
    counted = {p.location for p in protocol.placements if samples[p.sample_id].count is not None}
    if any(volumes[location] for location in counted):
        raise CompileError("Counted materials cannot be loaded as liquid volumes")
    states = [volumes.copy()]
    for index, step in enumerate(protocol.steps):
        if isinstance(step, (Transfer, Mix)):
            source = step.source if isinstance(step, Transfer) else step.location
            if source in counted:
                raise step_error(
                    index,
                    step,
                    "Counted material requires explicit external preparation before pipetting",
                )
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
        elif isinstance(step, Distribute):
            if step.source in counted:
                raise step_error(index, step, "Counted material cannot be pipetted")
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
        elif isinstance(step, ExternalPreparation):
            for port in step.inputs:
                if port.location not in volumes:
                    raise step_error(index, step, "Unknown external input location")
                if port.volume_ul > max(Decimal(0), volumes[port.location] - dead[port.location]):
                    raise step_error(
                        index, step, "External preparation exceeds available input volume"
                    )
                volumes[port.location] -= port.volume_ul
            for port in step.outputs:
                if port.location not in volumes or volumes[port.location]:
                    raise step_error(
                        index, step, "External output must occupy a known empty location"
                    )
                if port.volume_ul > capacities[port.location]:
                    raise step_error(index, step, "External output exceeds container capacity")
                volumes[port.location] = port.volume_ul
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


def count_trace(protocol: RecordedProtocol) -> tuple[dict[Location, int], ...]:
    """Count whole materials separately; pipetting never consumes those counts."""
    samples = {sample.id: sample for sample in protocol.samples}
    at = {place.location: samples[place.sample_id] for place in protocol.placements}
    counted = {location: sample for location, sample in at.items() if sample.count is not None}
    counts = {
        location: int(sample.count or 0) if sample.id in protocol.input_sample_ids else 0
        for location, sample in counted.items()
    }
    states = [counts.copy()]
    for index, step in enumerate(protocol.steps):
        if isinstance(step, ExternalPreparation):
            for port in (*step.inputs, *step.outputs):
                if port.location not in at or (port.count > 0) != (port.location in counted):
                    raise step_error(
                        index, step, "External ports must match declared material forms"
                    )
            for port in step.inputs:
                if port.count:
                    if port.count > counts[port.location]:
                        raise step_error(
                            index, step, "External preparation exceeds available material count"
                        )
                    counts[port.location] -= port.count
            for port in step.outputs:
                if port.count:
                    if counts[port.location]:
                        raise step_error(
                            index, step, "External counted output requires an empty location"
                        )
                    counts[port.location] = port.count
        elif isinstance(step, (Transfer, Distribute)):
            destinations = (step.destination,) if isinstance(step, Transfer) else step.destinations
            for destination in destinations:
                if destination in counted:
                    if (
                        counted[destination].form is not MaterialForm.PLATED_SAMPLE
                        or counts[destination]
                    ):
                        raise step_error(
                            index, step, "A deposition requires an empty plated-sample location"
                        )
                    counts[destination] = 1
        states.append(counts.copy())
    for location, sample in counted.items():
        if sample.id in protocol.output_sample_ids and counts[location] != sample.count:
            raise CompileError(
                "Declared output count does not match the protocol's material balance"
            )
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
