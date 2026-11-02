"""Liquid-handling settings recorded with operations, independent of robot SDKs."""

from dataclasses import dataclass
from decimal import Decimal
from typing import TypeAlias

from lab.units import Quantity, magnitude


@dataclass(frozen=True, init=False)
class LiquidClass:
    """A named, portable pair of aspiration and dispense flow rates.

    Supply volume/time quantities; the immutable record stores Decimal µL/s.
    These rates apply to every aspiration/dispense, including mixing cycles.
    Other settings and volume calibration retain the target's defaults. This
    class does not assert equivalent physical performance across instruments.
    """

    name: str
    aspiration_flow_rate_ul_s: Decimal
    dispense_flow_rate_ul_s: Decimal

    def __init__(
        self, name: str, *, aspiration_flow_rate: Quantity, dispense_flow_rate: Quantity
    ) -> None:
        _validate_name(name)
        object.__setattr__(self, "name", name)
        object.__setattr__(
            self, "aspiration_flow_rate_ul_s", magnitude(aspiration_flow_rate, "microliter/second")
        )
        object.__setattr__(
            self, "dispense_flow_rate_ul_s", magnitude(dispense_flow_rate, "microliter/second")
        )


@dataclass(frozen=True)
class HamiltonLiquidClass:
    """Exact built-in symbol in PyLabRobot's Hamilton STAR liquid-class catalogue.

    STAR compilation resolves and snapshots the installed SDK's definition.
    Other robot targets reject this reference. Selection must match the liquid,
    tips, volume range, and dispensing mode validated on the intended STAR.
    """

    name: str

    def __post_init__(self) -> None:
        _validate_name(self.name)
        if not self.name.isidentifier() or self.name.startswith("_"):
            raise ValueError("Hamilton liquid class must name a public built-in symbol")


LiquidClassSpec: TypeAlias = LiquidClass | HamiltonLiquidClass


def validate_liquid_class(value: LiquidClassSpec | None) -> None:
    if value is not None and not isinstance(value, (LiquidClass, HamiltonLiquidClass)):
        raise TypeError("Pass a LiquidClass or HamiltonLiquidClass, not an untyped name or mapping")


def _validate_name(name: str) -> None:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Liquid class name must be a nonempty string")
