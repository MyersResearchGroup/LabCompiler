"""Human-readable worksheets retain the exact operation order and settings."""

import re
from dataclasses import replace
from decimal import Decimal
from html import unescape

import pytest

from lab import HamiltonLiquidClass, LiquidClass, Protocol, compile, seconds, uL
from lab.documents.flow import build_flow_profile
from lab.samples import Sample


def test_procedure_rows_preserve_doses_destinations_cycles_and_distinct_classes():
    p = Protocol("Worksheet")
    source = p.container("stock", contents="Water", volume=100 * uL, capacity=200 * uL)
    plate = p.plate("plate", capacity=200 * uL)
    slow = LiquidClass(
        "aqueous", aspiration_flow_rate=10 * uL / seconds, dispense_flow_rate=15 * uL / seconds
    )
    fast = LiquidClass(
        "aqueous", aspiration_flow_rate=20 * uL / seconds, dispense_flow_rate=30 * uL / seconds
    )
    p.transfer(source, plate["A1"], volume=12.5 * uL, liquid_class=slow)
    p.distribute(
        source,
        (plate["A2"], plate["A3"]),
        volume=7.25 * uL,
        air_gap=2.5 * uL,
        liquid_class=fast,
    )
    p.mix(plate["A2"], volume=3 * uL, cycles=4, liquid_class=slow)
    p.wait(90 * seconds)
    p.manual("Inspect the plate <without recording a measurement>.")
    html = compile(p, to=None).files["protocol.html"]
    rows = re.findall(r'<tr id="step-\d+">.*?</tr>', html, flags=re.DOTALL)
    text = [" ".join(unescape(re.sub(r"<[^>]+>", " ", row)).split()) for row in rows]
    assert text == [
        "01 Transfer 12.5 µL stock:A1 plate:A1 aqueous",
        "02 Distribute 7.25 µL per destination stock:A1 plate:A2 plate:A3 aqueous Air gap 2.5 µL",
        "03 Mix 3 µL 4 cycles plate:A2 — aqueous",
        "04 Wait 90 seconds.",
        "05 Operator: Inspect the plate <without recording a measurement>.",
    ]
    assert 'href="#liquid-class-1"' in rows[0] and 'href="#liquid-class-1"' in rows[2]
    assert 'href="#liquid-class-2"' in rows[1]
    assert 'id="liquid-class-1"' in html and 'id="liquid-class-2"' in html
    assert "aqueous: aspirate 10 µL/s; dispense 15 µL/s" in html
    assert "aqueous: aspirate 20 µL/s; dispense 30 µL/s" in html


def test_summary_notes_and_materials_are_escaped_and_retained():
    p = Protocol(
        "<Worksheet>",
        description="Prepare an aliquot.\n\nInput notes: <script>alert(1)</script>\nLine two.",
    )
    source = p.container("stock", contents="Buffer <A&B>", volume=40 * uL, capacity=200 * uL)
    destination = p.container("output", capacity=200 * uL)
    p.transfer(source, destination, volume=10 * uL)
    html = compile(p, to=None).files["protocol.html"]
    header = html.split("<header>", 1)[1].split("</header>", 1)[0]
    assert "Prepare an aliquot." in header
    assert "Input notes:" not in header
    assert "&lt;Worksheet&gt;" in header
    assert "<script>" not in html
    assert '<details class="notes">' in html
    assert "Input notes: &lt;script&gt;alert(1)&lt;/script&gt;\nLine two." in html
    assert "Buffer &lt;A&amp;B&gt;" in html
    assert "Unspecified" in html


def test_inspectable_locations_distinguish_initial_loads_and_planned_outputs():
    p = Protocol("Aliquot")
    source = p.container("stock", contents="Buffer <A&B>", volume=100 * uL, capacity=200 * uL)
    output = p.container("output", capacity=200 * uL)
    p.add_sample(
        Sample(id="parent", label="Stock buffer", material_identity="buffer"),
        at=source,
        is_input=True,
    )
    p.transfer(source, output, volume=12.5 * uL)
    p.add_sample(
        Sample(
            id="aliquot",
            label="Planned buffer aliquot",
            material_identity="buffer",
            parent_ids=("parent",),
        ),
        at=output,
        is_output=True,
    )
    html = compile(p, to=None).files["protocol.html"]
    cards = dict(
        re.findall(r'<article id="([^"]+)" class="reference-card">(.*?)</article>', html, re.DOTALL)
    )
    source_card = next(card for card in cards.values() if ">stock:A1</h3>" in card)
    output_card = next(card for card in cards.values() if ">output:A1</h3>" in card)
    assert "Buffer &lt;A&amp;B&gt;" in source_card
    assert "<dt>Initial volume</dt><dd>100 µL</dd>" in source_card
    assert "<dt>Planned final volume</dt><dd>87.5 µL</dd>" in source_card
    assert "Initially empty" in output_card
    assert "<dt>Initial volume</dt><dd>0 µL</dd>" in output_card
    assert "<dt>Planned final volume</dt><dd>12.5 µL</dd>" in output_card
    assert "Planned output sample" in output_card and "Planned buffer aliquot" in output_card
    assert "<dt>Parent samples</dt><dd>parent</dd>" in output_card
    assert 'href="#step-1"' in output_card and "12.5 µL from" in output_card
    assert set(re.findall(r'data-inspect="([^"]+)"', html)) <= cards.keys()
    assert len(re.findall(r'id="([^"]+)"', html)) == len(set(re.findall(r'id="([^"]+)"', html)))


@pytest.mark.parametrize(
    "operation, expected",
    [
        ("transfer", [(0, 2, 6, 12), (2, 5, -4, 12)]),
        ("mix", [(0, 2, 6, 12), (2, 5, -4, 12), (5, 7, 6, 12), (7, 10, -4, 12)]),
        ("distribute", [(0, 4, 6, 24), (4, 7, -4, 12), (7, 10, -4, 12)]),
    ],
)
def test_flow_profile_plots_signed_rates_against_ideal_active_time(operation, expected):
    p = Protocol("Flow profile")
    source = p.container("source", contents="Water", volume=100 * uL, capacity=200 * uL)
    plate = p.plate("plate", capacity=200 * uL)
    if operation == "transfer":
        p.transfer(source, plate["A1"], volume=12 * uL)
    elif operation == "mix":
        p.mix(source, volume=12 * uL, cycles=2)
    else:
        p.distribute(source, (plate["A1"], plate["A2"]), volume=12 * uL, air_gap=2 * uL)
    profile = build_flow_profile(1, p.steps[0], Decimal(6), Decimal(4))
    assert [(s.start, s.end, s.rate, s.volume) for s in profile.segments] == expected
    assert profile.duration == expected[-1][1]
    assert [(tick.x, tick.time) for tick in profile.ticks] == [
        (48, 0),
        (174, profile.duration / 2),
        (300, profile.duration),
    ]
    assert all(
        segment.y < 100 if segment.rate > 0 else segment.y > 100 for segment in profile.segments
    )


def test_native_profiles_use_only_rates_captured_in_the_plan():
    p = Protocol("Native profile")
    source = p.container("source", contents="Water", volume=100 * uL, capacity=200 * uL)
    destination = p.container("destination", capacity=200 * uL)
    p.transfer(
        source, destination, volume=12 * uL, liquid_class=HamiltonLiquidClass("ExampleNativeClass")
    )
    bundle = compile(p, to=None)
    assert "Flow rate over time" not in bundle.files["protocol.html"]
    assert "This plan records the class name only" in bundle.files["protocol.html"]
    captured = replace(
        bundle,
        target=replace(
            bundle.target,
            name="STAR",
            configuration_json="""{
        "hamilton_liquid_classes": {"ExampleNativeClass": {
            "aspiration_flow_rate": 6.0, "dispense_flow_rate": 4.0
        }}
    }""",
        ),
    )
    html = captured.files["protocol.html"]
    assert "Flow rate over time" in html
    assert "Flow rate (µL/s)" in html and "Ideal active time (s)" in html
    assert "Not a measured trace" in html
    assert 'data-start="0" data-end="2" data-rate="6" data-volume="12"' in html
    assert 'data-start="2" data-end="5" data-rate="-4" data-volume="12"' in html
