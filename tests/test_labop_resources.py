import hashlib
import json
from importlib.resources import files

from rdflib import Graph


def test_vendored_labop_resources_match_upstream_hashes_and_parse():
    resources = files("lab.labop").joinpath("resources")
    manifest = json.loads(resources.joinpath("upstream.json").read_text())
    for name, item in manifest["files"].items():
        data = resources.joinpath(name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == item["sha256"]
        if name.endswith(".ttl"):
            assert Graph().parse(data=data, format="turtle")
    assert Graph().parse(data=resources.joinpath("lab.ttl").read_text(), format="turtle")
