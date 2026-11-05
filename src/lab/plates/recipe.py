"""Turn a plate design and a set of sources into recorded preparation operations.

``assemble()`` records, in order: serial dilution gradients (diluent fills, top doses, and
chained transfers), premixes for replicate groups where enabled, medium to every remaining
well, vehicle balance, direct stock doses grouped by equal volume, inoculation, and a final
mix. Concentrations refer to the final volume after inoculation.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import Enum

from lab.liquid_classes import LiquidClassSpec
from lab.model import TipPolicy
from lab.plates.design import Axis, PlateDesign, WellTarget, canonical_concentration
from lab.plates.dilution import SerialDilutionPlan, plan_serial_dilution, record_serial_dilution
from lab.plates.patterns import (
    SerialDilutionCandidate,
    find_replicate_groups,
    find_serial_dilutions,
    parallelize,
)
from lab.plates.premix import PremixPlan, plan_premix, record_premix
from lab.protocol import Plate, Protocol, Well
from lab.tips import prewet
from lab.units import Quantity, dilution_volume, magnitude, uL, units


class Strategy(Enum):
    """How gradients in a design are prepared."""

    DIRECT = "direct"
    """Every dose comes straight from its stock."""
    SERIAL = "serial"
    """Every detected gradient is a serial dilution; an infeasible gradient is an error."""
    AUTO = "auto"
    """Serial where a gradient is detected and feasible, direct everywhere else."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Stock:
    """A prepared solution of one solute at a known concentration in a declared well."""

    solute: str
    concentration: Quantity
    source: Well
    max_target: Quantity | None = None
    vehicle_fraction: Decimal = Decimal(0)
    liquid_class: LiquidClassSpec | None = None

    def __post_init__(self) -> None:
        value, _ = canonical_concentration(self.concentration)
        if value <= 0:
            raise ValueError(f"{self.solute}: a stock concentration must be positive.")
        if self.max_target is not None:
            canonical_concentration(self.max_target)
        if not 0 <= self.vehicle_fraction <= 1:
            raise ValueError(f"{self.solute}: vehicle fraction must be between 0 and 1.")


@dataclass(frozen=True, slots=True, kw_only=True)
class Mixing:
    volume: Quantity
    cycles: int
    liquid_class: LiquidClassSpec | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Settings:
    """Preparation choices that are not part of the design itself."""

    final_volume: Quantity
    inoculum_volume: Quantity
    vehicle_fraction: Decimal = Decimal(0)
    strategy: Strategy = Strategy.AUTO
    concentrate: Decimal = Decimal(2)
    serial_min_steps: int = 4
    premix_min_group: int | None = None
    premix_overage: Decimal = Decimal("1.1")
    gradient_axis: Axis = "row"
    source_mix: Mixing | None = None
    destination_mix: Mixing | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class WellPlan:
    """Resolved additions for one well; ``gradient_ul`` is what a serial dilution supplied."""

    target: WellTarget
    doses: tuple[tuple[Stock, Decimal], ...]
    gradient_ul: Decimal
    vehicle_ul: Decimal
    medium_ul: Decimal
    inoculum_ul: Decimal
    premixed: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class AssemblyReport:
    wells: tuple[WellPlan, ...]
    gradients: tuple[SerialDilutionPlan, ...]
    premixes: tuple[PremixPlan, ...]
    skipped_gradients: tuple[tuple[SerialDilutionCandidate, str], ...] = ()
    """Gradients ``Strategy.AUTO`` left to direct dosing, with the reason."""


def _by_concentration(stock: Stock) -> Decimal:
    return canonical_concentration(stock.concentration)[0]


def _covering(stock: Stock, target: Quantity) -> bool:
    value, unit = canonical_concentration(target)
    concentration = magnitude(stock.concentration, unit)
    limit = magnitude(stock.max_target, unit, positive=False) if stock.max_target else None
    return value <= concentration and (limit is None or value <= limit)


def select_stock(stocks: Sequence[Stock], solute: str, target: Quantity) -> Stock:
    """The least concentrated stock whose declared limit covers ``target``."""
    eligible = [s for s in stocks if s.solute == solute and _covering(s, target)]
    if not eligible:
        raise ValueError(f"no stock covers {solute} target {target:~P}")
    return min(eligible, key=_by_concentration)


def _gradient_stock(
    stocks: Sequence[Stock], candidate: SerialDilutionCandidate, k: Decimal
) -> Stock:
    top: Quantity = candidate.top * k * units(candidate.unit)
    stock = select_stock(stocks, candidate.solute, top)
    if stock.vehicle_fraction:
        raise ValueError(
            f"{candidate.solute}: a serial dilution of a vehicle-bearing stock needs a "
            "vehicle-balanced diluent; dose it directly instead"
        )
    return stock


def _plan_well(
    target: WellTarget,
    stocks: Sequence[Stock],
    settings: Settings,
    gradient: tuple[str, Decimal] | None,
    vehicle: Well | None,
) -> WellPlan:
    final = magnitude(settings.final_volume, "microliter")
    inoculum = (
        magnitude(settings.inoculum_volume, "microliter", positive=False)
        if target.material is not None
        else Decimal(0)
    )
    doses: list[tuple[Stock, Decimal]] = []
    for solute_target in target.targets:
        if solute_target.magnitude == 0 or (gradient and solute_target.solute == gradient[0]):
            continue
        try:
            stock = select_stock(stocks, solute_target.solute, solute_target.quantity)
        except ValueError as exc:
            raise ValueError(f"{target.well}: {exc}") from exc
        dose = dilution_volume(
            stock_concentration=stock.concentration,
            target_concentration=solute_target.quantity,
            final_volume=settings.final_volume,
        )
        doses.append((stock, magnitude(dose, "microliter")))
    carried = sum((stock.vehicle_fraction * dose for stock, dose in doses), Decimal(0))
    vehicle_volume = settings.vehicle_fraction * final - carried
    if vehicle is None and vehicle_volume != 0:
        raise ValueError(f"{target.well}: balancing the vehicle fraction needs a vehicle source")
    gradient_volume = gradient[1] if gradient else Decimal(0)
    medium = (
        final
        - inoculum
        - gradient_volume
        - vehicle_volume
        - sum((dose for _, dose in doses), Decimal(0))
    )
    if vehicle_volume < 0 or medium < 0:
        raise ValueError(f"{target.well}: additions exceed the final composition")
    return WellPlan(
        target=target,
        doses=tuple(doses),
        gradient_ul=gradient_volume,
        vehicle_ul=vehicle_volume,
        medium_ul=medium,
        inoculum_ul=inoculum,
    )


def _add_grouped(
    p: Protocol,
    source: Well,
    doses: Sequence[tuple[Well, Decimal]],
    liquid_class: LiquidClassSpec | None,
) -> None:
    """Share a tip across equal doses; retain individual transfers for unique doses.

    Under a tip-reuse policy the first move from a source follows a pre-wetting stroke of the
    largest dose, so every reused tip starts wet.
    """
    groups: dict[Decimal, list[Well]] = {}
    for destination, dose in doses:
        if dose:
            groups.setdefault(dose, []).append(destination)
    if groups and p.tip_policy is not TipPolicy.FRESH_PER_STEP:
        prewet(p, source, volume=max(groups) * uL, liquid_class=liquid_class)
    for dose, wells in groups.items():
        if len(wells) == 1:
            p.transfer(source, wells[0], volume=dose * uL, liquid_class=liquid_class)
        else:
            p.distribute(source, wells, volume=dose * uL, liquid_class=liquid_class)


def assemble(
    p: Protocol,
    design: PlateDesign,
    *,
    plate: Plate,
    stocks: Sequence[Stock],
    medium: Well,
    inocula: Mapping[str, Well],
    settings: Settings,
    vehicle: Well | None = None,
    waste: Well | None = None,
    diluent: Well | None = None,
    tubes: Sequence[Well] = (),
    medium_class: LiquidClassSpec | None = None,
    vehicle_class: LiquidClassSpec | None = None,
    culture_class: LiquidClassSpec | None = None,
) -> AssemblyReport:
    """Record the preparation of ``design`` into ``plate`` and report what was decided.

    ``inocula`` maps each design material to its source well. ``waste`` is required for
    serial dilutions and ``tubes`` for premixes. Gradients draw their diluent from
    ``diluent`` when given, otherwise from ``medium``; a separate well keeps one reservoir
    from running dry, since every gradient lane discards one transfer volume to waste.
    """
    if plate.shape != design.shape:
        raise ValueError("The plate's shape must match the design")
    final = magnitude(settings.final_volume, "microliter")
    if design.final_volume_ul != final:
        raise ValueError("Settings and design disagree on the final volume")
    if missing := set(design.materials) - set(inocula):
        raise ValueError(f"No inoculum source for {sorted(missing)}")
    if type(settings.serial_min_steps) is not int or settings.serial_min_steps < 2:
        raise ValueError("serial_min_steps must be an integer of at least 2")

    gradients: list[SerialDilutionPlan] = []
    skipped: list[tuple[SerialDilutionCandidate, str]] = []
    supplied: dict[str, tuple[str, Decimal]] = {}
    if settings.strategy is not Strategy.DIRECT:
        if waste is None:
            raise ValueError("Serial dilutions need a waste container")
        for solute in design.solutes:
            candidates = find_serial_dilutions(
                design,
                solute,
                axis=settings.gradient_axis,
                min_steps=settings.serial_min_steps,
            )
            if settings.gradient_axis == "row":
                candidates = parallelize(candidates)
            for candidate in candidates:
                try:
                    stock = _gradient_stock(stocks, candidate, settings.concentrate)
                    plan = plan_serial_dilution(
                        candidate,
                        stock_concentration=stock.concentration,
                        final_volume=settings.final_volume,
                        capacity=p.capacity(plate),
                        reserved=settings.inoculum_volume,
                        concentrate=settings.concentrate,
                    )
                    for well in (*candidate.wells, *candidate.zero_wells):
                        if well in supplied:
                            raise ValueError(
                                f"{well} takes part in two gradients; a two-dimensional "
                                "gradient needs an intermediate plate"
                            )
                except ValueError as exc:
                    if settings.strategy is Strategy.SERIAL:
                        raise
                    skipped.append((candidate, str(exc)))
                    continue
                record_serial_dilution(
                    p,
                    plan,
                    plate=plate,
                    stock=stock.source,
                    diluent=diluent if diluent is not None else medium,
                    waste=waste,
                    stock_class=stock.liquid_class,
                    diluent_class=medium_class,
                )
                gradients.append(plan)
                for well in (*candidate.wells, *candidate.zero_wells):
                    supplied[well] = (solute, plan.gradient_volume_ul)
        if settings.strategy is Strategy.SERIAL and not gradients:
            raise ValueError("No serial dilution gradient was found in this design")

    plans = [
        _plan_well(target, stocks, settings, supplied.get(target.well), vehicle)
        for target in design.wells
    ]

    premixes: list[PremixPlan] = []
    if settings.premix_min_group is not None:
        by_well = {plan.target.well: plan for plan in plans}
        free_tubes = list(tubes)
        for group in find_replicate_groups(design, min_size=settings.premix_min_group):
            members = [by_well[w] for w in group.wells]
            portions = {
                (m.medium_ul, m.vehicle_ul, tuple((id(s), d) for s, d in m.doses)) for m in members
            }
            if len(portions) != 1:
                continue
            components: list[tuple[Well, Quantity, LiquidClassSpec | None]] = []
            first = members[0]
            if first.medium_ul:
                components.append((medium, first.medium_ul * uL, medium_class))
            if first.vehicle_ul and vehicle is not None:
                components.append((vehicle, first.vehicle_ul * uL, vehicle_class))
            components.extend((s.source, d * uL, s.liquid_class) for s, d in first.doses)
            if len(components) < 2:
                continue
            if not free_tubes:
                raise ValueError("Premixing needs more tubes than were supplied")
            tube = free_tubes.pop(0)
            premix = plan_premix(
                group, components, tube_capacity=p.capacity(tube), overage=settings.premix_overage
            )
            record_premix(p, premix, tube=tube, plate=plate, mix_class=medium_class)
            premixes.append(premix)
            for index, well_plan in enumerate(plans):
                if well_plan.target.well in group.wells:
                    plans[index] = replace(well_plan, premixed=True)

    direct = [well_plan for well_plan in plans if not well_plan.premixed]
    _add_grouped(p, medium, [(plate[w.target.well], w.medium_ul) for w in direct], medium_class)
    if vehicle is not None:
        _add_grouped(
            p, vehicle, [(plate[w.target.well], w.vehicle_ul) for w in direct], vehicle_class
        )
    for stock in stocks:
        _add_grouped(
            p,
            stock.source,
            [(plate[w.target.well], d) for w in direct for s, d in w.doses if s is stock],
            stock.liquid_class,
        )
    for well_plan in plans:
        if well_plan.target.material is None or not well_plan.inoculum_ul:
            continue
        source = inocula[well_plan.target.material]
        destination = plate[well_plan.target.well]
        if settings.source_mix is not None:
            p.mix(
                source,
                volume=settings.source_mix.volume,
                cycles=settings.source_mix.cycles,
                liquid_class=settings.source_mix.liquid_class or culture_class,
            )
        p.transfer(
            source, destination, volume=well_plan.inoculum_ul * uL, liquid_class=culture_class
        )
        if settings.destination_mix is not None:
            p.mix(
                destination,
                volume=settings.destination_mix.volume,
                cycles=settings.destination_mix.cycles,
                liquid_class=settings.destination_mix.liquid_class or culture_class,
            )
    return AssemblyReport(
        wells=tuple(plans),
        gradients=tuple(gradients),
        premixes=tuple(premixes),
        skipped_gradients=tuple(skipped),
    )
