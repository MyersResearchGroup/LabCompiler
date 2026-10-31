"""Liquid-class intent survives recording and actual SDK lowering."""

import builtins
import json
from dataclasses import FrozenInstanceError
from decimal import Decimal
from io import StringIO

import pytest

from lab import CompileError, HamiltonLiquidClass, LiquidClass, Protocol, compile, mL, seconds, uL
from lab.equipment import LiquidHandler
from lab.model import encode
from tests.target_fixture import target

try:
    from opentrons.simulate import simulate
except ImportError:
    simulate = None
try:
    from pylabrobot.liquid_handling.backends import SerializingBackend
    from pylabrobot.liquid_handling.backends.hamilton.STAR_chatterbox import STARChatterboxBackend
    from pylabrobot.liquid_handling.liquid_classes.hamilton import star as native_classes
except ImportError:
    STARChatterboxBackend = None

BUILTIN = "HighVolumeFilter_Water_DispenseSurface_Part"


def rates():
    return LiquidClass(
        "gentle", aspiration_flow_rate=10 * uL / seconds, dispense_flow_rate=15 * uL / seconds
    )


def protocol(liquid_class, *, follow_default=False):
    p = Protocol("Liquid classes")
    source = p.container("water", contents="water", volume=300 * uL, capacity=360 * uL)
    plate = p.plate("aliquots", capacity=200 * uL)
    p.transfer(source, plate["A1"], volume=25 * uL, liquid_class=liquid_class)
    p.mix(plate["A1"], volume=20 * uL, cycles=2, liquid_class=liquid_class)
    p.distribute(source, (plate["A2"], plate["A3"]), volume=25 * uL, liquid_class=liquid_class)
    if follow_default:
        p.transfer(source, plate["A4"], volume=25 * uL)
    return p


def test_classes_are_typed_unit_checked_immutable_and_documented():
    spec = LiquidClass(
        "slow",
        aspiration_flow_rate=Decimal("0.6") * mL / (60 * seconds),
        dispense_flow_rate=15 * uL / seconds,
    )
    assert spec.aspiration_flow_rate_ul_s == Decimal(10)
    with pytest.raises(FrozenInstanceError):
        spec.name = "changed"
    assert encode(spec) == {
        "kind": "LiquidClass",
        "name": "slow",
        "aspiration_flow_rate_ul_s": "10",
        "dispense_flow_rate_ul_s": "15",
    }
    bundle = compile(protocol(spec), to=None)
    assert "slow: aspirate 10 µL/s; dispense 15 µL/s" in bundle.files["protocol.html"]
    assert json.loads(bundle.plan_json)["protocol"]["steps"][0]["liquid_class"] == encode(spec)
    assert all(s.liquid_class == spec for s in bundle.protocol.steps)
    assert dict(bundle.final_volumes) == dict(compile(protocol(None), to=None).final_volumes)
    assert encode(HamiltonLiquidClass(BUILTIN)) == {"kind": "HamiltonLiquidClass", "name": BUILTIN}
    assert (
        BUILTIN in compile(protocol(HamiltonLiquidClass(BUILTIN)), to=None).files["protocol.html"]
    )


@pytest.mark.parametrize(
    "rate",
    [
        10,
        True,
        0 * uL / seconds,
        -1 * uL / seconds,
        float("nan") * uL / seconds,
        float("inf") * uL / seconds,
        10 * uL,
        10 * seconds,
    ],
)
def test_invalid_rates_fail_at_authoring(rate):
    with pytest.raises((TypeError, ValueError)):
        LiquidClass("invalid", aspiration_flow_rate=rate, dispense_flow_rate=10 * uL / seconds)


@pytest.mark.parametrize("value", ["water", {}, 1])
def test_operations_reject_untyped_classes(value):
    with pytest.raises(TypeError, match="LiquidClass"):
        protocol(value)


@pytest.mark.parametrize("name", ["", " ", "_private", "a.b", "x; print('hi')"])
def test_native_reference_is_a_public_symbol(name):
    with pytest.raises(ValueError):
        HamiltonLiquidClass(name)


@pytest.mark.integration
@pytest.mark.skipif(simulate is None, reason="Opentrons SDK not installed")
@pytest.mark.parametrize("handler", ["ot2", "flex"])
def test_rates_apply_to_transfers_mixes_distributes_and_do_not_leak(handler, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bundle = compile(
        protocol(rates(), follow_default=True),
        target(handler),
        liquid_handler=LiquidHandler(handler),
        to=None,
    )
    log, _ = simulate(StringIO(bundle.files["protocol.py"]))
    aspirations = [
        e["payload"]["text"] for e in log if e["payload"]["text"].startswith("Aspirating")
    ]
    dispenses = [e["payload"]["text"] for e in log if e["payload"]["text"].startswith("Dispensing")]
    assert len(aspirations) == 5  # transfer, two mixes, distribution, default transfer
    assert len(dispenses) == 6
    assert all("at 10.0 uL/sec" in text for text in aspirations[:-1])
    assert all("at 15.0 uL/sec" in text for text in dispenses[:-1])
    assert "at 10.0 uL/sec" not in aspirations[-1]
    assert "at 15.0 uL/sec" not in dispenses[-1]
    with pytest.raises(CompileError, match="require the STAR"):
        compile(
            protocol(HamiltonLiquidClass(BUILTIN)),
            target(handler),
            liquid_handler=LiquidHandler(handler),
            to=None,
        )


@pytest.mark.integration
@pytest.mark.skipif(STARChatterboxBackend is None, reason="PyLabRobot SDK not installed")
async def test_star_portable_rates_reach_the_real_frontend():
    class Recorder(SerializingBackend):
        def __init__(self):
            super().__init__(num_channels=8)
            self.events = []

        async def send_command(self, command, data=None):
            self.events.append((command, data))

    bundle = compile(
        protocol(rates(), follow_default=True),
        target("star"),
        liquid_handler=LiquidHandler.STAR,
        to=None,
    )
    namespace = {"__name__": "_generated"}
    exec(builtins.compile(bundle.files["protocol.py"], "star.py", "exec"), namespace)
    backend = Recorder()
    await namespace["run"](backend)
    aspirations = [data["channels"][0] for name, data in backend.events if name == "aspirate"]
    dispenses = [data["channels"][0] for name, data in backend.events if name == "dispense"]
    assert [op["flow_rate"] for op in aspirations] == [10] * 5 + [None]
    assert [op["flow_rate"] for op in dispenses] == [15] * 5 + [None]
    assert [op["volume"] for op in aspirations] == [25, 20, 20, 25, 25, 25]


@pytest.mark.integration
@pytest.mark.skipif(STARChatterboxBackend is None, reason="PyLabRobot SDK not installed")
async def test_native_definition_is_frozen_and_used_for_both_operations(capsys, monkeypatch):
    bundle = compile(
        protocol(HamiltonLiquidClass(BUILTIN)),
        target("star"),
        liquid_handler=LiquidHandler.STAR,
        to=None,
    )
    definition = json.loads(bundle.plan_json)["target"]["configuration"]["hamilton_liquid_classes"][
        BUILTIN
    ]
    assert definition["curve"]["20.0"] == 23.9
    assert definition["aspiration_flow_rate"] == 250
    assert definition["dispense_flow_rate"] == 120
    # Generated execution uses the definition captured at compile time.
    monkeypatch.setattr(getattr(native_classes, BUILTIN), "aspiration_flow_rate", 42)
    namespace = {"__name__": "_generated"}
    exec(builtins.compile(bundle.files["protocol.py"], "star.py", "exec"), namespace)
    await namespace["run"](STARChatterboxBackend())
    lines = capsys.readouterr().out.splitlines()
    aspirations = [line for line in lines if line.startswith("C0AS")]
    dispenses = [line for line in lines if line.startswith("C0DS")]
    assert len(aspirations) == len(dispenses) == 5
    assert all("as2500" in line for line in aspirations)
    assert all("ds1200" in line for line in dispenses)
    # 25 uL requested -> 29.3 uL corrected by the native curve; ledger stays nominal.
    assert "av00293" in aspirations[0]
    assert "dv00293" in dispenses[0]
    assert bundle.protocol.steps[0].volume == 25


@pytest.mark.integration
@pytest.mark.skipif(STARChatterboxBackend is None, reason="PyLabRobot SDK not installed")
def test_star_rejects_unknown_builtin_before_emission():
    with pytest.raises(CompileError, match="Unknown Hamilton STAR liquid class"):
        compile(
            protocol(HamiltonLiquidClass("not_a_class")),
            target("star"),
            liquid_handler=LiquidHandler.STAR,
            to=None,
        )
