"""Public quantities; recorded plans use Decimal microlitres and seconds."""

from decimal import Decimal
from typing import Any

import pint

units = pint.UnitRegistry(non_int_type=Decimal)
uL = units.microliter
mL = units.milliliter
seconds = units.second
minutes = units.minute
Quantity = pint.Quantity


def parse_quantity(
    value: str | Quantity, *, unit: str | tuple[str, ...], positive: bool = True
) -> Quantity:
    """Parse text or normalize a quantity to finite Decimal magnitudes and checked units.

    ``unit`` is the output unit, or a tuple of alternatives for distinct dimensions
    (for example, ``("mole/liter", "gram/liter")`` for concentrations). The first
    compatible unit is used. Values must be positive; ``positive=False`` also
    permits zero. Bare numbers, incompatible dimensions, and invalid values
    raise ValueError. Existing Protocol operations still require Quantity objects.
    """
    alternatives = (unit,) if isinstance(unit, str) else unit
    try:
        quantity = units.Quantity(value) if isinstance(value, str) else value
        for candidate in alternatives:
            if isinstance(quantity, Quantity) and quantity.is_compatible_with(candidate):
                return magnitude(quantity, candidate, positive=positive) * units(candidate)
    except (pint.PintError, TypeError, ValueError, ArithmeticError) as exc:
        raise ValueError(str(exc)) from exc
    raise ValueError(f"Expected a quantity compatible with {' or '.join(alternatives)}")


def dilution_volume(
    *,
    stock_concentration: Quantity,
    target_concentration: Quantity,
    final_volume: Quantity,
) -> Quantity:
    """Return the stock volume needed for a target concentration, in microlitres.

    Calculate ``target_concentration / stock_concentration * final_volume``.
    Concentrations must have compatible mass/volume or amount/volume units;
    a unitless number or a mass-to-molar conversion is not accepted. The stock
    and final volume must be positive; the target may range from zero to the
    stock concentration. The returned quantity has a Decimal magnitude.

    The stock must be the only source of this solute. ``final_volume`` includes
    all additions, including inoculum. This calculation does not account for
    solute carryover, select a stock, or record any protocol steps. A zero target
    returns zero volume; the caller should omit that transfer.
    """
    if not isinstance(stock_concentration, Quantity):
        raise TypeError("Stock concentration must be a unit-bearing Quantity")
    if stock_concentration.check("[substance] / [length] ** 3"):
        unit = "mole/liter"
    elif stock_concentration.check("[mass] / [length] ** 3"):
        unit = "gram/liter"
    else:
        raise ValueError("Stock concentration must have mass/volume or amount/volume units")
    stock = magnitude(stock_concentration, unit)
    target = magnitude(target_concentration, unit, positive=False)
    total = magnitude(final_volume, "microliter")
    if target > stock:
        raise ValueError("Target concentration cannot exceed the stock concentration")
    return (target / stock * total) * uL


def celsius(value: int | float | str | Decimal) -> pint.Quantity:
    """An absolute Celsius temperature; avoids multiplying an offset unit."""
    if isinstance(value, bool):
        raise TypeError("A temperature must be numeric")
    return units.Quantity(Decimal(str(value)), units.degC)


def temperature(value: Any) -> Decimal:
    """Normalize an absolute temperature to Celsius, including negative values."""
    return magnitude(value, "kelvin", positive=False) - Decimal("273.15")


def magnitude(value: Any, unit: str, *, positive: bool = True) -> Decimal:
    """Normalize the decimal spelling of a quantity, rejecting unitless input."""
    if not isinstance(value, pint.Quantity) or isinstance(value.magnitude, bool):
        raise TypeError(f"Expected a quantity compatible with {unit}")
    try:
        # Normalize floats before conversion rather than expanding their binary representation.
        quantity = units.Quantity(Decimal(str(value.magnitude)), str(value.units))
        result = Decimal(str(quantity.to(unit).magnitude))
    except (ValueError, ArithmeticError, pint.PintError) as exc:
        raise ValueError(f"Expected a finite quantity compatible with {unit}") from exc
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise ValueError(f"Expected a {'positive' if positive else 'nonnegative'} finite {unit}")
    return result


def number(value: Decimal) -> str:
    """Canonical decimal text without exponent notation or insignificant zeros."""
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text
