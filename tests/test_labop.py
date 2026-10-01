import json
from dataclasses import replace
from urllib.parse import unquote

import pytest
import sbol3
from rdflib import RDF, URIRef

import lab
from examples.cloning import ASSEMBLIES
from examples.sbol_provenance import inputs
from lab import Protocol, celsius, seconds, uL
from lab.deck import Container, Deck, DeckSite
from lab.experiments.cloning import build_assembly
from lab.labop import export
from lab.labop.primitives import LAB, LABOP, LIQUID, OM, SBOL, UML
from lab.labware import PCR_PLATE_96
from lab.model import Origin
from lab.targets import LiquidHandler, Manual


def artifact(*, operations="operations"):
    if operations == "assembly":
        protocol = build_assembly(ASSEMBLIES)
    else:
        protocol = Protocol("Operation vocabulary")
        plate = protocol.plate("plate", capacity=100 * uL)
        protocol.load(plate["A1"], "water", volume=50 * uL)
        protocol.set_temperature(plate, celsius(25))
        protocol.distribute(plate["A1"], (plate["A2"], plate["A3"]), volume=2 * uL, air_gap=1 * uL)
        protocol.transfer(plate["A1"], plate["A4"], volume=2 * uL)
        protocol.mix(plate["A1"], volume=2 * uL, cycles=2)
        protocol.wait(1 * seconds)
        protocol.manual("Inspect the plate")
        protocol.thermocycle(
            plate,
            ((celsius(25), 1 * seconds),),
            lid_temperature=celsius(40),
            block_volume=10 * uL,
        )
    recorded = protocol.snapshot()
    return recorded, export(recorded)


@pytest.mark.parametrize("operations", ["operations", "assembly"])
def test_labop_has_protocols_exact_semantic_actions_and_no_execution_claims(operations):
    experiment, result = artifact(operations=operations)
    graph = result.graph()
    assert len(tuple(graph.subjects(RDF.type, LABOP.Protocol))) == 1
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
        f"{result.protocol}/step_{index + 1}" for index in range(len(experiment.steps))
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


@pytest.mark.parametrize("operations", ["operations", "assembly"])
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


def test_export_snapshots_authoring_and_rejects_invalid_material_accounting():
    protocol = Protocol("Direct export")
    source = protocol.container("source", contents="water", volume=10 * uL, capacity=20 * uL)
    destination = protocol.container("destination", capacity=20 * uL)
    protocol.transfer(source, destination, volume=2 * uL)
    recorded = protocol.snapshot()
    exported = export(protocol)
    compilation = lab.compile(protocol, Manual(), to=None)
    assert compilation.files["protocol.labop.ttl"] == exported.text == export(recorded).text
    protocol.transfer(source, destination, volume=20 * uL)
    assert export(recorded).text == exported.text
    assert compilation.files["protocol.labop.ttl"] == exported.text
    with pytest.raises(lab.CompileError):
        export(protocol)


@pytest.mark.integration
@pytest.mark.parametrize("handler", list(LiquidHandler))
def test_labop_is_identical_across_robot_and_manual_compilation(handler):
    pytest.importorskip("pylabrobot" if handler is LiquidHandler.STAR else "opentrons")
    protocol = Protocol("Shared target input")
    plate = protocol.plate("plate", capacity=100 * uL)
    protocol.load(plate["A1"], "water", volume=20 * uL)
    protocol.transfer(plate["A1"], plate["A2"], volume=10 * uL)
    manual = lab.compile(protocol, Manual(), to=None)
    deck = Deck(containers=(Container(id="plate", labware=PCR_PLATE_96, site=DeckSite.PLATES),))
    robot = lab.compile(protocol, deck=deck, liquid_handler=handler, to=None)
    assert (
        robot.files["protocol.labop.ttl"]
        == manual.files["protocol.labop.ttl"]
        == export(protocol).text
    )


def test_automatic_identities_are_accepted_without_rewriting_by_sbol3():
    protocol = Protocol("Automatic identity")
    protocol.wait(1 * seconds)
    graph = export(protocol).graph()
    for kind in (SBOL.TopLevel, SBOL.Identified):
        for identity in graph.subjects(RDF.type, kind):
            parsed = sbol3.Identified(str(identity), type_uri=str(kind))
            assert parsed.identity == str(identity)
            assert parsed.display_id == str(graph.value(identity, SBOL.displayId))


def test_export_identity_depends_on_meaning_and_accepts_an_explicit_sbol_identity():
    protocol, original = artifact()
    moved = replace(
        protocol,
        steps=tuple(
            replace(step, origin=Origin("/other/computer.py", 999)) for step in protocol.steps
        ),
    )
    assert export(moved).text == original.text
    identity = "https://example.org/protocols/aliquot"
    custom = export(protocol, identity=identity)
    assert custom.protocol == identity
    assert (URIRef(identity), RDF.type, LABOP.Protocol) in custom.graph()
    for invalid in (
        "",
        "relative",
        "https://example.org/bad-id",
        "https://bad host/protocol",
        "https://host_name",
        "https://example.org/plan?x=/valid",
    ):
        with pytest.raises(ValueError):
            export(protocol, identity=invalid)


def test_labop_links_native_sbol_designs_without_embedding_the_document():
    from_example, protocol = inputs()
    recorded = protocol.snapshot()
    stock = "https://example.org/aliquot/stock"
    recorded = replace(
        recorded,
        samples=(replace(recorded.samples[0], implementation=stock), *recorded.samples[1:]),
    )
    result = export(recorded)
    assert tuple(result.graph().subjects(LAB.implementation, URIRef(stock)))
    design = from_example.find("https://example.org/aliquot/design")
    assert len(tuple(result.graph().subjects(LAB.design, URIRef(design.identity)))) == 2
    assert not tuple(result.graph().subjects(RDF.type, SBOL.Component))
