# Typed protocol configuration

`lab.config` supplies Pydantic fields and models for application input files. Install `lab-compiler[config]` to use it; repository development dependencies also include Pydantic. Importing `lab` and using `Protocol` do not require Pydantic.

Define the application's schema using ordinary Pydantic fields and validators. Lab's quantity fields handle parsing, dimensional checks, Decimal normalization, JSON serialization, and JSON Schema:

```python
from lab.config import ConfigModel, Concentration, NonnegativeVolume, Volume


class ProtocolConfig(ConfigModel):
    final_volume: Volume
    source_dead_volume: NonnegativeVolume
    stock_concentration: Concentration


config = ProtocolConfig.model_validate(
    {
        "final_volume": "0.2 mL",
        "source_dead_volume": "20 uL",
        "stock_concentration": "1 mM",
    }
)
print(config.final_volume)  # 2E+2 microliter
print(config.model_dump_json())
```

| Field type | Accepted values and Python representation |
| --- | --- |
| `Volume` | Positive finite quantity, normalized to microlitres |
| `NonnegativeVolume` | As above, allowing zero; useful for dead volumes |
| `FlowRate` | Positive finite quantity, normalized to microlitres/second |
| `Concentration` | Nonnegative finite quantity, normalized to mole/litre or gram/litre |
| `ConcentrationUnit` | A separate mass/volume or amount/volume unit; retains its original scale for CSV numeric columns |
| `VolumeFraction` | Finite Decimal from zero through one |
| `NonnegativeDecimal` | Finite Decimal greater than or equal to zero |
| `NonemptyString` | String containing at least one non-whitespace character |

Quantity fields accept unit-bearing strings or Pint `Quantity` objects. They reject bare numbers and incompatible dimensions. Mass concentration cannot be converted to molar concentration without additional information. Quantities serialize as unit-bearing strings and appear as strings in JSON Schema; the validation runs when parsing, not in JSON Schema itself.

`ConfigModel` rejects unknown fields, allows these Pint types, and freezes attribute assignment. Its contained dictionaries are still mutable, following Pydantic's usual behavior. Parse the files once before building a protocol. Apply application-specific checks, such as stock coverage and composition balance, before recording operations; the compiler subsequently checks inventory, capacity, and equipment constraints.

`LiquidClassConfig` is a discriminated union of `PortableLiquidClassConfig` (`kind = "portable"`, aspiration/dispense rates) and `HamiltonLiquidClassConfig` (`kind = "hamilton"`, native symbol). `MixingConfig` supplies a volume, positive integer cycle count, and liquid-class name. Names remain references within the application's configuration; the application checks them against its registry and explicitly constructs Lab liquid classes. See [liquid classes](liquid-classes.md).

Existing Lab dataclasses can also be fields. For example, `deck: lab.deck.Deck` parses nested containers and layouts into typed Lab objects, including enums, locations, and Decimal limits. Pydantic applies their existing dataclass validation. The [concentration-series configuration](../examples/concentration_series/config.toml) demonstrates this without a separate deck parser or schema.

For applications that do not use Pydantic, `lab.units.parse_quantity(value, unit="microliter")` provides the same normalization. `unit` may also be a tuple of alternatives for different dimensions, such as `("mole/liter", "gram/liter")`. The first compatible unit is used. `positive=False` permits zero; negative or nonfinite values always fail. This input parser does not change the `Protocol` API: operations take quantity objects.
