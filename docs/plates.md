# Plate designs

`lab.plates` describes a plate by its intended end state and turns that description into
recorded operations. A `PlateDesign` lists, for each well, an optional material and the final
concentration of every solute. It records no steps. Three ways lead to a design:

| Entry point | Code | Use |
| --- | --- | --- |
| Explicit layout | `PlateDesign.build(name, final_volume=..., wells=[well_target("A1", ...), ...])` or a format plugin such as `lab.plugins.lmsf.read_layout` | A sheet or script that names every well |
| Requirements | `arrange(Requirements(materials=..., conditions=..., gradient=..., controls=...))` | "Four plasmids, two Sal levels, an 8-step 2-fold Van series, blanks"; the plate is laid out for you |
| Procedure | `Protocol` operations directly | Anything the two descriptions above cannot express |

`assemble()` prepares a design from declared sources and reports what it decided. The example
in [`examples/concentration_series/`](../examples/concentration_series/) is a thin command-line
wrapper around these functions.

## Designs

```python
from lab import uL, uM
from lab.plates import PlateDesign, well_target
from lab.units import units

ug_ml = units("ug/mL")
design = PlateDesign.build(
    "cultures",
    final_volume=200 * uL,
    wells=[
        well_target(
            f"A{column}",
            material="strain / plasmid-1",
            concentrations={"Van": van * uM, "Sal": 10 * uM, "TMP": 3 * ug_ml},
        )
        for column, van in zip(range(12, 4, -1), (100, 50, 25, 12.5, 6.25, 3.125, 1.5625, 0.78125))
    ],
)
design["A12"].concentration("Van").quantity  # 0.0001 mole / liter
design.row("A")  # WellTarget records in column order
design.solutes  # ('Sal', 'TMP', 'Van')
```

Concentrations are normalized to Decimal `mole/liter` or `gram/liter`, the dimensions
`lab.units.dilution_volume()` accepts; the two are never converted into each other. A target of
zero is kept because it marks an intended absence, for example a gradient's zero control. The
material is free text; a plugin such as `lab.plugins.lmsf` gives it a convention
(`"strain / plasmid"`). `lab.config.PlateDesignConfig` is the typed JSON or TOML form of a design
for applications installed with `lab-compiler[config]`.

## Patterns in a design

Two preparation patterns cover the layouts in use: direct dosing from a stock, and serial
dilution along a geometric gradient. `find_serial_dilutions(design, solute)` finds runs of
wells, adjacent along a row or column, whose concentrations of one solute descend by a constant
fold while the material and every other solute stay constant. Ratios are compared with a
relative tolerance so float-rounded sheet values such as `14.2857142857143` still form a 2-fold
series. A trailing well with the solute at zero in the same context is attached as the zero
control. `axis="set"` ignores position and groups scattered wells by concentration instead.

`parallelize()` merges row candidates that share the solute, fold, top, and column path into one
candidate whose levels are plate columns: what an 8-channel head or several independent STAR
channels move in one stroke. `find_replicate_groups()` lists wells with identical chemistry,
ignoring the material, for premixing.

```python
from lab.plates import find_serial_dilutions, parallelize

(gradient,) = parallelize(find_serial_dilutions(design, "Van"))
gradient.levels[0]  # ('A12',) ... or ('A12', 'C12', 'E12', 'G12') across four rows
gradient.fold, gradient.steps, gradient.zero_wells
```

## Serial dilution arithmetic

Every well below the top is pre-filled with the gradient volume `G` of diluent. Moving `t` from
a well at concentration `c` into `G` gives `c·t/(G+t)`; requiring `c/f` gives `t = G/(f−1)`.
Each well ends at `G`, the top is prepared at `G + t`, and the bottom level passes its surplus
`t` to waste. The peak `G + t = G·f/(f−1)` must fit the well, which is why folds below 2 are
rejected: a 1.5-fold series needs three times the gradient volume in one well.

`plan_serial_dilution()` sizes a gradient at `concentrate` times the final concentration in
`G = V/concentrate`. The constant additions made afterwards (co-solutes, vehicle, medium,
inoculum) bring every well to `V` and dilute the gradient solute by exactly `concentrate`. The
top stock dose is independent of that factor: `d0 = c0·V·f / (cs·(f−1))`.

| V = 200 µL, f = 2, stock 1000 µM, top 100 µM, 360 µL wells | concentrate 1 | concentrate 2 |
| --- | --- | --- |
| G | 200 | 100 |
| t | 200 | 100 |
| peak G + t | 400 (rejected: exceeds 360) | 200 |
| stock into the top well | 40 | 40 |
| diluent into the top well | 360 | 160 |
| diluent into every other well | 200 | 100 |
| smallest pipetted volume | 40 | 40 (direct dosing of 0.78125 µM needs 0.15625 µL) |

The gradient diluent is plain medium; co-solutes are dosed afterwards by the direct method, so
the transferred volume carries only medium and the gradient solute. A stock with a nonzero
vehicle fraction cannot feed a gradient, because the vehicle would dilute along the chain; such
solutes are dosed directly. `record_serial_dilution()` records the diluent fills, the top doses,
and one mix-then-transfer pair per step and lane, ending with a transfer to waste.

```python
from decimal import Decimal
from lab import Protocol, uL, uM
from lab.plates import plan_serial_dilution, record_serial_dilution

p = Protocol("gradient")
plate = p.plate("cultures", capacity=360 * uL)
stock = p.container("van", contents="Van 1000 uM", volume=1000 * uL, capacity=1500 * uL)
medium = p.container("medium", contents="medium", volume=15000 * uL, capacity=15000 * uL)
waste = p.container("waste", capacity=15000 * uL)
plan = plan_serial_dilution(
    gradient,
    stock_concentration=1000 * uM,
    final_volume=200 * uL,
    capacity=p.capacity(plate),
    reserved=10 * uL,  # inoculum added later
    concentrate=Decimal(2),
)
record_serial_dilution(p, plan, plate=plate, stock=stock, diluent=medium, waste=waste)
```

## Premixes

`find_replicate_groups()` with `plan_premix()` and `record_premix()` prepares the shared portion
of replicate wells once in a tube, then distributes one portion per well. Components are pipetted
at `n × overage` times their per-well dose, so a 2 µL selector dose for four replicates becomes
one 8.8 µL move at the default 1.1 overage. Premixing needs tubes or reservoir wells on the deck;
`assemble()` uses it only when `Settings.premix_min_group` is set and `tubes` are supplied.

## Requirements

```python
from decimal import Decimal
from lab.plates import ConditionSpec, ControlSpec, GradientSpec, Requirements, arrange

requirements = Requirements(
    name="cultures",
    final_volume=200 * uL,
    materials=("strain / p1", "strain / p2", "strain / p3", "strain / p4"),
    conditions=(
        ConditionSpec(label="Sal 10", concentrations={"Sal": 10 * uM, "TMP": 3 * ug_ml}),
        ConditionSpec(label="Sal 25", concentrations={"Sal": 25 * uM, "TMP": 3 * ug_ml}),
    ),
    gradient=GradientSpec(solute="Van", top=100 * uM, fold=Decimal(2), steps=8),
    controls=(ControlSpec(label="unselected", concentrations={"Van": 0 * uM, "Sal": 0 * uM}),),
)
requirements.well_count  # 68 of 96
arrangement = arrange(requirements)
arrangement.design  # series along rows, highest concentration in column 12
arrangement.unused_wells
```

`arrange()` raises with the overflow when the plate is too small, so a design tool can show
"over by N wells" instead of a partial layout. One gradient per plate is supported; a second
gradient in the other direction needs an intermediate plate.

## Assembling a design

```python
from lab.plates import Settings, Stock, Strategy, assemble

report = assemble(
    p,
    design,
    plate=plate,
    stocks=[Stock(solute="Van", concentration=1000 * uM, source=van, max_target=200 * uM), ...],
    medium=medium,
    diluent=second_medium_well,    # optional; gradients draw from here
    inocula={"strain / plasmid-1": culture_1, ...},
    vehicle=dmso,
    waste=waste,
    settings=Settings(
        final_volume=200 * uL,
        inoculum_volume=10 * uL,
        vehicle_fraction=Decimal("0.02"),
        strategy=Strategy.AUTO,
        concentrate=Decimal(2),
        serial_min_steps=4,
    ),
)
report.gradients            # SerialDilutionPlan per gradient actually recorded
report.skipped_gradients    # (candidate, reason) pairs AUTO left to direct dosing
report.wells                # per-well doses, gradient volume, vehicle, medium, inoculum
```

`Strategy.DIRECT` doses every well from its stocks. `Strategy.SERIAL` requires every detected
gradient of at least `serial_min_steps` levels to be feasible. `Strategy.AUTO` uses a serial
dilution where it is detected and fits, and doses directly elsewhere. Stocks are selected per
target as the least concentrated stock whose `max_target` covers it. The recipe balances the
vehicle fraction against vehicle carried by stocks, fills the remainder with medium, then
inoculates and mixes. Every balance is checked; a negative one is a `ValueError` naming the well.

## Lanes and multichannel heads

`Protocol.transfer()` and `mix()` accept sequences of wells. Lane `i` moves `source[i]` to
`destination[i]`; one source well feeds every lane; several lanes may share a destination such
as a waste trough. `Plate.column(n)` returns a column in row order, so a serial dilution step
reads as it is pipetted:

```python
p.transfer(plate.column(12), plate.column(11), volume=100 * uL)
p.mix(plate.column(11), volume=100 * uL, cycles=3)
```

These record `MultiTransfer` and `MultiMix` steps. Volume accounting treats every lane
separately; the worksheet lists the lanes and animates them together. Targets specialize:

| Target | Lane set that fills rows A–H of one column | Other lane sets |
| --- | --- | --- |
| Opentrons with an 8-channel head (`p300_multi_gen2`, `p20_multi_gen2`, `flex_8channel_50`, `flex_8channel_1000`) | One stroke addressed by the column's top well; the head draws from a matching column or one trough well and picks up a whole column of tips | A single-channel head runs the lanes in turn, with one tip for a shared source and a fresh tip per lane otherwise |
| Hamilton STAR with `n` configured `Channel`s | Up to `n` lanes per stroke on `use_channels=[0, …]`; alternating rows such as A/C/E/G are one stroke | Lanes are taken in groups of `n`; a shared trough is drawn lane by lane |
| Manual | "Transfer 100 µL along 8 lanes together from cultures column 12 (rows A–H) to column 11", for an 8-channel pipette | The lanes are listed |

`record_serial_dilution()` records every level wider than one well as a lane set, so a gradient
across rows A/C/E/G is four lanes per step: one STAR stroke, or four single-channel moves on an
OT-2 whose 8-channel head cannot skip rows.

## Tip policy

`Protocol(name, tip_policy=...)` records when tips are replaced; `compile(..., tip_policy=...)`
overrides it. `TipPolicy.FRESH_PER_STEP` takes a new tip for every transfer, distribution, or
mix. `REUSE_PER_SOURCE` keeps a tip while consecutive steps draw from one source with one liquid
class. `REUSE_WHEN_CONTAINED` keeps a tip while every destination holds only substances the
source also holds, so nothing foreign is carried back into the source: medium may go into empty
wells on one tip, a first additive may follow it into those wells on one tip, but the first well
that already holds a second additive forces a fresh tip. Declare what a source is made of with
`load(..., components=("medium", "Van"))`; a fill without components is one component named by
its material text.

A pre-wetting stroke is `prewet(p, source, volume=...)`: a one-cycle mix at the source that
leaves the volume accounting unchanged and, under a reuse policy, keeps the tip for the moves
that follow. `assemble()` records one before the first move from each source when the protocol's
policy reuses tips. `lab.tips.assign_tips()` numbers the tips of a recorded protocol and
`tip_count()` counts them (a multichannel stroke counts one tip per lane); the worksheet and
`plan.json` report the policy and the count, and robot targets pick up and drop tips at the
numbered boundaries.

## Advisories

Hard limits stay errors: a volume no head can pipette or an overflowing well raises
`CompileError`. Everything that compiles but deserves attention is a `Diagnostic` on
`Compilation.diagnostics`, in `plan.json`, and in the worksheet's Advisories section:

```python
bundle = compile(p, to=None, warn_below=2 * uL, resolution=Decimal("0.1") * uL)
for finding in bundle.diagnostics:
    finding.severity, finding.code, finding.message, finding.locations, finding.step_index
```

Each target reports its heads as `lab.limits.PipettingLimits`: the vendor range plus the floor at
which the lab trusts the head. The Opentrons P20 is trusted from 2 µL rather than its 1 µL
minimum, the P300 across its range, the Flex 1000 from 50 µL; a STAR channel's declared minimum
is its floor; the manual target assumes a conventional P2, P10, P20, P200, P1000 set
(`Manual(pipettes=...)` replaces it). `warn_below` raises every floor to the lab's own value.
A step below the floor of the smallest head that covers it, or outside every head, is a
`volume-below-trusted-minimum` warning; `resolution` adds `volume-resolution` notes for volumes
that cannot be dialed at that step.

Before a protocol exists, `advise_design(design, stocks, limits, warn_below=...)` reports the
direct dose each well would need from the given stock concentrations, the gradients
(`serial-dilution-available`) and replicate groups (`premix-available`) that would avoid small
doses. It is the design-time view that a layout tool shows while a plate is being edited.

## Format plugins

Sheet formats are not core concepts. `lab.plugins.lmsf` reads and writes the LMSF destination
CSV as a view of a `PlateDesign`:

```python
from lab.plugins.lmsf import read_layout, write_layout

design = read_layout(Path("layout.csv").read_text(), name="cultures", final_volume=200 * uL)
text = write_layout(design, solute_order=("Van", "Sal", "TMP"))
```

Other formats follow the same shape: a row model, a reader that yields a design, and a writer
that requires whatever the format needs.
