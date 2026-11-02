"""Numeric view data for idealized flow-versus-time diagrams."""

from dataclasses import dataclass
from decimal import Decimal

from lab.model import Distribute, Mix, Transfer
from lab.units import number


@dataclass(frozen=True)
class FlowSegment:
    start: Decimal
    end: Decimal
    rate: Decimal
    volume: Decimal
    x1: Decimal
    x2: Decimal
    y: Decimal
    action: str
    description: str


@dataclass(frozen=True)
class TimeTick:
    x: Decimal
    time: Decimal


@dataclass(frozen=True)
class FlowProfile:
    step_id: str
    label: str
    duration: Decimal
    maximum: Decimal
    segments: tuple[FlowSegment, ...]
    ticks: tuple[TimeTick, ...]


def build_flow_profile(
    index: int,
    step: Transfer | Distribute | Mix,
    aspiration: Decimal,
    dispense: Decimal,
    *,
    refill_between_destinations: bool = False,
) -> FlowProfile:
    """Calculate nominal V/Q phases; no HTML, ramps, or device delays."""
    label = f"Step {index} · {type(step).__name__} · {number(step.volume)} µL"
    if isinstance(step, Mix):
        phases = [(step.volume, aspiration), (step.volume, -dispense)] * step.cycles
        label += f" × {step.cycles} cycles"
    elif isinstance(step, Distribute):
        if refill_between_destinations:
            phases = [(step.volume, aspiration), (step.volume, -dispense)] * len(step.destinations)
        else:
            phases = [(step.volume * len(step.destinations), aspiration)]
            phases.extend((step.volume, -dispense) for _ in step.destinations)
        label += f" × {len(step.destinations)} destinations"
    else:
        phases = [(step.volume, aspiration), (step.volume, -dispense)]
    duration = sum((volume / abs(rate) for volume, rate in phases), Decimal(0))
    maximum = max(aspiration, dispense)
    elapsed = Decimal(0)
    segments = []
    for volume, rate in phases:
        end = elapsed + volume / abs(rate)
        action = "Aspiration" if rate > 0 else "Dispense"
        segments.append(
            FlowSegment(
                start=elapsed,
                end=end,
                rate=rate,
                volume=volume,
                x1=48 + elapsed / duration * 252,
                x2=48 + end / duration * 252,
                y=100 - rate / maximum * 70,
                action=action,
                description=f"{action}: {elapsed:.3g}–{end:.3g} s, "
                f"{number(abs(rate))} µL/s, {number(volume)} µL",
            )
        )
        elapsed = end
    return FlowProfile(
        step_id=f"step-{index}",
        label=label,
        duration=duration,
        maximum=maximum,
        segments=tuple(segments),
        ticks=tuple(
            TimeTick(48 + fraction * 252, duration * fraction)
            for fraction in (Decimal(0), Decimal("0.5"), Decimal(1))
        ),
    )
