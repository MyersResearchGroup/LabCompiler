"""The plate preview uses the same accounting and geometry as the compiled plan."""

import json
import re
from dataclasses import replace
from decimal import Decimal

from lab import LiquidClass, Protocol, celsius, compile, seconds, uL
from lab.documents.prepare import build_worksheet
from lab.samples import Location


def test_preview_tracks_serial_transfers_distribution_and_nonliquid_steps():
    p = Protocol("Preparation")
    source = p.container("stock", contents="Buffer", volume=300 * uL, capacity=400 * uL)
    plate = p.plate("assay", shape=(2, 3), capacity=200 * uL)
    p.transfer(source, plate["A1"], volume=100 * uL)
    p.mix(plate["A1"], volume=40 * uL, cycles=2)
    p.transfer(plate["A1"], plate["B3"], volume=30 * uL)
    p.distribute(source, (plate["A2"], plate["B1"]), volume=20 * uL, air_gap=2 * uL)
    p.wait(2 * seconds)
    p.set_temperature(plate, celsius(37))
    p.thermocycle(plate, [(celsius(37), 10 * seconds)], cycles=2)
    p.manual("Read the plate.")
    compilation = compile(p, to=None)
    worksheet = build_worksheet(compilation)
    assert worksheet.tip_count == 4
    wells = {
        well.location: well.reference.anchor
        for resource in worksheet.resources
        for well in resource.wells
    }
    stock, a1, a2, b1, b3 = [
        wells[Location(*location)]
        for location in (
            ("stock", "A1"),
            ("assay", "A1"),
            ("assay", "A2"),
            ("assay", "B1"),
            ("assay", "B3"),
        )
    ]
    frames = worksheet.preparation.frames
    assert len(frames) == len(compilation.protocol.steps) + 1
    volumes = [{well.anchor: well.volume for well in frame.volumes} for frame in frames]
    assert volumes[0] == {stock: 300, a1: 0, a2: 0, b1: 0, b3: 0}
    assert volumes[1] == {stock: 200, a1: 100, a2: 0, b1: 0, b3: 0}
    assert volumes[2] == volumes[1]
    assert volumes[3] == {stock: 200, a1: 70, a2: 0, b1: 0, b3: 30}
    assert volumes[4] == {stock: 160, a1: 70, a2: 20, b1: 20, b3: 30}
    assert all(volume == volumes[4] for volume in volumes[5:])
    assert all(sum(volume.values()) == 300 for volume in volumes)
    assert volumes[-1] == {
        wells[location]: volume
        for location, volume in compilation.final_volumes
        if location in wells
    }
    assert frames[1].source == stock and frames[1].destinations == (a1,)
    assert frames[2].active == (a1,) and frames[2].source is None
    assert frames[2].mix_cycles == 2
    assert frames[3].source == a1 and frames[3].destinations == (b3,)
    assert frames[4].destinations == (a2, b1)
    assert frames[5].source is None and frames[5].active == ()
    assert set(frames[6].active) == {a1, a2, b1, b3}
    assert frames[6].mix_cycles == 0
    assert frames[7].active == frames[6].active
    assert frames[8].active == ()

    layout = worksheet.preparation.plates[1]
    assert layout.rows == ("A", "B") and layout.columns == (1, 2, 3)
    assert [well.coordinate for well in layout.wells] == ["A1", "A2", "A3", "B1", "B2", "B3"]
    assert layout.wells[2].reference is None
    assert (layout.wells[-1].row, layout.wells[-1].column) == (1, 2)
    assert (layout.wells[-1].x, layout.wells[-1].y) == (104, 72)


def test_well_fill_area_uses_effective_physical_capacity():
    p = Protocol("Bound capacity")
    source = p.container("stock", contents="Buffer", volume=100 * uL, capacity=400 * uL)
    output = p.container("output", capacity=200 * uL)
    p.transfer(source, output, volume=25 * uL)
    compilation = compile(p, to=None)
    target = replace(
        compilation.target,
        bindings=tuple(
            replace(binding, capacity=Decimal(100))
            if binding.location.resource == "stock"
            else binding
            for binding in compilation.target.bindings
        ),
    )
    worksheet = build_worksheet(replace(compilation, target=target))
    initial, final = worksheet.preparation.frames
    assert initial.volumes[0].radius == 11
    assert abs(final.volumes[0].radius ** 2 / Decimal(121) - Decimal("0.75")) < Decimal("1e-24")
    assert final.volumes[1].volume == 25


def test_preview_payload_and_svg_labels_are_escaped_and_standalone():
    p = Protocol("Preview")
    source = p.container("stock", contents="Buffer <A&B>", volume=100 * uL, capacity=200 * uL)
    output = p.container("output", capacity=200 * uL)
    p.transfer(source, output, volume=10 * uL)
    instruction = '</script><script>alert("unsafe")</script>'
    p.manual(instruction)
    html = compile(p, to=None).files["protocol.html"]
    payload = re.search(
        r'<script type="application/json" data-preparation-frames>(.*?)</script>', html, re.DOTALL
    ).group(1)
    frames = json.loads(payload)
    assert frames[-1]["description"] == f"Operator: {instruction}"
    assert instruction not in html and "</script>" not in payload
    assert "Buffer &lt;A&amp;B&gt;" in html
    assert "<script src=" not in html and '<link rel="stylesheet"' not in html
    cards = set(re.findall(r'<article id="([^"]+)" class="reference-card">', html))
    assert set(re.findall(r'<a[^>]*\sdata-map-well="([^"]+)"', html)) <= cards
    assert len(re.findall('class="plate-map"', html)) == 2
    assert "data-preview-position" in html and 'data-preview-step="1"' in html


def test_instructions_without_resources_do_not_render_empty_plate_controls():
    p = Protocol("Operator only")
    p.manual("Prepare the work area.")
    html = compile(p, to=None).files["protocol.html"]
    assert "<lab-preparation>" not in html
    assert 'href="#preparation"' not in html
    assert "Prepare the work area." in html


def test_manual_distribution_refills_for_each_destination_without_splitting_the_step():
    p = Protocol("Manual aliquots")
    source = p.container("stock", contents="DMSO", volume=100 * uL, capacity=200 * uL)
    plate = p.plate("plate", capacity=200 * uL)
    dmso = LiquidClass(
        "dmso", aspiration_flow_rate=5 * uL / seconds, dispense_flow_rate=10 * uL / seconds
    )
    p.distribute(source, [plate[f"A{i}"] for i in range(1, 10)], volume=2 * uL, liquid_class=dmso)
    compilation = compile(p, to=None)
    manual = build_worksheet(compilation)
    assert len(manual.procedure) == 1
    frame = manual.preparation.frames[1]
    assert frame.refill_between_destinations
    assert "aspirate a fresh 2 µL from the source and dispense it completely" in frame.description
    assert [well.volume for well in frame.volumes] == [82, *([2] * 9)]
    phases = manual.liquid_classes[0].profiles[0].segments
    assert [(phase.volume, phase.rate) for phase in phases] == [(2, 5), (2, -10)] * 9

    robot = build_worksheet(replace(compilation, target=replace(compilation.target, name="OT-2")))
    assert not robot.preparation.frames[1].refill_between_destinations
    assert robot.preparation.frames[1].volumes == frame.volumes
    robot_phases = robot.liquid_classes[0].profiles[0].segments
    assert [(phase.volume, phase.rate) for phase in robot_phases] == [(18, 5), *([(2, -10)] * 9)]
