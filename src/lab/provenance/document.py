"""Explicit document ownership, immutable snapshots, and local interchange."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, TypeVar, cast

import sbol3 as native

from lab.provenance import sbol3 as adapter
from lab.provenance._schema import properties
from lab.provenance.types import Identified, Ref, TopLevel, require_iri
from lab.provenance.validation import (
    ProvenanceError,
    ValidationReport,
    validate_objects,
    validate_structure,
    values,
    walk,
)

T = TypeVar("T", bound=Identified)


def _find(objects: tuple[TopLevel, ...], identity: str) -> Identified:
    for top in objects:
        for obj in walk(top):
            if obj.identity == identity:
                return obj
    raise KeyError(identity)


def _normalized(objects: tuple[TopLevel, ...], namespace: str) -> tuple[TopLevel, ...]:
    validate_structure(objects).raise_for_errors()
    reserved = {obj.identity for top in objects for obj in walk(top) if obj.identity is not None}

    def normalize(obj: Identified, identity: str) -> Identified:
        changes: dict[str, Any] = {"identity": identity}
        if isinstance(obj, TopLevel) and obj.namespace is None:
            if identity.startswith(namespace):
                changes["namespace"] = namespace
            else:
                # Imported/mixed namespaces remain explicit, independent of the
                # document's namespace for new authoring.
                changes["namespace"] = identity.rsplit("/", 1)[0] if "/" in identity else namespace
        counts: dict[str, int] = {}
        for name, prop in properties(type(obj)).items():
            items = values(obj, name, prop)
            if not items:
                continue
            if prop.kind == "owned":
                children: list[Identified] = []
                for child in items:
                    assert isinstance(child, Identified)
                    child_id = child.identity
                    if child_id is None:
                        label = type(child).__name__
                        number = counts.get(label, 0) + 1
                        child_id = f"{identity}/{label}{number}"
                        while child_id in reserved:
                            number += 1
                            child_id = f"{identity}/{label}{number}"
                        counts[label] = number
                        reserved.add(child_id)
                    children.append(normalize(child, child_id))
                changes[name] = (
                    tuple(sorted(children, key=lambda child: child.identity or ""))
                    if prop.multiple
                    else children[0]
                )
            elif prop.multiple:
                # SBOL multi-valued properties are RDF sets. Biological order is
                # encoded by locations and constraints, never tuple position.
                changes[name] = tuple(
                    sorted(
                        items,
                        key=lambda item: item.identity if isinstance(item, Ref) else str(item),
                    )
                )
        return replace(obj, **changes)

    return tuple(
        cast(TopLevel, normalize(obj, obj.identity))
        for obj in sorted(objects, key=lambda obj: obj.identity)
    )


def _write(path: str | Path, text: str) -> Path:
    path = Path(path)
    if path.exists() and path.read_text(encoding="utf-8") != text:
        raise FileExistsError(f"{path} already contains a different artifact")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@dataclass(frozen=True, kw_only=True)
class DocumentSnapshot:
    namespace: str
    objects: tuple[TopLevel, ...]
    allow_external: bool = False
    _extra_rdf: str = ""

    def __post_init__(self) -> None:
        require_iri(self.namespace)
        if not isinstance(self.objects, tuple):
            raise TypeError("Snapshot objects must be a tuple")
        self.validate().raise_for_errors()

    def get(self, identity: str, expected: type[T]) -> T:
        obj = _find(self.objects, identity)
        if not isinstance(obj, expected):
            raise TypeError(f"{identity} is {type(obj).__name__}, not {expected.__name__}")
        return obj

    def resolve(self, ref: Ref[T]) -> T:
        return cast(T, _find(self.objects, ref.identity))

    def validate(self) -> ValidationReport:
        return validate_objects(self.objects, allow_external=self.allow_external)

    def to_sbol3(self) -> native.Document:
        """Return a new mutable pySBOL3 document, detached from this snapshot."""
        return adapter.to_sbol3(self.objects, self._extra_rdf)

    def to_turtle(self) -> str:
        return adapter.serialize(self.objects, self._extra_rdf)

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.to_turtle().encode("utf-8")).hexdigest()

    def write(self, path: str | Path) -> Path:
        return _write(path, self.to_turtle())


class Document:
    """An explicit collection of SBOL top-level objects.

    Adding objects does not add their referenced objects, infer missing designs,
    fetch URLs, or change a global namespace. ``freeze`` assigns identities to
    owned children and checks reference closure before returning a snapshot.
    """

    def __init__(self, *, namespace: str) -> None:
        self.namespace = require_iri(namespace)
        self._objects: dict[str, TopLevel] = {}
        self._extra_rdf = ""

    def iri(self, local_id: str) -> str:
        if not isinstance(local_id, str) or not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*(/[A-Za-z_][A-Za-z0-9_]*)*", local_id
        ):
            raise ValueError(
                "Use slash-separated SBOL display IDs containing letters, digits, or underscores"
            )
        return self.namespace.rstrip("/") + "/" + local_id

    @property
    def objects(self) -> tuple[TopLevel, ...]:
        return tuple(self._objects.values())

    def add(self, *objects: TopLevel) -> None:
        """Add top-level objects atomically; an identical addition is idempotent."""
        validate_structure(objects).raise_for_errors()
        updated = dict(self._objects)
        for obj in objects:
            existing = updated.get(obj.identity)
            if (
                existing is not None
                and existing != obj
                and _normalized((existing,), self.namespace) != _normalized((obj,), self.namespace)
            ):
                raise ValueError(f"Conflicting definition for {obj.identity}")
            updated[obj.identity] = existing or obj
        self._objects = updated

    def replace(self, *objects: TopLevel) -> None:
        """Explicitly revise existing definitions in this authoring document.

        Frozen snapshots remain unchanged. Identity and concrete type must already
        exist; freeze validates references and evidence after the revisions.
        """
        validate_structure(objects).raise_for_errors()
        updated = dict(self._objects)
        for obj in objects:
            if obj.identity not in updated:
                raise KeyError(obj.identity)
            if type(updated[obj.identity]) is not type(obj):
                raise TypeError("Replacing a definition cannot change its concrete type")
            updated[obj.identity] = obj
        self._objects = updated

    def get(self, identity: str, expected: type[T]) -> T:
        obj = _find(self.objects, identity)
        if not isinstance(obj, expected):
            raise TypeError(f"{identity} is {type(obj).__name__}, not {expected.__name__}")
        return obj

    def resolve(self, ref: Ref[T]) -> T:
        return cast(T, _find(self.objects, ref.identity))

    def validate(self, *, allow_external: bool = False) -> ValidationReport:
        try:
            objects = _normalized(self.objects, self.namespace)
        except ProvenanceError as error:
            return error.report
        return validate_objects(objects, allow_external=allow_external)

    def freeze(self, *, allow_external: bool = False) -> DocumentSnapshot:
        return DocumentSnapshot(
            namespace=self.namespace,
            objects=_normalized(self.objects, self.namespace),
            allow_external=allow_external,
            _extra_rdf=self._extra_rdf,
        )

    def to_sbol3(self, *, allow_external: bool = False) -> native.Document:
        return self.freeze(allow_external=allow_external).to_sbol3()

    def write(self, path: str | Path, *, allow_external: bool = False) -> Path:
        return self.freeze(allow_external=allow_external).write(path)

    @classmethod
    def from_snapshot(cls, snapshot: DocumentSnapshot) -> Document:
        result = cls(namespace=snapshot.namespace)
        result.add(*snapshot.objects)
        result._extra_rdf = snapshot._extra_rdf
        return result

    @classmethod
    def _from_objects(
        cls, objects: tuple[TopLevel, ...], extra_rdf: str, namespace: str | None
    ) -> Document:
        if namespace is None:
            namespaces = {obj.namespace for obj in objects if obj.namespace is not None}
            if len(namespaces) != 1:
                raise ValueError("Supply namespace= for an empty document or multiple namespaces")
            namespace = namespaces.pop()
        assert namespace is not None
        result = cls(namespace=namespace)
        result.add(*objects)
        result._extra_rdf = extra_rdf
        return result

    @classmethod
    def from_sbol3(cls, document: native.Document, *, namespace: str | None = None) -> Document:
        objects, extra = adapter.from_sbol3(document)
        return cls._from_objects(objects, extra, namespace)

    @classmethod
    def from_turtle(cls, text: str, *, namespace: str | None = None) -> Document:
        objects, extra = adapter.parse(text)
        return cls._from_objects(objects, extra, namespace)

    @classmethod
    def read(cls, path: str | Path, *, namespace: str | None = None) -> Document:
        """Read local Turtle; referenced resources are never fetched."""
        return cls.from_turtle(Path(path).read_text(encoding="utf-8"), namespace=namespace)
