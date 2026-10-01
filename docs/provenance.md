# Biological designs and provenance

`lab.provenance` represents SBOL3 designs and the activities, agents, plans, and material realizations associated with them. Its objects are immutable Python dataclasses. A `Document` collects explicitly added objects, and `freeze()` produces a validated `DocumentSnapshot` with stable identities for owned children. The module imports and exports SBOL3 Turtle and interoperates with pySBOL3 without changing its global namespace or builder registry.

## Author a document

```python
from lab.provenance import Component, Document, Sequence
from lab.provenance.vocabulary import DNA, IUPAC_DNA

document = Document(namespace="https://example.org/my_project")
sequence = Sequence(
    identity=document.iri("sequence"),
    elements="ACGTACGT",
    encoding=IUPAC_DNA,
)
design = Component(
    identity=document.iri("design"),
    types=(DNA,),
    sequences=(sequence.ref,),
)
document.add(sequence, design)
snapshot = document.freeze()
snapshot.write("build/design.ttl")
```

`Ref[T]` carries an absolute IRI and a Python target type. `design.ref` is a `Ref[Component]`; `snapshot.resolve(design.ref)` returns the corresponding component. `snapshot.get(identity, Component)` also checks the requested type at runtime. References never fetch remote resources. Adding an object does not automatically add its references.

Collections use tuples. SBOL multi-valued properties are unordered RDF sets, so freezing sorts them deterministically. Represent biological order with locations and constraints. Tuple position does not specify an assembly recipe.

`document.iri("design/feature")` constructs an IRI from slash-separated SBOL display IDs. It does not use a process-wide namespace. Top-level objects require explicit identities. Owned children such as `Usage`, `Association`, and `SubComponent` may omit theirs; freezing assigns names such as `planned_build/Usage1` in a new snapshot. Explicit child identities are preserved, and generated identities avoid existing ones. Give a child an explicit identity when another object needs to reference it before freezing.

`Document.add()` accepts top-level objects and rejects conflicting definitions of an existing IRI. Adding an identical definition is idempotent. Edits use `dataclasses.replace()` and a new identity where they describe a new design or record. `Document.from_snapshot(snapshot)` creates an independent authoring document containing an existing snapshot's objects and annotations.

## Model designs, materials, and activities

| Objects | Purpose |
| --- | --- |
| `Sequence`, `Component` | Sequence data and structural or functional designs |
| `SubComponent`, `SequenceFeature`, `LocalSubComponent`, `ExternallyDefined`, `ComponentReference` | Features owned by a component |
| `Range`, `Cut`, `EntireSequence` | Locations on a referenced sequence |
| `Constraint`, `Interaction`, `Participation`, `Interface` | Structural relationships and functional interactions |
| `CombinatorialDerivation`, `VariableFeature`, `Collection` | Design families and grouped objects |
| `Implementation` | A planned, recorded, or simulated material realization |
| `Activity`, `Usage`, `Association` | Work, qualified input roles, and qualified agent roles |
| `Agent`, `Plan` | Who or what is involved, and the method identity |
| `Attachment`, `ExperimentalData`, `Experiment`, `Model`, `Measure` | Linked evidence, datasets, computational models, and quantities |

Objects share `name`, `description`, `derived_from`, `generated_by`, and owned `measures`. Top-level objects also have `namespace` and attachment references. `Usage` and `Association` belong to an `Activity`; agents, plans, activities, and implementations are top-level objects.

```python
from lab.provenance import (
    Activity,
    Agent,
    AgentKind,
    Association,
    EvidenceState,
    Implementation,
    Plan,
    Usage,
)

planner = Agent(
    identity=document.iri("planner"),
    kind=AgentKind.SOFTWARE,
    software_version="0.1.0",
)
method = Plan(identity=document.iri("method"))
activity = Activity(
    identity=document.iri("planned_build"),
    usage=(Usage(entity=design.ref),),
    association=(Association(agent=planner.ref, plan=method.ref),),
    evidence_state=EvidenceState.PLANNED,
)
output = Implementation(
    identity=document.iri("planned_output"),
    derived_from=(design.ref,),
    generated_by=(activity.ref,),
    evidence_state=EvidenceState.PLANNED,
)
document.add(planner, method, activity, output)
```

`Implementation.derived_from` can identify the intended design. `built` describes the realized structure when that assertion is available. Planned implementations must leave `built` unset. An implementation's material inputs belong in the generating activity's usages; the model does not infer genetic lineage from every physical reagent contribution.

Evidence states are `UNKNOWN`, `PLANNED`, `RECORDED`, and `SIMULATED`. A recorded inventory assertion does not imply sequence verification or a successful experiment. Imported SBOL without an evidence state remains unknown. Planned activities cannot have execution timestamps. Recorded and simulated activities can include timezone-aware `datetime` values, and end times must not precede start times.

`Activity.types` contains ontology classifications encoded as `sbol:type`. `Activity.informed_by` references predecessor activities through `prov:wasInformedBy`; multiple activities can share a predecessor. `Plan.protocol` optionally links to a separately specified protocol IRI, including a LabOP protocol. The plan itself does not contain or execute a method body.

## Import, validate, and export

```python
document = Document.read("build/design.ttl")
document.validate().raise_for_errors()
snapshot = document.freeze()

native = snapshot.to_sbol3()  # A new, mutable pySBOL3 Document
native_report = native.validate()  # pySBOL3's additional SBOL/SHACL checks
restored = Document.from_sbol3(native).freeze()
assert restored.digest == snapshot.digest
```

Input is local SBOL3 Turtle. SBOL2 input and unsupported SBOL classes or properties fail explicitly. Foreign RDF annotations, including language-tagged values and nested blank nodes, survive import and export. Unknown annotations remain RDF rather than becoming inferred Python fields. A mixed-namespace or empty import needs an explicit `namespace=` for authoring new objects.

Lab checks field types, required values, unique identities and ownership, reference closure and target types, sequence bounds, feature reference scope, component containment and activity dependency cycles, and evidence assertions. `validate()` returns diagnostics with identity, field path, code, and message. `freeze()` raises `ProvenanceError` containing that report when checks fail. These checks are not a complete implementation of every SBOL specification rule; use the detached pySBOL3 document's validator for additional checks.

Use `freeze(allow_external=True)` for intentionally incomplete graphs. References to objects that are present still undergo type validation; unresolved references remain unresolved. The module never creates placeholder objects or downloads referenced attachments.

Turtle output is deterministic, with full IRIs and canonical blank-node identifiers. `snapshot.digest` is SHA-256 over that serialization, including retained annotations. This identifies the provenance document, independently of the compiler artifact digest. `write()` accepts an identical existing file and refuses to replace different contents.

The adapter pins pySBOL3 `1.2.0.post0`. It handles `Activity.informed_by` at the RDF boundary because the upstream property uses ownership. It also corrects that release's mappings of `Cut.at` and `SubComponent.role_integration` on detached returned instances, while preserving the standard `sbol:at` and `sbol:roleIntegration` predicates. Importing an existing pySBOL3 document normalizes those known mappings without mutating the original. No global classes, namespaces, or builders are patched.

The small Lab extension vocabulary is packaged at `lab/provenance/resources/lab.ttl`. It defines evidence state, agent kind, software version, and the link from a plan to a protocol. The namespace is an identifier; using it does not require a network lookup.

## Run the example

```sh
uv run --no-sync python -m examples.provenance --out build/provenance.ttl
```

[The example](../examples/provenance.py) builds a design, an inventory assertion, a prospective activity, and a planned output; checks the SBOL3 export; writes it; and verifies its round trip. It specifies no executable cloning method. The [experiment walkthrough](cloning-provenance.md) covers inventory planning, compiler integration, and LabOP protocol export; the [implementation status](provenance-implementation.md) lists the remaining integrations.

The mappings follow the [SBOL 3.1 specification](https://sbolstandard.org/docs/SBOL3.1.0.pdf) and [pySBOL3's provenance model](https://raw.githubusercontent.com/SynBioDex/pySBOL3/main/sbol3/provenance.py). Protocol interchange uses [LabOP](https://bioprotocols.github.io/labop/).
