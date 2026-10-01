import json
from importlib.resources import files
from urllib.parse import unquote

import pytest
from rdflib import OWL, RDF, RDFS, Graph, URIRef

from lab import ExperimentPlan, Protocol, ProtocolStage, celsius, seconds, uL
from lab.deck import Container, Deck, DeckSite
from lab.labop import export
from lab.labop.primitives import LAB, LABOP, LIQUID, OM, SBOL, UML
from lab.labware import PCR_PLATE_96
from lab.provenance import Document


def artifact(*, operations=True):
    protocol = Protocol("Operation vocabulary", identity="https://example.org/operations")
    plate = protocol.plate("plate", capacity=100 * uL)
    protocol.load(plate["A1"], "water", volume=50 * uL)
    protocol.set_temperature(plate, celsius(25))
    protocol.distribute(plate["A1"], (plate["A2"], plate["A3"]), volume=2 * uL, air_gap=1 * uL)
    protocol.wait(1 * seconds)
    protocol.manual("Inspect the plate")
    protocol.thermocycle(
        plate, ((celsius(25), 1 * seconds),), lid_temperature=celsius(40), block_volume=10 * uL
    )
    experiment = ExperimentPlan(
        identity="https://example.org/operation_experiment",
        provenance=Document(namespace="https://example.org/").freeze(),
        stages=(
            ProtocolStage(
                identity="https://example.org/operation_stage",
                protocol=protocol.snapshot(),
                deck=Deck(
                    containers=(
                        Container(id="plate", labware=PCR_PLATE_96, site=DeckSite.THERMOCYCLER),
                    )
                ),
            ),
        ),
    )
    return experiment, export(experiment)


@pytest.mark.parametrize("operations", [True])
def test_labop_has_protocols_exact_semantic_actions_and_no_execution_claims(operations):
    experiment, result = artifact(operations=operations)
    graph = result.graph()
    assert len(tuple(graph.subjects(RDF.type, LABOP.Protocol))) == 1 + len(experiment.stages)
    for kind in (
        LABOP.ProtocolExecution,
        LABOP.BehaviorExecution,
        LABOP.ActivityNodeExecution,
        LABOP.CallBehaviorExecution,
        LABOP.ActivityEdgeFlow,
    ):
        assert not tuple(graph.subjects(RDF.type, kind))
    for predicate in (LABOP.execution, LABOP.completedNormally):
        assert not tuple(graph.triples((None, predicate, None)))
    assert {str(node) for node in graph.subjects(LAB.semanticStep)} == {
        step.identity for stage in experiment.stages for step in stage.protocol.steps
    }
    for node in graph.subjects(RDF.type, LABOP.SampleArray):
        if str(graph.value(node, SBOL.name)) == "products":
            assert all(
                value is None
                for value in json.loads(
                    unquote(str(graph.value(node, LABOP.initial_contents)))
                ).values()
            )
    assert not tuple(graph.subjects(UML.behavior, URIRef(LIQUID + "Provision")))
    amounts = tuple(graph.subjects(RDF.type, OM.Measure))
    assert amounts and all(graph.value(node, OM.hasUnit) for node in amounts)


@pytest.mark.parametrize("operations", [True])
def test_calls_follow_upstream_required_parameter_names_and_directions(operations):
    _, result = artifact(operations=operations)
    graph = result.graph()
    for action in graph.subjects(RDF.type, UML.CallBehaviorAction):
        behavior = graph.value(action, UML.behavior, any=False)
        parameters = [
            graph.value(item, UML.propertyValue)
            for item in graph.objects(behavior, UML.ownedParameter)
        ]
        by_name = {str(graph.value(parameter, SBOL.name)): parameter for parameter in parameters}
        supplied = {}
        for direction, predicate in (("in", UML.input), ("out", UML.output)):
            for pin in graph.objects(action, predicate):
                name = str(graph.value(pin, SBOL.name))
                assert name in by_name, (behavior, name)
                assert graph.value(by_name[name], UML.direction) == UML[direction]
                assert name not in supplied
                supplied[name] = pin
                if direction == "in":
                    constant = graph.value(pin, UML.value)
                    incoming = tuple(graph.subjects(UML.target, pin))
                    assert bool(constant) != bool(incoming)
        for name, parameter in by_name.items():
            lower = graph.value(parameter, UML.lowerValue)
            if graph.value(lower, UML.integerValue).toPython() == 1:
                assert name in supplied, (behavior, name)


@pytest.mark.parametrize("operations", [True])
def test_generated_nodes_satisfy_pinned_ontology_cardinalities(operations):
    _, result = artifact(operations=operations)
    graph = result.graph()
    ontology = Graph()
    for name in ("uml.ttl", "labop.ttl", "lab.ttl"):
        ontology.parse(
            data=files("lab.labop").joinpath("resources", name).read_text(), format="turtle"
        )
    for subject in set(graph.subjects(RDF.type)):
        pending = list(graph.objects(subject, RDF.type))
        seen = set()
        while pending:
            kind = pending.pop()
            if kind in seen:
                continue
            seen.add(kind)
            for parent in ontology.objects(kind, RDFS.subClassOf):
                if isinstance(parent, URIRef):
                    pending.append(parent)
                    continue
                predicate = ontology.value(parent, OWL.onProperty)
                count = len(tuple(graph.objects(subject, predicate)))
                minimum = ontology.value(parent, OWL.minCardinality)
                maximum = ontology.value(parent, OWL.maxCardinality)
                exact = ontology.value(parent, OWL.cardinality)
                if minimum is not None:
                    assert count >= int(minimum), (subject, predicate, minimum)
                if maximum is not None:
                    assert count <= int(maximum), (subject, predicate, maximum)
                if exact is not None:
                    assert count == int(exact), (subject, predicate, exact)
    # Unknown properties in upstream namespaces generally indicate a spelling or version error.
    declared = set(ontology.subjects())
    for predicate in set(graph.predicates()):
        if str(predicate).startswith((str(LABOP), str(UML))):
            assert predicate in declared, predicate
