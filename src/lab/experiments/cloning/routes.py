"""Resolve typed recipes into explicit material demands, without choosing a route."""

from dataclasses import dataclass
from decimal import Decimal

from lab.experiments.cloning.methods import (
    AssemblyMethod,
    CloningMethods,
    ExternalPreparationMethod,
    Method,
    PlatingMethod,
    TransformationMethod,
)
from lab.experiments.cloning.systems import (
    AssemblyRecipe,
    ExternalPreparationRecipe,
    PlatingRecipe,
    Recipe,
    TransformationRecipe,
)
from lab.inventory import MaterialForm
from lab.provenance import Component, Ref


@dataclass(frozen=True)
class Demand:
    design: Ref[Component]
    form: MaterialForm
    amount: Decimal
    role: str


@dataclass(frozen=True)
class ResolvedRecipe:
    method: Method
    inputs: tuple[Demand, ...]
    output_amount: Decimal


def resolve_recipe(recipe: Recipe, methods: CloningMethods) -> ResolvedRecipe:
    if isinstance(recipe, AssemblyRecipe):
        assembly = methods.get(recipe.method, AssemblyMethod)
        if recipe.enzyme != assembly.enzyme:
            raise ValueError(f"Recipe {recipe.identity} and its method use different enzymes")
        inputs = tuple(
            Demand(
                addition.component,
                MaterialForm.DNA
                if len(assembly.reagents) <= index < len(assembly.reagents) + len(recipe.fragments)
                else MaterialForm.REAGENT,
                addition.volume_ul,
                f"addition_{index + 1}",
            )
            for index, addition in enumerate(
                assembly.additions(tuple(f.component for f in recipe.fragments))
            )
        )
        return ResolvedRecipe(assembly, inputs, assembly.output_volume_ul)
    if isinstance(recipe, TransformationRecipe):
        transformation = methods.get(recipe.method, TransformationMethod)
        transformation.reaction_volume(len(recipe.plasmids))
        return ResolvedRecipe(
            transformation,
            (
                Demand(
                    recipe.chassis,
                    MaterialForm.COMPETENT_CELLS,
                    transformation.cell_volume_ul,
                    "cells",
                ),
                *(
                    Demand(
                        plasmid, MaterialForm.DNA, transformation.dna_volume_ul, f"dna_{index + 1}"
                    )
                    for index, plasmid in enumerate(recipe.plasmids)
                ),
                Demand(
                    transformation.recovery.component,
                    MaterialForm.REAGENT,
                    transformation.recovery.volume_ul,
                    "recovery",
                ),
            ),
            transformation.output_volume_ul,
        )
    if isinstance(recipe, PlatingRecipe):
        plating = methods.get(recipe.method, PlatingMethod)
        return ResolvedRecipe(
            plating,
            (
                Demand(recipe.product, MaterialForm.CULTURE, plating.transfer_volume_ul, "culture"),
                *(
                    Demand(
                        plating.diluent,
                        MaterialForm.REAGENT,
                        plating.transfer_volume_ul * (factor - 1),
                        f"diluent_{index + 1}",
                    )
                    for index, factor in enumerate(plating.dilution_factors)
                ),
            ),
            Decimal(1),
        )
    if isinstance(recipe, ExternalPreparationRecipe):
        preparation = methods.get(recipe.method, ExternalPreparationMethod)
        if recipe.source_form.counted != bool(
            preparation.source_count
        ) or recipe.output_form.counted != bool(preparation.output_count):
            raise ValueError("Preparation quantity kinds must match the declared material forms")
        return ResolvedRecipe(
            preparation,
            (
                Demand(
                    recipe.source,
                    recipe.source_form,
                    Decimal(preparation.source_count)
                    if recipe.source_form.counted
                    else preparation.source_volume_ul,
                    "source",
                ),
                *(
                    Demand(
                        reagent.component,
                        MaterialForm.REAGENT,
                        reagent.volume_ul,
                        f"reagent_{index + 1}",
                    )
                    for index, reagent in enumerate(preparation.reagents)
                ),
            ),
            Decimal(preparation.output_count)
            if recipe.output_form.counted
            else preparation.output_volume_ul,
        )
    raise TypeError(f"Unsupported recipe {type(recipe).__name__}")
