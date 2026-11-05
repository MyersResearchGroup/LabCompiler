"""Lay out a plate from abstract requirements instead of explicit well coordinates.

Requirements are the product of materials, constant condition sets, and gradient levels,
plus individually specified controls. Gradients run along rows with the highest
concentration in the rightmost column, so one plate column holds one level and a
multichannel head moves column to column. Material and condition combinations stack down
the rows.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from itertools import product

from lab.plates.design import PlateDesign, Target, WellTarget, canonical_concentration, well_name
from lab.units import Quantity, magnitude


def _targets(concentrations: Mapping[str, Quantity]) -> tuple[Target, ...]:
    targets = []
    for solute, quantity in concentrations.items():
        value, unit = canonical_concentration(quantity)
        targets.append(Target(solute=solute, magnitude=value, unit=unit))
    return tuple(targets)


@dataclass(frozen=True, slots=True, kw_only=True)
class GradientSpec:
    """A geometric series of one solute: ``steps`` levels from ``top`` down by ``fold``."""

    solute: str
    top: Quantity
    fold: Decimal
    steps: int
    include_zero: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.fold, Decimal) or not self.fold.is_finite() or self.fold < 2:
            raise ValueError("Serial dilution folds below 2 do not fit a well; dose directly.")
        if type(self.steps) is not int or self.steps < 2:
            raise ValueError("A gradient needs at least two levels.")
        canonical_concentration(self.top)

    def levels(self) -> tuple[Target, ...]:
        """Targets from the top down, with an optional trailing zero control."""
        value, unit = canonical_concentration(self.top)
        series = [value / self.fold**i for i in range(self.steps)]
        if self.include_zero:
            series.append(Decimal(0))
        return tuple(Target(solute=self.solute, magnitude=v, unit=unit) for v in series)


@dataclass(frozen=True, slots=True, kw_only=True)
class ConditionSpec:
    """Constant concentrations shared by a block of wells."""

    label: str
    concentrations: Mapping[str, Quantity]

    def targets(self) -> tuple[Target, ...]:
        return _targets(self.concentrations)


@dataclass(frozen=True, slots=True, kw_only=True)
class ControlSpec:
    """Individually specified wells that take part in no gradient."""

    label: str
    concentrations: Mapping[str, Quantity]
    replicates: int = 1

    def targets(self) -> tuple[Target, ...]:
        return _targets(self.concentrations)


@dataclass(frozen=True, slots=True, kw_only=True)
class Requirements:
    name: str
    final_volume: Quantity
    materials: tuple[str, ...]
    conditions: tuple[ConditionSpec, ...]
    gradient: GradientSpec | None = None
    controls: tuple[ControlSpec, ...] = ()
    replicates: int = 1
    shape: tuple[int, int] = (8, 12)

    def __post_init__(self) -> None:
        if not self.materials or not self.conditions:
            raise ValueError("Requirements need at least one material and one condition set.")
        if type(self.replicates) is not int or self.replicates < 1:
            raise ValueError("Replicates must be a positive integer.")
        if self.gradient is not None:
            for condition in self.conditions:
                if self.gradient.solute in condition.concentrations:
                    raise ValueError(
                        f"{condition.label} fixes {self.gradient.solute}, the gradient solute."
                    )

    @property
    def series_length(self) -> int:
        return len(self.gradient.levels()) if self.gradient else 1

    @property
    def well_count(self) -> int:
        """The running count a design tool shows while requirements are edited."""
        series = len(self.materials) * len(self.conditions) * self.series_length
        controls = sum(control.replicates for control in self.controls) * len(self.materials)
        return series * self.replicates + controls


@dataclass(frozen=True, slots=True, kw_only=True)
class Arrangement:
    design: PlateDesign
    unused_wells: tuple[str, ...]
    rows: tuple[tuple[str, str, str], ...]
    """(row, material, condition label) for each series row, in placement order."""


def arrange(requirements: Requirements) -> Arrangement:
    """Place series along rows and material x condition blocks down the plate.

    Raises ValueError with the overflow when the plate is too small, so a design tool can
    report "over by N wells" rather than produce a partial layout.
    """
    rows, columns = requirements.shape
    capacity = rows * columns
    if requirements.well_count > capacity:
        raise ValueError(
            f"{requirements.well_count} wells requested; a {rows}x{columns} plate holds "
            f"{capacity} ({requirements.well_count - capacity} over)."
        )
    if requirements.series_length > columns:
        raise ValueError(
            f"A {requirements.series_length}-level series does not fit {columns} columns."
        )
    series_rows = len(requirements.materials) * len(requirements.conditions)
    series_rows *= requirements.replicates
    if series_rows > rows:
        raise ValueError(
            f"{series_rows} series rows requested; a {rows}x{columns} plate has {rows} rows."
        )
    wells: list[WellTarget] = []
    row_log: list[tuple[str, str, str]] = []
    levels: tuple[Target | None, ...] = (
        requirements.gradient.levels() if requirements.gradient else (None,)
    )
    # Series occupy the rightmost columns of their rows; controls fill from the left.
    series_start = columns - len(levels)
    limit = dict.fromkeys(range(rows), columns)
    left = dict.fromkeys(range(rows), 0)
    for row_index, (material, condition) in enumerate(
        combination
        for combination in product(requirements.materials, requirements.conditions)
        for _ in range(requirements.replicates)
    ):
        row_log.append((chr(65 + row_index), material, condition.label))
        limit[row_index] = series_start
        for offset, level in enumerate(reversed(levels)):
            targets = condition.targets() + ((level,) if level is not None else ())
            wells.append(
                WellTarget(
                    well=well_name(row_index, series_start + offset),
                    material=material,
                    targets=targets,
                )
            )
    for material in requirements.materials:
        for control in requirements.controls:
            for _ in range(control.replicates):
                open_rows = [r for r in range(rows) if left[r] < limit[r]]
                if not open_rows:
                    raise ValueError("No free wells remain for controls; use a larger plate.")
                row = min(open_rows, key=lambda r: (left[r], r))
                wells.append(
                    WellTarget(
                        well=well_name(row, left[row]), material=material, targets=control.targets()
                    )
                )
                left[row] += 1
    used = {w.well for w in wells}
    unused = tuple(
        well_name(r, c) for r in range(rows) for c in range(columns) if well_name(r, c) not in used
    )
    design = PlateDesign(
        name=requirements.name,
        shape=requirements.shape,
        final_volume_ul=magnitude(requirements.final_volume, "microliter"),
        wells=tuple(wells),
    )
    return Arrangement(design=design, unused_wells=unused, rows=tuple(row_log))
