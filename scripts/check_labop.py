"""Validate a generated protocol with isolated SBOLFactory 1.1.2 / pySBOL3.

Run this script in a separate interpreter: SBOLFactory registers global builders.
It is a validation tool, never a compiler dependency or execution engine.
"""

import argparse
from pathlib import Path

import sbol3
from rdflib import RDF, Graph, Namespace
from sbol_factory import SBOLFactory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("protocol", type=Path)
    parser.add_argument("--schemas", type=Path, required=True)
    args = parser.parse_args()
    lab_namespace = "https://the-lab-compiler.github.io/lab-py/ns#"
    SBOLFactory("lab", str(args.schemas / "lab.ttl"), lab_namespace)
    SBOLFactory("uml", str(args.schemas / "uml.ttl"), "http://bioprotocols.org/uml#")
    SBOLFactory("labop", str(args.schemas / "labop.ttl"), "http://bioprotocols.org/labop#")
    document = sbol3.Document()
    document.read(str(args.protocol))
    report = document.validate()
    assert not report.errors, tuple(str(error) for error in report.errors)
    graph = Graph().parse(args.protocol, format="turtle")
    namespace = Namespace(lab_namespace)
    profiles = tuple(graph.subjects(RDF.type, namespace.ThermalProfile))
    for identity in profiles:
        profile = document.find(str(identity))
        assert profile is not None and profile.holds
        for hold in profile.holds:
            assert hold.temperature.unit and hold.duration.unit
            assert hold.index >= 0
    print(
        f"{len(document.objects)} top-level objects, {len(profiles)} typed thermal profiles; "
        f"{len(report.errors)} errors, {len(report.warnings)} warnings"
    )


if __name__ == "__main__":
    main()
