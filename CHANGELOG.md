# Changelog

## Unreleased

### Added

- `lab.plates` describes a plate by its intended end state. `PlateDesign` and `WellTarget` hold per-well materials and solute concentrations; `find_serial_dilutions()` and `parallelize()` detect geometric gradients, `find_replicate_groups()` identical chemistry; `plan_serial_dilution()` and `record_serial_dilution()` build a gradient at a chosen concentrate factor with the arithmetic documented in `docs/plates.md`; `plan_premix()` and `record_premix()` prepare replicate portions once; `arrange()` lays out a plate from `Requirements`; `assemble()` prepares a design from declared stocks, medium, vehicle, and inocula under a `Strategy` of direct, serial, or automatic dosing and reports its decisions.
- `lab.config.PlateDesignConfig` and `PreparationConfig` are the typed JSON and TOML forms of a design and of the preparation settings. `lab.plugins.lmsf` reads and writes the LMSF destination CSV as a view of a design; the core package does not depend on that format.
- `Protocol.capacity()` returns the declared capacity of a plate or well. `lab.uM` and `lab.mM` are exported molar units.
- `compile(..., warn_below=, resolution=)` records advisory `Diagnostic`s on `Compilation.diagnostics`, in `plan.json`, and in a worksheet Advisories section: liquid steps below a head's trusted floor or the lab's own `warn_below`, and volumes that cannot be dialed at the given resolution. `lab.limits.PipettingLimits` describes each head's range and trusted floor; every `TargetPlan` lists its heads, and `Manual(pipettes=...)` takes a bench set. `lab.plates.advise_design()` gives the same advice for a design before any protocol exists. Hard limits remain `CompileError`s.

- `Protocol.transfer()` and `mix()` accept sequences of wells and record `MultiTransfer` and `MultiMix` lane sets; `Plate.column()` returns a column in row order. Opentrons 8-channel heads (`PipetteModel.P20_MULTI_GEN2`, `P300_MULTI_GEN2`, `FLEX_8CHANNEL_50`, `FLEX_8CHANNEL_1000`, with `TipRackModel.FLEX_50_UL` and `FLEX_1000_UL`) run a full-column lane set in one stroke; STAR layouts configure up to eight `Channel`s and run lanes across them; single-channel heads and the manual target run lanes in turn. The worksheet lists lanes and animates them together.
- `TipPolicy` is recorded with a protocol (`Protocol(name, tip_policy=...)`) or overridden with `compile(..., tip_policy=...)`: fresh per step, reuse per source, or reuse while every destination's contents are contained in the source's. `Protocol.load(..., components=...)` declares what a source is made of; `lab.tips` numbers and counts tips, and `prewet()` records a pre-wetting stroke. Generated robot programs and `plan.json` carry the policy; the worksheet shows the resulting tip count.

### Changed behavior

- The concentration-series example prepares the layout's Van gradients as serial dilutions by default (`preparation.strategy = "auto"`), draws gradient diluent from a second medium reservoir well, and discards one transfer volume per lane into a declared waste container. `--strategy direct` reproduces per-well dosing. Its configuration gains a `[preparation]` table, `medium.diluent_volume`, a `waste` container, one more tip rack per robot layout, and eight STAR channels so gradient columns move in one stroke. Serial dilution levels are recorded as lane sets, so the example compiles to 342 steps under `auto`.

## 0.3.0

### Changed behavior

- On OT-2 and Flex, `Protocol.thermocycle()` without `lid_temperature` now follows each block hold with the heated lid, disabling lid heating below 37 °C. An explicit lid temperature remains fixed. Thermocycling retains the final temperature settings after opening the lid; failures deactivate the module before propagating the error. `set_temperature()` also matches the thermocycler lid to the block. Generated STAR thermal callbacks document the same contract.
- Golden Gate assembly uses the automatic lid behavior instead of a fixed 42 °C lid, including during enzyme inactivation.
- Recorded transfer, distribution, and mixing steps in `plan.json` now include `liquid_class`, set to `null` when no class is supplied. Consumers of serialized operations should accept this additional field.

### Added

- A standalone, printable protocol worksheet with material and well inspection, liquid-class flow profiles, and an interactive preparation preview. The preview follows the compiler's calculated volume snapshots and illustrates transfers, distributions, and mixes; it does not simulate physical delivery or robot trajectories. Templates, styles, and scripts ship in the package, with no network access required to view the worksheet.
- `LiquidClass` supplies explicit aspiration and dispense rates for `Protocol.transfer()`, `distribute()`, and `mix()` across manual, OT-2, Flex, and STAR targets. `HamiltonLiquidClass` references native STAR definitions, which are captured in the generated plan and program; Opentrons targets reject these references.
- Optional `lab.config` models and quantity fields, installed with `lab-compiler[config]`, support typed application configuration with Pydantic. The core package remains independent of Pydantic. `lab.units.parse_quantity()` normalizes unit-bearing inputs, and `dilution_volume()` calculates stock volumes for compatible concentrations.
- A concentration-series example reads a destination CSV and TOML configuration, validates doses and source requirements, and prepares an inoculated plate for manual, OT-2, or STAR compilation. The included layout covers a full 96-well plate and supports selecting a subset through configuration.

### Dependencies and validation

- Jinja2 is now a core dependency for worksheet rendering. The optional robot SDK versions remain Opentrons 8.8.2 and PyLabRobot 0.2.1; supported Python versions remain 3.11 and 3.12.
- CI compares simulated OT-2 assembly, transformation, and plating behavior with a pinned PUDU revision, including lid settings, thermal profiles, transfers, and handoff wells. These required checks fail if the reference source or Opentrons SDK is missing.

## 0.2.0

### Migration from 0.1.0

- Pass shared decks with `lab.compile(protocol, deck=deck, liquid_handler=...)`. A positional second argument is now reserved for a concrete target.
- `lab.compile(protocol)` defaults to a manual target and writes a bundle to `~/.lab/<protocol>/<target>/`. Use `to=None` to compile without writing, `to=path` to choose a directory, or `LAB_HOME` to change the default root. Recompilation replaces the files in that protocol/target bundle; `Compilation.write()` still refuses to replace different existing artifacts.
- Replace the removed `lab.protocols` request and material API with the cloning experiment builders and `lab.samples`. `Assembly` and `Transformation` recipes take native `sbol3.Component` objects; pySBOL3 is now a core dependency.

### Added

- Sample identities, lineage, design references, and optional implementation references are preserved in the protocol snapshot. Declared outputs produce `manifest.json` and are available through `Compilation.manifest`.
- Assembly, transformation, and plating examples carry native SBOL design identities through their planned outputs, with a separate example for computational design provenance.
- An optional LabOP integration exports prospective protocol plans using upstream LabOP. It is installed from the checkout in a separate environment and is not imported by the core package.

### Repository

- Move the source repository to `https://github.com/MyersResearchGroup/LabCompiler` and update package URLs, README links, and publishing instructions. The PyPI package remains `lab-compiler`; the Python import remains `lab`.
