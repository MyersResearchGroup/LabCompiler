"""Target compositions for the wells of one plate, independent of how they are prepared."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Self

from lab.units import Quantity, magnitude, units

_DIMENSIONS = {
    "[substance] / [length] ** 3": "mole/liter",
    "[mass] / [length] ** 3": "gram/liter",
}

Axis = Literal["row", "column"]


def canonical_concentration(value: Quantity) -> tuple[Decimal, str]:
    """Normalize a concentration to a nonnegative Decimal in mole/liter or gram/liter."""
    if not isinstance(value, Quantity):
        raise TypeError("A concentration must be a unit-bearing Quantity")
    for dimensionality, unit in _DIMENSIONS.items():
        if value.check(dimensionality):
            return magnitude(value, unit, positive=False), unit
    raise ValueError("A concentration must have mass/volume or amount/volume units")


def well_name(row: int, column: int) -> str:
    """Row-major well text for zero-based coordinates, matching ``Plate.wells``."""
    return f"{chr(65 + row)}{column + 1}"


def coordinates(well: str) -> tuple[int, int]:
    """Zero-based (row, column) of a well name such as ``"H12"``."""
    if len(well) < 2 or not well[0].isalpha() or not well[1:].isdigit():
        raise ValueError(f"{well!r} is not a well name")
    return ord(well[0].upper()) - 65, int(well[1:]) - 1


@dataclass(frozen=True, slots=True, kw_only=True)
class Target:
    """One solute's intended final concentration in one well."""

    solute: str
    magnitude: Decimal
    unit: str

    def __post_init__(self) -> None:
        if not isinstance(self.solute, str) or not self.solute.strip():
            raise ValueError("A target needs a solute name.")
        if self.unit not in _DIMENSIONS.values():
            raise ValueError("Target units must be mole/liter or gram/liter.")
        if not isinstance(self.magnitude, Decimal) or not self.magnitude.is_finite():
            raise ValueError("A target concentration must be a finite Decimal.")
        if self.magnitude < 0:
            raise ValueError("A target concentration must be nonnegative.")

    @property
    def quantity(self) -> Quantity:
        return self.magnitude * units(self.unit)


Context = tuple[str | None, tuple[tuple[str, Decimal, str], ...]]


@dataclass(frozen=True, slots=True, kw_only=True)
class WellTarget:
    """What one well must contain at the end: a material and solute concentrations."""

    well: str
    material: str | None
    targets: tuple[Target, ...]

    def __post_init__(self) -> None:
        coordinates(self.well)
        if self.material is not None and not self.material.strip():
            raise ValueError(f"{self.well}: a material must be nonempty text or None.")
        if not isinstance(self.targets, tuple):
            raise TypeError("Targets must be a tuple of Target objects.")
        solutes = [target.solute for target in self.targets]
        if len(set(solutes)) != len(solutes):
            raise ValueError(f"{self.well}: each solute may have one target.")
        object.__setattr__(self, "targets", tuple(sorted(self.targets, key=lambda t: t.solute)))

    def concentration(self, solute: str) -> Target | None:
        return next((target for target in self.targets if target.solute == solute), None)

    def context(self, *, excluding: str | None = None, ignore_material: bool = False) -> Context:
        """A hashable key of everything except ``excluding``; equal keys mean the same diluent."""
        return (
            None if ignore_material else self.material,
            tuple(
                (target.solute, target.magnitude, target.unit)
                for target in self.targets
                if target.solute != excluding
            ),
        )

    @property
    def row(self) -> str:
        return self.well[0]

    @property
    def column(self) -> int:
        return int(self.well[1:])


def well_target(
    well: str, *, material: str | None = None, concentrations: Mapping[str, Quantity]
) -> WellTarget:
    """Build a WellTarget from unit-bearing quantities, for example ``{"Van": 100 * uM}``."""
    targets = []
    for solute, quantity in concentrations.items():
        value, unit = canonical_concentration(quantity)
        targets.append(Target(solute=solute, magnitude=value, unit=unit))
    return WellTarget(well=well, material=material, targets=tuple(targets))


@dataclass(frozen=True, slots=True, kw_only=True)
class PlateDesign:
    """The desired end state of a plate. It records no preparation steps."""

    name: str
    shape: tuple[int, int] = (8, 12)
    final_volume_ul: Decimal
    wells: tuple[WellTarget, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("A plate design needs a name.")
        rows, columns = self.shape
        if type(rows) is not int or type(columns) is not int or rows < 1 or columns < 1:
            raise ValueError("Shape must contain two positive integers.")
        if rows > 26:
            raise ValueError("At most 26 named rows are supported.")
        if not isinstance(self.wells, tuple):
            raise TypeError("Wells must be a tuple of WellTarget objects.")
        seen: set[str] = set()
        for target in self.wells:
            row, column = coordinates(target.well)
            if not (0 <= row < rows and 0 <= column < columns):
                raise ValueError(f"{target.well!r} is not a well of a {rows}x{columns} plate.")
            if target.well in seen:
                raise ValueError(f"{target.well} is specified twice.")
            seen.add(target.well)
        if (
            not isinstance(self.final_volume_ul, Decimal)
            or not self.final_volume_ul.is_finite()
            or self.final_volume_ul <= 0
        ):
            raise ValueError("Final volume must be a positive finite Decimal.")

    @classmethod
    def build(
        cls,
        name: str,
        *,
        final_volume: Quantity,
        wells: Iterable[WellTarget],
        shape: tuple[int, int] = (8, 12),
    ) -> Self:
        return cls(
            name=name,
            shape=shape,
            final_volume_ul=magnitude(final_volume, "microliter"),
            wells=tuple(wells),
        )

    def __getitem__(self, well: str) -> WellTarget:
        for target in self.wells:
            if target.well == well:
                return target
        raise KeyError(well)

    def __contains__(self, well: object) -> bool:
        return any(target.well == well for target in self.wells)

    @property
    def solutes(self) -> tuple[str, ...]:
        return tuple(sorted({t.solute for w in self.wells for t in w.targets}))

    @property
    def materials(self) -> tuple[str, ...]:
        return tuple(sorted({w.material for w in self.wells if w.material is not None}))

    def row(self, row: str) -> tuple[WellTarget, ...]:
        """Present wells in one row, in column order."""
        return tuple(sorted((w for w in self.wells if w.row == row), key=lambda w: w.column))

    def column(self, column: int) -> tuple[WellTarget, ...]:
        """Present wells in one column, in row order."""
        return tuple(sorted((w for w in self.wells if w.column == column), key=lambda w: w.row))

    def lines(self, axis: Axis) -> tuple[tuple[WellTarget, ...], ...]:
        rows, columns = self.shape
        if axis == "row":
            return tuple(self.row(chr(65 + r)) for r in range(rows))
        if axis == "column":
            return tuple(self.column(c + 1) for c in range(columns))
        raise ValueError("axis must be 'row' or 'column'")
