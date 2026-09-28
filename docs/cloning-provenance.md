# Planning, compiling, and recording an experiment

The pipeline is `SBOL designs + inventory + recipes + resolved methods → BuildPlan → ExperimentPlan → Compilation → explicit RunRecord`. Each arrow creates a new immutable snapshot. Target selection happens after the experiment has been frozen. SBOL, LabOP, methods, and robot programs are projections of those snapshots.

## Python API

```python
from lab import compile
from lab.experiments import cloning
from lab.inventory import Inventory
from lab.provenance import Document
from lab.targets import LiquidHandler

designs = Document.read("designs.ttl").freeze()
inventory = Inventory.read("inventory.json", document=designs)
system = cloning.CloningSystem.read("system.json")
methods = cloning.CloningMethods.read("methods.json")

build = cloning.plan(
    request,
    document=designs,
    inventory=inventory,
    system=system,
    methods=methods,
)
build.write("review/build")
build.require_ready()

experiment = cloning.build(build)
compile(experiment, LiquidHandler.OT2).write("review/ot2")
compile(experiment, LiquidHandler.FLEX).write("review/flex")
compile(experiment, LiquidHandler.STAR).write("review/star")
```

`BuildRequest.targets` accepts `BuildTarget(design=component.ref, volume_ul=Decimal(...), form=...)` for liquids and `CountTarget(design=component.ref, count=..., form=...)` for whole material units. A `CountTarget` defaults to `MaterialForm.PLATED_SAMPLE`. Method and system files are typed JSON snapshots written by `CloningMethods.write()` and `CloningSystem.write()`. Recipe parameter values cannot be recovered from SBOL usage edges: they must be specified separately.

The complete runnable [example](../examples/provenance_build.py) authors every input, using short synthetic DNA strings and arbitrary parameters as a software fixture. Run `uv run python -m examples.provenance_build --target manual`; choose `ot2`, `flex`, or `star` with the corresponding SDK extra installed.

## Designs, quantities, and alternative routes

`Stock` requires an explicit `Ref[Implementation]`, `Ref[Component]`, `MaterialForm`, and measured available volume. Pint inputs are normalized to immutable Decimal quantities. Inventory contains recorded implementations; catalog listings, planned products, and simulated material are not available physical inventory. An inventory design assertion does not establish that its sequence was verified. `CountedStock` records assessed whole-unit counts for bacterial stabs and plated samples. A supplier package count does not automatically establish an available material count. Both stock classes require recorded provenance; simulated material cannot satisfy physical inventory.

An `AssemblyRecipe` specifies an enzyme, intended product, method identity, and ordered `FragmentSelection` objects. Each fragment identifies its source component and zero-based Watson cut positions; `None` denotes a linear end. A circular single-cut linearization has equal left and right cuts. Reversal is explicit. The sequence calculator tracks both strands and sticky ends, checks ligation compatibility, and compares a specified target sequence modulo circular rotation. Linear products with unpaired ends require explicit end repair. No fragment is selected by size.

`AssemblyMethod` supplies all reagent and DNA volumes, diluent, thermal holds, cycle counts, mixing parameters, and planned usable output. The planner accounts for available quantities across the entire request, including repeated reaction batches and shared intermediates. It explores permitted recipe combinations and prefers existing stock, then fewer outstanding design/acquisition/preparation requirements, fewer dependent stages, and fewer reactions. `PlanningPolicy.max_states` bounds exploration explicitly; exceeding it fails rather than silently switching algorithms.

Calculated designs have recorded **computational** activities and content-dependent identities. Planned physical implementations point to them without asserting `Implementation.built`. `propose_edits()` examines only positions the caller marks editable. Applying a selected proposal creates a new design; it does not change inventory, establish biological function, or automatically modify a recipe.

The planner schedules assembly, transformation, plating, and explicitly defined external preparation. It matches both the component identity and material form; a design in culture is not available DNA. Missing material, an unspecified conversion procedure, or an invalid design remains an explicit requirement and blocks executable generation. Each reaction has its own output implementation. Allocations consume one shared quantity ledger across all requested targets; planning leaves the input inventory snapshot unchanged.

## Transformation, plating, and preparation

A `CloningSystem` contains an immutable tuple of typed recipes. Recipes choose biological inputs and intended products; `CloningMethods` supplies the full procedure parameters. For example, with authored components and methods:

```python
from lab.inventory import MaterialForm

system = cloning.CloningSystem(
    identity="https://example.org/system",
    recipes=(
        assembly_recipe,
        cloning.TransformationRecipe(
            identity="https://example.org/transform",
            product=strain.ref,
            chassis=cells.ref,
            plasmids=(plasmid.ref,),
            method=transformation_method.identity,
        ),
        cloning.PlatingRecipe(
            identity="https://example.org/plate",
            product=strain.ref,
            method=plating_method.identity,
        ),
        cloning.ExternalPreparationRecipe(
            identity="https://example.org/prepare_dna",
            source=vector.ref,
            source_form=MaterialForm.BACTERIAL_STAB,
            product=vector.ref,
            output_form=MaterialForm.DNA,
            method=preparation_method.identity,
        ),
    ),
)
request = cloning.BuildRequest(
    identity="https://example.org/build",
    targets=(cloning.CountTarget(design=strain.ref, count=2),),
)
```

`TransformationMethod` requires cell and DNA volumes, a recovery-medium `Reagent`, a treatment `profile`, a `recovery_profile`, mixing volumes/cycles, and a planned usable output volume. `initial_celsius` is optional. The generated protocol adds and mixes the cells, mixes and adds each allocated DNA source, applies the treatment profile, adds the recovery medium, and applies the recovery profile. Thermal block volumes follow the actual additions. The output has form `CULTURE` and represents the intended recovery mixture; it does not assert transformation success, clonality, or sequence verification.

`PlatingMethod` requires a substrate component, diluent component, transfer volume, ordered dilution factors, mixing volume/cycles, spot volume, and `spot_height_mm` above the destination well bottom. Each recipe invocation produces one `PLATED_SAMPLE` at the final dilution. Repeated count targets generate independent dilution series and spots. No colony count, incubation, or selection is inferred. The substrate is a supplied plate precondition, recorded in the method and planned SBOL usage; it is a consumable like tips and labware, not an automatically reserved inventory aliquot. A plated sample cannot be pipetted as liquid. Spotting height is carried into LabOP, Opentrons positioning, and the STAR dispense command.

`ExternalPreparationMethod` requires a procedure IRI and explicit instructions, plus exactly one source quantity (`source_volume_ul` or `source_count`) and one expected output quantity (`output_volume_ul` or `output_count`). Optional `reagents` name additional consumed liquids. The matching recipe specifies source and output forms; quantity kinds must agree with those forms. Instructions are preserved in SBOL, LabOP, methods, and the input snapshot. Supply complete instructions and a versioned procedure identifier; Lab does not fetch or invent the external procedure.

External preparation becomes a `ProtocolStage(external=True)` containing an `ExternalPreparation` operation. Its input consumption and prospective output are checked at the operation boundary, so an output is not falsely declared present at the start of the stage. `compile(experiment, LiquidHandler.OT2)` emits an operator document for this stage and robot code for the automated stages. Explicit per-stage target mappings must assign external stages to `Manual()`. A completed operator procedure and measured output can be supplied through `Run`; compilation alone creates no recorded stock.

The runnable [complete workflow](../examples/cloning_workflow.py) demonstrates counted input → external preparation → assembly → transformation → two plated samples using synthetic test data. Run `uv run python -m examples.cloning_workflow --target ot2` with the Opentrons extra, or choose `manual`, `flex`, or `star`.

## Supplier evidence

`AddgeneClient(token=...).plasmid(id)` explicitly retrieves one catalog item through Addgene's [read-only API](https://developers.addgene.org/access-options/). Parsing preserves depositor versus Addgene sequences, full versus partial sequence assertions, retrieval time, and original metadata. `parse_plasmid()` accepts saved responses without network access.

`CatalogEntry(item=..., design=component.ref, form=MaterialForm.BACTERIAL_STAB)` is a caller-reviewed mapping. Pass a frozen `Catalog` to `cloning.plan(catalog=...)`. Acquisition requests contain matching candidates, with Addgene first. A candidate is not an order or usable stock.

`QuoteRecord` and `OrderReference` record transactions performed outside Lab. `Receipt.record(document)` adds an observed arrival and material implementation. A liquid receipt enters `Inventory` only through an explicitly measured `receipt.stock(...)`. `receipt.counted_stock(identity=..., count=...)` records an explicitly assessed count for a counted material form. The declared preparation route can then consume that stock; the receipt alone does not establish a quantity or a usable liquid yield. Addgene authentication and response shape are contract-tested with synthetic fixtures; no authenticated live retrieval is claimed.

## Frozen experiments and hardware

`ExperimentPlan` contains a provenance snapshot and ordered `ProtocolStage` objects. Each stage holds a `RecordedProtocol`, logical deck requirements, dependencies, and material handoffs. `cloning.build()` retains full design and implementation IRIs, carries forward remaining liquid volumes and whole-unit counts after previous consumers, and separates usable-yield planning from the physical volume ledger.

Default automated stages use PCR plates for reagents and products, with separate dilution and substrate plates for plating. A caller may supply a different `reagent_container`; `preparation_container` controls the logical output container of external procedures. Counted external inputs occupy abstract storage positions that do not prescribe robot labware. Loading is an initial condition; the compiler does not invent aliquoting, stock preparation, or an additional liquid transfer during a plate handoff. A loading amount that exceeds a container's capacity fails validation.

Every operation has an identity. Supplying `Protocol(..., identity="https://.../protocol")` gives a stable parent for generated step identities. Without an explicit identity, the snapshot derives one from its contents. Semantic digests exclude developer source paths and hardware; target compilation digests include physical configuration and generated source. Source maps identify inclusive generated line spans for each semantic step.

`compile(experiment, handler)` resolves each stage's deck. A mapping from stage identities to concrete targets or decks supports custom equipment. Flex supports the 50 µL pipette's documented [volume modes](https://docs.opentrons.com/python-api/pipettes/volume-modes/), configured before picking up a tip. STAR thermal stages require supplied asynchronous runtime callbacks: `thermocycle` returns the plate after the profile, while `set_temperature` must maintain the hold with the plate accessible for subsequent pipetting. Generated STAR scripts use a software preview backend when invoked directly. SDK simulations and previews do not qualify physical equipment.

## LabOP and digital methods

`lab.labop.export(experiment)` writes static protocol RDF. It never invokes LabOP's execution engine. The packaged UML ontology, LabOP ontology, and primitive libraries are pinned to upstream commit `2e2bd88150c71a440771fd369f40295dfd622324`; their hashes and license are included in `lab/labop/resources`.

| Lab semantics | LabOP representation |
| --- | --- |
| Experiment and stage | Protocols with ordered subprotocol calls |
| Initial plate contents | Input SampleArray defaults and explicit loading conditions |
| Well selection | PlateCoordinates and object flows |
| Transfer | liquid_handling/Transfer; TransferAtHeight when a destination height is specified |
| External preparation | Named extension primitive with source/destination collections, procedure, instructions, and expected amounts |
| Counted material | Sample assertions and amount measures in OM one, separate from microlitres |
| Mixing | liquid_handling/PipetteMix, including volume and cycle count |
| Wait, persistent temperature, thermal profile, distribution, operator pause | Named Lab extension primitives with explicit typed parameters |
| Material handoff | Subprotocol output-to-input object flow |

The extension primitives are defined inside each artifact. Thermal profiles contain ordered holds with unit-bearing temperatures and durations. Distribution uses an ordered collection of sample aliases and preserves its air-gap and tip-reuse semantics. Lab extensions also retain material identities, initial volumes, planned sample assertions, and the semantic step payload. Initial loads never become fictitious Provision actions. The exporter supports whole-plate continuity across stage handoffs; arbitrary inter-stage well remapping requires an explicit material operation.

The bundle includes the Lab, LabOP, and UML schemas. The independent `scripts/check_labop.py` check loads them through SBOLFactory 1.1.2 and pySBOL3, validates the document, and verifies that thermal-profile parameters deserialize into typed objects. Run it in an isolated interpreter because SBOLFactory registers process-wide builders. This check does not execute the protocol.

Bundles contain `experiment.json`, `build.json` where applicable, `provenance.ttl`, `protocol.labop.ttl`, `methods.md`, input snapshots, schemas, per-stage protocol/target plans, manifests, source maps, and generated programs. Inputs include the build-plan provenance before its protocol links were resolved, inventory, catalog mappings, recipes, and methods. `bundle.json` records SHA-256 checksums of the other artifacts. Methods describe planned work until observations exist.

## Explicit run observations

```python
from lab.execution import Outcome, Run, RunMode, StepRecord

run = Run(
    compilation,
    identity="https://example.org/runs/42",
    mode=RunMode.PHYSICAL,
    agent=operator,
    started_at=observed_start,
)
run.record(
    StepRecord(
        step=step_identity,
        attempt=1,
        outcome=Outcome.SUCCEEDED,
        started_at=step_start,
        ended_at=step_end,
        observations=(),
        evidence=(),
    )
)
record = run.finalize(ended_at=observed_end)
record.write("runs/42")
```

Times, outcomes, attempts, measurements, and evidence come from the caller. Missing observations remain missing; skipped steps do not produce action firings. `record.complete` requires ordered successful coverage of every semantic step, using the latest recorded attempt. A successful step history does not automatically realize a product: `OutputRecord` explicitly records observed material under a new identity, with an optional measured volume or count and a structural assertion. The observed quantity kind must match the planned material form; planned counts and yields are never substituted for observations. Simulation produces simulated activities and implementations.

Observed SBOL and LabOP share activity identities and timestamps. LabOP stage observation windows aggregate supplied records; no structural UML token firings or unobserved subprotocol calls are fabricated. The execution bundle refers to the immutable compilation digest. Live robot event capture and automatic inventory updates are deferred.
