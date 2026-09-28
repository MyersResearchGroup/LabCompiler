"""Freeze planned cloning routes as ordinary protocols and explicit operator stages."""

from collections import defaultdict
from dataclasses import replace
from decimal import Decimal

from lab.artifacts import SourceArtifact, canonical_json
from lab.deck import Container, Deck, DeckSite
from lab.experiment import ExperimentPlan, MaterialHandoff, ProtocolStage
from lab.experiments.cloning.methods import (
    AssemblyMethod,
    ExternalPreparationMethod,
    PlatingMethod,
    TransformationMethod,
)
from lab.experiments.cloning.planning import Allocation, BuildPlan
from lab.experiments.cloning.systems import TransformationRecipe
from lab.labware import PCR_PLATE_96, ContainerSpec, LabwareKind, LabwareSpec
from lab.operations import MaterialPort
from lab.protocol import Protocol, Well
from lab.provenance import Activity, Document, EvidenceState, Implementation, Plan
from lab.samples import Location, Sample
from lab.units import celsius, seconds, uL, units


def _transfers(
    protocol: Protocol,
    wells: dict[str, Well],
    allocations: tuple[Allocation, ...],
    destination: Well,
) -> None:
    for allocation in allocations:
        protocol.transfer(
            wells[allocation.implementation.identity], destination, volume=allocation.volume_ul * uL
        )


def _role(inputs: tuple[Allocation, ...], name: str) -> tuple[Allocation, ...]:
    return tuple(allocation for allocation in inputs if allocation.role == name)


def build(
    plan: BuildPlan,
    *,
    reagent_container: Container | None = None,
    preparation_container: ContainerSpec | None = None,
) -> ExperimentPlan:
    """Generate one stage per selected reaction, preserving material quantities.

    External procedures compile as operator stages. Counted stocks occupy logical
    positions there; those positions do not prescribe robot labware. Every input
    load and substrate is an explicit precondition, not an inferred preparation.
    """
    plan.require_ready()
    reagent_container = reagent_container or Container(
        id="reagents",
        labware=PCR_PLATE_96,
        site=DeckSite.PLATES,
    )
    preparation_container = preparation_container or ContainerSpec(
        id="products", labware=PCR_PLATE_96
    )
    if reagent_container.id != "reagents" or preparation_container.id != "products":
        raise ValueError("Container ids must be 'reagents' and 'products', respectively")
    document = Document.from_snapshot(plan.document)
    stages: list[ProtocolStage] = []
    remaining = {stock.implementation.identity: stock.amount for stock in plan.inventory.stocks}
    produced_at: dict[str, tuple[str, Location, ContainerSpec]] = {}
    for task in plan.tasks:
        method = task.method
        if method is None or task.recipe is None:
            raise ValueError("The builder requires resolved typed recipes and methods")
        external = isinstance(method, ExternalPreparationMethod)
        plating = isinstance(method, PlatingMethod)
        product_container = (
            preparation_container
            if external
            else Container(
                id="agar" if plating else "products",
                labware=PCR_PLATE_96,
                site=DeckSite.MORE_PLATES if plating else DeckSite.THERMOCYCLER,
            )
        )
        protocol_id = task.identity + "/protocol"
        description = f"Planned {task.kind.value} using {method.identity}."
        if isinstance(method, PlatingMethod):
            description += (
                f" Supplied substrate: {method.substrate.identity}. "
                "Output is a deposited sample, not a confirmed colony."
            )
        protocol = Protocol(
            name=f"{task.kind.value.capitalize()} {len(stages) + 1}",
            identity=protocol_id,
            description=description,
        )
        containers: list[ContainerSpec] = [reagent_container, product_container]
        plates = {
            container.id: protocol.plate(
                container.id,
                shape=(container.labware.rows, container.labware.columns),
                capacity=container.labware.capacity_ul * uL,
            )
            for container in containers
        }
        container: ContainerSpec
        if plating:
            container = Container(id="dilutions", labware=PCR_PLATE_96, site=DeckSite.PLATES)
            containers.append(container)
            plates[container.id] = protocol.plate(
                container.id, capacity=container.labware.capacity_ul * uL
            )
        counted_stocks = {
            a.implementation for a in task.inputs if a.form.counted and a.producer is None
        }
        if counted_stocks:
            container = ContainerSpec(
                id="external_inputs",
                labware=LabwareSpec(
                    kind=LabwareKind.PLATE,
                    rows=1,
                    columns=len(counted_stocks),
                    capacity_ul=Decimal(1),
                ),
            )
            containers.append(container)
            plates[container.id] = protocol.plate(
                container.id, shape=(1, len(counted_stocks)), capacity=1 * uL
            )
        wells: dict[str, Well] = {}
        handoffs: list[MaterialHandoff] = []
        stock_index = count_index = 0
        samples: dict[str, Sample] = {}
        for allocation in task.inputs:
            identity = allocation.implementation.identity
            if identity in wells:
                continue
            if allocation.producer is None:
                if allocation.form.counted:
                    well = plates["external_inputs"][f"A{count_index + 1}"]
                    count_index += 1
                else:
                    names = plates["reagents"].wells
                    if stock_index >= len(names):
                        raise ValueError("Inputs exceed the reagent container's number of wells")
                    well = plates["reagents"][names[stock_index]]
                    stock_index += 1
            else:
                producer, source, previous_container = produced_at[identity]
                name = f"upstream_{len(handoffs) + 1}"
                container = Container(
                    id=name,
                    labware=previous_container.labware,
                    site=DeckSite.MORE_PLATES
                    if sum(
                        isinstance(item, Container) and item.site is DeckSite.PLATES
                        for item in containers
                    )
                    >= 2
                    else DeckSite.PLATES,
                )
                containers.append(container)
                plate = protocol.plate(
                    name,
                    shape=(container.labware.rows, container.labware.columns),
                    capacity=container.labware.capacity_ul * uL,
                )
                well = plate[source.well]
                handoffs.append(
                    MaterialHandoff(
                        producer=producer,
                        implementation=allocation.implementation,
                        source=source,
                        destination=Location(well.resource, well.name),
                        volume_ul=Decimal(0) if allocation.form.counted else remaining[identity],
                        count=int(remaining[identity]) if allocation.form.counted else None,
                    )
                )
            wells[identity] = well
            if not allocation.form.counted:
                protocol.load(well, identity, volume=remaining[identity] * uL)
            sample = Sample(
                id=protocol_id + f"/input_{len(samples) + 1}",
                material_identity=allocation.design.identity,
                label=allocation.design.identity,
                design=allocation.design,
                implementation=allocation.implementation,
                form=allocation.form,
                count=int(remaining[identity]) if allocation.form.counted else None,
            )
            samples[identity] = sample
            protocol.add_sample(sample, at=well, is_input=True)
        output = plates[product_container.id]["A1"]

        if isinstance(method, AssemblyMethod):
            _transfers(protocol, wells, task.inputs, output)
            protocol.mix(output, volume=method.mix_volume_ul * uL, cycles=method.mix_cycles)
            protocol.thermocycle(
                plates[product_container.id],
                tuple((celsius(h.celsius), h.seconds * seconds) for h in method.profile),
                cycles=method.cycles,
                lid_temperature=None if method.lid_celsius is None else celsius(method.lid_celsius),
            )
            physical_output = method.reaction_volume_ul
        elif isinstance(method, TransformationMethod):
            assert isinstance(task.recipe, TransformationRecipe)
            if method.initial_celsius is not None:
                protocol.set_temperature(
                    plates[product_container.id], celsius(method.initial_celsius)
                )
            _transfers(protocol, wells, _role(task.inputs, "cells"), output)
            protocol.mix(
                output, volume=method.cell_mix_volume_ul * uL, cycles=method.cell_mix_cycles
            )
            for allocation in task.inputs:
                if allocation.role.startswith("dna_"):
                    protocol.mix(
                        wells[allocation.implementation.identity],
                        volume=allocation.volume_ul * uL,
                        cycles=method.dna_mix_cycles,
                    )
                    _transfers(protocol, wells, (allocation,), output)
            physical_output = method.reaction_volume(len(task.recipe.plasmids))
            protocol.thermocycle(
                plates[product_container.id],
                tuple((celsius(h.celsius), h.seconds * seconds) for h in method.profile),
                block_volume=(physical_output - method.recovery.volume_ul) * uL,
            )
            _transfers(protocol, wells, _role(task.inputs, "recovery"), output)
            protocol.thermocycle(
                plates[product_container.id],
                tuple((celsius(h.celsius), h.seconds * seconds) for h in method.recovery_profile),
                block_volume=physical_output * uL,
            )
        elif isinstance(method, PlatingMethod):
            if len(method.dilution_factors) > len(plates["dilutions"].wells):
                raise ValueError("Dilution series exceeds the plate's number of wells")
            previous = None
            previous_sample = None
            for index, _factor in enumerate(method.dilution_factors):
                destination = plates["dilutions"][plates["dilutions"].wells[index]]
                additions = _role(task.inputs, f"diluent_{index + 1}")
                _transfers(protocol, wells, additions, destination)
                if previous is None:
                    _transfers(protocol, wells, _role(task.inputs, "culture"), destination)
                    parents = tuple(
                        samples[a.implementation.identity].id
                        for a in (*additions, *_role(task.inputs, "culture"))
                    )
                else:
                    protocol.transfer(previous, destination, volume=method.transfer_volume_ul * uL)
                    parents = (
                        *(samples[a.implementation.identity].id for a in additions),
                        str(previous_sample),
                    )
                protocol.mix(
                    destination, volume=method.mix_volume_ul * uL, cycles=method.mix_cycles
                )
                material = Implementation(
                    identity=task.identity + f"/dilution_{index + 1}",
                    derived_from=(task.design,),
                    generated_by=(document.get(task.identity, Activity).ref,),
                    evidence_state=EvidenceState.PLANNED,
                )
                document.add(material)
                sample = Sample(
                    id=material.identity,
                    material_identity=task.design.identity,
                    label=task.design.identity,
                    design=task.design,
                    implementation=material.ref,
                    role="dilution",
                    dilution=index + 1,
                    parent_ids=tuple(dict.fromkeys(parents)),
                )
                protocol.add_sample(sample, at=destination)
                previous, previous_sample = destination, sample.id
            assert previous is not None
            protocol.transfer(
                previous,
                output,
                volume=method.spot_volume_ul * uL,
                destination_height=method.spot_height_mm * units.millimeter,
            )
            physical_output = Decimal(1)
        else:
            consumed: dict[str, Decimal] = defaultdict(Decimal)
            for allocation in task.inputs:
                consumed[allocation.implementation.identity] += allocation.amount
            ports = tuple(
                MaterialPort(
                    location=Location(wells[identity].resource, wells[identity].name),
                    volume_ul=Decimal(0) if samples[identity].count is not None else amount,
                    count=int(amount) if samples[identity].count is not None else 0,
                )
                for identity, amount in consumed.items()
            )
            protocol.external_preparation(
                procedure=method.procedure,
                instructions=method.instructions,
                inputs=ports,
                outputs=(
                    MaterialPort(
                        location=Location(output.resource, output.name),
                        volume_ul=method.output_volume_ul,
                        count=method.output_count,
                    ),
                ),
            )
            physical_output = (
                Decimal(method.output_count)
                if task.output_form.counted
                else method.output_volume_ul
            )
        for allocation in task.inputs:
            remaining[allocation.implementation.identity] -= allocation.amount
        protocol.add_sample(
            Sample(
                id=task.output.identity,
                material_identity=task.design.identity,
                label=task.design.identity,
                design=task.design,
                implementation=task.output,
                form=task.output_form,
                count=task.output_count if task.output_form.counted else None,
                parent_ids=(str(previous_sample),)
                if plating
                else tuple(sample.id for sample in samples.values()),
                role=task.kind.value,
            ),
            at=output,
            is_output=True,
        )
        remaining[task.output.identity] = physical_output
        produced_at[task.output.identity] = (
            task.identity,
            Location(output.resource, output.name),
            product_container,
        )
        recorded = protocol.snapshot()
        specification = Plan(
            identity=task.identity + "/specification",
            protocol=recorded.identity,
            derived_from=(document.get(method.identity, Plan).ref,),
        )
        document.add(specification)
        activity = document.get(task.identity, Activity)
        document.replace(
            replace(
                activity,
                association=tuple(
                    replace(association, plan=specification.ref)
                    for association in activity.association
                ),
            )
        )
        stages.append(
            ProtocolStage(
                identity=task.identity,
                protocol=recorded,
                deck=Deck(containers=tuple(containers)),
                depends_on=task.depends_on,
                handoffs=tuple(handoffs),
                external=external,
            )
        )
    return ExperimentPlan(
        identity=plan.request.identity,
        provenance=document.freeze(),
        stages=tuple(stages),
        build_json=plan.plan_json,
        inputs=(
            SourceArtifact(name="build-provenance.ttl", text=plan.document.to_turtle()),
            SourceArtifact(
                name="inventory.json",
                text=canonical_json(
                    {"format": "lab.inventory.v1", "inventory": plan.inventory},
                ),
            ),
            SourceArtifact(
                name="catalog.json",
                text=canonical_json(
                    {"format": "lab.catalog.v1", "catalog": plan.catalog},
                ),
            ),
            SourceArtifact(
                name="system.json",
                text=canonical_json(
                    {"format": "lab.cloning-system.v1", "system": plan.system},
                ),
            ),
            SourceArtifact(
                name="methods.json",
                text=canonical_json(
                    {"format": "lab.cloning-methods.v1", "methods": plan.methods},
                ),
            ),
        ),
    )
