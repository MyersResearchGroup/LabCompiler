"""Compile a synthetic software fixture from SBOL designs through robot artifacts.

The short DNA strings and arbitrary method numbers demonstrate the compiler.
They are not a laboratory cloning recipe.
"""

import argparse
from decimal import Decimal

import lab
from lab import uL
from lab.experiments import cloning
from lab.experiments.cloning.methods import AssemblyMethod, CloningMethods, Reagent
from lab.experiments.cloning.planning import BuildRequest, BuildTarget
from lab.experiments.cloning.sequences import CIRCULAR, LINEAR
from lab.experiments.cloning.systems import AssemblyRecipe, CloningSystem, FragmentSelection
from lab.inventory import Inventory, MaterialForm, Stock
from lab.operations import Hold
from lab.provenance import Component, Document, EvidenceState, Implementation, Sequence
from lab.provenance.vocabulary import DNA, IUPAC_DNA, SMALL_MOLECULE
from lab.targets import LiquidHandler, Manual

NS = "https://example.org/lab_demo"


def inputs():
    volume, stock_volume = "3", "10"
    doc = Document(namespace=NS)
    vector_sequence = Sequence(
        identity=doc.iri("vector_sequence"), elements="AAAAGAATTCTTTT", encoding=IUPAC_DNA
    )

    insert_sequence = Sequence(
        identity=doc.iri("insert_sequence"), elements="GGGAATTCCCCGAATTCGG", encoding=IUPAC_DNA
    )
    vector = Component(
        identity=doc.iri("vector"), types=(DNA, CIRCULAR), sequences=(vector_sequence.ref,)
    )
    insert = Component(
        identity=doc.iri("insert"), types=(DNA, LINEAR), sequences=(insert_sequence.ref,)
    )
    target = Component(identity=doc.iri("target"), types=(DNA, CIRCULAR))
    buffer = Component(identity=doc.iri("buffer"), types=(SMALL_MOLECULE,))
    water = Component(identity=doc.iri("water"), types=(SMALL_MOLECULE,))
    doc.add(vector_sequence, insert_sequence, vector, insert, target, buffer, water)
    stocks = []
    for design, form in (
        (vector, MaterialForm.DNA),
        (insert, MaterialForm.DNA),
        (buffer, MaterialForm.REAGENT),
        (water, MaterialForm.REAGENT),
    ):
        implementation = Implementation(
            identity=design.identity + "/implementation",
            derived_from=(design.ref,),
            evidence_state=EvidenceState.RECORDED,
        )
        doc.add(implementation)
        stocks.append(
            Stock(
                identity=design.identity + "/stock",
                design=design.ref,
                implementation=implementation.ref,
                form=form,
                quantity=Decimal(stock_volume) * uL,
            )
        )
    recipe = AssemblyRecipe(
        identity=doc.iri("recipe"),
        product=target.ref,
        enzyme="EcoRI",
        fragments=(
            FragmentSelection(component=vector.ref, left_cut=5, right_cut=5),
            FragmentSelection(component=insert.ref, left_cut=3, right_cut=12),
        ),
        method=doc.iri("method"),
    )
    method = AssemblyMethod(
        identity=recipe.method,
        enzyme="EcoRI",
        dna_volume_ul=Decimal(1),
        reaction_volume_ul=Decimal(5),
        output_volume_ul=Decimal(5),
        reagents=(Reagent(component=buffer.ref, volume_ul=Decimal(1)),),
        diluent=water.ref,
        profile=(Hold(Decimal(25), Decimal(1)),),
        cycles=1,
        mix_volume_ul=Decimal(2),
        mix_cycles=1,
    )
    return (
        BuildRequest(
            identity=doc.iri("request"),
            targets=(BuildTarget(design=target.ref, volume_ul=Decimal(volume)),),
        ),
        dict(
            document=doc.freeze(),
            inventory=Inventory(identity=doc.iri("inventory"), stocks=tuple(stocks)),
            system=CloningSystem(identity=doc.iri("system"), recipes=(recipe,)),
            methods=CloningMethods(assemblies=(method,)),
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("manual", "ot2", "flex", "star"), default="manual")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    request, supplied = inputs()
    planned = cloning.plan(request, **supplied)
    planned.require_ready()
    experiment = cloning.build(planned)
    target = Manual() if args.target == "manual" else LiquidHandler(args.target)
    compilation = lab.compile(experiment, target)
    print(compilation.write(args.out or f"build/provenance/{args.target}"))


if __name__ == "__main__":
    main()
