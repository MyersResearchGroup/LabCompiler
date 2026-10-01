"""Check upstream export against compiler-generated fixtures, in the integration environment."""

import argparse
import copy
import json
from pathlib import Path
from typing import Any

import sbol3
from export import LAB, LABOP, OM, UML, export, semantic
from rdflib import RDF, Graph, Literal, Namespace, URIRef
from rdflib.compare import isomorphic
from rdflib.term import Node

SBOL = Namespace("http://sbols.org/v3#")


def literal(graph: Graph, subject: Node | None, predicate: URIRef) -> Literal:
    assert subject is not None
    value = graph.value(subject, predicate, any=False)
    assert isinstance(value, Literal), (subject, predicate, value)
    return value


def check(plan: dict[str, Any]) -> Graph:
    document = export(plan)
    graph: Graph = document.graph()
    recorded = semantic(plan["protocol"])
    protocols = list(graph.subjects(RDF.type, LABOP.Protocol))
    assert len(protocols) == 1
    assert graph.value(protocols[0], LAB.evidenceState) == LAB.planned
    assert not any("Execution" in str(kind) for kind in graph.objects(None, RDF.type))
    actions = sorted(
        graph.subjects(LAB.semanticStep), key=lambda node: int(literal(graph, node, LAB.stepIndex))
    )
    assert [json.loads(str(graph.value(node, LAB.semanticStep))) for node in actions] == recorded[
        "steps"
    ]
    for action, step in zip(actions, recorded["steps"], strict=True):
        pins = {str(graph.value(pin, SBOL.name)): pin for pin in graph.objects(action, UML.input)}
        if "volume" in step:
            argument = graph.value(pins["amount"], UML.value)
            amount = graph.value(argument, UML.identifiedValue)
            assert graph.value(amount, OM.hasUnit) == OM.microlitre
            assert float(literal(graph, amount, OM.hasNumericalValue)) == float(step["volume"])
        if step["kind"] == "Thermocycle":
            argument = graph.value(pins["profile"], UML.value)
            assert json.loads(str(literal(graph, argument, UML.stringValue))) == step["profile"]
            argument = graph.value(pins["cycles"], UML.value)
            assert int(literal(graph, argument, UML.integerValue)) == step["cycles"]
    samples = {sample["id"]: sample for sample in recorded["samples"]}
    for resource in recorded["resources"]:
        container = next(
            node
            for node in graph.subjects(RDF.type, LABOP.ContainerSpec)
            if str(graph.value(node, SBOL.name)) == resource["name"]
        )
        assert json.loads(str(graph.value(container, LAB.resource))) == resource
        array = next(graph.subjects(LABOP.containerType, container))
        expected = [
            {"well": place["location"]["well"], "sample": samples[place["sample_id"]]}
            for place in recorded["placements"]
            if place["location"]["resource"] == resource["name"]
        ]
        assert json.loads(str(graph.value(array, LAB.samples))) == expected
    for node in graph.subjects(RDF.type, OM.Measure):
        assert graph.value(node, OM.hasUnit)
        assert graph.value(node, OM.hasNumericalValue) is not None
    for name in ("design", "implementation"):
        expected_refs = {
            sample[name]
            for sample in recorded["samples"]
            if sample[name] is not None
            and any(place["sample_id"] == sample["id"] for place in recorded["placements"])
        }
        assert {str(value) for value in graph.objects(None, LAB[name])} == expected_refs

    for action in graph.subjects(RDF.type, UML.CallBehaviorAction):
        behavior = graph.value(action, UML.behavior)
        for item in graph.objects(behavior, UML.ownedParameter):
            parameter = graph.value(item, UML.propertyValue)
            name = str(graph.value(parameter, SBOL.name))
            direction = graph.value(parameter, UML.direction)
            predicate = UML.output if direction == UML["out"] else UML.input
            parameter_pins = [
                pin
                for pin in graph.objects(action, predicate)
                if str(graph.value(pin, SBOL.name)) == name
            ]
            assert len(parameter_pins) == 1, (behavior, name)
            if direction == UML["in"]:
                lower = graph.value(parameter, UML.lowerValue)
                required = int(literal(graph, lower, UML.integerValue)) == 1
                if required:
                    assert graph.value(parameter_pins[0], UML.value) is not None or list(
                        graph.subjects(UML.target, parameter_pins[0])
                    )

    # There must be one sequential control path, including all selection/group actions.
    first = next(graph.subjects(RDF.type, UML.InitialNode))
    last = next(graph.subjects(RDF.type, UML.FinalNode))
    successors: dict[Node, Node] = {}
    for edge in graph.subjects(RDF.type, UML.ControlFlow):
        source = graph.value(edge, UML.source)
        target = graph.value(edge, UML.target)
        assert source is not None and target is not None
        assert source not in successors
        successors[source] = target
    visited: list[Node] = []
    node = first
    while node != last:
        assert node not in visited
        visited.append(node)
        node = successors[node]
    assert set(visited[1:]) == set(graph.subjects(RDF.type, UML.CallBehaviorAction))
    assert [item for item in visited if item in actions] == actions
    assert len(visited) == len(successors)

    # Check RDF serialization. Upstream Protocol's constructor mutates control flow on read.
    restored = Graph().parse(data=document.write_string(sbol3.TURTLE), format="turtle")
    assert isomorphic(graph, restored)
    return graph


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixtures", type=Path)
    args = parser.parse_args()
    paths = sorted(args.fixtures.rglob("plan.json"))
    if not paths:
        raise ValueError("Generate the example compilation fixtures first")
    kinds: set[str] = set()
    for path in paths:
        plan = json.loads(path.read_text())
        graph = check(plan)
        kinds.update(step["kind"] for step in plan["protocol"]["steps"])
        print(f"{path}: validated and RDF round-tripped")
    assert kinds == {
        "Transfer",
        "Mix",
        "Distribute",
        "Wait",
        "SetTemperature",
        "Thermocycle",
        "ManualInstruction",
    }
    moved = copy.deepcopy(plan)
    moved["target"] = {"name": "Different robot"}
    for step in moved["protocol"]["steps"]:
        step["origin"] = {"file": "/another/computer.py", "line": 999}
    assert isomorphic(graph, export(moved).graph())
    moved["protocol"]["name"] += " changed"
    assert set(graph.subjects(RDF.type, LABOP.Protocol)) != set(
        export(moved).graph().subjects(RDF.type, LABOP.Protocol)
    )
    for invalid, expected_error in (
        ({"format": "unknown"}, "Expected a lab.plan.v1"),
        ({**plan, "units": {}}, "Unsupported compilation units"),
        (
            {**plan, "protocol": {**plan["protocol"], "steps": [{"kind": "unknown"}]}},
            "No LabOP mapping for unknown",
        ),
    ):
        try:
            export(invalid)
        except ValueError as error:
            assert expected_error in str(error)
        else:
            raise AssertionError("Unsupported input must fail")
    print("Operation mappings, control flow, SBOL links, round-trips, and identities passed")


if __name__ == "__main__":
    main()
