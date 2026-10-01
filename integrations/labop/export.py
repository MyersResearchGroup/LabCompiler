"""Export lab.plan.v1 with upstream LabOP in a separate Python environment."""

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any
from urllib.parse import quote

import labop
import sbol3
import uml
from rdflib import Namespace
from rdflib.compare import to_canonical_graph

LAB = Namespace("https://the-lab-compiler.github.io/lab-py/ns#")
LABOP = Namespace("http://bioprotocols.org/labop#")
UML = Namespace("http://bioprotocols.org/uml#")
OM = Namespace("http://www.ontology-of-units-of-measure.org/resource/om-2/")
EXT = "https://the-lab-compiler.github.io/lab-py/labop/primitives/"

# These describe Lab operations whose semantics differ from the upstream library.
EXTENSIONS = {
    "Wait": (("duration", OM.Measure),),
    "OperatorPause": (("instruction", UML.ValueSpecification),),
    "SetTemperature": (("samples", LABOP.SampleCollection), ("temperature", OM.Measure)),
    "Thermocycle": (
        ("samples", LABOP.SampleCollection),
        ("profile", UML.ValueSpecification),
        ("cycles", UML.ValueSpecification),
    ),
    "Distribute": (
        ("source", LABOP.SampleCollection),
        ("destinations", LABOP.SampleCollection),
        ("amount", OM.Measure),
    ),
}
DESCRIPTIONS = {
    "Wait": "Wait for the duration without changing material.",
    "OperatorPause": "Pause for the operator instruction; no material change is modeled.",
    "SetTemperature": "Set a persistent temperature; the hold continues after this action.",
    "Thermocycle": "Repeat the ordered JSON profile of {celsius, seconds} holds, "
    "then release temperature control.",
    "Distribute": "Transfer equal amounts to ordered destinations, reusing one tip. "
    "Air gap is air; a target may split aspirations within its capacity.",
}


def semantic(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: semantic(item) for key, item in value.items() if key != "origin"}
    if isinstance(value, list):
        return [semantic(item) for item in value]
    return value


def text(obj: sbol3.Identified, name: str, value: str) -> None:
    setattr(obj, "lab_" + name, sbol3.TextProperty(obj, str(LAB[name]), 0, 1, initial_value=value))


def measure(value: str | int | float, unit: str) -> sbol3.Measure:
    return sbol3.Measure(float(value), str(OM[unit]))


def export(plan: dict[str, Any]) -> sbol3.Document:
    """Map a compiler artifact to an upstream SBOL document; never execute the plan."""
    if plan.get("format") != "lab.plan.v1":
        raise ValueError("Expected a lab.plan.v1 compilation artifact")
    if plan["units"] != {"volume": "microliter", "duration": "second", "temperature": "celsius"}:
        raise ValueError("Unsupported compilation units")
    recorded = semantic(plan["protocol"])
    digest = hashlib.sha256(json.dumps(recorded, sort_keys=True).encode()).hexdigest()
    identity = "https://the-lab-compiler.github.io/lab-py/protocols/p_" + digest
    document = sbol3.Document()
    protocol = labop.Protocol(identity, name=recorded["name"], description=recorded["description"])
    document.add(protocol)
    # Upstream starts with an initial-to-final edge; replace it with the actual sequence.
    protocol.edges.clear()
    text(protocol, "planDigest", digest)
    protocol.evidence = sbol3.URIProperty(
        protocol, str(LAB.evidenceState), 1, 1, initial_value=str(LAB.planned)
    )
    labop.import_library("liquid_handling")
    labop.import_library("sample_arrays")
    sources: dict[str, uml.ActivityParameterNode] = {}
    selections: dict[tuple[str, str], uml.OutputPin] = {}
    samples = {sample["id"]: sample for sample in recorded["samples"]}
    for index, resource in enumerate(recorded["resources"]):
        spec = labop.ContainerSpec(f"{identity}/container_{index}", name=resource["name"])
        document.add(spec)
        text(spec, "resource", json.dumps(resource, sort_keys=True))
        array = labop.SampleArray(name=resource["name"], container_type=spec)
        fills = {fill["well"]: fill["material"] for fill in resource["fills"]}
        wells = {
            f"{chr(65 + row)}{column}": fills.get(f"{chr(65 + row)}{column}")
            for column in range(1, resource["columns"] + 1)
            for row in range(resource["rows"])
        }
        array.initial_contents = quote(json.dumps(wells, sort_keys=True))
        text(array, "sampleFormat", "json")
        annotations = [
            {"well": place["location"]["well"], "sample": samples[place["sample_id"]]}
            for place in recorded["placements"]
            if place["location"]["resource"] == resource["name"]
        ]
        text(array, "samples", json.dumps(annotations, sort_keys=True))
        for name in ("design", "implementation"):
            refs = sorted({item["sample"][name] for item in annotations if item["sample"][name]})
            setattr(
                array,
                name,
                sbol3.URIProperty(array, str(LAB[name]), 0, math.inf, initial_value=refs),
            )
        sources[resource["name"]] = protocol.input_value(
            f"collection_{index}",
            str(LABOP.SampleCollection),
            default_value=uml.LiteralIdentified(value=array),
        )

    def well(location: dict[str, str]) -> uml.OutputPin:
        key = location["resource"], location["well"]
        if key not in selections:
            selections[key] = protocol.primitive_step(
                "PlateCoordinates", source=sources[key[0]], coordinates=key[1]
            ).output_pin("samples")
        return selections[key]

    def extension(name: str) -> labop.Primitive:
        primitive = document.find(EXT + name)
        if primitive is None:
            primitive = labop.Primitive(EXT + name, description=DESCRIPTIONS[name])
            document.add(primitive)
            for parameter, kind in EXTENSIONS[name]:
                primitive.add_input(parameter, str(kind))
            optional = {"Thermocycle": ("lidTemperature", "blockVolume"), "Distribute": ("airGap",)}
            for parameter in optional.get(name, ()):
                primitive.add_input(parameter, str(OM.Measure), optional=True)
        return primitive

    for index, step in enumerate(recorded["steps"]):
        kind = step["kind"]
        primitive: str | labop.Primitive
        arguments: dict[str, object]
        if kind == "Transfer":
            primitive = "Transfer"
            arguments = {
                "source": well(step["source"]),
                "destination": well(step["destination"]),
                "amount": measure(step["volume"], "microlitre"),
            }
        elif kind == "Mix":
            primitive = "PipetteMix"
            arguments = {
                "samples": well(step["location"]),
                "amount": measure(step["volume"], "microlitre"),
                "cycleCount": measure(step["cycles"], "one"),
            }
        elif kind == "Wait":
            primitive, arguments = extension(kind), {"duration": measure(step["seconds"], "second")}
        elif kind == "ManualInstruction":
            primitive, arguments = extension("OperatorPause"), {"instruction": step["text"]}
        elif kind == "Distribute":
            members = [well(location) for location in step["destinations"]]
            group = document.find(EXT + f"OrderedGroup_{len(members)}")
            if group is None:
                group = labop.Primitive(
                    EXT + f"OrderedGroup_{len(members)}",
                    description="Return the ordered sample aliases, including duplicates; "
                    "no laboratory operation.",
                )
                document.add(group)
                for member in range(len(members)):
                    group.add_input(f"member_{member}", str(LABOP.SampleCollection))
                group.add_output("samples", str(LABOP.SampleCollection))
            selected = protocol.primitive_step(
                group, **{f"member_{i}": value for i, value in enumerate(members)}
            )
            primitive = extension(kind)
            arguments = {
                "source": well(step["source"]),
                "destinations": selected.output_pin("samples"),
                "amount": measure(step["volume"], "microlitre"),
            }
            if step["air_gap"] is not None:
                arguments["airGap"] = measure(step["air_gap"], "microlitre")
        elif kind in ("SetTemperature", "Thermocycle"):
            primitive, arguments = extension(kind), {"samples": sources[step["resource"]]}
            if kind == "SetTemperature":
                arguments["temperature"] = measure(step["celsius"], "degreeCelsius")
            else:
                arguments.update(
                    profile=json.dumps(step["profile"], sort_keys=True), cycles=step["cycles"]
                )
                for field, name, unit in (
                    ("lid_celsius", "lidTemperature", "degreeCelsius"),
                    ("block_volume", "blockVolume", "microlitre"),
                ):
                    if step[field] is not None:
                        arguments[name] = measure(step[field], unit)
        else:
            raise ValueError(f"No LabOP mapping for {kind}")
        action = protocol.primitive_step(primitive, **arguments)
        text(action, "semanticStep", json.dumps(step, sort_keys=True))
        text(action, "stepIndex", str(index))
    outputs = {
        place["location"]["resource"]
        for place in recorded["placements"]
        if place["sample_id"] in recorded["output_sample_ids"]
    }
    for index, name in enumerate(sorted(outputs)):
        protocol.designate_output(f"result_{index}", str(LABOP.SampleCollection), sources[name])
    protocol.order(protocol.get_last_step(), protocol.final())
    report = document.validate()
    if report.errors:
        raise ValueError("\n".join(str(error) for error in report.errors))
    return document


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    document = export(json.loads(args.plan.read_text()))
    graph = to_canonical_graph(document.graph())
    output = "\n".join(sorted(graph.serialize(format="nt").splitlines())) + "\n"
    path = args.out or args.plan.with_name("protocol.labop.ttl")
    if path.exists() and path.read_text() != output:
        raise FileExistsError(f"{path} already contains a different artifact")
    path.write_text(output)
    print(path)


if __name__ == "__main__":
    main()
