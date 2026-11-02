"""Plain operation descriptions shared by documents and robot targets."""

from lab.liquid_classes import HamiltonLiquidClass, LiquidClass
from lab.model import (
    Distribute,
    ManualInstruction,
    Mix,
    SetTemperature,
    Step,
    Thermocycle,
    Transfer,
    Wait,
)
from lab.units import number


def describe(step: Step) -> str:
    liquid = ""
    if isinstance(step, (Transfer, Distribute, Mix)):
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
        case Mix(location, volume, cycles, _):
            return f"Mix {location}: {cycles} cycles of {number(volume)} µL.{liquid}"
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
