"""Plan and record a serial dilution gradient from a detected candidate.

Every well below the top is pre-filled with the gradient volume ``G`` of diluent. Moving ``t``
from a well at concentration ``c`` into ``G`` gives ``c * t / (G + t)``; requiring ``c / f``
gives ``t = G / (f - 1)``. Each well ends at ``G``, the top is prepared at ``G + t``, and the
bottom passes its surplus ``t`` to waste. Building at ``concentrate`` times the final
concentration in ``G = V / concentrate`` keeps the peak volume ``G + t`` inside the well; the
constant additions made afterwards bring every well to ``V`` and dilute the gradient solute by
exactly ``concentrate``.
"""

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal

from lab.liquid_classes import LiquidClassSpec
from lab.plates.patterns import SerialDilutionCandidate
from lab.protocol import Plate, Protocol, Well
from lab.units import Quantity, magnitude, uL


@dataclass(frozen=True, slots=True, kw_only=True)
class SerialDilutionPlan:
    """Volumes for one gradient, in microlitres."""

    candidate: SerialDilutionCandidate
    concentrate: Decimal
    gradient_volume_ul: Decimal
    transfer_volume_ul: Decimal
    peak_volume_ul: Decimal
    top_stock_ul: Decimal
    top_diluent_ul: Decimal

    @property
    def moves(self) -> int:
        """Well-to-well transfers plus the final transfer to waste, per lane."""
        return self.candidate.steps * self.candidate.width


def plan_serial_dilution(
    candidate: SerialDilutionCandidate,
    *,
    stock_concentration: Quantity,
    final_volume: Quantity,
    capacity: Quantity,
    reserved: Quantity = 0 * uL,
    concentrate: Decimal = Decimal(1),
) -> SerialDilutionPlan:
    """Size a gradient built at ``concentrate`` times the final concentration.

    ``reserved`` is the volume every well still needs after the gradient (co-solutes,
    vehicle, inoculum). Raises ValueError naming the minimum workable ``concentrate`` when
    the peak mixing volume exceeds ``capacity``.
    """
    if not isinstance(concentrate, Decimal) or not concentrate.is_finite() or concentrate < 1:
        raise ValueError("concentrate must be a Decimal of at least 1.")
    stock = magnitude(stock_concentration, candidate.unit)
    volume = magnitude(final_volume, "microliter")
    cap = magnitude(capacity, "microliter")
    held = magnitude(reserved, "microliter", positive=False)
    fold = candidate.fold
    gradient = volume / concentrate
    transfer = gradient / (fold - 1)
    peak = gradient + transfer
    if peak > cap:
        minimum = (volume * fold / (cap * (fold - 1))).quantize(Decimal("0.01"), ROUND_CEILING)
        raise ValueError(
            f"Peak volume {peak} µL exceeds the {cap} µL well; "
            f"use concentrate >= {minimum} or build the gradient in a deeper plate."
        )
    if gradient + held > volume:
        raise ValueError(
            f"Gradient volume {gradient} µL plus {held} µL of later additions "
            f"exceeds the {volume} µL final volume."
        )
    top_concentration = candidate.top * concentrate
    if top_concentration > stock:
        raise ValueError(
            f"{top_concentration} {candidate.unit} exceeds the stock at {stock} {candidate.unit}."
        )
    top_stock = top_concentration / stock * peak
    return SerialDilutionPlan(
        candidate=candidate,
        concentrate=concentrate,
        gradient_volume_ul=gradient,
        transfer_volume_ul=transfer,
        peak_volume_ul=peak,
        top_stock_ul=top_stock,
        top_diluent_ul=peak - top_stock,
    )


def record_serial_dilution(
    p: Protocol,
    plan: SerialDilutionPlan,
    *,
    plate: Plate,
    stock: Well,
    diluent: Well,
    waste: Well,
    mix_cycles: int = 3,
    stock_class: LiquidClassSpec | None = None,
    diluent_class: LiquidClassSpec | None = None,
) -> None:
    """Record diluent fills, the top dose, and the chain of mix-then-transfer moves.

    Levels are paired by index: level ``i`` well ``j`` feeds level ``i + 1`` well ``j``, and a
    level wider than one well is recorded as one lane set so multichannel targets run it in
    one stroke. The bottom level passes its surplus to ``waste`` so every well ends at the
    gradient volume. Zero-control wells receive the gradient volume of diluent and no stock.
    """
    c = plan.candidate
    top = [plate[w] for w in c.levels[0]]
    below = [plate[w] for level in c.levels[1:] for w in level]
    zeros = [plate[w] for w in c.zero_wells]
    gradient = plan.gradient_volume_ul * uL
    transfer = plan.transfer_volume_ul * uL
    mix_volume = (plan.peak_volume_ul / 2) * uL

    p.distribute(diluent, top, volume=plan.top_diluent_ul * uL, liquid_class=diluent_class)
    if below or zeros:
        p.distribute(diluent, [*below, *zeros], volume=gradient, liquid_class=diluent_class)
    p.distribute(stock, top, volume=plan.top_stock_ul * uL, liquid_class=stock_class)

    def lane_set(wells: tuple[str, ...]) -> Well | list[Well]:
        return plate[wells[0]] if len(wells) == 1 else [plate[w] for w in wells]

    for upper, lower in zip(c.levels, c.levels[1:], strict=False):
        p.mix(lane_set(upper), volume=mix_volume, cycles=mix_cycles, liquid_class=stock_class)
        p.transfer(lane_set(upper), lane_set(lower), volume=transfer, liquid_class=stock_class)
    bottom = c.levels[-1]
    p.mix(lane_set(bottom), volume=mix_volume, cycles=mix_cycles, liquid_class=stock_class)
    p.transfer(
        lane_set(bottom),
        waste if len(bottom) == 1 else [waste] * len(bottom),
        volume=transfer,
        liquid_class=stock_class,
    )
