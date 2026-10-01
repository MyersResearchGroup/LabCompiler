"""Explicit fragment selections and allowed assembly routes."""

import json
from dataclasses import dataclass, field
from pathlib import Path

from Bio.Restriction.Restriction import RestrictionBatch

from lab.artifacts import canonical_json, write_bundle
from lab.inventory import MaterialForm
from lab.provenance import Component, Ref
from lab.provenance.types import require_iri


@dataclass(frozen=True, kw_only=True)
class FragmentSelection:
    """Select a digest fragment by zero-based Watson cut positions.

    ``None`` denotes an end of a linear molecule. For a circular molecule, equal
    cut positions select a single-cut linearization. Reversal is explicit.
    """

    component: Ref[Component]
    left_cut: int | None
    right_cut: int | None
    reverse_complement: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.component, Ref):
            raise TypeError("Fragment component must be a Ref[Component]")
        for position in (self.left_cut, self.right_cut):
            if position is not None and (type(position) is not int or position < 0):
                raise ValueError("Cut positions must be nonnegative integers or None")
        if type(self.reverse_complement) is not bool:
            raise TypeError("reverse_complement must be a bool")


@dataclass(frozen=True, kw_only=True)
class AssemblyRecipe:
    kind: str = field(default="assembly", init=False)
    identity: str
    product: Ref[Component]
    enzyme: str
    fragments: tuple[FragmentSelection, ...]
    method: str
    circular: bool = True

    @property
    def output_form(self) -> MaterialForm:
        return MaterialForm.DNA

    def __post_init__(self) -> None:
        require_iri(self.identity)
        require_iri(self.method)
        if not isinstance(self.product, Ref):
            raise TypeError("Recipe product must be a Ref[Component]")
        if (
            not isinstance(self.fragments, tuple)
            or not self.fragments
            or not all(isinstance(item, FragmentSelection) for item in self.fragments)
        ):
            raise ValueError("Recipes need an ordered tuple of selected fragments")
        if len(RestrictionBatch([self.enzyme])) != 1:
            raise ValueError("Select one restriction enzyme")
        if type(self.circular) is not bool:
            raise TypeError("circular must be a bool")


@dataclass(frozen=True, kw_only=True)
class TransformationRecipe:
    kind: str = field(default="transformation", init=False)
    identity: str
    product: Ref[Component]
    chassis: Ref[Component]
    plasmids: tuple[Ref[Component], ...]
    method: str

    @property
    def output_form(self) -> MaterialForm:
        return MaterialForm.CULTURE

    def __post_init__(self) -> None:
        require_iri(self.identity)
        require_iri(self.method)
        if not isinstance(self.product, Ref) or not isinstance(self.chassis, Ref):
            raise TypeError("Transformation product and chassis must be component references")
        if (
            not isinstance(self.plasmids, tuple)
            or not self.plasmids
            or not all(isinstance(p, Ref) for p in self.plasmids)
        ):
            raise ValueError("Supply an ordered, nonempty tuple of plasmid references")
        if len(set(self.plasmids)) != len(self.plasmids):
            raise ValueError("Transformation plasmids must be distinct")


@dataclass(frozen=True, kw_only=True)
class PlatingRecipe:
    kind: str = field(default="plating", init=False)
    identity: str
    product: Ref[Component]
    method: str

    @property
    def output_form(self) -> MaterialForm:
        return MaterialForm.PLATED_SAMPLE

    def __post_init__(self) -> None:
        require_iri(self.identity)
        require_iri(self.method)
        if not isinstance(self.product, Ref):
            raise TypeError("Plating design must be a component reference")


@dataclass(frozen=True, kw_only=True)
class ExternalPreparationRecipe:
    kind: str = field(default="preparation", init=False)
    identity: str
    product: Ref[Component]
    source: Ref[Component]
    source_form: MaterialForm
    output_form: MaterialForm
    method: str

    def __post_init__(self) -> None:
        require_iri(self.identity)
        require_iri(self.method)
        if not isinstance(self.product, Ref) or not isinstance(self.source, Ref):
            raise TypeError("Preparation source and product must be component references")
        if not isinstance(self.source_form, MaterialForm) or not isinstance(
            self.output_form, MaterialForm
        ):
            raise TypeError("Preparation needs explicit source and output material forms")


Recipe = AssemblyRecipe | TransformationRecipe | PlatingRecipe | ExternalPreparationRecipe


@dataclass(frozen=True, kw_only=True)
class CloningSystem:
    identity: str
    recipes: tuple[Recipe, ...]

    def __post_init__(self) -> None:
        require_iri(self.identity)
        if not isinstance(self.recipes, tuple) or not all(
            isinstance(
                recipe,
                (AssemblyRecipe, TransformationRecipe, PlatingRecipe, ExternalPreparationRecipe),
            )
            for recipe in self.recipes
        ):
            raise TypeError("Recipes must be an immutable tuple of typed cloning recipes")
        if len({recipe.identity for recipe in self.recipes}) != len(self.recipes):
            raise ValueError("Recipe identities must be unique")
        object.__setattr__(
            self, "recipes", tuple(sorted(self.recipes, key=lambda item: item.identity))
        )

    def routes(
        self, product: Ref[Component], form: MaterialForm = MaterialForm.DNA
    ) -> tuple[Recipe, ...]:
        return tuple(
            recipe
            for recipe in self.recipes
            if recipe.product == product and recipe.output_form is form
        )

    def write(self, path: str | Path) -> Path:
        path = Path(path)
        write_bundle(
            path.parent,
            {
                path.name: canonical_json(
                    {
                        "format": "lab.cloning-system.v1",
                        "system": self,
                    }
                )
            },
        )
        return path

    @classmethod
    def read(cls, path: str | Path) -> "CloningSystem":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("format") != "lab.cloning-system.v1":
            raise ValueError("Expected lab.cloning-system.v1")
        recipes: list[Recipe] = []
        for row in data["system"]["recipes"]:
            kind = row.pop("kind")
            row["product"] = Ref(row["product"]["identity"])
            if kind == "assembly":
                row["fragments"] = tuple(
                    FragmentSelection(
                        **{**fragment, "component": Ref(fragment["component"]["identity"])}
                    )
                    for fragment in row["fragments"]
                )
                recipes.append(AssemblyRecipe(**row))
            elif kind == "transformation":
                row["chassis"] = Ref(row["chassis"]["identity"])
                row["plasmids"] = tuple(Ref(p["identity"]) for p in row["plasmids"])
                recipes.append(TransformationRecipe(**row))
            elif kind == "plating":
                recipes.append(PlatingRecipe(**row))
            elif kind == "preparation":
                row["source"] = Ref(row["source"]["identity"])
                row["source_form"] = MaterialForm(row["source_form"])
                row["output_form"] = MaterialForm(row["output_form"])
                recipes.append(ExternalPreparationRecipe(**row))
            else:
                raise ValueError(f"Unknown recipe kind: {kind}")
        return cls(identity=data["system"]["identity"], recipes=tuple(recipes))
