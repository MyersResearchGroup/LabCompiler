"""Export a protocol plan directly and check the matching compilation artifact."""

import argparse
from pathlib import Path

import lab
from examples.sbol_provenance import inputs
from lab.labop import export


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("build/labop_export"))
    args = parser.parse_args()
    _, protocol = inputs()
    compilation = lab.compile(protocol, to=None)
    rdf = export(compilation.protocol)
    assert compilation.files["protocol.labop.ttl"] == rdf.text
    output = compilation.write(args.out)
    print(f"Protocol: {rdf.protocol}")
    print(f"LabOP: {output / 'protocol.labop.ttl'}")


if __name__ == "__main__":
    main()
