"""SBOL3 RDF interchange and a detached pySBOL3 adapter.

RDF is the interoperability boundary. Constructing the graph explicitly preserves
owned identities and avoids pySBOL3's Activity.informed_by reparenting behavior.
No builder registrations or process-wide namespaces are changed.
"""

from datetime import datetime
from enum import Enum
from typing import Any

import sbol3 as native
from rdflib import RDF, XSD, BNode, Graph, Literal, URIRef
from rdflib.compare import to_canonical_graph
from rdflib.term import Identifier, Node
from sbol3.identified import extract_display_id, is_valid_display_id

from lab.provenance._schema import RDF_TYPES, Property, properties
from lab.provenance.types import Identified, Ref, TopLevel, require_iri
from lab.provenance.validation import values, walk
from lab.provenance.vocabulary import SBOL, EvidenceState


def _n3(term: Node) -> str:
    assert isinstance(term, (URIRef, BNode, Literal))
    return term.n3()


def canonical_rdf(graph: Graph) -> str:
    """Deterministic Turtle using full IRIs and canonical blank-node identifiers."""
    canonical = to_canonical_graph(graph)
    return "".join(sorted(f"{_n3(s)} {_n3(p)} {_n3(o)} .\n" for s, p, o in canonical))


def graph_for(objects: tuple[TopLevel, ...], extra_rdf: str = "") -> Graph:
    graph = Graph()
    if extra_rdf:
        graph.parse(data=extra_rdf, format="turtle")
    for top in objects:
        for obj in walk(top):
            assert obj.identity is not None
            subject = URIRef(obj.identity)
            graph.add((subject, RDF.type, URIRef(RDF_TYPES[type(obj)])))
            if not RDF_TYPES[type(obj)].startswith(SBOL):
                base = "TopLevel" if isinstance(obj, TopLevel) else "Identified"
                graph.add((subject, RDF.type, URIRef(SBOL + base)))
            # Preserve an imported displayId; otherwise derive it as pySBOL3 does.
            if not list(graph.objects(subject, URIRef(SBOL + "displayId"))):
                display_id = extract_display_id(obj.identity)
                if display_id is not None:
                    graph.add((subject, URIRef(SBOL + "displayId"), Literal(display_id)))
            for name, prop in properties(type(obj)).items():
                for value in values(obj, name, prop):
                    # Absence means unknown; do not fabricate assertions on import.
                    if value is EvidenceState.UNKNOWN:
                        continue
                    if isinstance(value, (Ref, Identified)):
                        assert value.identity is not None
                        term: Identifier = URIRef(value.identity)
                    elif isinstance(value, Enum):
                        term = URIRef(value.value)
                    elif prop.kind == "iri":
                        term = URIRef(str(value))
                    elif prop.kind == "float":
                        term = Literal(float(value), datatype=XSD.float)  # type: ignore[arg-type]
                    else:
                        term = Literal(value)
                    graph.add((subject, URIRef(prop.predicate), term))
    return graph


def serialize(objects: tuple[TopLevel, ...], extra_rdf: str = "") -> str:
    return canonical_rdf(graph_for(objects, extra_rdf))


def parse(data: str, *, format: str = "turtle") -> tuple[tuple[TopLevel, ...], str]:
    """Parse local RDF data; formats that can fetch remote contexts are excluded."""
    if format not in {"turtle", "nt"}:
        raise ValueError("Provenance input must be Turtle or N-Triples")
    # A relative IRI without an explicit @base must not silently become a path
    # inside the caller's current working directory.
    graph = Graph().parse(data=data, format=format, publicID="urn:lab:provenance:input")
    by_type: dict[Node, type[Identified]] = {URIRef(iri): cls for cls, iri in RDF_TYPES.items()}
    classes: dict[Node, type[Identified]] = {}
    for subject, _, rdf_type in graph.triples((None, RDF.type, None)):
        if str(rdf_type).startswith("http://sbols.org/v2#"):
            raise ValueError("SBOL2 input is unsupported; supply an SBOL3 document")
        if rdf_type not in by_type:
            if str(rdf_type) in {SBOL + "TopLevel", SBOL + "Identified"}:
                continue
            if str(rdf_type).startswith(SBOL):
                raise ValueError(f"Unsupported SBOL class {rdf_type}")
            continue
        if not isinstance(subject, URIRef):
            raise ValueError("SBOL objects need absolute IRI identities, not blank nodes")
        if subject in classes and classes[subject] is not by_type[rdf_type]:
            raise ValueError(f"Conflicting SBOL classes for {subject}")
        classes[subject] = by_type[rdf_type]

    consumed = Graph()
    cache: dict[Node, Identified] = {}
    visiting: set[Node] = set()
    owners: dict[Node, Node] = {}

    def decode(term: Node, prop: Property) -> object:
        if prop.kind in {"iri", "reference", "owned", "enum"}:
            if not isinstance(term, URIRef):
                raise ValueError(f"{prop.predicate} requires an IRI, got {term!r}")
            require_iri(str(term))
            if prop.kind == "reference":
                return Ref(str(term))
            if prop.kind == "owned":
                return build(term)
            if prop.kind == "enum":
                assert prop.target is not None
                return prop.target(str(term))
            return str(term)
        if not isinstance(term, Literal):
            raise ValueError(f"{prop.predicate} requires a literal, got {term!r}")
        python = term.toPython()
        if prop.kind == "text":
            if term.language or (term.datatype is not None and term.datatype != XSD.string):
                raise ValueError(f"{prop.predicate} requires an untagged string literal")
            return str(term)
        if prop.kind == "integer" and type(python) is int:
            return python
        if prop.kind == "float" and type(python) in {float, int}:
            return float(python)
        if prop.kind == "datetime" and isinstance(python, datetime):
            return python
        raise ValueError(f"Invalid {prop.kind} literal for {prop.predicate}: {term!r}")

    def build(subject: Node) -> Identified:
        if subject in visiting:
            raise ValueError(f"Cyclic SBOL ownership at {subject}")
        if subject in cache:
            return cache[subject]
        cls = classes.get(subject)
        if cls is None:
            raise ValueError(f"Missing or unsupported owned object {subject}")
        visiting.add(subject)
        args: dict[str, Any] = {"identity": str(subject)}
        consumed.add((subject, RDF.type, URIRef(RDF_TYPES[cls])))
        for name, prop in properties(cls).items():
            predicate = URIRef(prop.predicate)
            terms = sorted(graph.objects(subject, predicate), key=_n3)
            if not prop.multiple and len(terms) > 1:
                raise ValueError(f"{subject}.{name} must have at most one value")
            if prop.required and not terms:
                raise ValueError(f"{subject}.{name} is required")
            if prop.kind == "owned":
                for term in terms:
                    if term in owners:
                        raise ValueError(f"Owned object {term} has more than one owner/property")
                    owners[term] = subject
            if terms:
                decoded = tuple(decode(term, prop) for term in terms)
                args[name] = decoded if prop.multiple else decoded[0]
                for term in terms:
                    consumed.add((subject, predicate, term))
        result = cls(**args)
        cache[subject] = result
        visiting.remove(subject)
        return result

    objects = tuple(
        build(subject)
        for subject in sorted(classes, key=str)
        if issubclass(classes[subject], TopLevel)
    )
    for subject, cls in classes.items():
        if not issubclass(cls, TopLevel) and subject not in owners:
            raise ValueError(f"Orphaned owned object {subject}")
    for subject, _, _ in graph.triples((None, URIRef(SBOL + "hasNamespace"), None)):
        if subject not in classes:
            raise ValueError(f"Unsupported top-level object {subject}")
    extra = graph - consumed
    for subject in classes:
        display_id = extract_display_id(str(subject))
        declared = tuple(graph.objects(subject, URIRef(SBOL + "displayId")))
        if len(declared) > 1:
            raise ValueError(f"{subject}.displayId must have at most one value")
        if declared:
            value = declared[0]
            if (
                not isinstance(value, Literal)
                or value.language
                or value.datatype not in {None, XSD.string}
                or not is_valid_display_id(str(value))
                or (display_id is not None and str(value) != display_id)
            ):
                raise ValueError(f"Invalid displayId for {subject}: {value!r}")
        if display_id is not None:
            extra.remove((subject, URIRef(SBOL + "displayId"), None))
        if not RDF_TYPES[classes[subject]].startswith(SBOL):
            base = "TopLevel" if issubclass(classes[subject], TopLevel) else "Identified"
            extra.remove((subject, RDF.type, URIRef(SBOL + base)))
    # Foreign annotation graphs, including nested blank nodes, are retained. An
    # unrecognized SBOL property is an unsupported schema, not an annotation.
    for subject, predicate, _ in extra:
        if str(predicate).startswith(SBOL) and predicate != URIRef(SBOL + "displayId"):
            raise ValueError(f"Unsupported SBOL property {predicate} on {subject}")
    assert all(isinstance(obj, TopLevel) for obj in objects)
    return tuple(obj for obj in objects if isinstance(obj, TopLevel)), canonical_rdf(extra)


def to_sbol3(objects: tuple[TopLevel, ...], extra_rdf: str = "") -> native.Document:
    document = native.Document()
    document.read_string(serialize(objects, extra_rdf), native.TURTLE)
    # Two further mapping defects in the pinned pySBOL3 release are repaired on
    # these detached instances only. Standard predicates remain intact in RDF.
    for top in objects:
        for obj in walk(top):
            converted = document.find(obj.identity)
            if isinstance(converted, native.Cut):
                object.__setattr__(
                    converted, "at", native.IntProperty(converted, SBOL + "at", 1, 1)
                )
            if isinstance(converted, native.SubComponent):
                object.__setattr__(
                    converted,
                    "role_integration",
                    native.URIProperty(converted, SBOL + "roleIntegration", 0, 1),
                )
    return document


def from_sbol3(document: native.Document) -> tuple[tuple[TopLevel, ...], str]:
    if not isinstance(document, native.Document):
        raise TypeError("Pass a pySBOL3 Document")
    graph = document.graph()
    # Normalize only the known legacy predicates emitted by this pySBOL3 version.
    for subject in graph.subjects(RDF.type, URIRef(SBOL + "Cut")):
        for value in tuple(graph.objects(subject, URIRef(SBOL + "start"))):
            graph.remove((subject, URIRef(SBOL + "start"), value))
            graph.add((subject, URIRef(SBOL + "at"), value))
    for subject in graph.subjects(RDF.type, URIRef(SBOL + "SubComponent")):
        for value in tuple(graph.objects(subject, URIRef(SBOL + "role"))):
            if str(value) in {SBOL + "mergeRoles", SBOL + "overrideRoles"}:
                graph.remove((subject, URIRef(SBOL + "role"), value))
                graph.add((subject, URIRef(SBOL + "roleIntegration"), value))
    return parse(canonical_rdf(graph))
