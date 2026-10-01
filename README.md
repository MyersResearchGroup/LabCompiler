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

Describe biological designs with native pySBOL3 components. An experiment builder records their laboratory operations in a `Protocol`, and `compile()` validates and compiles that protocol for the selected target.

```python
import sbol3
from lab import compile
from lab.equipment import LiquidHandler
from lab.experiments.cloning import Transformation, build_transformation, transformation_deck

designs = sbol3.Document()
strain = sbol3.Component("https://example.org/my_strain", sbol3.SBO_FUNCTIONAL_ENTITY)
chassis = sbol3.Component("https://example.org/my_cells", sbol3.SBO_FUNCTIONAL_ENTITY)
plasmid = sbol3.Component("https://example.org/my_plasmid", sbol3.SBO_DNA)
designs.add([strain, chassis, plasmid])

protocol = build_transformation(
    [Transformation(strain=strain, chassis=chassis, plasmids=[plasmid])],
    name="transformation",
)
compiled = compile(
    protocol,
    deck=transformation_deck(on_module=True),
    liquid_handler=LiquidHandler.OT2,
)
```

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

compiled = compile(protocol(), deck=deck(), liquid_handler=LiquidHandler.STAR)
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

`compile()` writes the bundle to `~/.lab/<protocol>/<target>/`. `LAB_HOME` replaces `~/.lab`, and `to` chooses another directory. `to=None` returns the compilation without writing. Compiling the same protocol and target again replaces that bundle.

`Compilation.write()` writes these files to a chosen directory:

| File | Contents | When written |
| --- | --- | --- |
| `protocol.html` | Printable protocol and setup instructions | Every compilation |
| `plan.json` | Recorded protocol, sample declarations, target configuration, bindings, and final volumes | Every compilation |
| `manifest.json` | Planned output samples and logical placements | When the protocol declares outputs |
| `protocol.py` | Generated robot program | Robot targets |

The manifest uses logical container and well names; physical bindings remain in `plan.json`. Rewriting an identical bundle succeeds. If any generated file would replace different contents, `write()` rejects the write before changing any bundle files.

## Development

From the repository root:

```sh
uv sync --locked --all-extras --python 3.12
uv run --no-sync ruff check .
uv run --no-sync mypy
uv run --no-sync pytest
```

See the [release guide](https://github.com/the-lab-compiler/lab-py/blob/master/docs/releasing.md) for package validation and PyPI publishing.
