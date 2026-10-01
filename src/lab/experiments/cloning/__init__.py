"""Cloning recipes, decks, and workflow composition for caller-supplied designs.

Stage recipes live in :mod:`lab.experiments.cloning.stages`. A deck names
containers and sites; compilation lowers it for one handler.
"""

from lab.experiments.cloning.decks import assembly_deck, plating_deck, transformation_deck
from lab.experiments.cloning.stages import (
    AssemblyLayout,
    AssemblyReaction,
    AssemblyVolumes,
    PlatingVolumes,
    TransformationLayout,
    build_assembly,
    build_plating,
    build_transformation,
    layout_assembly,
    layout_transformation,
    plating_plates,
    record_assembly,
    record_plating,
    record_transformation,
)
from lab.experiments.cloning.types import (
    BSAI,
    Assembly,
    Transformation,
)
from lab.experiments.cloning.workflow import golden_gate

__all__ = [
    "Assembly",
    "AssemblyLayout",
    "AssemblyReaction",
    "AssemblyVolumes",
    "BSAI",
    "PlatingVolumes",
    "Transformation",
    "TransformationLayout",
    "assembly_deck",
    "build_assembly",
    "build_plating",
    "build_transformation",
    "golden_gate",
    "layout_assembly",
    "layout_transformation",
    "plating_plates",
    "plating_deck",
    "record_assembly",
    "record_plating",
    "record_transformation",
    "transformation_deck",
]
