"""Advisory diagnostics: trusted pipetting floors, resolution notes, and design-time advice."""

import json
import tomllib
from decimal import Decimal
from pathlib import Path

import pytest

from examples.concentration_series import inputs
from examples.concentration_series import protocol as recipe
from lab import Protocol, compile, uL, uM
from lab.diagnostics import BELOW_TRUSTED_MINIMUM, VOLUME_RESOLUTION, Severity, advise
from lab.equipment import LiquidHandler, PipetteModel
from lab.limits import MANUAL_LIMITS, OPENTRONS_LIMITS, PipettingLimits, smallest_covering
from lab.plates import advise_design
from lab.plates.advice import PREMIX_AVAILABLE, SERIAL_DILUTION_AVAILABLE
from lab.plugins.lmsf import read_layout
from lab.samples import Location
from lab.targets import Manual
from lab.units import units

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "examples/concentration_series/layout.csv"
CONFIG = ROOT / "examples/concentration_series/config.toml"


def example(strategy: str, **preparation):
    raw = tomllib.loads(CONFIG.read_text())
    raw["preparation"] = raw["preparation"] | {"strategy": strategy} | preparation
    config = inputs.ProtocolConfig.model_validate(raw)
    design = read_layout(CSV.read_text(), name="cultures", final_volume=config.final_volume)
    return recipe.protocol(design, config)[0], config


def test_limits_validate_and_select_the_smallest_covering_head():
    assert smallest_covering(MANUAL_LIMITS, Decimal("1.5625")).label == "P2"
    assert smallest_covering(MANUAL_LIMITS, Decimal(150)).label == "P200"
    assert smallest_covering(MANUAL_LIMITS, Decimal("0.1")) is None
    assert OPENTRONS_LIMITS[PipetteModel.P20_SINGLE_GEN2].trusted_minimum_ul == 2
    with pytest.raises(ValueError, match="trusted <= max"):
        PipettingLimits(
            label="x",
            min_volume_ul=Decimal(1),
            max_volume_ul=Decimal(10),
            trusted_minimum_ul=Decimal(20),
        )
    with pytest.raises(ValueError, match="at least one channel"):
        PipettingLimits(
            label="x",
            min_volume_ul=Decimal(1),
            max_volume_ul=Decimal(10),
            trusted_minimum_ul=Decimal(1),
            channels=0,
        )


def test_direct_dosing_below_the_trusted_floor_is_an_advisory_not_an_error():
    p, config = example("direct", warn_below="2 uL")
    bundle = compile(p, to=None, warn_below=config.preparation.warn_below)
    (warning,) = bundle.diagnostics
    assert warning.severity is Severity.WARNING and warning.code == BELOW_TRUSTED_MINIMUM
    assert warning.message.startswith("1.5625 µL is below the trusted 2 µL floor of P2")
    assert {location.well for location in warning.locations} == {"A5", "C5", "E5", "G5"}
    assert bundle.protocol.steps[warning.step_index].volume == Decimal("1.5625")
    assert warning.origin is not None
    assert json.loads(bundle.plan_json)["diagnostics"][0]["code"] == BELOW_TRUSTED_MINIMUM
    html = bundle.files["protocol.html"]
    assert "Advisories" in html and f'href="#step-{warning.step_index + 1}"' in html
    assert "below the trusted 2 µL floor" in html

    # The manual target's P2 trusts 0.5 µL, so without a lab floor nothing is flagged.
    assert compile(p, to=None).diagnostics == ()
    assert "Advisories" not in compile(p, to=None).files["protocol.html"]


def test_serial_strategy_removes_the_small_dose_and_robots_apply_their_own_floors():
    p, config = example("auto", warn_below="2 uL")
    assert compile(p, to=None, warn_below=config.preparation.warn_below).diagnostics == ()
    direct, config = example("direct")
    bundle = compile(direct, deck=config.deck, liquid_handler=LiquidHandler.OT2, to=None)
    assert [d.message.split(";")[0] for d in bundle.diagnostics] == [
        "1.5625 µL is below the trusted 2 µL floor of P20 single GEN2"
    ]
    assert bundle.target.limits == (
        OPENTRONS_LIMITS[PipetteModel.P300_SINGLE_GEN2],
        OPENTRONS_LIMITS[PipetteModel.P20_SINGLE_GEN2],
    )


def test_resolution_notes_and_out_of_range_steps_without_failing_compilation():
    p = Protocol("tiny")
    source = p.container("stock", contents="dye", volume=100 * uL, capacity=1500 * uL)
    plate = p.plate("plate", capacity=360 * uL)
    p.transfer(source, plate["A1"], volume=Decimal("0.1") * uL)
    p.transfer(source, plate["A2"], volume=Decimal("1.55") * uL)
    p.transfer(source, plate["A3"], volume=10 * uL)
    bundle = compile(p, to=None, resolution=Decimal("0.1") * uL)
    codes = [(d.step_index, d.code, d.severity) for d in bundle.diagnostics]
    assert codes == [
        (0, BELOW_TRUSTED_MINIMUM, Severity.WARNING),
        (1, VOLUME_RESOLUTION, Severity.INFO),
    ]
    assert "outside the range of any configured pipette" in bundle.diagnostics[0].message
    assert bundle.diagnostics[1].message.startswith("1.55 µL cannot be dialed at 0.1 µL")
    bare = compile(p, Manual(pipettes=()), to=None)
    assert bare.diagnostics == ()
    assert advise(bundle.protocol, (), warn_below=Decimal(5)) and all(
        d.step_index in (0, 1) for d in advise(bundle.protocol, (), warn_below=Decimal(5))
    )


def test_design_advice_names_small_doses_and_available_patterns():
    design = read_layout(CSV.read_text(), name="cultures", final_volume=200 * uL)
    stocks = {"Van": 100 * uM, "Sal": 100 * uM, "TMP": 300 * units("ug/mL")}
    findings = advise_design(design, stocks, tuple(OPENTRONS_LIMITS.values()))
    warnings = [f for f in findings if f.severity is Severity.WARNING]
    assert {f.locations[0].well for f in warnings} == {"A5", "C5", "E5", "G5"}
    assert warnings[0].message == (
        "A5: Van needs 1.5625 µL of stock, below the trusted floor of P20 single GEN2."
    )
    serial = [f for f in findings if f.code == SERIAL_DILUTION_AVAILABLE]
    assert [f.message.split(" can be")[0] for f in serial] == [
        "Sal: 3-step 2-fold gradient across 4 row(s) from 0.00001 mole/liter",
        "Sal: 3-step 2-fold gradient across 4 row(s) from 0.0000142857142857143 mole/liter",
        "Van: 8-step 2-fold gradient across 4 row(s) from 0.0001 mole/liter",
        "Van: 5-step 2-fold gradient across 4 row(s) from 0.0001 mole/liter",
    ]
    premix = [f for f in findings if f.code == PREMIX_AVAILABLE]
    assert len(premix) == 24 and all(len(f.locations) == 4 for f in premix)
    assert all(f.locations[0] == Location("cultures", f.locations[0].well) for f in findings)
    stricter = advise_design(
        design, stocks, tuple(OPENTRONS_LIMITS.values()), warn_below=5 * uL, serial_min_steps=4
    )
    # Van at 0.78125 and 1.5625 µM (1.5625 and 3.125 µL) plus every 2 µL TMP dose.
    tmp_wells = sum(1 for w in design.wells if w.concentration("TMP").magnitude)
    assert tmp_wells == 84
    assert len([f for f in stricter if f.severity is Severity.WARNING]) == 8 + tmp_wells
    assert len([f for f in stricter if f.code == SERIAL_DILUTION_AVAILABLE]) == 2
