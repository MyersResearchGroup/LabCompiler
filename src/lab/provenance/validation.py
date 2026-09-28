"""Structural, reference, and evidence checks for Lab provenance documents."""

import math
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

from sbol3.identified import extract_display_id

from lab.provenance._schema import Property, properties
from lab.provenance.types import (
    Activity,
    Attachment,
    CombinatorialDerivation,
    Component,
    ComponentReference,
    Cut,
    Identified,
    Implementation,
    Range,
    Ref,
    Sequence,
    SequenceLocation,
    SubComponent,
    TopLevel,
    VariableFeature,
    require_iri,
)
from lab.provenance.vocabulary import INLINE, REVERSE_COMPLEMENT, SBOL, EvidenceState


@dataclass(frozen=True)
class Diagnostic:
    identity: str | None
    path: str
    code: str
    message: str


@dataclass(frozen=True)
class ValidationReport:
    errors: tuple[Diagnostic, ...] = ()

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def raise_for_errors(self) -> None:
        if self.errors:
            raise ProvenanceError(self)


class ProvenanceError(ValueError):
    def __init__(self, report: ValidationReport) -> None:
        self.report = report
        super().__init__(
            "\n".join(
                f"{item.identity or '<unidentified>'}.{item.path}: {item.message} [{item.code}]"
                for item in report.errors
            )
        )


def values(obj: Identified, name: str, prop: Property) -> tuple[object, ...]:
    value = getattr(obj, name)
    if prop.multiple:
        return value if isinstance(value, tuple) else (value,)
    return () if value is None else (value,)


def owned(obj: Identified) -> tuple[Identified, ...]:
    return tuple(
        value
        for name, prop in properties(type(obj)).items()
        if prop.kind == "owned"
        for value in values(obj, name, prop)
        if isinstance(value, Identified)
    )


def walk(obj: Identified) -> tuple[Identified, ...]:
    return (obj, *(descendant for child in owned(obj) for descendant in walk(child)))


def _valid_value(value: object, prop: Property) -> bool:
    if prop.kind == "reference":
        return isinstance(value, Ref)
    if prop.kind in {"owned", "enum"}:
        return prop.target is not None and isinstance(value, prop.target)
    if prop.kind == "integer":
        return type(value) is int
    if prop.kind == "float":
        return type(value) in {float, int} and math.isfinite(value)  # type: ignore[arg-type]
    if prop.kind == "datetime":
        return isinstance(value, datetime)
    if not isinstance(value, str):
        return False
    if prop.kind == "iri":
        try:
            require_iri(value)
        except ValueError:
            return False
    return True


def validate_structure(objects: tuple[TopLevel, ...]) -> ValidationReport:
    """Check field shapes before ownership normalization or RDF serialization."""
    errors: list[Diagnostic] = []

    def visit(obj: Identified) -> None:
        try:
            schema = properties(type(obj))
        except TypeError as error:
            errors.append(Diagnostic(obj.identity, "type", "unsupported-type", str(error)))
            return
        for name, prop in schema.items():
            raw = getattr(obj, name)
            items = values(obj, name, prop)
            if prop.multiple and not isinstance(raw, tuple):
                errors.append(Diagnostic(obj.identity, name, "field-type", "Expected a tuple"))
            if prop.required and not items:
                errors.append(Diagnostic(obj.identity, name, "required", "A value is required"))
            for value in items:
                if not _valid_value(value, prop):
                    errors.append(
                        Diagnostic(
                            obj.identity, name, "field-type", f"Invalid {prop.kind} value {value!r}"
                        )
                    )
                elif prop.kind == "owned" and isinstance(value, Identified):
                    visit(value)
            if (
                prop.multiple
                and prop.kind != "owned"
                and all(_valid_value(value, prop) for value in items)
                and len(set(items)) != len(items)
            ):
                errors.append(
                    Diagnostic(
                        obj.identity,
                        name,
                        "duplicate-value",
                        "SBOL properties contain unique values",
                    )
                )

    for obj in objects:
        if not isinstance(obj, TopLevel):
            errors.append(Diagnostic(None, "objects", "field-type", "Expected a TopLevel object"))
        else:
            visit(obj)
    return ValidationReport(tuple(errors))


def validate_objects(
    objects: tuple[TopLevel, ...], *, allow_external: bool = False
) -> ValidationReport:
    structural = validate_structure(objects)
    if not structural.is_valid:
        return structural
    errors: list[Diagnostic] = []
    index: dict[str, Identified] = {}

    def error(obj: Identified, path: str, code: str, message: str) -> None:
        errors.append(Diagnostic(obj.identity, path, code, message))

    for top in objects:
        for obj in walk(top):
            if obj.identity is None:
                error(
                    obj, "identity", "missing-identity", "Freeze the document to assign identities"
                )
            elif obj.identity in index:
                error(obj, "identity", "duplicate-identity", "An identity has more than one owner")
            else:
                index[obj.identity] = obj
    for obj in index.values():
        try:
            extract_display_id(obj.identity)
        except ValueError as invalid_identity:
            error(obj, "identity", "display-id", str(invalid_identity))
        if (
            isinstance(obj, TopLevel)
            and obj.namespace is not None
            and urlsplit(obj.identity).netloc
            and not obj.identity.startswith(obj.namespace)
        ):
            error(obj, "namespace", "namespace", "Namespace must be a prefix of the identity")
        for name, prop in properties(type(obj)).items():
            if prop.kind != "reference":
                continue
            for value in values(obj, name, prop):
                assert isinstance(value, Ref)
                target = index.get(value.identity)
                if target is None and not allow_external:
                    error(obj, name, "unresolved-reference", f"Missing {value.identity}")
                elif (
                    target is not None
                    and prop.target is not None
                    and not isinstance(target, prop.target)
                ):
                    error(
                        obj,
                        name,
                        "reference-type",
                        f"{value.identity} must reference {prop.target.__name__}",
                    )
        orientation = getattr(obj, "orientation", None)
        if orientation is not None and orientation not in {INLINE, REVERSE_COMPLEMENT}:
            error(obj, "orientation", "orientation", "Expected inline or reverseComplement")
        if (
            isinstance(obj, SubComponent)
            and obj.role_integration is not None
            and obj.role_integration not in {SBOL + "mergeRoles", SBOL + "overrideRoles"}
        ):
            error(
                obj, "role_integration", "role-integration", "Expected mergeRoles or overrideRoles"
            )
        if isinstance(obj, SequenceLocation):
            if obj.order is not None and obj.order < 1:
                error(obj, "order", "location-order", "Order must be positive")
            sequence = index.get(obj.sequence.identity)
            length = len(sequence.elements) if isinstance(sequence, Sequence) else None
            if isinstance(obj, Range) and (
                obj.start < 1 or obj.end < obj.start or (length is not None and obj.end > length)
            ):
                error(
                    obj,
                    "start/end",
                    "sequence-range",
                    "Range must be within its sequence (1-based, inclusive)",
                )
            if isinstance(obj, Cut) and (obj.at < 0 or (length is not None and obj.at > length)):
                error(obj, "at", "sequence-cut", "Cut must be between zero and sequence length")
        if isinstance(obj, Attachment):
            if obj.size is not None and obj.size < 0:
                error(obj, "size", "attachment-size", "Size must not be negative")
            if (obj.hash is None) != (obj.hash_algorithm is None):
                error(obj, "hash", "attachment-hash", "Supply both hash and hash_algorithm")
        if (
            isinstance(obj, Implementation)
            and obj.evidence_state is EvidenceState.PLANNED
            and obj.built is not None
        ):
            error(
                obj,
                "built",
                "planned-realization",
                "Use derived_from for the intended design of a planned output",
            )
        if isinstance(obj, Activity):
            times = tuple(t for t in (obj.start_time, obj.end_time) if t is not None)
            aware = all(t.tzinfo is not None and t.utcoffset() is not None for t in times)
            if not aware:
                error(obj, "start_time/end_time", "timezone", "Timestamps must include a timezone")
            if (
                aware
                and obj.start_time is not None
                and obj.end_time is not None
                and obj.end_time < obj.start_time
            ):
                error(obj, "end_time", "time-order", "End precedes start")
            if times and obj.evidence_state is EvidenceState.PLANNED:
                error(
                    obj,
                    "start_time/end_time",
                    "planned-execution",
                    "Planned activities cannot assert execution times",
                )
        if isinstance(obj, VariableFeature) and obj.cardinality not in {
            SBOL + word for word in ("one", "zeroOrOne", "oneOrMore", "zeroOrMore")
        }:
            error(obj, "cardinality", "cardinality", "Unknown SBOL cardinality")
        if isinstance(obj, CombinatorialDerivation):
            if obj.strategy is not None and obj.strategy not in {
                SBOL + "sample",
                SBOL + "enumerate",
            }:
                error(obj, "strategy", "strategy", "Unknown SBOL derivation strategy")
            template = index.get(obj.template.identity)
            if isinstance(template, Component):
                members = {feature.identity for feature in template.features}
                for variable in obj.variable_features:
                    if variable.variable.identity not in members:
                        error(
                            variable,
                            "variable",
                            "feature-scope",
                            "Variable must belong to the template",
                        )
        if isinstance(obj, Component):
            members = {feature.identity for feature in obj.features}
            scoped: list[tuple[Identified, str, Ref[Identified]]] = [
                (constraint, name, ref)
                for constraint in obj.constraints
                for name, ref in (("subject", constraint.subject), ("object", constraint.object))
            ]
            scoped.extend(
                (p, "participant", p.participant)
                for interaction in obj.interactions
                for p in interaction.participations
            )
            if obj.interface is not None:
                scoped.extend(
                    (obj.interface, name, ref)
                    for name in ("inputs", "outputs", "nondirectionals")
                    for ref in getattr(obj.interface, name)
                )
            for scoped_child, name, ref in scoped:
                if ref.identity not in members:
                    error(
                        scoped_child, name, "feature-scope", "Feature must belong to this component"
                    )
            for feature in obj.features:
                if isinstance(feature, ComponentReference):
                    child = index.get(feature.in_child_of.identity)
                    child_component = (
                        index.get(child.instance_of.identity)
                        if isinstance(child, SubComponent)
                        else None
                    )
                    if feature.in_child_of.identity not in members:
                        error(
                            feature,
                            "in_child_of",
                            "feature-scope",
                            "SubComponent must belong to this component",
                        )
                    if isinstance(
                        child_component, Component
                    ) and feature.refers_to.identity not in {
                        item.identity for item in child_component.features
                    }:
                        error(
                            feature,
                            "refers_to",
                            "feature-scope",
                            "Feature must belong to the referenced component",
                        )

    # A temporal dependency graph and a component containment graph must be acyclic.
    def check_cycles(cls: type[Activity] | type[Component], field: str) -> None:
        visited: set[str] = set()
        visiting: set[str] = set()

        def visit(identity: str) -> None:
            obj = index.get(identity)
            if not isinstance(obj, cls) or identity in visited:
                return
            if identity in visiting:
                error(obj, field, "dependency-cycle", "Cyclic dependency")
                return
            visiting.add(identity)
            refs = (
                obj.informed_by
                if isinstance(obj, Activity)
                else tuple(
                    feature.instance_of
                    for feature in obj.features
                    if isinstance(feature, SubComponent)
                )
            )
            for ref in refs:
                visit(ref.identity)
            visiting.remove(identity)
            visited.add(identity)

        for identity in index:
            visit(identity)

    check_cycles(Activity, "informed_by")
    check_cycles(Component, "features")
    return ValidationReport(tuple(errors))
