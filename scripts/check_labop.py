"""Validate a generated protocol with isolated SBOLFactory 1.1.2 / pySBOL3.

Run this script in a separate interpreter: SBOLFactory registers global builders.
It is a validation tool, never a compiler dependency or execution engine.
"""

import argparse
import hashlib
import json
from pathlib import Path

import requests
import sbol3
from rdflib import RDF, Graph, Namespace
from sbol_factory import SBOLFactory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("protocol", type=Path)
    parser.add_argument("--schemas", type=Path, required=True)
    parser.add_argument("--fetch", action="store_true", help="Download pinned validation schemas")
    args = parser.parse_args()
    resources = Path(__file__).resolve().parents[1] / "src/lab/labop/resources"
    manifest = json.loads((resources / "upstream.json").read_text())
    args.schemas.mkdir(parents=True, exist_ok=True)
    for name, info in manifest["files"].items():
        path = args.schemas / name
        if args.fetch:
            url = f"https://raw.githubusercontent.com/Bioprotocols/labop/{manifest['commit']}/{info['path']}"
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            data = response.content
            assert hashlib.sha256(data).hexdigest() == info["sha256"], name
            path.write_bytes(data)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == info["sha256"], name
    upstream = Graph()
    for name in ("liquid_handling.ttl", "sample_arrays.ttl"):
        upstream.parse(args.schemas / name, format="turtle")
    selected = Graph()
    for identity in manifest["subset"]["identities"]:
        for triple in upstream:
            if str(triple[0]) == identity or str(triple[0]).startswith(identity + "/"):
                selected.add(triple)
    assert set(selected) == set(Graph().parse(resources / "primitives.ttl", format="turtle"))
    lab_namespace = "https://the-lab-compiler.github.io/lab-py/ns#"
    SBOLFactory("lab", str(resources / "lab.ttl"), lab_namespace)
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
