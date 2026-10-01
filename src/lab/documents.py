"""A printable document rendered from the same frozen data as robot code."""

from html import escape
from typing import Any

from lab.operations import (
    Distribute,
    ExternalPreparation,
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
    match step:
        case Transfer(source, destination, volume, _, height):
            placement = "" if height is None else f" at {number(height)} mm above the well bottom"
            return f"Transfer {number(volume)} µL from {source} to {destination}{placement}."
        case Distribute(source, destinations, volume, air_gap, _):
            wells = ", ".join(str(destination) for destination in destinations)
            gap = f" Air gap {number(air_gap)} µL." if air_gap is not None else ""
            return f"Distribute {number(volume)} µL from {source} to {wells}.{gap}"
        case Mix(location, volume, cycles, _):
            return f"Mix {location}: {cycles} cycles of {number(volume)} µL."
        case Wait(seconds, _):
            unit = "second" if seconds == 1 else "seconds"
            return f"Wait {number(seconds)} {unit}."
        case Thermocycle(resource, profile, cycles, lid, _, block_volume):
            holds = "; ".join(
                f"{number(hold.celsius)} °C for {number(hold.seconds)} s" for hold in profile
            )
            lid_text = "unheated lid" if lid is None else f"lid at {number(lid)} °C"
            block = (
                f" Reported block volume {number(block_volume)} µL."
                if block_volume is not None
                else ""
            )
            return (
                f"Thermocycle all of {resource}: {holds}; {cycles} cycle(s); {lid_text}. "
                "Wait for completion, turn off temperature control, and release the plate."
                f"{block}"
            )
        case SetTemperature(resource, celsius, _):
            return f"Hold {resource} at {number(celsius)} °C."
        case ManualInstruction(text, _):
            return f"Operator: {text}"
        case ExternalPreparation(procedure, instructions, inputs, outputs, _):

            def describe_ports(ports: tuple) -> str:
                return "; ".join(
                    f"{port.count} unit(s) at {port.location}"
                    if port.count
                    else f"{number(port.volume_ul)} µL at {port.location}"
                    for port in ports
                )

            return (
                f"External procedure <{procedure}>: {instructions} "
                f"Consume {describe_ports(inputs)}. Expected output: {describe_ports(outputs)}. "
                "Record completion and measured output before treating material "
                "as available inventory."
            )
        case _:
            raise TypeError(f"Unsupported step: {type(step).__name__}")


def render(compilation: Any) -> str:
    p, target = compilation.protocol, compilation.target
    bindings = {binding.location: binding for binding in target.bindings}
    samples = {sample.id: sample for sample in p.samples}
    resources = []
    for resource in p.resources:
        fills = (
            "; ".join(
                [
                    f"{fill.well}: {fill.material}, {number(fill.volume)} µL"
                    for fill in resource.fills
                ]
                + [
                    f"{place.location.well}: {samples[place.sample_id].label}, "
                    f"{samples[place.sample_id].count} unit(s)"
                    for place in p.placements
                    if place.location.resource == resource.name
                    and place.sample_id in p.input_sample_ids
                    and samples[place.sample_id].count is not None
                ]
            )
            or "Initially empty"
        )
        locations = [
            binding.physical
            for location, binding in bindings.items()
            if location.resource == resource.name
        ]
        physical = (
            ", ".join(locations)
            if len(locations) <= 4
            else f"{locations[0]} … {locations[-1]} ({len(locations)} wells; see plan.json)"
        )
        resources.append(
            f"<tr><th>{escape(resource.name)}</th><td>{escape(fills)}</td>"
            f"<td class='locations'>{escape(physical)}</td></tr>"
        )
    steps = "".join(f"<li><p>{escape(describe(step))}</p></li>" for step in p.steps)
    setup = (
        "<h2>Equipment setup</h2><ul>"
        + "".join(f"<li>{escape(line)}</li>" for line in target.setup)
        + "</ul>"
        if target.setup
        else ""
    )
    totals = "".join(
        f"<tr><th>{escape(str(location))}</th><td>{number(volume)} µL</td></tr>"
        for location, volume in compilation.final_volumes
        if volume
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(p.name)}</title>
<style>
:root {{ color-scheme: light; font-family: system-ui, sans-serif; color: #18312f; }}
body {{ max-width: 920px; padding: 56px 28px; margin: auto; line-height: 1.6; }}
header {{ border-top: 5px solid #25756a; padding-top: 24px; margin-bottom: 36px; }}
h1 {{ font-size: clamp(2rem, 5vw, 3rem); letter-spacing: -.035em; margin: 6px 0; }}
h2 {{ margin-top: 36px; font-size: 1.2rem; }}
.eyebrow, small {{ color: #60736e; }}
.eyebrow {{ text-transform: uppercase; letter-spacing: .1em; }}
table {{ width: 100%; border-collapse: collapse; font-size: .9rem; }}
th, td {{ text-align: left; vertical-align: top; border-bottom: 1px solid #dbe5e1; padding: 12px; }}
.locations {{ overflow-wrap: anywhere; }} ol {{ padding-left: 28px; }}
li {{ padding: 8px 0 16px 12px; border-bottom: 1px solid #e3ebe7; break-inside: avoid; }}
li p {{ margin: 0; }} small, code {{ overflow-wrap: anywhere; }}
.note {{ padding: 16px; background: #f0f5f2; border-radius: 8px; }}
footer {{ margin-top: 40px; font-size: .8rem; color: #60736e; }}
@media (max-width: 600px) {{ body {{ padding: 24px 16px; }} th, td {{ padding: 8px 4px; }} }}
@media print {{ body {{ padding: 0; font-size: 10pt; }} header {{ margin-bottom: 16px; }}
small {{ display: none; }} h2 {{ break-after: avoid; }} tr {{ break-inside: avoid; }} }}
</style></head><body>
<header><div class="eyebrow">Lab / Compiled protocol</div>
<h1>{escape(p.name)}</h1><p>{escape(p.description)}</p>
<p>Target: <strong>{escape(target.name)}</strong></p></header>
<p class="note">Planned quantities. Use a fresh tip for each transfer or mix.</p>
{setup}
<h2>Materials and placement</h2><table><thead><tr>
<th>Resource</th><th>Declared initial contents</th>
<th>Physical locations</th></tr></thead><tbody>{"".join(resources)}</tbody></table>
<h2>Procedure</h2><ol>{steps}</ol>
<h2>Calculated final contents</h2><table>{totals}</table>
<footer>Plan {compilation.digest}<br>Quantities are in microlitres unless stated otherwise.
Inspect plan.json for complete bindings and target configuration.</footer>
</body></html>
"""
