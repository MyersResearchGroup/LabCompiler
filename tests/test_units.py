from decimal import Decimal

import pytest

from lab import Quantity, mL, uL
from lab.units import dilution_volume, parse_quantity, units


@pytest.mark.parametrize(
    "stock,target,final,expected",
    [
        (1 * units.millimolar, 25 * units.micromolar, Decimal("0.2") * mL, "5"),
        (
            Decimal("0.3") * units.milligram / mL,
            3 * units.microgram / mL,
            200 * uL,
            "2",
        ),
        (100 * units.micromolar, Decimal("0.78125") * units.micromolar, 200 * uL, "1.5625"),
        (1 * units.millimolar, 0 * units.micromolar, 200 * uL, "0"),
        (1 * units.millimolar, 1000 * units.micromolar, 200 * uL, "200"),
    ],
)
def test_dilution_volume_normalizes_units_and_returns_decimal_microlitres(
    stock, target, final, expected
):
    result = dilution_volume(
        stock_concentration=stock, target_concentration=target, final_volume=final
    )
    assert isinstance(result, Quantity)
    assert isinstance(result.magnitude, Decimal)
    assert result.units == uL
    assert result.magnitude == Decimal(expected)


@pytest.mark.parametrize(
    "changes",
    [
        {"stock_concentration": 100},
        {"stock_concentration": True},
        {"stock_concentration": 0 * units.micromolar},
        {"stock_concentration": -1 * units.micromolar},
        {"stock_concentration": Decimal("NaN") * units.micromolar},
        {"stock_concentration": Decimal("Infinity") * units.micromolar},
        {"stock_concentration": 1 * units.second},
        {"stock_concentration": 1 * uL},
        {"stock_concentration": units.Quantity(1, "dimensionless")},
        {"target_concentration": 10},
        {"target_concentration": -1 * units.micromolar},
        {"target_concentration": Decimal("NaN") * units.micromolar},
        {"target_concentration": Decimal("Infinity") * units.micromolar},
        {"target_concentration": 101 * units.micromolar},
        {"target_concentration": 1 * units.microgram / mL},
        {"target_concentration": 1 * units.second},
        {"final_volume": 200},
        {"final_volume": 0 * uL},
        {"final_volume": -1 * uL},
        {"final_volume": Decimal("Infinity") * uL},
        {"final_volume": 1 * units.second},
    ],
)
def test_dilution_volume_rejects_invalid_values_or_dimensions(changes):
    values = dict(
        stock_concentration=100 * units.micromolar,
        target_concentration=10 * units.micromolar,
        final_volume=200 * uL,
    )
    values.update(changes)
    with pytest.raises((TypeError, ValueError)):
        dilution_volume(**values)


@pytest.mark.parametrize(
    "value, unit, positive, expected",
    [
        ("0.2 mL", "microliter", True, 200 * uL),
        (Quantity(0.1, "milliliter"), "microliter", True, 100 * uL),
        ("30 uL/minute", "microliter/second", True, Decimal("0.5") * uL / units.second),
        ("0 uL", "microliter", False, 0 * uL),
        (
            "3 ug/mL",
            ("mole/liter", "gram/liter"),
            True,
            Decimal("0.003") * units.gram / units.liter,
        ),
        (
            "10 uM",
            ("mole/liter", "gram/liter"),
            True,
            Decimal("0.00001") * units.mole / units.liter,
        ),
        ("0 uM", ("mole/liter", "gram/liter"), False, 0 * units.mole / units.liter),
    ],
)
def test_parse_quantity_checks_dimensions_and_normalizes_decimal_values(
    value, unit, positive, expected
):
    parsed = parse_quantity(value, unit=unit, positive=positive)
    assert parsed.units == expected.units
    # Pint rounds reciprocal conversion factors (for example, 1/60) in Decimal arithmetic.
    assert parsed.magnitude == pytest.approx(
        expected.magnitude, rel=Decimal("1e-26"), abs=Decimal(0)
    )
    assert isinstance(parsed.magnitude, Decimal)


@pytest.mark.parametrize(
    "value",
    [
        10,
        True,
        None,
        "10",
        "10 seconds",
        "1 unknown_unit",
        "-1 uL",
        "0 uL",
        Quantity(Decimal("NaN"), "microliter"),
        Quantity(Decimal("Infinity"), "microliter"),
    ],
)
def test_parse_quantity_rejects_invalid_or_unitless_inputs(value):
    with pytest.raises(ValueError):
        parse_quantity(value, unit="microliter")
