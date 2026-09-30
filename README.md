<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/the-lab-compiler/lab-py/master/docs/assets/brand/wordmark-full-dark.svg">
    <img alt="The Lab Compiler" src="https://raw.githubusercontent.com/the-lab-compiler/lab-py/master/docs/assets/brand/wordmark-full-light.svg" width="620">
  </picture>
</p>

<p align="center">
  <em>A compiler for biological engineering. Describe experiments in Python, check the protocol, and produce a document or a robot program.</em>
</p>

Lab Compiler is the Python compiler for laboratory work. A `Protocol` records materials, wells, steps, and optional sample identities and lineage. Compilation checks volumes, units, bindings, and sample declarations, then writes a printable document and code for the liquid handler you name.

Protocol containers, steps, and decks are expressed in Lab types. A `Deck` holds shared container requirements and can include a `DeckLayout` for each liquid handler. Compilation selects the layout, checks the handler's capabilities, and translates Lab equipment and placements into Opentrons or PyLabRobot configuration. Supported presets handle simple layouts. You do not need to construct SDK deck objects.

## Install

Install the `lab-compiler` distribution with Python 3.11 or 3.12, and import it as `lab`:

```sh
pip install lab-compiler
```

The base package compiles printable documents. Install the optional SDK for your robot target:

```sh
pip install "lab-compiler[opentrons]"  # OT-2 and Flex
pip install "lab-compiler[star]"       # Hamilton STAR
```

Use `"lab-compiler[opentrons,star]"` to install both SDKs.

## Write a protocol

You define the strain, chassis, and plasmids in a typed request, then compile that request for the selected target. This example compiles a heat-shock transformation using material identifiers supplied by the user.

```python
from lab import compile
from lab.equipment import LiquidHandler
from lab.experiments.cloning import (
    Transformation,
    TransformationRequest,
    transformation_deck,
)
from lab.part import Part

request = TransformationRequest(
    id="my-transformation",
    transformations=(
        Transformation(
            id="transformation-1",
            strain=Part("https://example.org/my-strain/1"),
            chassis=Part("https://example.org/my-cells/1"),
            plasmids=[Part("https://example.org/my-plasmid/1")],
        ),
    ),
)
compiled = compile(
    request,
    hardware=transformation_deck(on_module=True),
    liquid_handler=LiquidHandler.OT2,
)
compiled.write("build/transformation")
```

`compile` accepts an `Assembly`, an `AssemblyRequest`, a `TransformationRequest`, or a `PlatingRequest`. One assembly is a protocol named by `assembly.id`. An `AssemblyRequest` names a protocol that holds several assemblies. `Transformation` names an output strain, its chassis, and its plasmids as `Part` IRIs. Compilation assigns logical wells and records the transfers, heat shock, and recovery steps. The result includes an output manifest for downstream stages. The [cloning example](https://github.com/the-lab-compiler/lab-py/blob/master/examples/cloning.py) defines its materials, assemblies, and transformations directly and links assembly, transformation, and plating.

`transformation_deck(on_module=True)` names the 24-well DNA block, the cell tubes, and the reaction plate. This is an Opentrons preset; the example uses `LiquidHandler.OT2`. For a document with no robot, import `Manual` from `lab.targets` and use `compile(request, Manual())`.

`lab.compile()` lays out a cloning request, then snapshots its operations, samples, lineage, and output placements, validates them, and produces one `Compilation`. A `Protocol` compiles the same way. Inspect `compiled.protocol` for that recorded snapshot. Hardware targets consume the same recorded operations used by the document renderer. `build_assembly`, `build_transformation`, and `build_plating` return the `Protocol` when you want it before choosing a target.

## Samples and protocol outputs

`compiled.manifest` is an `OutputManifest` containing the declared output samples and their logical placements. Pass an assembly's manifest as `inputs` when compiling a `TransformationRequest`, then pass the transformation's manifest as `inputs` when compiling a `PlatingRequest`. The [cloning example](https://github.com/the-lab-compiler/lab-py/blob/master/examples/cloning.py) compiles each stage separately.

The core cloning types live in `lab.experiments.cloning.types` and are exported from `lab.experiments.cloning`. `Assembly` describes a product and its constituent parts; `Transformation` describes a strain, its chassis, and its plasmids. `AssemblyRequest` groups assemblies, `TransformationRequest` groups transformations, and `PlatingRequest` selects and orders source samples by id.

A plating request's sample ids cover the entire input manifest. Transformation and plating each validate and interpret their input manifest for their own layout, which accepts a single source container. Their requests can set `source_stage_id` to assert the expected input protocol. `Assembly` and `AssemblyRequest` have no upstream input.

For custom protocols, declare typed sample metadata with `protocol.add_sample(sample, at=well, is_input=True)` or `is_output=True`, using `Sample` from `lab.samples`. Loads and operations own volume accounting. Parent ids refer to samples in the same protocol; imported samples identify their upstream protocol and sample separately. Compilation checks sample references and locations and rejects cyclic lineage. Manifests describe planned outputs, not completed execution.

`lab.samples` also defines `Location(resource, well)`, `SamplePlacement`, and `OutputManifest`. Recorded operations, sample placements, target bindings, and final volume accounting use the same logical `Location` type. For example, an output placement's `location` can be used directly as a key in `dict(compiled.final_volumes)`. `lab.part.Part` identifies a biological part by its SBOL IRI; cloning types and stage builders live under `lab.experiments.cloning`.

## Describe a deck

This OT-2 deck places two 96-well plates in slots 1 and 2, a 300 µL tip rack in slot 3, and a P300 pipette on the left mount. It uses the same equipment and placement types as the [deck layouts example](https://github.com/the-lab-compiler/lab-py/blob/master/examples/deck_layouts.py).

```python
from lab.deck import Deck, DeckLayout, Pipette, Placement, Slot, TipRack
from lab.equipment import LabwareModel, LiquidHandler, Mount, PipetteModel, TipRackModel
from lab.labware import PLATE_96, ContainerSpec

deck = Deck(
    containers=(
        ContainerSpec(id="sources", labware=PLATE_96),
        ContainerSpec(id="assay", labware=PLATE_96),
    ),
    layouts=(
        DeckLayout(
            liquid_handler=LiquidHandler.OT2,
            placements=(
                Placement(
                    container="sources",
                    model=LabwareModel.CORNING_96_360_UL,
                    location=Slot("1"),
                ),
                Placement(
                    container="assay",
                    model=LabwareModel.CORNING_96_360_UL,
                    location=Slot("2"),
                ),
            ),
            tip_racks=(
                TipRack(
                    id="tips",
                    model=TipRackModel.OPENTRONS_300_UL,
                    location=Slot("3"),
                ),
            ),
            pipettes=(
                Pipette(
                    model=PipetteModel.P300_SINGLE_GEN2,
                    mount=Mount.LEFT,
                    tip_racks=("tips",),
                ),
            ),
        ),
    ),
)
```

The container ids, `sources` and `assay`, match the names used by the protocol. `DeckLayout` assigns each container a physical model and position, and connects the pipette to its tip rack. A deck can include additional layouts for Flex or STAR using the same container ids.

A deck with explicit layouts must include one for the selected liquid handler. Preset decks, such as `transformation_deck`, instead use `Container` and `DeckSite` from `lab.deck` to assign containers to supported placement groups. A bare `ContainerSpec` requires an explicit layout.

## Describe physical layouts in Lab

Use `DeckLayout` when equipment or placement needs to be explicit. Each layout names its `LiquidHandler` and places the same shared containers. All objects below belong to Lab:

| Object | Purpose |
| --- | --- |
| `Placement` | A container's physical labware model, position, and optional well mapping |
| `Slot`, `Rail`, `HolderSite` | A deck slot, carrier rail, or indexed site on a named holder |
| `Carrier`, `Module` | Equipment that occupies a position and holds other resources |
| `TipRack` | A tip model and its position |
| `Pipette`, `Channel` | A mounted pipette or independent channel, with its assigned tip racks |

Equipment models are typed identifiers from `lab.equipment`; each backend resolves the models it supports. A STAR layout can place a carrier at `Rail(20)` and a plate at `HolderSite("plates", 3)`. An Opentrons layout can place the same logical container at `Slot("2")` or on a named module. The shared model does not impose one thermal device on every handler. Targets enforce their supported device counts, models, locations, and module footprints.

The [deck layouts example](https://github.com/the-lab-compiler/lab-py/blob/master/examples/deck_layouts.py) prepares duplicate BSA standards and purified protein samples in a flat-bottom assay plate using a reservoir of prepared BCA working reagent. `protocol()` describes the transfers; `deck()` shares the container requirements and calls `opentrons_layout()` for OT-2 and Flex and `hamilton_layout()` for STAR. The logical source wells map to physical wells `B1`–`B12` on each handler; STAR also specifies carriers, rails, and occupied carrier sites. Mixing, incubation, and absorbance reading remain an explicit operator handoff following the [Pierce BCA guide](https://www.thermofisher.com/TFS-Assets/LSG/manuals/MAN0011430_Pierce_BCA_Protein_Asy_UG.pdf). The example imports only Lab types and the Python standard library, with layouts to adapt to installed equipment.

```python
from examples.deck_layouts import deck, protocol
from lab import compile
from lab.equipment import LiquidHandler

compiled = compile(protocol(), deck(), liquid_handler=LiquidHandler.STAR)
```

The compiler rejects missing layouts, conflicting placements, invalid holder references, incompatible labware, and unsupported target features. It preserves the authored Lab deck in `plan.json` alongside the resolved backend configuration. Physical geometry comes from the selected equipment definitions; the STAR backend constructs and serializes the PyLabRobot resource hierarchy internally.

For Opentrons, place thermal labware on a supported `Module`. STAR layouts currently support one independent `Channel` and external thermal integration. Declare thermal containers in `DeckLayout.external_thermal_resources` and supply the async `thermocycle` callback when the generated `run()` executes. The callback is responsible for controlling the device and returning the plate to its original position. Compilation checks the declaration; the software preview only reports the request. On-deck `Module` models are not yet supported by the STAR backend.

## Try it

The examples live in the source repository. With [uv](https://docs.astral.sh/uv/) installed, set up Python 3.12 and both optional SDKs:

```sh
git clone https://github.com/the-lab-compiler/lab-py.git
cd lab-py
uv sync --locked --all-extras --python 3.12
uv run --no-sync python -m examples.cloning --target manual
uv run --no-sync python -m examples.cloning --target ot2
uv run --no-sync python -m examples.deck_layouts --target star
uv run --no-sync python -m examples.deck_layouts --target ot2
uv run --no-sync python -m examples.deck_layouts --target flex
```

Use `manual` or `ot2` for the cloning example. Its assembly recipe includes transfers below the current Flex preset's supported pipetting range. The deck layouts example supports `ot2`, `flex`, and `star`.

The cloning example writes separate `assembly`, `transformation`, and `plating` bundles under `build/cloning/<target>`, including for the manual target. The deck layouts example writes to `build/decks/<target>`. Both accept `--out` to choose a different output directory. Compilation never connects to hardware.

`Compilation.write()` writes these files:

| File | Contents | When written |
| --- | --- | --- |
| `protocol.html` | Printable protocol and setup instructions | Every compilation |
| `plan.json` | Recorded protocol, sample declarations, target configuration, bindings, and final volumes | Every compilation |
| `manifest.json` | Planned output samples and logical placements | When the protocol declares outputs |
| `protocol.py` | Generated robot program | Robot targets |

The manifest uses logical container and well names; physical bindings remain in `plan.json`. Rewriting an identical bundle succeeds. If any generated file would replace different contents, `write()` rejects the write before changing any bundle files.

## Status

Lab Compiler is an early prototype. It checks a plan and emits a document or device program for the OT-2, Flex, and STAR. Software checks are not calibration, collision safety, or qualification of a physical run. Generated instructions need a person and a facility before anyone uses them at the bench.

## Development

From the repository root:

```sh
uv sync --locked --all-extras --python 3.12
uv run --no-sync ruff check .
uv run --no-sync mypy
uv run --no-sync pytest
```

Shared types and compiler code live directly under `src/lab`; experiment families and target implementations have their own packages:

```text
src/lab/
    protocol.py       # Protocol builder, Plate, Well
    model.py          # Recorded operations, protocol snapshots, target plans
    part.py           # SBOL part identity
    samples.py        # Sample, Location, SamplePlacement, OutputManifest
    labware.py        # Logical labware specifications
    equipment.py      # Liquid handlers and equipment identifiers
    deck.py           # Container requirements and physical layouts
    units.py          # Quantities and unit conversion
    compiler.py       # Compilation and output bundles
    validation.py     # Volume, binding, and sample validation
    documents.py      # Printable protocol rendering
    experiments/
        cloning/      # Cloning types, stage builders, decks, and workflow composition
            types.py  # Assembly and transformation designs, stage inputs
    targets/          # Manual, Opentrons, and STAR backends
```

See the [release guide](https://github.com/the-lab-compiler/lab-py/blob/master/docs/releasing.md) for package validation and PyPI publishing.
