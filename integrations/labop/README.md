# Export a compilation as LabOP

Because LabOP is not currently on PyPi, we have made the integration with the Lab Compiler optional. The integration reads the compiler's `lab.plan.v1` JSON artifact and writes a prospective LabOP protocol. It uses upstream LabOP's classes, primitive libraries, and SBOL serialization.

Install the integration from a checkout, using a separate Python 3.11 or 3.12 environment:

```sh
python3.12 -m venv integrations/labop/.venv
integrations/labop/.venv/bin/python -m pip install -e . -r integrations/labop/requirements.txt
```

The upstream revision and direct compatibility dependencies are pinned in `requirements.txt`. This environment uses `pip` because upstream's container-ontology URL is not accepted by `uv`. Upstream's transitive dependencies are not fully locked. Installation needs network access; export uses the installed libraries. This environment is separate because upstream import modifies pySBOL3's registrations, classes, and process logging.

Run the example to compile with Lab and write LabOP:

```sh
integrations/labop/.venv/bin/python -m examples.labop_export
```

The example writes `build/labop/protocol.labop.ttl` beside `plan.json` and `protocol.html`. `--out directory` selects another output directory. It refuses to replace a different existing artifact. Normal compilation emits `plan.json`, HTML, and target artifacts; LabOP is an explicit export step.

The integration consumes only the protocol portion of `plan.json`. Target configuration and authoring file locations do not affect the exported identity. Samples retain SBOL design and implementation references. The RDF records the intended operations, initial resource metadata, and declared outputs; it records no execution or observed products.

Upstream owns the LabOP/UML object model, RDF structure, and the `Transfer`, `PipetteMix`, and `PlateCoordinates` definitions. The adapter owns the mapping from Lab's operations. Small Lab extension declarations cover waits, persistent temperatures, thermal profiles, ordered distribution, and operator instructions. Consumers need support for those semantics; graph validation does not establish executable compatibility with every LabOP interpreter.

Thermocycle's `profile` argument is an ordered JSON list of `{celsius, seconds}` holds from the compiler artifact. Resource metadata and sample placements are JSON annotations on upstream container and sample objects; design and implementation identities are also RDF URI annotations. This preserves Lab data without defining another object model or extension ontology.

Checks validate the constructed upstream document, operation parameters, control-flow order, and RDF serialization round-trips. Upstream's behavior-enabled `Protocol` constructor adds an initial-to-final edge when reading a document; object-level readback can therefore change its graph. The adapter does not patch upstream or run its execution engine.

`plan.json` is the integration boundary. The adapter imports no Lab Python modules, and Lab imports no adapter. A future supported PyPI release of LabOP can replace the Git requirement without changing compiler inputs, the artifact format, or adding a second protocol model. There is no bundled fallback implementation.

To check the integration against fresh compiler output:

```sh
integrations/labop/.venv/bin/python -m examples.labop_export --out build/labop_check/operations
uv run python -m examples.cloning --target manual --out build/labop_check/cloning
integrations/labop/.venv/bin/python integrations/labop/check.py build/labop_check
```
