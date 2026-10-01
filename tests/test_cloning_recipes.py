from decimal import Decimal

from lab.experiments import cloning
from lab.experiments.cloning.routes import resolve_recipe
from lab.inventory import MaterialForm
from lab.operations import Hold
from lab.provenance import Ref


def test_recipe_and_method_round_trip_preserves_material_demands(tmp_path):
    root = "https://example.org/recipes/"
    method = cloning.TransformationMethod(
        identity=root + "method",
        cell_volume_ul=Decimal(2),
        dna_volume_ul=Decimal(1),
        recovery=cloning.Reagent(component=Ref(root + "medium"), volume_ul=Decimal(5)),
        output_volume_ul=Decimal(6),
        profile=(Hold(Decimal(25), Decimal(1)),),
        recovery_profile=(Hold(Decimal(25), Decimal(2)),),
        cell_mix_volume_ul=Decimal(2),
        cell_mix_cycles=1,
        dna_mix_cycles=1,
    )
    recipe = cloning.TransformationRecipe(
        identity=root + "recipe",
        product=Ref(root + "product"),
        chassis=Ref(root + "cells"),
        plasmids=(Ref(root + "plasmid"),),
        method=method.identity,
    )
    methods = cloning.CloningMethods(transformations=(method,))
    system = cloning.CloningSystem(identity=root + "system", recipes=(recipe,))
    assert cloning.CloningMethods.read(methods.write(tmp_path / "methods.json")) == methods
    assert cloning.CloningSystem.read(system.write(tmp_path / "system.json")) == system
    demands = resolve_recipe(recipe, methods)
    assert demands.output_amount == 6
    assert [(demand.form, demand.amount) for demand in demands.inputs] == [
        (MaterialForm.COMPETENT_CELLS, Decimal(2)),
        (MaterialForm.DNA, Decimal(1)),
        (MaterialForm.REAGENT, Decimal(5)),
    ]
