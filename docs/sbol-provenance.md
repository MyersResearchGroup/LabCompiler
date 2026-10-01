# SBOL designs and provenance

Use pySBOL3 to author, read, validate, and write biological designs and provenance. `Component` and `Sequence` describe designs; `Activity`, `Usage`, `Association`, `Agent`, and `Plan` describe their provenance. A collection of designs is an ordinary `sbol3.Document`, named `designs` in the examples.

## Provide designs to an experiment

Assembly and transformation recipes take native `sbol3.Component` objects. They can come from Python authoring or an existing SBOL file:

```python
import sbol3
from lab import compile
from lab.experiments.cloning import Transformation, build_transformation

designs = sbol3.Document()
designs.read("designs.ttl")
protocol = build_transformation(
    [Transformation(
        strain=designs.find("https://example.org/my_strain"),
        chassis=designs.find("https://example.org/my_cells"),
        plasmids=[designs.find("https://example.org/my_plasmid")],
    )],
    name="transformation",
)
compiled = compile(protocol, to=None)
```

An `Assembly` names the product, backbone, parts, and restriction enzyme used by that procedure. A `Transformation` names the strain, chassis, and plasmids. These recipes specify procedure inputs; their components retain the full native SBOL API for sequences, features, roles, and provenance. Use `designs.validate()` to check the SBOL graph.

`build_assembly()` and `build_transformation()` copy component identities and names into protocol samples. `build_plating()` consumes a preceding output manifest and preserves design links on its products. `lab.compile()` accepts only the resulting `Protocol`; it captures the immutable snapshot internally. The compiler does not select an experiment from a biological design.

## Preserve identity and provenance

`Sample.design` identifies the intended design and must match `material_identity`. `Sample.implementation` optionally identifies a supplied material record. Both fields contain absolute IRI strings and are included in the plan and output manifest. Builders retain no mutable SBOL objects in the protocol snapshot.

An imported sample retains its upstream annotations. A new product carries its intended design without asserting a physical implementation. Parent sample IDs describe material contributions, not genetic ancestry. Native SBOL provenance stays in the caller-owned `designs` container; references do not automatically fetch or embed other documents.

Use full SBOL3 identities when authoring objects, such as `https://example.org/dna_1`, to avoid global namespace settings. The final segment must be a valid SBOL display ID. Write designs alongside compilation artifacts with `designs.write("designs.ttl", sbol3.TURTLE)`.

## Examples

[transformation.py](../examples/transformation.py) compiles native SBOL designs with explicit procedure quantities. [cloning.py](../examples/cloning.py) carries those identities through assembly, transformation, and plating. Both save the native SBOL graph alongside their artifacts.

[sbol_provenance.py](../examples/sbol_provenance.py) records computational design creation with SBOL provenance objects, then authors a planned aliquot protocol. Its sequence and quantities are synthetic inputs; its activity describes design authoring, not laboratory execution.

```sh
uv run python -m examples.sbol_provenance --out build/sbol_provenance
uv run python -m examples.cloning --target manual --out build/cloning
uv run --extra opentrons python -m examples.transformation
```
