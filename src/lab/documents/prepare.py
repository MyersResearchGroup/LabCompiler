"""Prepare typed worksheet data without constructing or escaping markup."""

from __future__ import annotations

import json
from decimal import Decimal

import lab.compiler as compiler
from lab.documents.descriptions import describe
from lab.documents.flow import build_flow_profile
from lab.documents.models import (
    AdvisoryRow,
    DetailFields,
    IncomingTransfer,
    LiquidClassView,
    ProcedureRow,
    Reference,
    ResourceView,
    WellView,
    Worksheet,
)
from lab.documents.preparation import build_preparation
from lab.liquid_classes import LiquidClass, LiquidClassSpec
from lab.model import (
    LIQUID_STEPS,
    Distribute,
    Mix,
    MultiMix,
    MultiTransfer,
    Step,
    TargetPlan,
    Transfer,
    lanes,
)
from lab.samples import Location
from lab.tips import tip_count
from lab.units import number


def _procedure_row(
    index: int,
    step: Step,
    locations: dict[Location, Reference],
    classes: dict[LiquidClassSpec, Reference],
) -> ProcedureRow:
    destinations: tuple[Location, ...]
    sources: tuple[Location, ...] = ()
    if not isinstance(step, LIQUID_STEPS):
        return ProcedureRow(index, type(step).__name__, instruction=describe(step))
    if isinstance(step, Mix):
        source, destinations = step.location, ()
        note = f"{step.cycles} cycles"
    elif isinstance(step, MultiMix):
        source, destinations, sources = step.locations[0], (), step.locations
        note = f"{step.cycles} cycles at {len(step.locations)} wells together"
    elif isinstance(step, Distribute):
        source, destinations = step.source, step.destinations
        note = "per destination"
    elif isinstance(step, MultiTransfer):
        source, destinations, sources = step.sources[0], step.destinations, step.sources
        note = f"per lane, {len(destinations)} lanes together"
    else:
        source, destinations = step.source, (step.destination,)
        note = ""
    return ProcedureRow(
        number=index,
        action=type(step).__name__,
        volume=step.volume,
        volume_note=note,
        source=locations[source],
        destinations=tuple(locations[well] for well in destinations),
        sources=tuple(locations[well] for well in sources),
        liquid_class=classes.get(step.liquid_class) if step.liquid_class else None,
        air_gap=step.air_gap if isinstance(step, Distribute) else None,
    )


def _native_rates(target: TargetPlan, name: str) -> tuple[Decimal, Decimal] | None:
    configuration = json.loads(target.configuration_json, parse_float=Decimal, parse_int=Decimal)
    definition = configuration.get("hamilton_liquid_classes", {}).get(name, {})
    aspiration, dispense = (
        definition.get("aspiration_flow_rate"),
        definition.get("dispense_flow_rate"),
    )
    if (
        isinstance(aspiration, Decimal)
        and aspiration.is_finite()
        and aspiration > 0
        and isinstance(dispense, Decimal)
        and dispense.is_finite()
        and dispense > 0
    ):
        return aspiration, dispense
    return None


def _liquid_class(
    reference: Reference, spec: LiquidClassSpec, steps: tuple[Step, ...], target: TargetPlan
) -> LiquidClassView:
    rates: tuple[Decimal, Decimal] | None
    fields: DetailFields
    notes: tuple[str, ...]
    if isinstance(spec, LiquidClass):
        rates = spec.aspiration_flow_rate_ul_s, spec.dispense_flow_rate_ul_s
        description = (
            f"{spec.name}: aspirate {number(rates[0])} µL/s; dispense {number(rates[1])} µL/s"
        )
        fields = (
            ("Type", "Portable flow rates"),
            ("Aspiration", f"{number(rates[0])} µL/s"),
            ("Dispense", f"{number(rates[1])} µL/s"),
        )
        notes = (
            "Applied to aspiration and dispense, including mixing cycles. "
            "Other settings and volume calibration use target defaults.",
        )
    else:
        rates = _native_rates(target, spec.name)
        description = f"Hamilton STAR liquid class {spec.name}"
        fields = (("Type", "Hamilton STAR built-in"), ("Catalogue symbol", spec.name))
        notes = (
            "The STAR target resolves this symbol against its installed PyLabRobot "
            "catalogue and captures the definition in plan.json.",
        )
        if rates is None:
            notes += (
                "This plan records the class name only. Compile for STAR to capture "
                "the rates needed for a flow profile.",
            )
    profiles = (
        tuple(
            build_flow_profile(
                index, step, *rates, refill_between_destinations=target.name == "Manual"
            )
            for index, step in enumerate(steps, 1)
            if isinstance(step, LIQUID_STEPS) and step.liquid_class == spec
        )
        if rates is not None
        else ()
    )
    return LiquidClassView(reference, description, fields, notes, profiles)


def build_worksheet(compilation: compiler.Compilation) -> Worksheet:
    p, target = compilation.protocol, compilation.target
    fills = {
        Location(resource.name, fill.well): fill
        for resource in p.resources
        for fill in resource.fills
    }
    samples = {sample.id: sample for sample in p.samples}
    placed = {placement.location: samples[placement.sample_id] for placement in p.placements}
    used = set(fills) | set(placed)
    incoming: dict[Location, list[tuple[int, Location, Transfer | Distribute | MultiTransfer]]]
    incoming = {}
    for index, step in enumerate(p.steps, 1):
        if isinstance(step, LIQUID_STEPS):
            for source, destination in lanes(step):
                used.add(source)
                if destination is not None and isinstance(
                    step, (Transfer, Distribute, MultiTransfer)
                ):
                    used.add(destination)
                    incoming.setdefault(destination, []).append((index, source, step))
    locations = {
        location: Reference(f"location-{index}", str(location))
        for index, location in enumerate(
            (Location(resource.name, well) for resource in p.resources for well in resource.wells),
            1,
        )
        if location in used
    }
    classes = {
        spec: Reference(f"liquid-class-{index}", spec.name)
        for index, spec in enumerate(
            dict.fromkeys(
                step.liquid_class
                for step in p.steps
                if isinstance(step, LIQUID_STEPS) and step.liquid_class is not None
            ),
            1,
        )
    }
    bindings = {binding.location: binding for binding in target.bindings}
    final_volumes = dict(compilation.final_volumes)
    resources = []
    for index, resource in enumerate(p.resources, 1):
        wells = []
        for location, reference in locations.items():
            if location.resource != resource.name:
                continue
            fill, binding, sample = fills.get(location), bindings[location], placed.get(location)
            sample_fields: DetailFields = (
                (("Sample ID", sample.id), ("Material identity", sample.material_identity))
                if sample
                else ()
            )
            if sample and sample.parent_ids:
                sample_fields += (("Parent samples", ", ".join(sample.parent_ids)),)
            wells.append(
                WellView(
                    reference=reference,
                    location=location,
                    fill=fill,
                    physical=binding.physical,
                    final_volume=final_volumes[location],
                    fields=(
                        ("Initial volume", f"{number(fill.volume) if fill else '0'} µL"),
                        ("Planned final volume", f"{number(final_volumes[location])} µL"),
                        ("Capacity", f"{number(min(resource.capacity, binding.capacity))} µL"),
                        (
                            "Dead volume",
                            f"{number(max(resource.dead_volume, binding.dead_volume))} µL",
                        ),
                        ("Physical location", binding.physical),
                    ),
                    sample=sample,
                    sample_fields=sample_fields,
                    sample_heading="Planned output sample"
                    if sample and sample.id in p.output_sample_ids
                    else "Declared sample",
                    incoming=tuple(
                        IncomingTransfer(i, locations[source], step.volume)
                        for i, source, step in incoming.get(location, ())
                    ),
                )
            )
        physical = [
            binding.physical
            for location, binding in bindings.items()
            if location.resource == resource.name
        ]
        resources.append(
            ResourceView(
                reference=Reference(f"resource-{index}", resource.name),
                resource=resource,
                geometry=f"{resource.rows} × {resource.columns} wells"
                if resource.rows != 1 or resource.columns != 1
                else "Single container",
                physical_summary=", ".join(physical)
                if len(physical) <= 4
                else f"{physical[0]} … {physical[-1]} ({len(physical)} wells)",
                fields=(
                    ("Layout", f"{resource.rows} rows × {resource.columns} columns"),
                    ("Declared capacity / well", f"{number(resource.capacity)} µL"),
                    ("Declared dead volume / well", f"{number(resource.dead_volume)} µL"),
                ),
                wells=tuple(wells),
                loaded_wells=tuple(well for well in wells if well.fill),
                final_wells=tuple(well for well in wells if well.final_volume),
            )
        )
    summary, _, notes = p.description.partition("\n\n")
    return Worksheet(
        protocol=p,
        target=target,
        digest=compilation.digest,
        tip_count=tip_count(p),
        summary=summary,
        notes=notes,
        resources=tuple(resources),
        procedure=tuple(
            _procedure_row(i, step, locations, classes) for i, step in enumerate(p.steps, 1)
        ),
        liquid_classes=tuple(
            _liquid_class(ref, spec, p.steps, target) for spec, ref in classes.items()
        ),
        preparation=build_preparation(compilation, tuple(resources)),
        advisories=tuple(
            AdvisoryRow(
                diagnostic=diagnostic,
                step_number=diagnostic.step_index + 1
                if diagnostic.step_index is not None
                else None,
                locations=tuple(
                    locations[location]
                    for location in diagnostic.locations
                    if location in locations
                ),
            )
            for diagnostic in compilation.diagnostics
        ),
    )
