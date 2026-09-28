from datetime import UTC, datetime, timedelta

import pytest
from rdflib import RDF, Graph, URIRef

from lab.provenance import (
    Activity,
    Agent,
    AgentKind,
    Association,
    Component,
    Document,
    EvidenceState,
    Implementation,
    Plan,
    Ref,
    Usage,
)
from lab.provenance.vocabulary import DNA, LAB, PROV, SBOL

NS = "https://example.org/provenance"


def test_qualified_provenance_has_correct_directions_and_owned_cardinalities():
    document = Document(namespace=NS)
    design = Component(identity=document.iri("design"), types=(DNA,))
    software = Agent(
        identity=document.iri("compiler"), kind=AgentKind.SOFTWARE, software_version="1.2"
    )
    plan = Plan(identity=document.iri("method"), protocol=document.iri("protocol"))
    planned = Activity(
        identity=document.iri("planned"),
        evidence_state=EvidenceState.PLANNED,
        types=(LAB + "assembly",),
        usage=(Usage(entity=design.ref, roles=(LAB + "design",)),),
        association=(Association(agent=software.ref, plan=plan.ref, roles=(LAB + "planner",)),),
    )
    output = Implementation(
        identity=document.iri("output"),
        derived_from=(design.ref,),
        generated_by=(planned.ref,),
        evidence_state=EvidenceState.PLANNED,
    )
    document.add(design, software, plan, planned, output)
    frozen = document.freeze()
    graph = Graph().parse(data=frozen.to_turtle(), format="turtle")
    assert (
        URIRef(output.identity),
        URIRef(PROV + "wasGeneratedBy"),
        URIRef(planned.identity),
    ) in graph
    assert (URIRef(planned.identity), URIRef(SBOL + "type"), URIRef(LAB + "assembly")) in graph
    assert (URIRef(planned.identity), RDF.type, URIRef(LAB + "assembly")) not in graph
    assert not list(graph.objects(URIRef(output.identity), URIRef(SBOL + "built")))
    native = frozen.to_sbol3()
    assert len(native.objects) == 5
    activity = native.find(planned.identity)
    assert len(activity.usage) == len(activity.association) == 1
    assert activity.association[0].agent == software.identity
    assert activity.association[0].plan == plan.identity
    assert activity.start_time is None and activity.end_time is None
    assert not native.validate().errors
    assert Document.from_sbol3(native).freeze() == frozen


def test_shared_predecessors_round_trip_without_reparenting():
    document = Document(namespace=NS)
    parent = Activity(identity=document.iri("parent"))
    left = Activity(identity=document.iri("left"), informed_by=(parent.ref,))
    right = Activity(identity=document.iri("right"), informed_by=(parent.ref,))
    document.add(parent, left, right)
    native = document.to_sbol3()
    assert len(native.objects) == 3
    assert native.find(left.identity).informed_by[0] is native.find(parent.identity)
    assert native.find(right.identity).informed_by[0] is native.find(parent.identity)
    assert Document.from_sbol3(native).freeze() == document.freeze()


def test_execution_timestamps_retain_timezone_and_evidence_kind():
    start = datetime(2026, 9, 27, 10, tzinfo=UTC)
    document = Document(namespace=NS)
    activity = Activity(
        identity=document.iri("execution"),
        start_time=start,
        end_time=start + timedelta(seconds=5),
        evidence_state=EvidenceState.SIMULATED,
    )
    document.add(activity)
    frozen = document.freeze()
    restored = Document.from_sbol3(frozen.to_sbol3()).freeze()
    assert restored == frozen
    assert restored.resolve(activity.ref).evidence_state is EvidenceState.SIMULATED


@pytest.mark.parametrize(
    "activity,code",
    [
        (Activity(identity=NS + "/a", start_time=datetime(2026, 1, 1)), "timezone"),
        (
            Activity(
                identity=NS + "/a",
                start_time=datetime(2026, 1, 2, tzinfo=UTC),
                end_time=datetime(2026, 1, 1, tzinfo=UTC),
            ),
            "time-order",
        ),
        (
            Activity(
                identity=NS + "/a",
                start_time=datetime(2026, 1, 1, tzinfo=UTC),
                evidence_state=EvidenceState.PLANNED,
            ),
            "planned-execution",
        ),
    ],
)
def test_invalid_execution_assertions_are_rejected(activity, code):
    document = Document(namespace=NS)
    document.add(activity)
    with pytest.raises(ValueError, match=code):
        document.freeze()


def test_planned_implementation_does_not_assert_realized_structure():
    document = Document(namespace=NS)
    component = Component(identity=document.iri("design"), types=(DNA,))
    document.add(
        component,
        Implementation(
            identity=document.iri("output"),
            built=component.ref,
            evidence_state=EvidenceState.PLANNED,
        ),
    )
    with pytest.raises(ValueError, match="planned-realization"):
        document.freeze()


def test_activity_cycles_and_wrong_reference_types_are_rejected():
    document = Document(namespace=NS)
    a = Activity(identity=document.iri("a"), informed_by=(Ref(document.iri("b")),))
    b = Activity(identity=document.iri("b"), informed_by=(a.ref,))
    document.add(a, b)
    with pytest.raises(ValueError, match="dependency-cycle"):
        document.freeze()
    document = Document(namespace=NS)
    document.add(Agent(identity=document.iri("b")), a)
    with pytest.raises(ValueError, match="reference-type"):
        document.freeze()
