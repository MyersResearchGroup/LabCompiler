# Liquid classes

A liquid class supplies pipetting settings for a particular operation. Attach it to `Protocol.transfer()`, `distribute()`, or `mix()`. It is separate from the material identity and concentration: two transfers of the same material can need different settings because their tips, volumes, or destinations differ.

## Portable custom settings

```python
from lab import LiquidClass, Protocol, seconds, uL

gentle = LiquidClass(
    "gentle culture",
    aspiration_flow_rate=20 * uL / seconds,
    dispense_flow_rate=30 * uL / seconds,
)
p = Protocol("Culture aliquot")
source = p.container("source", contents="culture", volume=200 * uL, capacity=360 * uL)
destination = p.container("destination", capacity=360 * uL)
p.transfer(source, destination, volume=50 * uL, liquid_class=gentle)
p.mix(destination, volume=40 * uL, cycles=3, liquid_class=gentle)
```

`LiquidClass` currently expresses **aspiration and dispense flow rates**. Both are required, finite, positive quantities with volume/time dimensions. Construction converts them to immutable Decimal µL/s values. A name describes the settings; it does not identify a calibrated fluid in a robot's catalogue.

| Target | Application |
| --- | --- |
| Manual | Print the name and both rates beside the operation |
| OT-2 and Flex | Set the selected pipette's absolute aspiration/dispense rates for the operation, then restore its previous rates |
| STAR | Pass absolute `flow_rates` to each PyLabRobot aspiration and dispense |

Mixing uses these same rates for each cycle. Distribution applies them to the target's aspirations and dispenses; distribution batching remains target-specific. With `liquid_class=None`, the operation uses the existing target defaults.

This portable class is deliberately a small settings profile. It does **not** specify a volume-correction curve, air gap, blowout, liquid-level detection, heights, surface following, settling times, or dispensing mode. Those retain the target/SDK defaults. In particular, changing rates or naming a class `dmso` does not select a Hamilton DMSO calibration: the generated STAR program does not populate PyLabRobot liquid-identity trackers, so automatic class selection uses the SDK's default liquid. Use an explicit Hamilton reference when the native calibration is required.

The common meaning of µL/s supports software portability. Equivalent physical delivery still depends on the device, tips, liquid, volume, and method. Add further portable fields when their meaning and lowering can be demonstrated on the relevant targets; keep device-specific calibration separate.

## Hamilton built-ins

PyLabRobot provides a catalogue imported from VENUS, and its STAR backend accepts native classes on both aspiration and dispense. A native definition contains more than rates, including a volume-correction curve. See the [PyLabRobot liquid-class guide](https://docs.pylabrobot.org/stable/user_guide/00_liquid-handling/hamilton-star/hamilton-liquid-classes.html).

Reference an exact symbol through Lab's SDK-independent type:

```python
from lab import HamiltonLiquidClass

water = HamiltonLiquidClass("HighVolumeFilter_Water_DispenseSurface_Part")
p.transfer(source, destination, volume=50 * uL, liquid_class=water)
```

The symbol above is an illustration of lookup, not a recommendation for the concentration example's 300 µL tips. Select a definition validated for the intended liquid, tip/filter, channel/head, volume range, and dispense mode. Lab does not infer this compatibility from the symbol's spelling.

With `lab-compiler[star]` installed, discover the available symbols without connecting to hardware:

```python
from lab.targets.star import hamilton_liquid_class_names

names = hamilton_liquid_class_names()
print([name for name in names if "DMSO" in name])
```

STAR compilation checks that the symbol resolves to a native liquid-class object. It snapshots the definition into `plan.json` and the generated program, alongside the required PyLabRobot version. The program reconstructs that captured definition and passes it to both operations. Unknown symbols fail compilation. OT-2 and Flex reject Hamilton references; manual compilation can document a reference without installing PyLabRobot but cannot resolve it.

The native backend performs volume correction. Lab records requested liquid volumes, so it does not pre-correct them or apply the curve a second time. Its inventory calculation and channel/tip checks concern nominal volumes; they are not a physical calibration or an exhaustive check of corrected piston movements and transport air. A native class on `mix()` applies to repeated ordinary aspirations and dispenses, not a separate Hamilton firmware mixing command.

Capturing the definition does not imply that every field is used by the pinned backend. PyLabRobot's method-level defaults and implementation determine effective behavior, including which class fields are consumed. Native built-ins are supported here; importing an arbitrary local VENUS class or creating a full native Hamilton definition through Lab is not yet implemented. For a custom class beyond portable rates, first obtain its actual parameter table and method usage, then add a typed native representation and trace tests for the fields required.

## CSV preparation

The standalone [concentration-series example](../examples/concentration_series/) reads its class registry from the [protocol configuration TOML](../examples/concentration_series/config.toml) supplied with `--config`. Each stock, the medium, the balancing vehicle, and each culture names a class. Source and destination mixing name their classes separately: the final mixture is not interchangeable with its constituent stocks.

`ProtocolConfig` is a Pydantic schema with the same nesting and field names as the TOML, validated once in `main()` before protocol construction. Its liquid-class and mixing schemas come from the optional [`lab.config`](configuration.md) module. `[liquid_classes.aqueous]` becomes `config.liquid_classes["aqueous"]`, a typed configuration entry. `config.medium.liquid_class` remains the string `"aqueous"`; the schema checks that this name exists. A Hamilton entry's TOML key is its local alias, and its `name` is the native STAR catalogue symbol.

At the start of `protocol()`, a dictionary comprehension converts the entries to Lab's `LiquidClass` or `HamiltonLiquidClass` objects. Operations look them up explicitly, for example `liquid_class=liquid_classes[config.medium.liquid_class]`. Configuration loading does not substitute objects for names. The plan records `config.model_dump_json()` from the validated model used by the recipe.

```toml
[liquid_classes.culture]
kind = "portable"
aspiration_flow_rate = "20 uL/s"
dispense_flow_rate = "20 uL/s"
```

For STAR, a class table can instead contain `kind = "hamilton"` and `name = "<exact built-in symbol>"`. The example numbers are development inputs. They are not measured performance data or reconstructed LMSF settings.

## Establishing LMSF equivalence

Ask for one matched run containing the destination CSV, source setup, resolved LMSF parameters, complete VENUS method export and dependencies, native liquid-class definitions, and a simulation trace plus a successful instrument trace. For each pipetting step, identify the selected class and any method overrides: tip/filter and channel/head, aspiration/dispense modes, LLD and heights, mixing, air/blowout, settling, and correction settings. Record VENUS, firmware, and instrument configuration versions.

Compare the generated sequence and effective settings with that run before measuring delivery accuracy and precision over the actual volume range. A matching class name, nominal volume ledger, or software trace alone does not demonstrate the same physical result. These artifacts also establish whether direct PyLabRobot execution is suitable or whether LMSF needs to retain VENUS as its execution layer.
