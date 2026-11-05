"""Design-time advice: what a design would need before any protocol exists."""

from collections.abc import Mapping
from decimal import Decimal

from lab.diagnostics import BELOW_TRUSTED_MINIMUM, Diagnostic, Diagnostics, Severity
from lab.limits import PipettingLimits, smallest_covering
from lab.plates.design import PlateDesign
from lab.plates.patterns import find_replicate_groups, find_serial_dilutions, parallelize
from lab.samples import Location
from lab.units import Quantity, dilution_volume, magnitude, uL, units

SERIAL_DILUTION_AVAILABLE = "serial-dilution-available"
PREMIX_AVAILABLE = "premix-available"
_SHOWN = {"mole/liter": "umol/L", "gram/liter": "ug/mL"}


def shown_concentration(magnitude_value: Decimal, unit: str) -> str:
    """Present a canonical concentration in the bench units of its dimension."""
    shown = _SHOWN[unit]
    value = Decimal(str((magnitude_value * units(unit)).to(shown).magnitude)).normalize()
    return f"{value:f} {shown.replace('umol/L', 'µM').replace('ug/mL', 'µg/mL')}"


def advise_design(
    design: PlateDesign,
    stocks: Mapping[str, Quantity],
    limits: tuple[PipettingLimits, ...],
    *,
    warn_below: Quantity | None = None,
    serial_min_steps: int = 3,
    premix_min_group: int = 3,
) -> Diagnostics:
    """Report direct doses below the trusted floor and the patterns that would avoid them.

    ``stocks`` maps each solute to the concentration of the stock it would be dosed from.
    Solutes without a stock are not assessed. Findings name design wells on the design's
    own resource name, since no protocol exists yet.
    """
    floor = magnitude(warn_below, "microliter") if warn_below is not None else None
    found: list[Diagnostic] = []
    final_volume = design.final_volume_ul * uL
    for well in design.wells:
        for target in well.targets:
            if target.magnitude == 0 or target.solute not in stocks:
                continue
            dose = magnitude(
                dilution_volume(
                    stock_concentration=stocks[target.solute],
                    target_concentration=target.quantity,
                    final_volume=final_volume,
                ),
                "microliter",
            )
            head = smallest_covering(limits, dose)
            trusted = max(
                head.trusted_minimum_ul if head is not None else dose,
                floor if floor is not None else dose,
            )
            if head is None or dose < trusted:
                label = head.label if head is not None else "any configured pipette"
                found.append(
                    Diagnostic(
                        severity=Severity.WARNING,
                        code=BELOW_TRUSTED_MINIMUM,
                        message=f"{well.well}: {target.solute} needs {dose.normalize():f} µL "
                        f"of stock, below the trusted floor of {label}.",
                        locations=(Location(design.name, well.well),),
                    )
                )
    for solute in design.solutes:
        candidates = find_serial_dilutions(design, solute, min_steps=serial_min_steps)
        for candidate in parallelize(candidates):
            found.append(
                Diagnostic(
                    severity=Severity.INFO,
                    code=SERIAL_DILUTION_AVAILABLE,
                    message=f"{solute}: {candidate.steps}-step {candidate.fold:f}-fold gradient "
                    f"across {candidate.width} row(s) from "
                    f"{shown_concentration(candidate.top, candidate.unit)} "
                    "can be prepared as a serial dilution.",
                    locations=tuple(Location(design.name, w) for w in candidate.wells),
                )
            )
    for group in find_replicate_groups(design, min_size=premix_min_group):
        found.append(
            Diagnostic(
                severity=Severity.INFO,
                code=PREMIX_AVAILABLE,
                message=f"{len(group.wells)} wells share one composition; a premix would "
                f"pipette each component once at {len(group.wells)} times the dose.",
                locations=tuple(Location(design.name, w) for w in group.wells),
            )
        )
    return tuple(found)
