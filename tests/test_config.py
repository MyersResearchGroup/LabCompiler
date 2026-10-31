"""Reusable configuration types validate and serialize independently of any example."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from lab import Quantity, uL
from lab.config import (
    Concentration,
    ConcentrationUnit,
    ConfigModel,
    FlowRate,
    HamiltonLiquidClassConfig,
    LiquidClassConfig,
    MixingConfig,
    NonnegativeVolume,
    Volume,
    VolumeFraction,
)
from lab.units import units


class Settings(ConfigModel):
    volume: Volume
    dead_volume: NonnegativeVolume
    flow_rate: FlowRate
    concentration: Concentration
    concentration_units: ConcentrationUnit
    vehicle_fraction: VolumeFraction
    mixing: MixingConfig
    liquid_classes: dict[str, LiquidClassConfig]


def inputs():
    return {
        "volume": "0.2 mL",
        "dead_volume": "0 uL",
        "flow_rate": "30 uL/minute",
        "concentration": "3 ug/mL",
        "concentration_units": "umol/L",
        "vehicle_fraction": "0.02",
        "mixing": {"volume": "50 uL", "cycles": 3, "liquid_class": "aqueous"},
        "liquid_classes": {
            "aqueous": {
                "kind": "portable",
                "aspiration_flow_rate": "20 uL/s",
                "dispense_flow_rate": "30 uL/s",
            },
            "native": {"kind": "hamilton", "name": "HighVolumeFilter_Water_DispenseSurface_Part"},
        },
    }


def test_config_quantities_are_typed_and_round_trip_with_units():
    parsed = Settings.model_validate(inputs())
    assert parsed.volume == 200 * uL
    assert parsed.dead_volume == 0 * uL
    assert parsed.flow_rate.units == uL / units.second
    assert parsed.flow_rate.magnitude == pytest.approx(
        Decimal("0.5"), rel=Decimal("1e-26"), abs=Decimal(0)
    )
    assert parsed.concentration == units("0.003 g/L")
    # A separate units column retains its scale, unlike a complete concentration.
    assert 10 * parsed.concentration_units == units("10 umol/L")
    assert parsed.vehicle_fraction == Decimal("0.02")
    assert parsed.mixing.liquid_class == "aqueous"
    assert isinstance(parsed.liquid_classes["native"], HamiltonLiquidClassConfig)
    assert all(
        isinstance(getattr(parsed, field).magnitude, Decimal)
        for field in ("volume", "dead_volume", "flow_rate", "concentration")
    )
    assert Settings.model_validate_json(parsed.model_dump_json()) == parsed
    assert parsed.model_dump(mode="json")["volume"] == "2E+2 microliter"
    schema = Settings.model_json_schema()
    assert schema["properties"]["volume"]["type"] == "string"
    assert schema["properties"]["concentration_units"]["type"] == "string"
    assert schema["additionalProperties"] is False
    with pytest.raises(ValidationError, match="frozen"):
        parsed.volume = 10 * uL


@pytest.mark.parametrize(
    "field, value",
    [
        ("volume", "0 uL"),
        ("dead_volume", "-1 uL"),
        ("flow_rate", "1 uL"),
        ("flow_rate", "0 uL/s"),
        ("concentration", "2 seconds"),
        ("concentration", Quantity(Decimal("NaN"), "mole/liter")),
        ("concentration_units", "microliter"),
        ("concentration_units", "invented_unit"),
        ("vehicle_fraction", "1.01"),
        ("vehicle_fraction", "NaN"),
        ("mixing", {"volume": "20 uL", "cycles": True, "liquid_class": "aqueous"}),
        ("liquid_classes", {"native": {"kind": "hamilton", "name": "_private"}}),
        ("unexpected", 1),
    ],
)
def test_config_reports_invalid_fields(field, value):
    with pytest.raises(ValidationError) as caught:
        Settings.model_validate(inputs() | {field: value})
    assert all(error["loc"][0] == field for error in caught.value.errors())
