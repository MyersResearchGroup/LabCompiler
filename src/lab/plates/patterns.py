"""Recognize preparation patterns in a plate design: serial dilutions and replicates."""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from lab.plates.design import Context, PlateDesign, WellTarget

CandidateAxis = Literal["row", "column", "set"]


@dataclass(frozen=True, slots=True, kw_only=True)
class SerialDilutionCandidate:
    """Wells whose concentrations of one solute form a geometric series in one diluent context.

    ``levels`` lists the top concentration first; every well in one level holds the same
    concentration. A width above one means several lanes run in parallel, which is what a
    multichannel head or several independent channels pipette in one stroke.
    """

    solute: str
    unit: str
    top: Decimal
    fold: Decimal
    levels: tuple[tuple[str, ...], ...]
    axis: CandidateAxis
    context: Context
    zero_wells: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if len(self.levels) < 2 or any(not level for level in self.levels):
            raise ValueError("A serial dilution needs at least two nonempty levels.")
        if len({len(level) for level in self.levels}) != 1:
            raise ValueError("Every level of a serial dilution holds the same number of wells.")
        if self.fold <= 1 or self.top <= 0:
            raise ValueError("A serial dilution needs a positive top and a fold above one.")

    @property
    def steps(self) -> int:
        return len(self.levels)

    @property
    def width(self) -> int:
        return len(self.levels[0])

    @property
    def wells(self) -> tuple[str, ...]:
        return tuple(well for level in self.levels for well in level)

    def concentration(self, level: int) -> Decimal:
        """The nominal concentration at ``level`` (zero is the top)."""
        return self.top / self.fold**level


def _close(a: Decimal, b: Decimal, tolerance: Decimal) -> bool:
    return abs(a - b) <= tolerance * max(abs(a), abs(b))


def _snap(ratio: Decimal) -> Decimal:
    """Present 2.0000000000000028 as 2 while keeping genuinely fractional folds."""
    rounded = ratio.quantize(Decimal("1e-9")).normalize()
    integral = rounded.to_integral_value()
    return integral if rounded == integral else rounded


def _runs(
    values: Sequence[Decimal], *, min_fold: Decimal, min_steps: int, tolerance: Decimal
) -> list[tuple[int, int, Decimal]]:
    """Maximal descending geometric runs as inclusive (start, end, fold) index triples."""
    runs: list[tuple[int, int, Decimal]] = []
    start = 0
    while start < len(values) - 1:
        first, second = values[start], values[start + 1]
        if first <= 0 or second <= 0 or first / second < min_fold * (1 - tolerance):
            start += 1
            continue
        fold = first / second
        end = start + 1
        while end + 1 < len(values) and values[end + 1] > 0:
            if not _close(values[end] / values[end + 1], fold, tolerance):
                break
            end += 1
        if end - start + 1 >= min_steps:
            runs.append((start, end, _snap(fold)))
        start = end
    return runs


def _segments(line: Sequence[WellTarget], solute: str) -> list[list[WellTarget]]:
    """Split a physical line into stretches that share material and every other solute."""
    segments: list[list[WellTarget]] = []
    for well in line:
        if well.concentration(solute) is None:
            segments.append([])
            continue
        if (
            segments
            and segments[-1]
            and segments[-1][-1].context(excluding=solute) == well.context(excluding=solute)
        ):
            segments[-1].append(well)
        else:
            segments.append([well])
    return [segment for segment in segments if segment]


def _magnitude(well: WellTarget, solute: str) -> Decimal:
    target = well.concentration(solute)
    if target is None:
        raise ValueError(f"{well.well} has no {solute} target")
    return target.magnitude


def _unit(well: WellTarget, solute: str) -> str:
    target = well.concentration(solute)
    if target is None:
        raise ValueError(f"{well.well} has no {solute} target")
    return target.unit


def find_serial_dilutions(
    design: PlateDesign,
    solute: str,
    *,
    axis: CandidateAxis = "row",
    min_fold: Decimal = Decimal(2),
    min_steps: int = 3,
    tolerance: Decimal = Decimal("1e-9"),
) -> tuple[SerialDilutionCandidate, ...]:
    """Find geometric runs of ``solute`` whose other conditions are constant.

    ``axis="row"`` or ``"column"`` requires physical adjacency along that line, in either
    direction. ``axis="set"`` ignores position: wells sharing a context are sorted by
    concentration, so a scattered gradient is still reported, each level holding every well
    at that concentration. A trailing well with the solute at zero in the same context is
    attached as a zero control.
    """
    if min_steps < 2:
        raise ValueError("A serial dilution needs at least two levels.")
    found: list[SerialDilutionCandidate] = []
    if axis == "set":
        groups: dict[Context, list[WellTarget]] = {}
        for well in design.wells:
            if well.concentration(solute) is not None:
                groups.setdefault(well.context(excluding=solute), []).append(well)
        for context, members in groups.items():
            by_value: dict[Decimal, list[str]] = {}
            for well in members:
                by_value.setdefault(_magnitude(well, solute), []).append(well.well)
            values = sorted(by_value, reverse=True)
            unit = _unit(members[0], solute)
            for start, end, fold in _runs(
                values, min_fold=min_fold, min_steps=min_steps, tolerance=tolerance
            ):
                series = values[start : end + 1]
                found.append(
                    SerialDilutionCandidate(
                        solute=solute,
                        unit=unit,
                        top=series[0],
                        fold=fold,
                        levels=tuple(tuple(sorted(by_value[v])) for v in series),
                        axis="set",
                        context=context,
                        zero_wells=tuple(sorted(by_value.get(Decimal(0), ()))),
                    )
                )
        return tuple(found)
    if axis not in ("row", "column"):
        raise ValueError("axis must be 'row', 'column', or 'set'")
    for line in design.lines(axis):
        for segment in _segments(line, solute):
            for ordered in (segment, segment[::-1]):
                values = [_magnitude(w, solute) for w in ordered]
                for start, end, fold in _runs(
                    values, min_fold=min_fold, min_steps=min_steps, tolerance=tolerance
                ):
                    chain = ordered[start : end + 1]
                    top = chain[0].concentration(solute)
                    assert top is not None
                    trailing = ordered[end + 1 : end + 2]
                    zeros = tuple(w.well for w in trailing if _magnitude(w, solute) == 0)
                    found.append(
                        SerialDilutionCandidate(
                            solute=solute,
                            unit=top.unit,
                            top=top.magnitude,
                            fold=fold,
                            levels=tuple((w.well,) for w in chain),
                            axis=axis,
                            context=chain[0].context(excluding=solute),
                            zero_wells=zeros,
                        )
                    )
    return tuple(found)


def parallelize(
    candidates: Sequence[SerialDilutionCandidate], *, tolerance: Decimal = Decimal("1e-9")
) -> tuple[SerialDilutionCandidate, ...]:
    """Merge width-1 row candidates with equal solute, fold, top, and column path into one.

    Merged candidates have ``axis="column"``: each level is one plate column, listed in row
    order, which an 8-channel head or several STAR channels move together. The context of the
    first row is kept; the rows may assign different materials, since a gradient is built
    before inoculation.
    """
    groups: dict[tuple[str, Decimal, Decimal, tuple[int, ...]], list[SerialDilutionCandidate]]
    groups = {}
    for candidate in candidates:
        if candidate.axis != "row" or candidate.width != 1:
            raise ValueError("parallelize() merges single-width row candidates.")
        columns = tuple(int(level[0][1:]) for level in candidate.levels)
        key = next(
            (
                k
                for k in groups
                if k[0] == candidate.solute
                and k[2] == candidate.fold
                and k[3] == columns
                and _close(k[1], candidate.top, tolerance)
            ),
            (candidate.solute, candidate.top, candidate.fold, columns),
        )
        groups.setdefault(key, []).append(candidate)
    merged = []
    for members in groups.values():
        members.sort(key=lambda c: c.levels[0][0][0])
        first = members[0]
        merged.append(
            SerialDilutionCandidate(
                solute=first.solute,
                unit=first.unit,
                top=first.top,
                fold=first.fold,
                axis="column",
                levels=tuple(tuple(m.levels[i][0] for m in members) for i in range(first.steps)),
                context=first.context,
                zero_wells=tuple(z for m in members for z in m.zero_wells),
            )
        )
    return tuple(merged)


@dataclass(frozen=True, slots=True, kw_only=True)
class ReplicateGroup:
    """Wells with identical solute targets; ``materials`` lists what differs between them."""

    context: Context
    wells: tuple[str, ...]
    materials: tuple[str | None, ...]


def find_replicate_groups(design: PlateDesign, *, min_size: int = 2) -> tuple[ReplicateGroup, ...]:
    """Group wells by chemistry alone, so the shared portion can be prepared once."""
    groups: dict[Context, list[WellTarget]] = {}
    for well in design.wells:
        groups.setdefault(well.context(ignore_material=True), []).append(well)
    return tuple(
        ReplicateGroup(
            context=context,
            wells=tuple(w.well for w in members),
            materials=tuple(w.material for w in members),
        )
        for context, members in groups.items()
        if len(members) >= min_size
    )
