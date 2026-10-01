"""Native SBOL design provenance and an annotated aliquot protocol."""

import argparse
from pathlib import Path

import sbol3

import lab
from lab.samples import Sample


def inputs() -> tuple[sbol3.Document, lab.Protocol]:
    namespace = "https://example.org/aliquot"
    sequence = sbol3.Sequence(
        f"{namespace}/sequence", elements="ACGTACGT", encoding=sbol3.IUPAC_DNA_ENCODING
    )
    author = sbol3.Agent(f"{namespace}/author", name="SBOL example script")
    activity = sbol3.Activity(
        f"{namespace}/design_creation",
        usage=[sbol3.Usage(sequence.identity)],
        association=[sbol3.Association(author)],
    )
    design = sbol3.Component(
        f"{namespace}/design",
        sbol3.SBO_DNA,
        sequences=[sequence],
        generated_by=[activity],
    )
    document = sbol3.Document()
    document.add([sequence, author, activity, design])
    protocol = lab.Protocol("Annotated aliquot")
    plate = protocol.plate("plate", capacity=100 * lab.uL)
    protocol.load(plate["A1"], design.identity, volume=20 * lab.uL)
    protocol.add_sample(
        Sample(id="source", material_identity=design.identity, label="DNA", design=design.identity),
        at=plate["A1"],
        is_input=True,
    )
    protocol.add_sample(
        Sample(
            id="aliquot",
            material_identity=design.identity,
            label="Planned aliquot",
            design=design.identity,
            parent_ids=("source",),
        ),
        at=plate["A2"],
        is_output=True,
    )
    protocol.transfer(plate["A1"], plate["A2"], volume=2 * lab.uL)
    return document, protocol


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("build/sbol_provenance"))
    args = parser.parse_args()
    document, protocol = inputs()
    assert not document.validate().errors
    output = lab.compile(protocol, to=None).write(args.out)
    document.write(str(output / "designs.ttl"), sbol3.TURTLE)
    print(output)


if __name__ == "__main__":
    main()
