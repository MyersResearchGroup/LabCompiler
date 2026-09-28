"""Frozen logical experiments, stages, and material handoffs."""

import hashlib
from dataclasses import dataclass
from decimal import Decimal

from lab.artifacts import SourceArtifact, canonical_json
from lab.deck import Deck
from lab.model import RecordedProtocol, semantic
from lab.provenance import DocumentSnapshot, EvidenceState, Implementation, Ref
from lab.provenance.types import require_iri
from lab.samples import Location
from lab.validation import count_trace, logical_bindings, validate


@dataclass(frozen=True, kw_only=True)
class MaterialHandoff:
    producer: str
    implementation: Ref[Implementation]
    source: Location
    destination: Location
    volume_ul: Decimal
    count: int | None = None

    def __post_init__(self) -> None:
        require_iri(self.producer)
        if (
            not isinstance(self.volume_ul, Decimal)
            or not self.volume_ul.is_finite()
            or self.volume_ul < 0
            or (self.count is None and self.volume_ul <= 0)
            or (
                self.count is not None
                and (type(self.count) is not int or self.count <= 0 or self.volume_ul != 0)
            )
        ):
            raise ValueError("A handoff needs either positive volume or a positive count")


@dataclass(frozen=True, kw_only=True)
class ProtocolStage:
    identity: str
    protocol: RecordedProtocol
    deck: Deck
    depends_on: tuple[str, ...] = ()
    handoffs: tuple[MaterialHandoff, ...] = ()
    external: bool = False

    def __post_init__(self) -> None:
        require_iri(self.identity)
        if type(self.external) is not bool:
            raise TypeError("Stage external must be a bool")
        if not isinstance(self.protocol, RecordedProtocol) or not isinstance(self.deck, Deck):
            raise TypeError("Stages require a frozen protocol and logical deck")
        if not isinstance(self.depends_on, tuple) or not isinstance(self.handoffs, tuple):
            raise TypeError("Dependencies and handoffs must be immutable tuples")
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("Stage dependencies must be unique")
        requirements = {item.id: item.labware for item in self.deck.containers}
        if set(requirements) != {resource.name for resource in self.protocol.resources}:
            raise ValueError("Stage deck requirements must cover exactly the protocol resources")
        for resource in self.protocol.resources:
            spec = requirements[resource.name]
            if (spec.rows, spec.columns) != (resource.rows, resource.columns) or (
                resource.capacity > spec.capacity_ul
            ):
                raise ValueError("Stage deck geometry and capacity must match the protocol")


@dataclass(frozen=True, kw_only=True)
class ExperimentPlan:
    identity: str
    provenance: DocumentSnapshot
    stages: tuple[ProtocolStage, ...]
    build_json: str | None = None
    inputs: tuple[SourceArtifact, ...] = ()

    def __post_init__(self) -> None:
        require_iri(self.identity)
        if not isinstance(self.inputs, tuple) or not all(
            isinstance(item, SourceArtifact) for item in self.inputs
        ):
            raise TypeError("Experiment inputs must be a tuple of SourceArtifact objects")
        if len({item.name for item in self.inputs}) != len(self.inputs):
            raise ValueError("Input artifact paths must be unique")
        if not isinstance(self.stages, tuple) or not all(
            isinstance(stage, ProtocolStage) for stage in self.stages
        ):
            raise TypeError("Experiment stages must be an immutable tuple")
        self.provenance.validate().raise_for_errors()
        previous: dict[str, ProtocolStage] = {}
        step_ids: set[str | None] = set()
        protocol_ids: set[str | None] = set()
        # Track carry-over material after every consumer, not the original yield.
        remaining: dict[Ref[Implementation], Decimal] = {}
        forms: dict[Ref[Implementation], object] = {}
        for stage in self.stages:
            if stage.identity in previous or not set(stage.depends_on) <= previous.keys():
                raise ValueError("Stages need unique identities and earlier dependencies")
            if stage.protocol.identity in protocol_ids:
                raise ValueError("Stage protocols need unique identities")
            protocol_ids.add(stage.protocol.identity)
            for step in stage.protocol.steps:
                if step.identity in step_ids:
                    raise ValueError("Experiment step identities must be unique")
                step_ids.add(step.identity)
            final = validate(stage.protocol, logical_bindings(stage.protocol))
            final_counts = count_trace(stage.protocol)[-1]
            samples = {sample.id: sample for sample in stage.protocol.samples}
            places = {place.sample_id: place.location for place in stage.protocol.placements}
            at = {location: samples[key] for key, location in places.items()}
            initial = {
                Location(resource.name, fill.well): fill.volume
                for resource in stage.protocol.resources
                for fill in resource.fills
            }
            for key in stage.protocol.input_sample_ids:
                if samples[key].count is not None:
                    initial[places[key]] = Decimal(samples[key].count or 0)
            for location, count in final_counts.items():
                final[location] = Decimal(count)
            input_refs = [
                samples[key].implementation
                for key in stage.protocol.input_sample_ids
                if samples[key].implementation is not None
            ]
            if len(set(input_refs)) != len(input_refs):
                raise ValueError("Split input aliquots require distinct implementation identities")
            for sample in samples.values():
                if sample.design is not None:
                    self.provenance.resolve(sample.design)
                if sample.implementation is not None:
                    material = self.provenance.resolve(sample.implementation)
                    if (
                        sample.implementation in forms
                        and forms[sample.implementation] != sample.form
                    ):
                        raise ValueError(
                            "An implementation cannot change material form across stages"
                        )
                    forms[sample.implementation] = sample.form
                    if sample.design is None or (
                        material.built != sample.design
                        and sample.design not in material.derived_from
                    ):
                        raise ValueError("Sample design conflicts with its implementation")
            handoff_locations = {handoff.destination for handoff in stage.handoffs}
            if len(handoff_locations) != len(stage.handoffs):
                raise ValueError("An input location needs exactly one handoff")
            for sample_id in stage.protocol.input_sample_ids:
                sample = samples[sample_id]
                material_ref = sample.implementation
                if material_ref is None or places[sample_id] in handoff_locations:
                    continue
                material = self.provenance.resolve(material_ref)
                if material.evidence_state is EvidenceState.PLANNED:
                    raise ValueError("A planned input requires an upstream material handoff")
                location = places[sample_id]
                if material_ref in remaining and remaining[material_ref] != initial.get(location):
                    raise ValueError("Initial volume conflicts with the remaining stock quantity")
                remaining[material_ref] = final[location]
            for handoff in stage.handoffs:
                if handoff.producer not in stage.depends_on:
                    raise ValueError("Handoff producer must be a stage dependency")
                producer = previous[handoff.producer]
                outputs = producer.protocol.output_manifest()
                output_at = {place.location: place.sample_id for place in outputs.placements}
                source = next(
                    (
                        sample
                        for sample in outputs.samples
                        if sample.id == output_at.get(handoff.source)
                    ),
                    None,
                )
                destination = at.get(handoff.destination)
                if (
                    source is None
                    or destination is None
                    or source.implementation != handoff.implementation
                    or destination.implementation != handoff.implementation
                    or destination.design != source.design
                    or destination.form != source.form
                    or (handoff.count is not None) != (destination.count is not None)
                ):
                    raise ValueError("Handoff must preserve the exact material and design")
                amount = Decimal(handoff.count) if handoff.count is not None else handoff.volume_ul
                if initial.get(handoff.destination) != amount:
                    raise ValueError("Handoff volume must match the destination precondition")
                if remaining.get(handoff.implementation) != amount:
                    raise ValueError("Handoff volume conflicts with remaining upstream material")
                remaining[handoff.implementation] = final[handoff.destination]
            for sample_id in stage.protocol.output_sample_ids:
                output_ref = samples[sample_id].implementation
                if output_ref is not None:
                    if output_ref in remaining:
                        raise ValueError("An output implementation must have one producing stage")
                    remaining[output_ref] = final[places[sample_id]]
            previous[stage.identity] = stage

    @property
    def plan_json(self) -> str:
        return canonical_json(
            {
                "format": "lab.experiment.v1",
                "identity": self.identity,
                "provenance_sha256": self.provenance.digest,
                "stages": [
                    {
                        "identity": stage.identity,
                        "protocol": semantic(stage.protocol),
                        "depends_on": stage.depends_on,
                        "handoffs": stage.handoffs,
                        "external": stage.external,
                        # Physical layouts do not define the logical experiment.
                        "containers": stage.deck.containers,
                    }
                    for stage in self.stages
                ],
                "build": self.build_json,
                "inputs": {item.name: item.digest for item in self.inputs},
            }
        )

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.plan_json.encode()).hexdigest()
