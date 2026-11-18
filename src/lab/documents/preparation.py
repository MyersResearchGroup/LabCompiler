"""Plate geometry and operation snapshots from the compiler's volume accounting."""

from __future__ import annotations

import lab.compiler as compiler
from lab.documents.descriptions import describe
from lab.documents.models import (
    PlateMapView,
    PlateWellView,
    PreparationFrame,
    PreparationView,
    ResourceView,
    WellVolume,
)
from lab.model import (
    Distribute,
    Mix,
    MultiMix,
    MultiTransfer,
    SetTemperature,
    Thermocycle,
    Transfer,
)
from lab.samples import Location
from lab.units import number
from lab.validation import volume_trace


def build_preparation(
    compilation: compiler.Compilation, resources: tuple[ResourceView, ...]
) -> PreparationView:
    wells = {well.location: well for resource in resources for well in resource.wells}
    bindings = {binding.location: binding for binding in compilation.target.bindings}
    capacities = {
        well.location: min(resource.resource.capacity, bindings[well.location].capacity)
        for resource in resources
        for well in resource.wells
    }
    trace = volume_trace(compilation.protocol, compilation.target.bindings)
    frames = []
    for index, volumes in enumerate(trace):
        source = None
        destinations: tuple[str, ...] = ()
        sources: tuple[str, ...] = ()
        active: tuple[str, ...] = ()
        mix_cycles = 0
        refill_between_destinations = False
        lanes = False
        description = (
            "Before the first operation. Inspect a well to review its inputs and sample details."
        )
        if index:
            step = compilation.protocol.steps[index - 1]
            description = describe(step)
            if isinstance(step, (Transfer, Distribute)):
                source = wells[step.source].reference.anchor
                targets = (step.destination,) if isinstance(step, Transfer) else step.destinations
                destinations = tuple(wells[location].reference.anchor for location in targets)
                if isinstance(step, Distribute) and compilation.target.name == "Manual":
                    refill_between_destinations = True
                    description += (
                        f" For each destination, aspirate a fresh {number(step.volume)} µL "
                        "from the source and dispense it completely."
                    )
            elif isinstance(step, MultiTransfer):
                sources = tuple(wells[location].reference.anchor for location in step.sources)
                source = sources[0]
                destinations = tuple(
                    wells[location].reference.anchor for location in step.destinations
                )
                lanes = True
            elif isinstance(step, Mix):
                active = (wells[step.location].reference.anchor,)
                mix_cycles = step.cycles
            elif isinstance(step, MultiMix):
                active = tuple(wells[location].reference.anchor for location in step.locations)
                mix_cycles = step.cycles
            elif isinstance(step, (Thermocycle, SetTemperature)):
                active = tuple(
                    well.reference.anchor
                    for location, well in wells.items()
                    if location.resource == step.resource
                )
        frames.append(
            PreparationFrame(
                number=index,
                description=description,
                source=source,
                destinations=destinations,
                active=active,
                mix_cycles=mix_cycles,
                refill_between_destinations=refill_between_destinations,
                sources=sources if sources else ((source,) if source else ()),
                lanes=lanes,
                volumes=tuple(
                    WellVolume(
                        well.reference.anchor,
                        volumes[location],
                        11 * (volumes[location] / capacities[location]).sqrt(),
                    )
                    for location, well in wells.items()
                ),
            )
        )
    initial = {volume.anchor: volume for volume in frames[0].volumes}
    plates = []
    for resource in resources:
        cells = []
        for row in range(resource.resource.rows):
            for column in range(resource.resource.columns):
                coordinate = f"{chr(65 + row)}{column + 1}"
                well = wells.get(Location(resource.resource.name, coordinate))
                cells.append(
                    PlateWellView(
                        coordinate=coordinate,
                        row=row,
                        column=column,
                        x=40 + column * 32,
                        y=40 + row * 32,
                        reference=well.reference if well else None,
                        initial=initial[well.reference.anchor] if well else None,
                    )
                )
        plates.append(
            PlateMapView(
                resource=resource,
                rows=tuple(chr(65 + row) for row in range(resource.resource.rows)),
                columns=tuple(range(1, resource.resource.columns + 1)),
                wells=tuple(cells),
                width=resource.resource.columns * 32 + 32,
                height=resource.resource.rows * 32 + 32,
            )
        )
    return PreparationView(tuple(plates), tuple(frames))
