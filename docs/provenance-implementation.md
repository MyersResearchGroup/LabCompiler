# Provenance implementation status

Lab owns immutable design, inventory, planning, and protocol models. SBOL3 describes designs, supplied materials, and planned work; LabOP describes the protocol's operations and flows. Target programs are compiled from the same frozen semantics.

## Implemented path

1. **SBOL3 foundation.** `provenance/types.py`, `_schema.py`, `document.py`, `validation.py`, `sbol3.py`, and `resources/lab.ttl` implement the taxonomy, explicit references, authoring, validation, deterministic snapshots, and pySBOL3 interchange. Ordinary imports keep annotations resolvable at runtime.
2. **Cloning planning.** `inventory.py` and cloning `systems.py`, `methods.py`, `_dna.py`, `sequences.py`, `domestication.py`, `routes.py`, and `planning.py` handle measured stock snapshots, explicit fragments, sequence calculations, caller-selected edit proposals, alternative routes, shared quantities, repeated batches, and multi-level dependencies. Typed transformation, counted plating, and explicit external preparation share the allocation and dependency model. Unspecified preparation and acquisition remain requirements. Sequence calculations use Biopython 1.84 enzyme definitions; pydna is not a runtime dependency.
3. **Supplier records.** `suppliers/types.py` and `addgene.py` provide read-only catalog retrieval, preserved sequence evidence, reviewed design mappings, acquisition requests, external quote/order references, and counted receipts. No purchasing action or available stock is inferred from a listing.
4. **Shared experiment compilation.** `experiment.py`, `operations.py`, `target.py`, `model.py`, and cloning `build.py` freeze stage protocols and material handoffs. Samples retain design and implementation references. The compiler accepts an experiment, supports per-stage target selection, and writes source maps with stable semantic step identities. Shared target values live in `target.py` so importing validation does not initialize the backend package.
5. **LabOP and methods.** `labop/protocol.py`, `primitives.py`, pinned resources, `artifacts.py`, and `methods.py` produce static protocol RDF, typed parameter and sample flows, methods, and artifact checksums. Exact upstream primitives are used where their semantics match. Named extensions specify the remaining operations. Compilation never invokes an execution engine.
6. **Robot targets.** Flex supports 50 µL pipettes and explicit volume-mode changes. STAR separates persistent-temperature and thermal-profile callbacks. The assembly, transformation, and plating paths have software simulation/preview coverage for OT-2, Flex, and STAR; physical qualification is outside those checks.

The developer walkthrough is [Planning and compiling an experiment](cloning-provenance.md). [The complete runnable example](../examples/cloning_workflow.py) supplies all inputs using a synthetic software fixture.

## Boundaries and remaining integrations

External procedures remain operator stages; their expected yields are prospective until observed. Plating plans one deposited sample per reaction and does not infer colony formation or selection. The substrate, tips, and labware are supplied consumables, not stock reservations. The existing standalone transformation and plating builders also remain available for direct protocol authoring.

The LabOP integration ends at protocol export; post-execution recording is outside its scope. Durable inventory reservation/depletion, automatic procurement, arbitrary inter-stage well remapping, and physical instrument qualification are not implemented. The Addgene adapter is contract-tested without authenticated live credentials. LabOP validation checks pinned ontology cardinalities and primitive contracts; it does not claim that every third-party interpreter implements the Lab extension primitives.
