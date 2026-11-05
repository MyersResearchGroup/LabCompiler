"""Classify lane sets for multichannel heads; shared by the robot targets."""

from collections.abc import Sequence
from dataclasses import dataclass

from lab.model import LiquidStep, MultiMix, MultiTransfer, Resource, lanes
from lab.samples import Location


@dataclass(frozen=True, slots=True, kw_only=True)
class ColumnStroke:
    """Lanes a fixed-spacing multichannel head executes by addressing the top well of a column.

    ``source_top`` is the first lane's source: the top well of a source column, or the one
    trough well every lane draws from. ``destination_top`` is the top well of the destination
    column. ``rows`` are the contiguous rows covered, starting at A.
    """

    source_top: Location
    destination_top: Location | None
    rows: tuple[str, ...]

    @property
    def width(self) -> int:
        return len(self.rows)


def _column(locations: Sequence[Location], resources: dict[str, Resource]) -> bool:
    """True when ``locations`` are rows A.. of one column of one resource, in order."""
    if len({location.resource for location in locations}) != 1:
        return False
    resource = resources.get(locations[0].resource)
    if resource is None or resource.rows < len(locations):
        return False
    columns = {location.well[1:] for location in locations}
    rows = [location.well[0] for location in locations]
    return len(columns) == 1 and rows == [chr(65 + index) for index in range(len(locations))]


def column_stroke(step: LiquidStep, resources: dict[str, Resource]) -> ColumnStroke | None:
    """A stroke when every lane lies in rows A.. of one column, else None.

    Sources must be either one shared well or the matching rows of one column. Rows that do
    not start at A, alternate, or span several columns are not strokes for fixed-spacing heads.
    """
    if not isinstance(step, (MultiTransfer, MultiMix)):
        return None
    pairs = lanes(step)
    if len(pairs) < 2:
        return None
    sources = tuple(source for source, _ in pairs)
    destinations = tuple(destination for _, destination in pairs if destination is not None)
    if isinstance(step, MultiMix):
        if not _column(sources, resources):
            return None
        return ColumnStroke(
            source_top=sources[0], destination_top=None, rows=tuple(s.well[0] for s in sources)
        )
    if not _column(destinations, resources):
        return None
    shared = len(set(sources)) == 1
    if not shared and not _column(sources, resources):
        return None
    return ColumnStroke(
        source_top=sources[0],
        destination_top=destinations[0],
        rows=tuple(d.well[0] for d in destinations),
    )
