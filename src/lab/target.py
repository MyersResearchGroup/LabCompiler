"""Frozen physical target artifacts, independent of device SDKs."""

from dataclasses import dataclass
from decimal import Decimal

from lab.samples import Location


@dataclass(frozen=True)
class Binding:
    """One exact physical location and its usable constraints, in microlitres."""

    location: Location
    physical: str
    capacity: Decimal
    dead_volume: Decimal


@dataclass(frozen=True)
class TargetPlan:
    name: str
    bindings: tuple[Binding, ...]
    configuration_json: str
    source: str | None = None
    setup: tuple[str, ...] = ()
