from pathlib import Path

import pytest
import sbol3
from rdflib import RDF, Graph, Literal, URIRef
from rdflib.compare import isomorphic

from lab.provenance import Activity, Component, Document, EvidenceState
from lab.provenance.vocabulary import SBOL

NS = "https://example.org/native"
FIXTURE = Path(__file__).parents[1] / "fixtures/provenance/design.ttl"


def test_independently_authored_rdf_and_foreign_annotations_are_preserved():
    original = Graph().parse(FIXTURE, format="turtle")
    document = Document.read(FIXTURE)
    exported = Graph().parse(data=document.freeze().to_turtle(), format="turtle")
    assert isomorphic(original, exported)
    restored = Document.from_turtle(document.freeze().to_turtle()).freeze()
    assert restored.digest == document.freeze().digest
    native = restored.to_sbol3()
    assert native.find("https://example.org/fixture/design/site/cut").at == 4
    assert isomorphic(original, native.graph())


def test_native_provenance_document_import_has_no_inferred_execution_claims():
    native = sbol3.Document()
    agent = sbol3.Agent(NS + "/agent")
    plan = sbol3.Plan(NS + "/plan")
    design = sbol3.Component(NS + "/design", [sbol3.SBO_DNA])
    activity = sbol3.Activity(
        NS + "/activity",
        usage=[sbol3.Usage(design.identity)],
        association=[sbol3.Association(agent=agent, plan=plan)],
    )
    native.add([agent, plan, design, activity])
    imported = Document.from_sbol3(native).freeze()
    result = imported.get(activity.identity, Activity)
    assert result.evidence_state is EvidenceState.UNKNOWN
    assert result.start_time is None and result.end_time is None
    assert result.usage[0].identity == activity.usage[0].identity
    assert isomorphic(native.graph(), imported.to_sbol3().graph())


def test_native_cut_and_role_integration_defects_are_normalized_only_at_adapter_boundary():
    native = sbol3.Document()
    sequence = sbol3.Sequence(NS + "/sequence", elements="ACGT", encoding=sbol3.IUPAC_DNA_ENCODING)
    part = sbol3.Component(NS + "/part", [sbol3.SBO_DNA])
    feature = sbol3.SubComponent(
        part, role_integration=SBOL + "mergeRoles", locations=[sbol3.Cut(sequence, 2)]
    )
    design = sbol3.Component(NS + "/design", [sbol3.SBO_DNA], features=[feature])
    native.add([sequence, part, design])
    imported = Document.from_sbol3(native).freeze()
    sub = imported.get(design.identity, Component).features[0]
    assert sub.role_integration == SBOL + "mergeRoles"
    assert sub.roles == ()
    assert sub.locations[0].at == 2
    graph = Graph().parse(data=imported.to_turtle(), format="turtle")
    assert (
        URIRef(feature.identity),
        URIRef(SBOL + "roleIntegration"),
        URIRef(SBOL + "mergeRoles"),
    ) in graph
    assert not list(graph.triples((None, URIRef(SBOL + "start"), None)))
    # The source document remains untouched.
    assert feature.role_integration == SBOL + "mergeRoles"
    assert list(native.graph().triples((None, URIRef(SBOL + "start"), None)))


def test_input_with_multiple_scalar_values_is_not_silently_truncated():
    graph = Graph().parse(FIXTURE, format="turtle")
    graph.add(
        (URIRef("https://example.org/fixture/sequence"), URIRef(SBOL + "elements"), Literal("AAAA"))
    )
    with pytest.raises(ValueError, match="at most one"):
        Document.from_turtle(graph.serialize(format="turtle"))


@pytest.mark.parametrize("rdf_type", ["http://sbols.org/v2#ComponentDefinition", SBOL + "Unknown"])
def test_unsupported_sbol_versions_and_classes_fail_explicitly(rdf_type):
    text = f"<{NS}/object> <{RDF.type}> <{rdf_type}> ."
    with pytest.raises(ValueError, match="SBOL2|Unsupported SBOL"):
        Document.from_turtle(text, namespace=NS)


def test_unknown_sbol_properties_are_not_disguised_as_annotations():
    graph = Graph().parse(FIXTURE, format="turtle")
    graph.add(
        (
            URIRef("https://example.org/fixture/design"),
            URIRef(SBOL + "unimplemented"),
            Literal("value"),
        )
    )
    with pytest.raises(ValueError, match="Unsupported SBOL property"):
        Document.from_turtle(graph.serialize(format="turtle"))


def test_relative_iris_do_not_depend_on_the_working_directory():
    with pytest.raises(ValueError, match="relative"):
        Document.from_turtle(f"<relative> <{RDF.type}> <{SBOL}Component> .", namespace=NS)


def test_display_ids_cannot_conflict_with_object_identity():
    graph = Graph().parse(FIXTURE, format="turtle")
    graph.set(
        (URIRef("https://example.org/fixture/design"), URIRef(SBOL + "displayId"), Literal("other"))
    )
    with pytest.raises(ValueError, match="Invalid displayId"):
        Document.from_turtle(graph.serialize(format="turtle"))


def test_orphaned_and_multiply_owned_children_are_rejected_on_import():
    graph = Graph().parse(FIXTURE, format="turtle")
    graph.remove((URIRef("https://example.org/fixture/design"), URIRef(SBOL + "hasFeature"), None))
    with pytest.raises(ValueError, match="Orphaned"):
        Document.from_turtle(graph.serialize(format="turtle"))
    graph = Graph().parse(FIXTURE, format="turtle")
    first = URIRef("https://example.org/fixture/design")
    second = URIRef("https://example.org/fixture/second")
    for _, predicate, obj in tuple(graph.triples((first, None, None))):
        if predicate != URIRef(SBOL + "displayId"):
            graph.add((second, predicate, obj))
    with pytest.raises(ValueError, match="more than one owner"):
        Document.from_turtle(graph.serialize(format="turtle"))
