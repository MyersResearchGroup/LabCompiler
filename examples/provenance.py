"""Author and round-trip an SBOL3 design and prospective build provenance.

Run ``python -m examples.provenance --out build/provenance.ttl``. The short
sequence is illustrative; this example specifies no executable cloning method.
"""

import argparse
from pathlib import Path

from lab import __version__
from lab.provenance import (
    Activity,
    Agent,
    AgentKind,
    Association,
    Component,
    Document,
    EvidenceState,
    Implementation,
    Plan,
    Sequence,
    SubComponent,
    Usage,
)
from lab.provenance.vocabulary import DNA, IUPAC_DNA, LAB


def build_document() -> Document:
    document = Document(namespace="https://example.org/lab/provenance_example")
    sequence = Sequence(
        identity=document.iri("input_sequence"),
        elements="ACGTACGT",
        encoding=IUPAC_DNA,
    )
    part = Component(
        identity=document.iri("input_design"),
        name="Illustrative input design",
        types=(DNA,),
        sequences=(sequence.ref,),
    )
    product = Component(
        identity=document.iri("product_design"),
        name="Intended product design",
        types=(DNA,),
        features=(SubComponent(instance_of=part.ref),),
    )
    stock = Implementation(
        identity=document.iri("input_stock"),
        derived_from=(part.ref,),
        evidence_state=EvidenceState.RECORDED,
        description="An author-supplied inventory assertion; sequence verification unspecified.",
    )
    planner = Agent(
        identity=document.iri("lab_compiler"),
        name="Lab Compiler",
        kind=AgentKind.SOFTWARE,
        software_version=__version__,
    )
    method = Plan(
        identity=document.iri("method"),
        description="Prospective method identity; detailed protocol specification is separate.",
    )
    activity = Activity(
        identity=document.iri("planned_build"),
        evidence_state=EvidenceState.PLANNED,
        usage=(Usage(entity=stock.ref, roles=(LAB + "inputMaterial",)),),
        association=(Association(agent=planner.ref, plan=method.ref, roles=(LAB + "planner",)),),
    )
    output = Implementation(
        identity=document.iri("planned_output"),
        derived_from=(product.ref,),
        generated_by=(activity.ref,),
        evidence_state=EvidenceState.PLANNED,
    )
    document.add(sequence, part, product, stock, planner, method, activity, output)
    return document


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("build/provenance.ttl"))
    args = parser.parse_args()
    frozen = build_document().freeze()
    report = frozen.to_sbol3().validate()
    if report.errors:
        raise ValueError(str(report))
    output = frozen.write(args.out)
    restored = Document.read(output).freeze()
    assert restored.digest == frozen.digest
    print(f"Wrote {output}; {len(frozen.objects)} top-level objects; SHA-256 {frozen.digest}")


if __name__ == "__main__":
    main()
