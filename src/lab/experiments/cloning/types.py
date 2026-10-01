"""Core cloning types: assembly designs, transformation designs, and stage inputs."""

from collections.abc import Sequence
from dataclasses import dataclass

from sbol3 import SBO_PROTEIN, Component


def _component(value: object, *, name: str) -> None:
    if not isinstance(value, Component):
        raise TypeError(f"{name} must be an SBOL Component.")


def _components(values: object, *, name: str) -> tuple[Component, ...]:
    if isinstance(values, (str, Component)) or not isinstance(values, Sequence):
        raise TypeError(f"{name} must be a sequence of SBOL Components.")
    if not values:
        raise ValueError(f"{name} must be a nonempty sequence of SBOL Components.")
    if not all(isinstance(value, Component) for value in values):
        raise TypeError(f"{name} must be SBOL Components.")
    return tuple(values)


BSAI = Component("https://SBOL2Build.org/BsaI", SBO_PROTEIN)


@dataclass(frozen=True, slots=True, kw_only=True)
class Assembly:
    product: Component
    backbone: Component
    parts: Sequence[Component]
    restriction_enzyme: Component

    def __post_init__(self) -> None:
        _component(self.product, name="Product")
        _component(self.backbone, name="Backbone")
        _component(self.restriction_enzyme, name="Restriction enzyme")
        object.__setattr__(self, "parts", _components(self.parts, name="Parts"))

    def _inputs(self) -> dict[str, object]:
        return {
            "Product": self.product.identity,
            "Backbone": self.backbone.identity,
            "PartsList": [part.identity for part in self.parts],
            "Restriction Enzyme": self.restriction_enzyme.identity,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class Transformation:
    strain: Component
    chassis: Component
    plasmids: Sequence[Component]

    def __post_init__(self) -> None:
        _component(self.strain, name="Strain")
        _component(self.chassis, name="Chassis")
        object.__setattr__(self, "plasmids", _components(self.plasmids, name="Plasmids"))

    def _inputs(self) -> dict[str, object]:
        return {
            "Strain": self.strain.identity,
            "Chassis": self.chassis.identity,
            "Plasmids": [part.identity for part in self.plasmids],
        }
