"""Explicit SBOL3 property mapping shared by validation and serialization."""

from dataclasses import dataclass
from typing import Literal

from lab.provenance.types import (
    Activity,
    Agent,
    Association,
    Attachment,
    Collection,
    CombinatorialDerivation,
    Component,
    ComponentReference,
    Constraint,
    Cut,
    EntireSequence,
    Experiment,
    ExperimentalData,
    ExternallyDefined,
    Feature,
    Identified,
    Implementation,
    Interaction,
    Interface,
    LocalSubComponent,
    Measure,
    Model,
    Participation,
    Plan,
    Range,
    Sequence,
    SequenceFeature,
    SequenceLocation,
    SubComponent,
    TopLevel,
    Usage,
    VariableFeature,
)
from lab.provenance.vocabulary import LAB, OM, PROV, SBOL, AgentKind, EvidenceState

Kind = Literal["text", "iri", "reference", "owned", "integer", "float", "datetime", "enum"]


@dataclass(frozen=True)
class Property:
    predicate: str
    kind: Kind
    multiple: bool = False
    required: bool = False
    target: type | None = None


# These mappings are intentionally explicit: Python attribute names are not RDF
# predicate names, and Activity.types is sbol:type, not rdf:type.
PROPERTIES: dict[type[Identified], dict[str, Property]] = {
    Identified: {
        "name": Property(SBOL + "name", "text"),
        "description": Property(SBOL + "description", "text"),
        "derived_from": Property(PROV + "wasDerivedFrom", "reference", True, target=Identified),
        "generated_by": Property(PROV + "wasGeneratedBy", "reference", True, target=Activity),
        "measures": Property(SBOL + "hasMeasure", "owned", True, target=Measure),
    },
    TopLevel: {
        "namespace": Property(SBOL + "hasNamespace", "iri"),
        "attachments": Property(SBOL + "hasAttachment", "reference", True, target=Attachment),
    },
    Sequence: {
        "elements": Property(SBOL + "elements", "text", required=True),
        "encoding": Property(SBOL + "encoding", "iri", required=True),
    },
    Component: {
        "types": Property(SBOL + "type", "iri", True, True),
        "roles": Property(SBOL + "role", "iri", True),
        "sequences": Property(SBOL + "hasSequence", "reference", True, target=Sequence),
        "features": Property(SBOL + "hasFeature", "owned", True, target=Feature),
        "constraints": Property(SBOL + "hasConstraint", "owned", True, target=Constraint),
        "interactions": Property(SBOL + "hasInteraction", "owned", True, target=Interaction),
        "interface": Property(SBOL + "hasInterface", "owned", target=Interface),
        "models": Property(SBOL + "hasModel", "reference", True, target=Model),
    },
    Feature: {
        "roles": Property(SBOL + "role", "iri", True),
        "orientation": Property(SBOL + "orientation", "iri"),
    },
    SubComponent: {
        "instance_of": Property(SBOL + "instanceOf", "reference", required=True, target=Component),
        "role_integration": Property(SBOL + "roleIntegration", "iri"),
        "locations": Property(SBOL + "hasLocation", "owned", True, target=SequenceLocation),
        "source_locations": Property(
            SBOL + "sourceLocation", "owned", True, target=SequenceLocation
        ),
    },
    SequenceFeature: {
        "locations": Property(SBOL + "hasLocation", "owned", True, True, SequenceLocation),
    },
    LocalSubComponent: {
        "types": Property(SBOL + "type", "iri", True, True),
        "locations": Property(SBOL + "hasLocation", "owned", True, target=SequenceLocation),
    },
    ExternallyDefined: {
        "types": Property(SBOL + "type", "iri", True, True),
        "definition": Property(SBOL + "definition", "iri", required=True),
    },
    ComponentReference: {
        "in_child_of": Property(
            SBOL + "inChildOf", "reference", required=True, target=SubComponent
        ),
        "refers_to": Property(SBOL + "refersTo", "reference", required=True, target=Feature),
    },
    SequenceLocation: {
        "sequence": Property(SBOL + "hasSequence", "reference", required=True, target=Sequence),
        "orientation": Property(SBOL + "orientation", "iri"),
        "order": Property(SBOL + "order", "integer"),
    },
    Range: {
        "start": Property(SBOL + "start", "integer", required=True),
        "end": Property(SBOL + "end", "integer", required=True),
    },
    Cut: {"at": Property(SBOL + "at", "integer", required=True)},
    EntireSequence: {},
    Constraint: {
        "restriction": Property(SBOL + "restriction", "iri", required=True),
        "subject": Property(SBOL + "subject", "reference", required=True, target=Feature),
        "object": Property(SBOL + "object", "reference", required=True, target=Feature),
    },
    Interaction: {
        "types": Property(SBOL + "type", "iri", True, True),
        "participations": Property(SBOL + "hasParticipation", "owned", True, target=Participation),
    },
    Participation: {
        "roles": Property(SBOL + "role", "iri", True, True),
        "participant": Property(SBOL + "participant", "reference", required=True, target=Feature),
    },
    Interface: {
        "inputs": Property(SBOL + "input", "reference", True, target=Feature),
        "outputs": Property(SBOL + "output", "reference", True, target=Feature),
        "nondirectionals": Property(SBOL + "nondirectional", "reference", True, target=Feature),
    },
    Implementation: {
        "built": Property(SBOL + "built", "reference", target=Component),
        "evidence_state": Property(LAB + "evidenceState", "enum", target=EvidenceState),
    },
    Attachment: {
        "source": Property(SBOL + "source", "iri", required=True),
        "format": Property(SBOL + "format", "iri"),
        "size": Property(SBOL + "size", "integer"),
        "hash": Property(SBOL + "hash", "text"),
        "hash_algorithm": Property(SBOL + "hashAlgorithm", "text"),
    },
    Model: {
        "source": Property(SBOL + "source", "iri", required=True),
        "language": Property(SBOL + "language", "iri", required=True),
        "framework": Property(SBOL + "framework", "iri", required=True),
    },
    Collection: {"members": Property(SBOL + "member", "reference", True, target=TopLevel)},
    Experiment: {"members": Property(SBOL + "member", "reference", True, target=ExperimentalData)},
    ExperimentalData: {},
    CombinatorialDerivation: {
        "template": Property(SBOL + "template", "reference", required=True, target=Component),
        "strategy": Property(SBOL + "strategy", "iri"),
        "variable_features": Property(
            SBOL + "hasVariableFeature", "owned", True, target=VariableFeature
        ),
    },
    VariableFeature: {
        "cardinality": Property(SBOL + "cardinality", "iri", required=True),
        "variable": Property(SBOL + "variable", "reference", required=True, target=Feature),
        "variants": Property(SBOL + "variant", "reference", True, target=Component),
        "variant_collections": Property(
            SBOL + "variantCollection", "reference", True, target=Collection
        ),
        "variant_derivations": Property(
            SBOL + "variantDerivation", "reference", True, target=CombinatorialDerivation
        ),
        "variant_measures": Property(SBOL + "variantMeasure", "owned", True, target=Measure),
    },
    Measure: {
        "value": Property(OM + "hasNumericalValue", "float", required=True),
        "unit": Property(OM + "hasUnit", "iri", required=True),
        "types": Property(SBOL + "type", "iri", True),
    },
    Usage: {
        "entity": Property(PROV + "entity", "reference", required=True, target=Identified),
        "roles": Property(PROV + "hadRole", "iri", True),
    },
    Association: {
        "agent": Property(PROV + "agent", "reference", required=True, target=Agent),
        "plan": Property(PROV + "hadPlan", "reference", target=Plan),
        "roles": Property(PROV + "hadRole", "iri", True),
    },
    Agent: {
        "kind": Property(LAB + "agentKind", "enum", target=AgentKind),
        "software_version": Property(LAB + "softwareVersion", "text"),
    },
    Plan: {"protocol": Property(LAB + "protocol", "iri")},
    Activity: {
        "types": Property(SBOL + "type", "iri", True),
        "usage": Property(PROV + "qualifiedUsage", "owned", True, target=Usage),
        "association": Property(PROV + "qualifiedAssociation", "owned", True, target=Association),
        "informed_by": Property(PROV + "wasInformedBy", "reference", True, target=Activity),
        "start_time": Property(PROV + "startedAtTime", "datetime"),
        "end_time": Property(PROV + "endedAtTime", "datetime"),
        "evidence_state": Property(LAB + "evidenceState", "enum", target=EvidenceState),
    },
}

ABSTRACT = {Identified, TopLevel, Feature, SequenceLocation}
RDF_TYPES = {
    cls: (
        PROV
        if cls in {Activity, Agent, Plan, Usage, Association}
        else OM
        if cls is Measure
        else SBOL
    )
    + cls.__name__
    for cls in PROPERTIES
    if cls not in ABSTRACT
}


def properties(cls: type[Identified]) -> dict[str, Property]:
    if cls not in RDF_TYPES:
        raise TypeError(f"Unsupported provenance class: {cls.__name__}")
    result: dict[str, Property] = {}
    for base in reversed(cls.__mro__):
        result.update(PROPERTIES.get(base, {}))
    return result
