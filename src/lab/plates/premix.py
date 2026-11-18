"""Prepare the shared portion of replicate wells once, then distribute it.

For ``n`` wells sharing a composition, the per-well portion is the sum of the doses identical
across the group. The tube holds ``n`` portions times an overage factor, so each component is
pipetted once at ``n`` times its per-well dose.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from lab.liquid_classes import LiquidClassSpec
from lab.plates.patterns import ReplicateGroup
from lab.protocol import Plate, Protocol, Well
from lab.units import Quantity, magnitude, uL


@dataclass(frozen=True, slots=True, kw_only=True)
class PremixComponent:
    source: Well
    dose_ul: Decimal
    liquid_class: LiquidClassSpec | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class PremixPlan:
    group: ReplicateGroup
    components: tuple[PremixComponent, ...]
    overage: Decimal
    portion_ul: Decimal
    total_ul: Decimal

    def component_volume(self, component: PremixComponent) -> Decimal:
        return component.dose_ul * len(self.group.wells) * self.overage


def plan_premix(
    group: ReplicateGroup,
    components: Sequence[tuple[Well, Quantity, LiquidClassSpec | None]],
    *,
    tube_capacity: Quantity,
    overage: Decimal = Decimal("1.1"),
) -> PremixPlan:
    """Size one tube for ``group``; raises ValueError when it would overflow the tube."""
    if not isinstance(overage, Decimal) or not overage.is_finite() or overage < 1:
        raise ValueError("overage must be a Decimal of at least 1.")
    parts = tuple(
        PremixComponent(source=well, dose_ul=magnitude(dose, "microliter"), liquid_class=lc)
        for well, dose, lc in components
    )
    if not parts:
        raise ValueError("A premix needs at least one component.")
    portion = sum((part.dose_ul for part in parts), Decimal(0))
    total = portion * len(group.wells) * overage
    cap = magnitude(tube_capacity, "microliter")
    if total > cap:
        raise ValueError(f"Premix of {total} µL exceeds the {cap} µL tube; split the group.")
    return PremixPlan(
        group=group, components=parts, overage=overage, portion_ul=portion, total_ul=total
    )


def record_premix(
    p: Protocol,
    plan: PremixPlan,
    *,
    tube: Well,
    plate: Plate,
    mix_cycles: int = 5,
    mix_class: LiquidClassSpec | None = None,
) -> None:
    """Assemble the tube largest component first, mix, then share one portion per well."""
    for part in sorted(plan.components, key=lambda c: c.dose_ul, reverse=True):
        p.transfer(
            part.source,
            tube,
            volume=plan.component_volume(part) * uL,
            liquid_class=part.liquid_class,
        )
    p.mix(tube, volume=(plan.total_ul / 2) * uL, cycles=mix_cycles, liquid_class=mix_class)
    p.distribute(
        tube,
        [plate[w] for w in plan.group.wells],
        volume=plan.portion_ul * uL,
        liquid_class=mix_class,
    )
