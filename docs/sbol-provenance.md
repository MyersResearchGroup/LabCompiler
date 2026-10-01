# SBOL designs and provenance

Use pySBOL3 directly to author, read, validate, and write SBOL documents. Its `Component` and `Sequence` describe biological designs. `Activity`, `Usage`, `Association`, `Agent`, and `Plan` represent provenance. Lab links protocol samples to these documents through identity strings.

```python
import sbol3
from lab.samples import Sample

design = sbol3.Component("https://example.org/project/design", sbol3.SBO_DNA)
document = sbol3.Document()
document.add(design)
sample = Sample(
    id="dna",
    material_identity=design.identity,
    label="Input DNA",
    design=design.identity,
)
assert not document.validate().errors
document.write("designs.ttl", sbol3.TURTLE)
```

Pass full identities to pySBOL3 constructors to avoid a process-wide namespace. Use `document.find(identity)` to resolve local references and `document.validate()` for SBOL validation. The SBOL document belongs to the caller; Lab captures only identity strings in its existing immutable protocol snapshot.

`Sample.design` identifies the intended design and must match `material_identity`. `Sample.implementation` optionally identifies a supplied material record. Both fields require absolute IRIs. They are included in the compilation's plan and output manifest. Lab neither fetches referenced documents nor embeds them automatically.

Typed cloning requests preserve full part IRIs through assembly, transformation, and plating. Importing an upstream sample preserves its annotations; declaring a new product preserves its intended design without asserting a physical implementation. Parent sample IDs describe material contributions, not genetic ancestry.

The [example](../examples/sbol_provenance.py) records computational design creation with native SBOL provenance objects, then authors a planned aliquot protocol. Its short sequence and quantities are synthetic software inputs. The design activity describes document authoring; it does not claim laboratory execution.

```sh
uv run python -m examples.sbol_provenance --out build/sbol_provenance
```
