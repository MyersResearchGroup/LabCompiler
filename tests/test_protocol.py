import json
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal

import pytest

import lab
from lab import CompileError, Protocol, celsius, mL, seconds, uL
from lab.samples import Location
from lab.targets import Manual
from lab.units import units
from lab.validation import logical_bindings, volume_trace
from tests.thermal_fixture import thermal_aliquots


def simple():
    p = Protocol("Water")
    a = p.container("a", contents="water", volume=100 * uL, capacity=200 * uL)
    b = p.container("b", capacity=200 * uL)
    return p, a, b


def test_compile_defaults_to_manual_and_rejects_a_positional_deck():
    p, a, b = simple()
    p.transfer(a, b, volume=1 * uL)
    assert lab.compile(p, to=None).target.name == "Manual"
    with pytest.raises(TypeError):
        lab.compile(p, Manual(), liquid_handler="ot2")


def test_normal_python_composition_and_exact_quantities():
    p = Protocol("Fractional volumes")
    a = p.container("a", contents="water", volume=1 * mL, capacity=2 * mL)
    b = p.container("b", capacity=200 * uL)

    def aliquot():
        for _ in range(3):
            p.transfer(a, b, volume=0.1 * uL)

    aliquot()
    bundle = lab.compile(p, Manual())
    totals = dict(bundle.final_volumes)
    assert totals[Location("a", "A1")] == Decimal("999.7")
    assert totals[Location("b", "A1")] == Decimal("0.3")
    assert sum(totals.values()) == 1000
    data = json.loads(bundle.plan_json)
    assert data["protocol"]["steps"][0]["volume"] == "0.1"
    assert data["protocol"]["steps"][0]["kind"] == "Transfer"
    assert data["protocol"]["steps"][0]["origin"]["file"].endswith("test_protocol.py")


def test_snapshot_and_artifacts_are_detached(tmp_path):
    p, a, b = simple()
    p.transfer(a, b, volume=25 * uL)
    bundle = lab.compile(p, Manual())
    original = bundle.files
    digest = bundle.digest
    p.transfer(a, b, volume=25 * uL)
    p.name = "Changed"
    assert bundle.files == original
    assert bundle.digest == digest
    assert lab.compile(p, Manual()).digest != digest
    with pytest.raises(FrozenInstanceError):
        bundle.protocol.name = "changed"
    bundle.write(tmp_path / "artifact")
    bundle.write(tmp_path / "artifact")
    assert json.loads((tmp_path / "artifact/plan.json").read_text()) == json.loads(bundle.plan_json)
    with pytest.raises(FileExistsError):
        lab.compile(p, Manual()).write(tmp_path / "artifact")
    assert (tmp_path / "artifact/plan.json").read_text() == original["plan.json"]


def test_compile_writes_under_lab_home_and_can_skip_or_choose(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_HOME", str(tmp_path))
    protocol, source, destination = simple()
    protocol.transfer(source, destination, volume=1 * uL)
    compiled = lab.compile(protocol, Manual())
    assert compiled.directory == tmp_path / "Water" / "Manual"
    original = (compiled.directory / "plan.json").read_text()
    protocol.transfer(source, destination, volume=1 * uL)
    replaced = lab.compile(protocol, Manual())
    assert (replaced.directory / "plan.json").read_text() != original
    skipped = lab.compile(protocol, Manual(), to=None)
    assert skipped.directory is None
    chosen = tmp_path / "chosen"
    explicit = lab.compile(protocol, Manual(), to=chosen)
    assert explicit.directory == chosen
    assert (chosen / "protocol.html").is_file()


def test_checks_intermediate_state_before_later_replenishment():
    p, a, b = simple()
    p.transfer(a, b, volume=80 * uL)
    p.transfer(a, b, volume=30 * uL)
    p.transfer(b, a, volume=80 * uL)
    with pytest.raises(CompileError, match=r"Step 2: a:A1 needs 30 µL; only 20"):
        lab.compile(p, Manual())


def test_mix_respects_remaining_liquid_and_dead_volume():
    p = Protocol("Residual volume")
    a = p.container("a", contents="water", volume=100 * uL, capacity=200 * uL, dead_volume=20 * uL)
    b = p.container("b", capacity=200 * uL)
    p.transfer(a, b, volume=60 * uL)
    p.mix(a, volume=30 * uL, cycles=2)
    with pytest.raises(CompileError, match="only 20"):
        lab.compile(p, Manual())


def test_overflow():
    p, a, b = simple()
    p.load(b, "water", volume=190 * uL)
    p.transfer(a, b, volume=25 * uL)
    with pytest.raises(CompileError, match="overflows b:A1"):
        lab.compile(p, Manual())


def test_bound_capacity_and_aliases_are_rechecked():
    p, a, b = simple()
    p.transfer(a, b, volume=25 * uL)

    class SmallerContainer:
        def prepare(self, recorded):
            plan = Manual().prepare(recorded)
            bindings = tuple(
                replace(binding, capacity=Decimal(10))
                if binding.location.resource == "b"
                else binding
                for binding in plan.bindings
            )
            return replace(plan, bindings=bindings)

    with pytest.raises(CompileError, match="overflows"):
        lab.compile(p, SmallerContainer())

    class AliasedContainers:
        def prepare(self, recorded):
            plan = Manual().prepare(recorded)
            return replace(
                plan, bindings=tuple(replace(b, physical="same well") for b in plan.bindings)
            )

    with pytest.raises(CompileError, match="share one physical well"):
        lab.compile(p, AliasedContainers())


@pytest.mark.parametrize(
    "amount", [0 * uL, -1 * uL, float("nan") * uL, float("inf") * uL, 25, 2 * seconds, True]
)
def test_bad_transfer_quantities(amount):
    p, a, b = simple()
    with pytest.raises((ValueError, TypeError)):
        p.transfer(a, b, volume=amount)


def test_ownership_initialization_and_indexing():
    p, a, b = simple()
    other, foreign, _ = simple()
    with pytest.raises(ValueError, match="belong"):
        p.transfer(a, foreign, volume=1 * uL)
    with pytest.raises(ValueError, match="differ"):
        p.transfer(a, a, volume=1 * uL)
    with pytest.raises(ValueError, match="already exists"):
        p.container("a", capacity=200 * uL)
    with pytest.raises(ValueError, match="already has"):
        p.load(a, "water", volume=50 * uL)
    plate = p.plate("plate", capacity=200 * uL)
    with pytest.raises(KeyError):
        _ = plate["I1"]
    with pytest.raises(KeyError):
        plate.row("a")
    p.transfer(a, b, volume=25 * uL)
    with pytest.raises(ValueError, match="before recording"):
        p.load(plate["A1"], "water", volume=50 * uL)
    with pytest.raises(ValueError):
        p.mix(b, volume=1 * uL, cycles=True)


def test_empty_protocol_is_rejected():
    with pytest.raises(CompileError, match="at least one step"):
        lab.compile(Protocol("Empty"), Manual())


def test_manual_wait_and_html_escaping():
    p = Protocol("<script>alert(1)</script>")
    p.manual('<img src=x onerror="bad()">')
    p.wait(2 * units.minute)
    bundle = lab.compile(p, Manual())
    assert bundle.protocol.steps[-1].seconds == 120
    html = bundle.files["protocol.html"]
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "<img" not in html
    assert "protocol.py" not in bundle.files
    assert lab.compile(p, Manual()).digest == bundle.digest


def test_stage_handoffs_share_wells_and_preserve_volume():
    p = thermal_aliquots()
    bundle = lab.compile(p, Manual())
    totals = dict(bundle.final_volumes)
    assert totals[Location("water", "A1")] == 200
    assert [totals[Location("aliquots", f"A{i}")] for i in range(1, 5)] == [50, 0, 25, 25]
    assert sum(totals.values()) == 300
    assert bundle.protocol.steps[4].source == bundle.protocol.steps[0].destination
    assert bundle.protocol.steps[6].source == bundle.protocol.steps[4].destination
    trace = volume_trace(bundle.protocol, logical_bindings(bundle.protocol))
    assert trace[3] == trace[4]
    assert trace[5] == trace[6]
    data = json.loads(bundle.plan_json)
    assert data["protocol"]["steps"][3]["kind"] == "Thermocycle"
    assert data["protocol"]["steps"][3]["profile"] == [
        {"celsius": "25", "seconds": "1"},
        {"celsius": "30", "seconds": "1"},
    ]
    assert "Thermocycle all of aliquots" in bundle.files["protocol.html"]


def test_thermal_units_and_snapshot():
    p = Protocol("Temperature units")
    plate = p.plate("plate", capacity=100 * uL)
    p.load(plate["A1"], "water", volume=50 * uL)
    profile = [(273.15 * units.kelvin, 0.1 * units.minute)]
    p.thermocycle(plate, profile)
    profile.clear()
    assert p.steps[0].profile[0].celsius == 0
    assert p.steps[0].profile[0].seconds == 6
    assert p.steps[0].lid_celsius is None
    bundle = lab.compile(p, Manual())
    p.thermocycle(plate, [(celsius(-10), 1 * seconds)])
    assert len(bundle.protocol.steps) == 1


@pytest.mark.parametrize(
    "profile,cycles,lid",
    [
        ([], 1, None),
        ([(celsius(25), 1 * seconds)], 0, None),
        ([(celsius(25), 1 * seconds)], True, None),
        ([(celsius(25), 0 * seconds)], 1, None),
        ([(25, 1 * seconds)], 1, None),
        ([(celsius("nan"), 1 * seconds)], 1, None),
        ([(celsius(-300), 1 * seconds)], 1, None),
        ([(celsius(25), 1 * uL)], 1, None),
        ([(celsius(25), 1 * seconds)], 1, 50),
    ],
)
def test_bad_thermal_profile(profile, cycles, lid):
    p = Protocol("Bad thermal profile")
    plate = p.plate("plate", capacity=100 * uL)
    with pytest.raises((ValueError, TypeError)):
        p.thermocycle(plate, profile, cycles=cycles, lid_temperature=lid)
    assert not p.steps


def test_thermal_plate_ownership_and_empty_plate():
    p = Protocol("Thermal ownership")
    plate = p.plate("plate", capacity=100 * uL)
    with pytest.raises(ValueError, match="belong"):
        Protocol("Other").thermocycle(plate, [(celsius(25), 1 * seconds)])
    p.thermocycle(plate, [(celsius(25), 1 * seconds)])
    with pytest.raises(CompileError, match="empty plate"):
        lab.compile(p, Manual())
