"""Immutable SBOL3 design and provenance types, with no global document state."""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from datetime import datetime
from typing import Generic, Self, TypeVar
from urllib.parse import urlsplit

from lab.provenance.vocabulary import AgentKind, EvidenceState

T_co = TypeVar("T_co", bound="Identified", covariant=True)


def require_iri(value: str) -> str:
    """Require an absolute IRI; never resolve, fetch, or rewrite it."""
    if (
        not isinstance(value, str)
        or not value
        or re.search(r'[\s<>"{}|\\^`\x00-\x1f\x7f]', value)
        or not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", value)
    ):
        raise ValueError(f"Expected an absolute IRI, got {value!r}")
    parsed = urlsplit(value)
    if parsed.scheme in {"http", "https"} and not parsed.netloc:
        raise ValueError(f"Expected an absolute IRI, got {value!r}")
    return value


@dataclass(frozen=True, slots=True)
class Ref(Generic[T_co]):
    identity: str

    def __post_init__(self) -> None:
        require_iri(self.identity)


@dataclass(frozen=True, kw_only=True)
class Identified:
    """Common SBOL metadata. Only owned objects may omit an identity."""

    identity: str | None = None
    name: str | None = None
    description: str | None = None
    derived_from: tuple[Ref[Identified], ...] = ()
    generated_by: tuple[Ref[Activity], ...] = ()
    measures: tuple[Measure, ...] = ()

    def __post_init__(self) -> None:
        if self.identity is not None:
            require_iri(self.identity)
        # Reject shallowly frozen objects containing mutable collections.
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, (list, dict, set)):
                raise TypeError(
                    f"{type(self).__name__}.{field.name} must be immutable; use a tuple"
                )

    @property
    def ref(self) -> Ref[Self]:
        if self.identity is None:
            raise ValueError("Assign an identity, or retrieve the owned object after freezing")
        return Ref(self.identity)


@dataclass(frozen=True, kw_only=True)
class TopLevel(Identified):
    identity: str
    namespace: str | None = None
    attachments: tuple[Ref[Attachment], ...] = ()

    def __post_init__(self) -> None:
        super().__post_init__()
        require_iri(self.identity)
        if self.namespace is not None:
            require_iri(self.namespace)


@dataclass(frozen=True, kw_only=True)
class Measure(Identified):
    value: float
    unit: str
    types: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class Sequence(TopLevel):
    elements: str
    encoding: str


@dataclass(frozen=True, kw_only=True)
class SequenceLocation(Identified):
    sequence: Ref[Sequence]
    orientation: str | None = None
    order: int | None = None


@dataclass(frozen=True, kw_only=True)
class Range(SequenceLocation):
    """One-based, inclusive sequence coordinates."""

    start: int
    end: int


@dataclass(frozen=True, kw_only=True)
class Cut(SequenceLocation):
    """Position between bases; zero denotes the beginning of a sequence."""

    at: int


@dataclass(frozen=True, kw_only=True)
class EntireSequence(SequenceLocation):
    pass


@dataclass(frozen=True, kw_only=True)
class Feature(Identified):
    roles: tuple[str, ...] = ()
    orientation: str | None = None


@dataclass(frozen=True, kw_only=True)
class SubComponent(Feature):
    instance_of: Ref[Component]
    role_integration: str | None = None
    locations: tuple[SequenceLocation, ...] = ()
    source_locations: tuple[SequenceLocation, ...] = ()


@dataclass(frozen=True, kw_only=True)
class SequenceFeature(Feature):
    locations: tuple[SequenceLocation, ...]


@dataclass(frozen=True, kw_only=True)
class LocalSubComponent(Feature):
    types: tuple[str, ...]
    locations: tuple[SequenceLocation, ...] = ()


@dataclass(frozen=True, kw_only=True)
class ExternallyDefined(Feature):
    types: tuple[str, ...]
    definition: str


@dataclass(frozen=True, kw_only=True)
class ComponentReference(Feature):
    in_child_of: Ref[SubComponent]
    refers_to: Ref[Feature]


@dataclass(frozen=True, kw_only=True)
class Constraint(Identified):
    restriction: str
    subject: Ref[Feature]
    object: Ref[Feature]


@dataclass(frozen=True, kw_only=True)
class Participation(Identified):
    roles: tuple[str, ...]
    participant: Ref[Feature]


@dataclass(frozen=True, kw_only=True)
class Interaction(Identified):
    types: tuple[str, ...]
    participations: tuple[Participation, ...] = ()


@dataclass(frozen=True, kw_only=True)
class Interface(Identified):
    inputs: tuple[Ref[Feature], ...] = ()
    outputs: tuple[Ref[Feature], ...] = ()
    nondirectionals: tuple[Ref[Feature], ...] = ()


@dataclass(frozen=True, kw_only=True)
class Component(TopLevel):
    types: tuple[str, ...]
    roles: tuple[str, ...] = ()
    sequences: tuple[Ref[Sequence], ...] = ()
    features: tuple[Feature, ...] = ()
    constraints: tuple[Constraint, ...] = ()
    interactions: tuple[Interaction, ...] = ()
    interface: Interface | None = None
    models: tuple[Ref[Model], ...] = ()


@dataclass(frozen=True, kw_only=True)
class Implementation(TopLevel):
    """A planned, recorded, or simulated realization of a design.

    ``derived_from`` names the intended design. ``built`` describes the realized
    structure, when known. A planned output does not assert that it was built.
    """

    built: Ref[Component] | None = None
    evidence_state: EvidenceState = EvidenceState.UNKNOWN


@dataclass(frozen=True, kw_only=True)
class Attachment(TopLevel):
    source: str
    format: str | None = None
    size: int | None = None
    hash: str | None = None
    hash_algorithm: str | None = None


@dataclass(frozen=True, kw_only=True)
class Model(TopLevel):
    source: str
    language: str
    framework: str


@dataclass(frozen=True, kw_only=True)
class Collection(TopLevel):
    members: tuple[Ref[TopLevel], ...] = ()


@dataclass(frozen=True, kw_only=True)
class ExperimentalData(TopLevel):
    pass


@dataclass(frozen=True, kw_only=True)
class Experiment(TopLevel):
    members: tuple[Ref[ExperimentalData], ...] = ()


@dataclass(frozen=True, kw_only=True)
class VariableFeature(Identified):
    cardinality: str
    variable: Ref[Feature]
    variants: tuple[Ref[Component], ...] = ()
    variant_collections: tuple[Ref[Collection], ...] = ()
    variant_derivations: tuple[Ref[CombinatorialDerivation], ...] = ()
    variant_measures: tuple[Measure, ...] = ()


@dataclass(frozen=True, kw_only=True)
class CombinatorialDerivation(TopLevel):
    template: Ref[Component]
    strategy: str | None = None
    variable_features: tuple[VariableFeature, ...] = ()


@dataclass(frozen=True, kw_only=True)
class Usage(Identified):
    entity: Ref[Identified]
    roles: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class Association(Identified):
    agent: Ref[Agent]
    plan: Ref[Plan] | None = None
    roles: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class Agent(TopLevel):
    kind: AgentKind | None = None
    software_version: str | None = None


@dataclass(frozen=True, kw_only=True)
class Plan(TopLevel):
    """Method identity; ``protocol`` links to its separately specified protocol."""

    protocol: str | None = None


@dataclass(frozen=True, kw_only=True)
class Activity(TopLevel):
    types: tuple[str, ...] = ()
    usage: tuple[Usage, ...] = ()
    association: tuple[Association, ...] = ()
    informed_by: tuple[Ref[Activity], ...] = ()
    start_time: datetime | None = None
    end_time: datetime | None = None
    evidence_state: EvidenceState = EvidenceState.UNKNOWN
