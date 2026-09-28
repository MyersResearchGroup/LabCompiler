# Reporter-reference case

An illustrative lab wants to prepare two deposited samples of a reporter reference for a later fluorescence study. Its starting records include vector receipt R-042 (lot V-17), insert aliquot I-07, and host aliquot H1-03. Procedure P01 is an external preparation boundary: the vector arrives as a counted stab, not an available DNA aliquot.

The names, receipt, lots, locations, and study are fictional. This case supplies realistic record-keeping relationships, not a reproduced experiment. The short DNA strings are compiler surrogates, not the sequences of an actual reporter or plasmid; quantities, cut selections, and method parameters are the arbitrary software fixture from `examples/cloning_workflow.py`. No sequence or fluorescence verification is asserted.

## Input files

| File | What it supplies |
|---|---|
| `designs-and-materials.ttl` | SBOL3 designs, named material implementations, and their asserted relationships. Readable Turtle with namespace prefixes. |
| `inventory.json` | Available material forms, quantities, and storage locations, referencing the SBOL identities. |
| `system.json` | Explicit preparation, assembly, transformation, and plating routes. |
| `methods.json` | The method identity and parameters selected by each route. |

The notebook reads these files directly using `Document.read`, `Inventory.read`, `CloningSystem.read`, and `CloningMethods.read`. It does not substitute objects held inside a display helper. The presentation helpers in [notebook_views.py](../../notebook_views.py) inspect the resulting objects and RDF; their Turtle excerpts are checked to contain only triples from the named source file.

The narrative stops its illustrative run record after external preparation, with a synthetic observed quantity of 8 µL versus the planned 10 µL. Downstream work remains unobserved, and the run is incomplete. This demonstrates the distinction between a full prospective method and a partial observation record without fabricating completion of the experiment.
