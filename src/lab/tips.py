"""Tip reuse: a composition ledger per well and the tip number of every recorded step.

A tip is kept while a policy allows it and replaced otherwise. ``REUSE_WHEN_CONTAINED`` is
the containment rule: a tip moving one reagent may continue while every destination holds
only substances the source also holds, so nothing foreign is carried back into the source.
The first destination holding a second additive forces a fresh tip.
"""

from collections.abc import Sequence
from typing import Any

from lab.liquid_classes import LiquidClassSpec
from lab.model import (
    LIQUID_STEPS,
    Distribute,
    LiquidStep,
    MultiMix,
    MultiTransfer,
    RecordedProtocol,
    TipPolicy,
    Transfer,
    lanes,
)
from lab.protocol import Protocol, Well
from lab.samples import Location

Composition = frozenset[str]
TipAssignment = tuple[int | None, ...]
"""A tip number per recorded step, or None for steps that use no tip."""


def composition_trace(protocol: RecordedProtocol) -> tuple[dict[Location, Composition], ...]:
    """Component sets per well before each step; transfers union into their destinations.

    A fill without declared components contributes its material text as one component.
    """
    state: dict[Location, Composition] = {
        Location(resource.name, well): frozenset()
        for resource in protocol.resources
        for well in resource.wells
    }
    for resource in protocol.resources:
        for fill in resource.fills:
            state[Location(resource.name, fill.well)] = frozenset(
                fill.components or (fill.material,)
            )
    states = [dict(state)]
    for step in protocol.steps:
        if isinstance(step, (Transfer, Distribute, MultiTransfer)):
            for source, destination in lanes(step):
                assert destination is not None
                state[destination] = state[destination] | state[source]
        states.append(dict(state))
    return tuple(states)


def _sources(step: LiquidStep) -> frozenset[Location]:
    return frozenset(source for source, _ in lanes(step))


def _destinations(step: LiquidStep) -> tuple[Location, ...]:
    return tuple(destination for _, destination in lanes(step) if destination is not None)


def assign_tips(protocol: RecordedProtocol, policy: TipPolicy | None = None) -> TipAssignment:
    """Number the tips a protocol uses under ``policy`` (default: the protocol's own).

    Consecutive liquid steps share a number while the policy keeps the tip. A mix at a
    destination ends the run; a mix at the source, the pre-wetting stroke, keeps it.
    """
    policy = protocol.tip_policy if policy is None else policy
    if not isinstance(policy, TipPolicy):
        raise TypeError("Pass a TipPolicy member")
    ledger = composition_trace(protocol)
    numbers: list[int | None] = []
    current = 0
    previous: LiquidStep | None = None
    for index, step in enumerate(protocol.steps):
        if not isinstance(step, LIQUID_STEPS):
            numbers.append(None)
            continue
        reuse = False
        if previous is not None and policy is not TipPolicy.FRESH_PER_STEP:
            same_source = _sources(step) == _sources(previous)
            same_class = step.liquid_class == previous.liquid_class
            before = ledger[index]
            contained = all(
                before[destination] <= before[source]
                for source, destination in lanes(step)
                if destination is not None
            )
            reuse = same_source and same_class
            if policy is TipPolicy.REUSE_WHEN_CONTAINED:
                reuse = reuse and contained
        if not reuse:
            current += 1
        numbers.append(current)
        previous = step
    return tuple(numbers)


def tip_count(protocol: RecordedProtocol, policy: TipPolicy | None = None) -> int:
    """Distinct tips under ``policy``; multichannel strokes count one tip per lane."""
    assignment = assign_tips(protocol, policy)
    widths: dict[int, int] = {}
    for step, number in zip(protocol.steps, assignment, strict=True):
        if number is None or not isinstance(step, LIQUID_STEPS):
            continue
        width = len(step.destinations) if isinstance(step, MultiTransfer) else 1
        if isinstance(step, MultiMix):
            width = len(step.locations)
        widths[number] = max(widths.get(number, 0), width)
    return sum(widths.values())


def prewet(
    p: Protocol,
    source: Well | Sequence[Well],
    *,
    volume: Any,
    liquid_class: LiquidClassSpec | None = None,
) -> None:
    """Aspirate and return one working volume so later moves start from a wet tip.

    Recorded as a one-cycle mix at the source: the volume accounting is unchanged, and a
    reuse policy keeps the tip for the transfers that follow.
    """
    p.mix(source, volume=volume, cycles=1, liquid_class=liquid_class)
