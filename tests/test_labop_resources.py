import hashlib
import json
from importlib.resources import files

from rdflib import RDF, Graph

from lab.labop.primitives import LABOP


def test_selected_upstream_primitives_match_the_packaged_checksum():
    resources = files("lab.labop").joinpath("resources")
    manifest = json.loads(resources.joinpath("upstream.json").read_text())
    subset = manifest["subset"]
    data = resources.joinpath(subset["file"]).read_bytes()
    assert hashlib.sha256(data).hexdigest() == subset["sha256"]
    graph = Graph().parse(data=data, format="turtle")
    assert {str(node) for node in graph.subjects(RDF.type, LABOP.Primitive)} == set(
        subset["identities"]
    )
    assert Graph().parse(data=resources.joinpath("lab.ttl").read_text(), format="turtle")
