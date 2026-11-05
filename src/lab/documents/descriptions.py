"""Plain operation descriptions shared by documents and robot targets."""

from lab.liquid_classes import HamiltonLiquidClass, LiquidClass
from lab.model import (
    LIQUID_STEPS,
    Distribute,
    ManualInstruction,
    Mix,
    MultiMix,
    MultiTransfer,
    SetTemperature,
    Step,
    Thermocycle,
    Transfer,
    Wait,
)
from lab.samples import Location
from lab.units import number


def column_of(locations: tuple[Location, ...]) -> str | None:
    """``"cultures column 12 (rows A–H)"`` when locations run down one column, else None."""
    if len(locations) < 2 or len({location.resource for location in locations}) != 1:
        return None
    columns = {location.well[1:] for location in locations}
    rows = [location.well[0] for location in locations]
    if len(columns) != 1 or rows != sorted(rows) or len(set(rows)) != len(rows):
        return None
    contiguous = all(ord(b) - ord(a) == 1 for a, b in zip(rows, rows[1:], strict=False))
    span = f"rows {rows[0]}–{rows[-1]}" if contiguous else f"rows {', '.join(rows)}"
    return f"{locations[0].resource} column {columns.pop()} ({span})"


def _lanes(locations: tuple[Location, ...]) -> str:
    return column_of(locations) or ", ".join(str(location) for location in locations)


def describe(step: Step) -> str:
    liquid = ""
    if isinstance(step, LIQUID_STEPS):
        if isinstance(step.liquid_class, LiquidClass):
            spec = step.liquid_class
            liquid = (
                f" Liquid class {spec.name}: aspirate {number(spec.aspiration_flow_rate_ul_s)} "
                f"µL/s; dispense {number(spec.dispense_flow_rate_ul_s)} µL/s."
            )
        elif isinstance(step.liquid_class, HamiltonLiquidClass):
            liquid = f" Hamilton STAR liquid class {step.liquid_class.name}."
    match step:
        case Transfer(source, destination, volume, _):
            return f"Transfer {number(volume)} µL from {source} to {destination}.{liquid}"
        case Distribute(source, destinations, volume, air_gap, _):
            wells = ", ".join(str(destination) for destination in destinations)
            gap = f" Air gap {number(air_gap)} µL." if air_gap is not None else ""
            return f"Distribute {number(volume)} µL from {source} to {wells}.{gap}{liquid}"
        case MultiTransfer(sources, destinations, volume, _):
            lane_count = len(destinations)
            origin_text = f"{sources[0]}" if len(set(sources)) == 1 else _lanes(sources)
            return (
                f"Transfer {number(volume)} µL along {lane_count} lanes together from "
                f"{origin_text} to {_lanes(destinations)}.{liquid}"
            )
        case Mix(location, volume, cycles, _):
            return f"Mix {location}: {cycles} cycles of {number(volume)} µL.{liquid}"
        case MultiMix(locations, volume, cycles, _):
            return (
                f"Mix {_lanes(locations)} together: {cycles} cycles of {number(volume)} µL.{liquid}"
            )
        case Wait(seconds, _):
            unit = "second" if seconds == 1 else "seconds"
            return f"Wait {number(seconds)} {unit}."
        case Thermocycle(resource, profile, cycles, lid, _, block_volume):
            holds = "; ".join(
                f"{number(hold.celsius)} °C for {number(hold.seconds)} s" for hold in profile
            )
            lid_text = (
                "lid follows block; lid heating off below the device's heated lid range"
                if lid is None
                else f"lid at {number(lid)} °C"
            )
            block = (
                f" Reported block volume {number(block_volume)} µL."
                if block_volume is not None
                else ""
            )
            return (
                f"Thermocycle all of {resource}: {holds}; {cycles} cycle(s); {lid_text}. "
                "Wait for completion, keep the final temperature settings, and release the plate."
                f"{block}"
            )
        case SetTemperature(resource, celsius, _):
            return (
                f"Hold {resource} at {number(celsius)} °C. "
                "For a device with a heated lid, match its target within the heated lid range; "
                "turn lid heating off below that range."
            )
        case ManualInstruction(text, _):
            return f"Operator: {text}"
        case _:
            raise TypeError(f"Unsupported step: {type(step).__name__}")
