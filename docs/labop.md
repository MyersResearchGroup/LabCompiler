# Export a protocol as LabOP

`lab.labop.export()` projects an authored `Protocol` or its immutable `RecordedProtocol` snapshot into a static LabOP/UML graph. It uses the compiler's existing logical-resource and volume validation. Export runs offline and needs no hardware target or robot SDK.

```python
import lab
from lab.labop import export

protocol = lab.Protocol("Aliquot")
source = protocol.container("source", contents="water", volume=20 * lab.uL, capacity=100 * lab.uL)
destination = protocol.container("destination", capacity=100 * lab.uL)
protocol.transfer(source, destination, volume=2 * lab.uL)
compilation = lab.compile(protocol, to=None)
rdf = export(compilation.protocol)
print(rdf.text)
assert compilation.files["protocol.labop.ttl"] == rdf.text
compilation.write("build/aliquot")
```

Every compilation bundle includes `protocol.labop.ttl`. The same authored protocol yields identical LabOP for manual, OT-2, Flex, and STAR compilation. Exporting `compiled.protocol` uses the captured snapshot, so later authoring changes cannot affect it. Compilation uses the existing stage builders and target implementations.

The returned `LabOPDocument` provides `protocol` (the protocol IRI), `text`, `digest` (SHA-256 of the Turtle), and `graph()` (a detached RDFLib graph). Automatic identities depend on protocol meaning, excluding authoring file paths and line numbers. `export(protocol, identity="https://example.org/protocols/aliquot")` supplies a chosen IRI; its last path segment must be a valid SBOL display ID.

The graph describes initial contents, ordered actions, parameters, declared output collections, and optional SBOL design and implementation links. Loads describe starting conditions. Actions describe intended work. No execution records, completion status, or observed products are inferred.

Lab uses upstream `Transfer`, `PipetteMix`, and `PlateCoordinates` definitions. Extensions describe waits, persistent temperatures, thermal profiles, ordered distribution, and operator instructions. Consumers must implement these extensions to interpret the complete plan. RDF validation does not establish executable compatibility with every LabOP interpreter.

Only the three used upstream primitives and their owned definitions are packaged. [The manifest](../src/lab/labop/resources/upstream.json) records the upstream commit, original file hashes, selection rule, selected identities, and subset checksum. The upstream license is included. Full schemas are used separately for validation; they are not a runtime dependency.

Run the example, then validate its RDF against the pinned full schemas with SBOLFactory in a separate interpreter:

```sh
uv run python -m examples.labop_export --out build/labop_export
uv run --isolated --no-project --with sbol_factory==1.1.2 python scripts/check_labop.py \
  build/labop_export/protocol.labop.ttl --schemas /tmp/labop-schemas --fetch
```

`--fetch` downloads the pinned schema and primitive files and verifies their hashes. Subsequent checks can omit it. The validator also checks that the packaged primitive subset exactly matches the selection from upstream. SBOLFactory's global builders are confined to that validation process.
