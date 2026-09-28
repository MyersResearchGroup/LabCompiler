"""Compile all cloning stages from explicit synthetic inputs.

The arbitrary sequences, quantities, and parameters exercise the compiler;
this example does not supply validated laboratory methods.
"""

import argparse
from dataclasses import replace
from decimal import Decimal

import lab
from examples.provenance_build import NS
from examples.provenance_build import inputs as assembly_inputs
from lab import uL
from lab.experiments import cloning
from lab.experiments.cloning.methods import (
    ExternalPreparationMethod,
    PlatingMethod,
    Reagent,
    TransformationMethod,
)
from lab.experiments.cloning.planning import BuildRequest, CountTarget
from lab.experiments.cloning.systems import (
    CloningSystem,
    ExternalPreparationRecipe,
    PlatingRecipe,
    TransformationRecipe,
)
from lab.inventory import CountedStock, Inventory, MaterialForm, Stock
from lab.operations import Hold
from lab.provenance import Component, Document, EvidenceState, Implementation
from lab.provenance.vocabulary import SMALL_MOLECULE
from lab.targets import LiquidHandler, Manual


def inputs(count=2):
    _, inputs = assembly_inputs()
    doc = Document.from_snapshot(inputs["document"])
    vector = doc.get(NS + "/vector", Component)
    target = doc.get(NS + "/target", Component)
    cells = Component(identity=NS + "/cells", types=("https://example.org/cell",))
    strain = Component(identity=NS + "/strain", types=("https://example.org/cell",))
    broth = Component(identity=NS + "/broth", types=(SMALL_MOLECULE,))
    substrate = Component(identity=NS + "/substrate", types=(SMALL_MOLECULE,))
    doc.add(cells, strain, broth, substrate)
    stocks = [s for s in inputs["inventory"].stocks if s.design != vector.ref]
    for design, form in ((cells, MaterialForm.COMPETENT_CELLS), (broth, MaterialForm.REAGENT)):
        material = Implementation(
            identity=design.identity + "/material",
            derived_from=(design.ref,),
            evidence_state=EvidenceState.RECORDED,
        )
        doc.add(material)
        stocks.append(
            Stock(
                identity=design.identity + "/stock",
                design=design.ref,
                implementation=material.ref,
                form=form,
                quantity=20 * uL,
            )
        )
    material = Implementation(
        identity=NS + "/received_stab",
        derived_from=(vector.ref,),
        evidence_state=EvidenceState.RECORDED,
    )
    doc.add(material)
    stocks.append(
        CountedStock(
            identity=NS + "/stab_stock",
            design=vector.ref,
            implementation=material.ref,
            form=MaterialForm.BACTERIAL_STAB,
            count=1,
        )
    )
    preparation = ExternalPreparationMethod(
        identity=NS + "/preparation_method",
        procedure=NS + "/sop",
        instructions=(
            "Apply the supplied test procedure and record the resulting material and quantity."
        ),
        source_count=1,
        output_volume_ul=Decimal(10),
    )
    transformation = TransformationMethod(
        identity=NS + "/transformation_method",
        cell_volume_ul=Decimal(2),
        dna_volume_ul=Decimal(1),
        recovery=Reagent(component=broth.ref, volume_ul=Decimal(2)),
        profile=(Hold(Decimal(25), Decimal(1)),),
        recovery_profile=(Hold(Decimal(25), Decimal(1)),),
        output_volume_ul=Decimal(5),
        cell_mix_volume_ul=Decimal(1),
        cell_mix_cycles=1,
        dna_mix_cycles=1,
        initial_celsius=Decimal(25),
    )
    plating = PlatingMethod(
        identity=NS + "/plating_method",
        substrate=substrate.ref,
        diluent=broth.ref,
        transfer_volume_ul=Decimal(1),
        dilution_factors=(Decimal(2), Decimal(3)),
        spot_volume_ul=Decimal(1),
        spot_height_mm=Decimal(2),
        mix_volume_ul=Decimal(1),
        mix_cycles=1,
    )
    system = CloningSystem(
        identity=NS + "/complete_system",
        recipes=(
            *inputs["system"].recipes,
            ExternalPreparationRecipe(
                identity=NS + "/preparation_recipe",
                product=vector.ref,
                source=vector.ref,
                source_form=MaterialForm.BACTERIAL_STAB,
                output_form=MaterialForm.DNA,
                method=preparation.identity,
            ),
            TransformationRecipe(
                identity=NS + "/transformation_recipe",
                product=strain.ref,
                chassis=cells.ref,
                plasmids=(target.ref,),
                method=transformation.identity,
            ),
            PlatingRecipe(
                identity=NS + "/plating_recipe", product=strain.ref, method=plating.identity
            ),
        ),
    )
    return BuildRequest(
        identity=NS + "/integrated_request", targets=(CountTarget(design=strain.ref, count=count),)
    ), {
        **inputs,
        "document": doc.freeze(),
        "inventory": Inventory(identity=NS + "/integrated_inventory", stocks=tuple(stocks)),
        "system": system,
        "methods": replace(
            inputs["methods"],
            transformations=(transformation,),
            platings=(plating,),
            preparations=(preparation,),
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("manual", "ot2", "flex", "star"), default="manual")
    parser.add_argument("--out")
    args = parser.parse_args()
    request, supplied = inputs()
    plan = cloning.plan(request, **supplied)
    plan.require_ready()
    experiment = cloning.build(plan)
    target = Manual() if args.target == "manual" else LiquidHandler(args.target)
    compilation = lab.compile(experiment, target)
    print(compilation.write(args.out or f"build/cloning/{args.target}"))
    for stage in compilation.stages:
        print(f"{stage.protocol.name}: {stage.target.name}")


if __name__ == "__main__":
    main()
