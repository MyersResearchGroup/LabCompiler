from dataclasses import replace
from decimal import Decimal

from lab import uL
from lab.experiments.cloning.methods import AssemblyMethod, CloningMethods, Reagent
from lab.experiments.cloning.planning import BuildRequest, BuildTarget
from lab.experiments.cloning.sequences import CIRCULAR, LINEAR
from lab.experiments.cloning.systems import AssemblyRecipe, CloningSystem, FragmentSelection
from lab.inventory import Inventory, MaterialForm, Stock
from lab.operations import Hold
from lab.provenance import Component, Document, EvidenceState, Implementation, Sequence
from lab.provenance.vocabulary import DNA, IUPAC_DNA, SMALL_MOLECULE

NS = "https://example.org/planning"


def planning_case(*, volume="3", stock_volume="10"):
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


def multilevel_case():
    request, inputs = planning_case()
    doc = Document.from_snapshot(inputs["document"])
    intermediate = doc.get(NS + "/target", Component)
    finals = tuple(replace(intermediate, identity=NS + f"/final_{i}") for i in range(2))
    doc.add(*finals)
    first = inputs["system"].recipes[0]
    recipes = tuple(
        replace(
            first,
            identity=NS + f"/second_recipe_{index}",
            product=final.ref,
            fragments=(
                first.fragments[0],
                FragmentSelection(component=intermediate.ref, left_cut=14, right_cut=0),
            ),
        )
        for index, final in enumerate(finals)
    )
    return (
        replace(
            request,
            targets=tuple(BuildTarget(design=final.ref, volume_ul=Decimal(3)) for final in finals),
        ),
        {
            **inputs,
            "document": doc.freeze(),
            "system": replace(inputs["system"], recipes=(first, *recipes)),
        },
    )
