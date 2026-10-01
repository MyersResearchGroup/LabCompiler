"""OT-2 command equivalence with the PUDU assembly, transformation, and plating methods."""

import json
import os
import re
from collections.abc import Mapping, Sequence
from decimal import Decimal
from io import StringIO
from pathlib import Path

import pytest
import sbol3

import lab
from examples.transformation import (
    CELL_ALIQUOT,
    CELLS_PER_REACTION,
    DH5ALPHA,
    DNA_1,
    DNA_2,
    REPLICATES,
    STRAIN_1,
    STRAIN_2,
    TRANSFORMATIONS,
)
from lab.experiments.cloning import (
    Assembly,
    Transformation,
    assembly_deck,
    build_assembly,
    build_plating,
    build_transformation,
    layout_assembly,
    plating_deck,
    record_assembly,
    transformation_deck,
)
from lab.experiments.cloning.addresses import well_at
from lab.samples import Location, OutputManifest, Sample, SamplePlacement
from lab.targets import LiquidHandler
from lab.units import magnitude

try:
    from opentrons.simulate import simulate
except ImportError:
    simulate = None

PUDU = Path.home() / "git/MyersResearchGroup/PUDU/src"
requires_robots = pytest.mark.skipif(
    simulate is None or not PUDU.is_dir(),
    reason="Opentrons SDK or PUDU checkout missing",
)

_ASPIRATE = re.compile(r"Aspirating ([0-9.]+) uL from ([A-H]\d+) of (.+?) at ")
_DISPENSE = re.compile(r"Dispensing ([0-9.]+) uL into ([A-H]\d+) of (.+?) at ")


def _log(source: str, tmp_path: Path) -> list[dict]:
    assert simulate is not None
    previous = Path.cwd()
    try:
        os.chdir(tmp_path)
        log, _bundle = simulate(StringIO(source))
    finally:
        os.chdir(previous)
    return log


def _transfers(log: Sequence[Mapping]) -> list[tuple[str, str, str, str, str]]:
    pending: tuple[str, str] | None = None
    found = []
    for event in log:
        text = str(event.get("payload", {}).get("text", ""))
        aspirate = _ASPIRATE.search(text)
        dispense = _DISPENSE.search(text)
        if aspirate:
            pending = (_place(aspirate.group(3)), aspirate.group(2))
        elif dispense and pending is not None:
            destination = (_place(dispense.group(3)), dispense.group(2))
            if destination != pending:
                found.append((*pending, *destination, _volume(dispense.group(1))))
    return found


def _profiles(log: Sequence[Mapping]) -> list[tuple[tuple[tuple[float, float], ...], int | None]]:
    found = []
    for event in log:
        payload = event.get("payload", {})
        text = str(payload.get("text", ""))
        if not text.startswith("Thermocycler starting"):
            continue
        steps = []
        for step in payload["steps"]:
            if "hold_time_seconds" in step:
                seconds = float(step["hold_time_seconds"])
            else:
                seconds = float(step["hold_time_minutes"]) * 60
            steps.append((float(step["temperature"]), seconds))
        found.append((tuple(steps), payload.get("repetitions")))
    return found


def _handoff(log: Sequence[Mapping]) -> dict:
    for event in log:
        text = str(event.get("payload", {}).get("text", ""))
        marker = "HANDOFF "
        if marker in text:
            return json.loads(text.split(marker, 1)[1])
    raise AssertionError("PUDU protocol did not publish a handoff")


def _place(labware: str) -> str:
    text = " ".join(labware.split())
    for noise in (
        " on Temperature Module GEN1",
        " on Temperature Module",
        " on Thermocycler Module",
        " on Thermocycler",
    ):
        text = text.replace(noise, "")
    return re.sub(r" on \d+$", "", text).strip()


def _volume(text: str) -> str:
    return format(Decimal(text).normalize(), "f")


def _protocol(body: str) -> str:
    return (
        "import json\nimport sys\n"
        f"sys.path.insert(0, {str(PUDU)!r})\n"
        "from opentrons import protocol_api\n"
        "metadata = {'protocolName': 'pudu equivalence', 'author': 'lab'}\n"
        "requirements = {'robotType': 'OT-2', 'apiLevel': '2.21'}\n"
        f"{body}\n"
    )


def _lab_source(protocol, deck) -> str:
    return lab.compile(protocol, deck=deck, liquid_handler=LiquidHandler.OT2).files["protocol.py"]


def _assert_product_locations(
    protocol: lab.Protocol, expected: Mapping[str, Sequence[str]]
) -> None:
    outputs = protocol.snapshot().output_manifest()
    locations = {placement.sample_id: placement.location for placement in outputs.placements}
    assert {sample.material_identity for sample in outputs.samples} == set(expected)
    for material, wells in expected.items():
        assert [
            locations[sample.id].well
            for sample in outputs.samples
            if sample.material_identity == material
        ] == list(wells)


@pytest.mark.integration
@requires_robots
def test_sbol_assembly_matches_pudu_transfers_profiles_and_wells(tmp_path):
    assemblies = [
        {
            "Product": "https://SBOL2Build.org/composite_1/1",
            "Backbone": "https://sbolcanvas.org/pSB1C3/1",
            "PartsList": ["https://sbolcanvas.org/GFP/1"],
            "Restriction Enzyme": "https://SBOL2Build.org/BsaI/1",
        }
    ]
    entry = assemblies[0]
    recipe = Assembly(
        product=sbol3.Component(entry["Product"].removesuffix("/1"), sbol3.SBO_DNA),
        backbone=sbol3.Component(entry["Backbone"].removesuffix("/1"), sbol3.SBO_DNA),
        parts=[
            sbol3.Component(uri.removesuffix("/1"), sbol3.SBO_DNA) for uri in entry["PartsList"]
        ],
        restriction_enzyme=sbol3.Component(
            entry["Restriction Enzyme"].removesuffix("/1"), sbol3.SBO_PROTEIN
        ),
    )
    protocol = build_assembly((recipe,), name="SBOL loop assembly")
    ours = _log(_lab_source(protocol, assembly_deck()), tmp_path)
    source = _protocol(
        "assemblies = "
        + json.dumps(assemblies)
        + """
def run(protocol: protocol_api.ProtocolContext):
    from pudu.assembly import SBOLLoopAssembly
    engine = SBOLLoopAssembly(assemblies=assemblies, output_xlsx=False, protocol_name='equiv')
    engine.run(protocol)
    protocol.comment('HANDOFF ' + json.dumps(engine.product_uri_to_wells))
"""
    )
    pudu = _log(source, tmp_path)
    assert _transfers(ours) == _transfers(pudu)
    assert _profiles(ours) == _profiles(pudu)
    _assert_product_locations(
        protocol, {uri.removesuffix("/1"): wells for uri, wells in _handoff(pudu).items()}
    )


@pytest.mark.integration
@requires_robots
@pytest.mark.parametrize(
    ("assemblies", "factory"),
    [
        (
            [{"parts": ["part1", "part2"], "backbone": "acceptor", "restriction_enzyme": "BsaI"}],
            "Domestication",
        ),
        (
            [
                {
                    "promoter": ["GVP0008"],
                    "rbs": "B0034",
                    "cds": "sfGFP",
                    "terminator": "B0015",
                    "receiver": "Odd_1",
                }
            ],
            "ManualLoopAssembly",
        ),
    ],
)
def test_other_assembly_formats_match_pudu(tmp_path, assemblies, factory):
    layout = layout_assembly(assemblies)
    protocol = lab.Protocol(factory)
    reagents = protocol.plate("reagents", shape=(4, 6), capacity=1500 * lab.uL)
    products = protocol.plate("products", capacity=100 * lab.uL)
    for index, material, volume in layout.stocks:
        protocol.load(well_at(reagents, index), material, volume=volume * lab.uL)
    outputs = record_assembly(protocol, layout, reagents, products)
    ours = _log(_lab_source(protocol, assembly_deck()), tmp_path)
    source = _protocol(
        "assemblies = "
        + json.dumps(assemblies)
        + f"""
def run(protocol: protocol_api.ProtocolContext):
    from pudu.assembly import {factory}
    engine = {factory}(assemblies=assemblies, output_xlsx=False, protocol_name='equiv')
    engine.run(protocol)
    protocol.comment('HANDOFF ' + json.dumps(engine.product_uri_to_wells))
"""
    )
    pudu = _log(source, tmp_path)
    assert _transfers(ours) == _transfers(pudu)
    assert _profiles(ours) == _profiles(pudu)
    assert {key: [well.name for well in wells] for key, wells in outputs.items()} == _handoff(pudu)


@pytest.mark.integration
@requires_robots
def test_heat_shock_matches_pudu_transfers_and_well_labels(tmp_path):
    strains = [
        {
            "Strain": "https://SBOL2Build.org/composite_strain_1/1",
            "Chassis": "https://sbolcanvas.org/DH5alpha/1",
            "Plasmids": ["https://SBOL2Build.org/composite_plasmid_1/1"],
        }
    ]
    locations = {"https://SBOL2Build.org/composite_plasmid_1/1": ["A1"]}
    entry = strains[0]
    recipe = Transformation(
        strain=sbol3.Component(entry["Strain"].removesuffix("/1"), sbol3.SBO_FUNCTIONAL_ENTITY),
        chassis=sbol3.Component(entry["Chassis"].removesuffix("/1"), sbol3.SBO_FUNCTIONAL_ENTITY),
        plasmids=[
            sbol3.Component(uri.removesuffix("/1"), sbol3.SBO_DNA) for uri in entry["Plasmids"]
        ],
    )
    protocol = build_transformation(
        (recipe,), {uri.removesuffix("/1"): wells for uri, wells in locations.items()}
    )
    ours = _log(_lab_source(protocol, transformation_deck()), tmp_path)
    source = _protocol(
        "strains = "
        + json.dumps(strains)
        + "\nlocations = "
        + json.dumps(locations)
        + """
def run(protocol: protocol_api.ProtocolContext):
    from pudu.transformation import HeatShockTransformation
    engine = HeatShockTransformation(
        transformation_data=strains, plasmid_locations=locations, replicates=2
    )
    engine.run(protocol)
    protocol.comment('HANDOFF ' + json.dumps(engine.dict_of_parts_in_thermocycler))
"""
    )
    pudu = _log(source, tmp_path)
    assert _transfers(ours) == _transfers(pudu)
    outputs = protocol.snapshot().output_manifest()
    locations = {placement.sample_id: placement.location for placement in outputs.placements}
    assert {
        locations[sample.id].well: list(sample.contents) for sample in outputs.samples
    } == _handoff(pudu)


@pytest.mark.integration
@requires_robots
def test_triplicate_transformation_matches_pudu_transfers_and_well_labels(tmp_path):
    """Two DNAs, three replicates, 20 µL of cells from one 1 mL aliquot."""
    # PUDU names materials from SBOL2 versioned identities.
    strains = [
        {
            "Strain": strain.identity + "/1",
            "Chassis": DH5ALPHA.identity + "/1",
            "Plasmids": [dna.identity + "/1"],
        }
        for strain, dna in ((STRAIN_1, DNA_1), (STRAIN_2, DNA_2))
    ]
    # Recovery media is grouped by the 300 µL tip rack this OT-2 protocol loads.
    tiprack = "opentrons_96_tiprack_300ul"
    protocol = build_transformation(
        TRANSFORMATIONS,
        name="transformation",
        replicates=REPLICATES,
        transfer_volume_competent_cell=CELLS_PER_REACTION,
        tube_volume_competent_cell=CELL_ALIQUOT,
    )
    ours = _log(_lab_source(protocol, transformation_deck(on_module=True)), tmp_path)
    source = _protocol(
        "strains = "
        + json.dumps(strains)
        + f"""
def run(protocol: protocol_api.ProtocolContext):
    from pudu.transformation import HeatShockTransformation
    engine = HeatShockTransformation(
        transformation_data=strains,
        replicates={REPLICATES},
        transfer_volume_competent_cell={int(magnitude(CELLS_PER_REACTION, "microliter"))},
        tube_volume_competent_cell={int(magnitude(CELL_ALIQUOT, "microliter"))},
        tiprack_p200_labware={tiprack!r},
    )
    engine.run(protocol)
    protocol.comment('HANDOFF ' + json.dumps(engine.dict_of_parts_in_thermocycler))
"""
    )
    pudu = _log(source, tmp_path)
    assert _transfers(ours) == _transfers(pudu)
    outputs = protocol.snapshot().output_manifest()
    locations = {placement.sample_id: placement.location for placement in outputs.placements}
    assert {
        locations[sample.id].well: list(sample.contents) for sample in outputs.samples
    } == _handoff(pudu)


@pytest.mark.integration
@requires_robots
def test_plating_matches_pudu_transfers(tmp_path):
    bacteria = {"A1": ["composite_strain_1", "Competent_Cell_DH5alpha"], "B1": "composite_strain_1"}
    inputs = OutputManifest(
        protocol_id="source",
        samples=tuple(
            Sample(
                id=well,
                material_identity=well,
                label=well,
                contents=tuple(value) if isinstance(value, list) else (value,),
            )
            for well, value in bacteria.items()
        ),
        placements=tuple(
            SamplePlacement(sample_id=well, location=Location("source", well)) for well in bacteria
        ),
    )
    protocol = build_plating(inputs, name="Plating", replicates=1, number_dilutions=2)
    ours = _log(_lab_source(protocol, plating_deck()), tmp_path)
    source = _protocol(
        "bacteria = "
        + json.dumps(bacteria)
        + """
def run(protocol: protocol_api.ProtocolContext):
    from pudu.plating import Plating
    Plating(bacterium_locations=bacteria, replicates=1, number_dilutions=2).run(protocol)
"""
    )
    pudu = _log(source, tmp_path)
    assert _transfers(ours) == _transfers(pudu)
