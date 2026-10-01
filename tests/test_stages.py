"""Stage layouts: wells, volumes, and the manual plan that chains them."""

from decimal import Decimal

import pytest
import sbol3

import lab
from examples.cloning import ASSEMBLIES as CLONING_ASSEMBLIES
from examples.cloning import STRAINS as CLONING_STRAINS
from lab.experiments.cloning import (
    BSAI,
    Assembly,
    Transformation,
    assembly_deck,
    build_assembly,
    build_plating,
    build_transformation,
    golden_gate,
    layout_assembly,
    layout_transformation,
    plating_deck,
    plating_plates,
    transformation_deck,
)
from lab.experiments.cloning.addresses import well_name
from lab.model import Mix, Transfer
from lab.samples import Location
from lab.targets import Labware, LiquidHandler, Manual
from lab.targets.lower import lower_deck
from tests.cloning_fixture import ASSEMBLIES, STRAINS


def test_sbol_assembly_assigns_column_major_products_and_water():
    layout = layout_assembly(ASSEMBLIES)
    assert list(layout.products) == [
        "https://SBOL2Build.org/composite_plasmid_1/1",
        "https://SBOL2Build.org/composite_plasmid_2/1",
    ]
    assert layout.products["https://SBOL2Build.org/composite_plasmid_1/1"] == (0,)
    assert layout.products["https://SBOL2Build.org/composite_plasmid_2/1"] == (1,)
    assert well_name(0) == "A1"
    assert well_name(1) == "B1"
    water = next(volume for _index, name, volume in layout.stocks if name == "Deionized Water")
    assert water == 4  # 2 µL in each of two five-part reactions


def test_domestication_and_manual_loop_cover_the_other_assembly_inputs():
    domestic = layout_assembly(
        [{"parts": ["part1", "part2"], "backbone": "acceptor", "restriction_enzyme": "BsaI"}]
    )
    assert [well_name(index) for index in domestic.products["part1"]] == ["A1"]
    assert [well_name(index) for index in domestic.products["part2"]] == ["B1"]
    manual = layout_assembly(
        [
            {
                "promoter": ["GVP0008"],
                "rbs": "B0034",
                "cds": "sfGFP",
                "terminator": "B0015",
                "receiver": "Odd_1",
            }
        ]
    )
    assert len(manual.reactions) == 1
    assert manual.reactions[0].destination == 0
    assert "Restriction Enzyme BSAI" in manual.positions
    assert "Odd_1" in manual.positions


def test_heat_shock_labels_follow_cell_dna_then_media():
    layout = layout_transformation(
        STRAINS[:1],
        {"https://SBOL2Build.org/composite_plasmid_1/1": ["A1"]},
        replicates=2,
    )
    assert layout.contents["A1"][0] == "composite_strain_1"
    assert layout.contents["A1"][1] == "Competent_Cell_DH5alpha"
    assert layout.contents["A1"][2] == "composite_plasmid_1"
    assert layout.contents["A1"][-1] == "Media_1"
    assert list(layout.contents) == ["A1", "B1"]


def test_plating_stays_on_one_plate_until_a_half_is_full():
    assert plating_plates(8, 2, 1) == (False, False)
    assert plating_plates(49, 2, 1) == (True, True)


def test_compiled_requests_link_stages_through_the_snapshot():
    assembled = lab.compile(
        build_assembly(CLONING_ASSEMBLIES, name="sbol-loop-assembly"),
        Manual(),
    )
    outputs = assembled.manifest
    locations = {placement.sample_id: placement.location for placement in outputs.placements}
    assert [
        locations[sample.id]
        for sample in outputs.samples
        if sample.material_identity == CLONING_ASSEMBLIES[0].product.identity
    ] == [Location("products", "A1")]
    assert any(isinstance(step, Transfer) for step in assembled.protocol.steps)
    transformed = lab.compile(
        build_transformation(CLONING_STRAINS, inputs=assembled.manifest, name="heat-shock"),
        Manual(),
    )
    plated = lab.compile(
        build_plating(transformed.manifest),
        Manual(),
    )
    assert plated.manifest.samples
    assert {type(step) for step in plated.protocol.steps} >= {Transfer, Mix}
    for compiled in (assembled, transformed, plated):
        assert compiled.manifest == compiled.protocol.output_manifest()
        assert "protocol.html" in compiled.files
        assert "manifest.json" in compiled.files
        assert "protocol.py" not in compiled.files
    imported = [sample for sample in transformed.protocol.samples if sample.role == "dna"]
    assert {sample.source_sample_id for sample in imported} == {
        sample.id for sample in assembled.manifest.samples
    }
    assert {sample.source_protocol_id for sample in imported} == {assembled.manifest.protocol_id}


def test_transformation_uses_caller_defined_materials():
    transformation = Transformation(
        strain=sbol3.Component("https://example.org/custom_strain", sbol3.SBO_DNA),
        chassis=sbol3.Component("https://example.org/custom_cells", sbol3.SBO_DNA),
        plasmids=[sbol3.Component("https://example.org/custom_plasmid", sbol3.SBO_DNA)],
    )
    compiled = lab.compile(
        build_transformation((transformation,), name="custom-transformation"), Manual()
    )
    assert compiled.protocol.name == "custom-transformation"
    assert compiled.manifest.protocol_id == "custom-transformation"
    assert {sample.material_identity for sample in compiled.manifest.samples} == {
        transformation.strain.identity
    }
    assert {
        sample.material_identity for sample in compiled.protocol.samples if sample.role == "dna"
    } == {transformation.plasmids[0].identity}
    assert all(
        sample.contents[:3] == ("custom_strain", "Competent_Cell_custom_cells", "custom_plasmid")
        for sample in compiled.manifest.samples
    )


def test_assembly_accepts_unversioned_part_iris():
    product = sbol3.Component("https://example.org/design/product", sbol3.SBO_DNA)
    insert = sbol3.Component("https://example.org/parts/reporter", sbol3.SBO_DNA)
    assembly = Assembly(
        product=product,
        backbone=sbol3.Component("https://example.org/parts/backbone", sbol3.SBO_DNA),
        parts=[insert],
        restriction_enzyme=BSAI,
    )
    assert assembly.parts == (insert,)
    compiled = lab.compile(build_assembly((assembly,), name="example-assembly"), Manual())
    assert compiled.protocol.name == "example-assembly"
    outputs = compiled.manifest
    locations = {placement.sample_id: placement.location for placement in outputs.placements}
    assert [
        locations[sample.id]
        for sample in outputs.samples
        if sample.material_identity == product.identity
    ] == [Location("products", "A1")]
    assert any(sample.label == "Restriction Enzyme BsaI" for sample in compiled.protocol.samples)


def test_compiler_accepts_protocols_and_keeps_the_snapshot_internal():
    protocol = build_assembly(CLONING_ASSEMBLIES)
    compiled = lab.compile(protocol, Manual())
    assert compiled.protocol == protocol.snapshot()
    for value in (CLONING_ASSEMBLIES[0], compiled.protocol):
        with pytest.raises(TypeError, match="Compile a Protocol"):
            lab.compile(value, Manual())
    with pytest.raises(TypeError, match="inputs"):
        lab.compile(protocol, Manual(), inputs=compiled.manifest)


def test_cloning_recipes_require_native_sbol_components():
    plasmid = sbol3.Component("https://example.org/plasmid", sbol3.SBO_DNA)
    with pytest.raises(TypeError, match="Parts"):
        Assembly(
            product=plasmid, backbone=plasmid, parts=[plasmid.identity], restriction_enzyme=BSAI
        )
    with pytest.raises(TypeError, match="Strain"):
        Transformation(strain=plasmid.identity, chassis=plasmid, plasmids=[plasmid])
    with pytest.raises(TypeError, match="Plasmids"):
        Transformation(strain=plasmid, chassis=plasmid, plasmids="https://example.org/plasmid")
    with pytest.raises(ValueError, match="nonempty"):
        Transformation(strain=plasmid, chassis=plasmid, plasmids=[])


@pytest.mark.parametrize("designs", [{}, {"assemblies": ASSEMBLIES}, {"strains": STRAINS}])
def test_chained_plan_requires_both_design_inputs(designs):
    with pytest.raises(TypeError, match="required positional argument"):
        golden_gate(**designs)


def test_compiling_a_deck_requires_a_liquid_handler():
    protocol = golden_gate(CLONING_ASSEMBLIES, CLONING_STRAINS)
    with pytest.raises(TypeError, match="deck="):
        lab.compile(protocol, assembly_deck())
    with pytest.raises(TypeError, match="liquid_handler"):
        lab.compile(protocol, deck=assembly_deck())
    with pytest.raises(TypeError):
        lab.compile(protocol, deck=assembly_deck(), liquid_handler="ot2")  # type: ignore[arg-type]


def test_cloning_deck_presets_lower_to_the_same_containers_for_opentrons():
    for liquid_handler in (LiquidHandler.OT2, LiquidHandler.FLEX):
        assert set(lower_deck(assembly_deck(), liquid_handler).labware) == {"reagents", "products"}
        assert set(lower_deck(transformation_deck(), liquid_handler).labware) == {
            "dna",
            "tubes",
            "products",
        }
        assert set(lower_deck(plating_deck(), liquid_handler).labware) == {
            "sources",
            "dilutions",
            "agar",
            "broth",
        }
    with pytest.raises(TypeError):
        lower_deck(assembly_deck(), "ot2")  # type: ignore[arg-type]


def test_ot2_lowering_uses_the_cloning_slots():
    assembly = lower_deck(assembly_deck(), LiquidHandler.OT2, (Decimal(2), Decimal(20)))
    assert assembly.labware["reagents"] == Labware(
        "opentrons_24_aluminumblock_nest_1.5ml_snapcap",
        "1",
        module="temperature module",
    )
    assert assembly.labware["products"].load_name == "nest_96_wellplate_100ul_pcr_full_skirt"
    assert assembly.labware["products"].slot == "thermocycler"
    assert assembly.pipette == "p20_single_gen2"
    assert assembly.tip_racks[0].slot == "2"

    shock = lower_deck(transformation_deck(), LiquidHandler.OT2, (Decimal(2), Decimal(60)))
    assert shock.labware["dna"].slot == "2"
    assert shock.labware["tubes"].slot == "3"
    assert shock.pipette == "p300_single_gen2"
    assert shock.mount == "right"
    assert shock.small_pipette == "p20_single_gen2"
    assert shock.tip_racks[0].slot == "6"
    assert shock.small_tip_racks[0].slot == "9"

    chilled = lower_deck(
        transformation_deck(on_module=True), LiquidHandler.OT2, (Decimal(2), Decimal(60))
    )
    assert chilled.labware["dna"].slot == "1"
    assert chilled.labware["dna"].module == "temperature module"

    plating = lower_deck(
        plating_deck(second_dilution=True, second_agar=True),
        LiquidHandler.OT2,
        (Decimal(2),),
    )
    assert {name: item.slot for name, item in plating.labware.items()} == {
        "sources": "thermocycler",
        "dilutions": "2",
        "agar": "5",
        "broth": "4",
        "dilutions_2": "3",
        "agar_2": "6",
    }
    assert plating.tip_racks[0].slot == "9"
    assert plating.labware["sources"].load_name == "biorad_96_wellplate_200ul_pcr"


def test_compiler_rejects_unsupported_star_preset_equipment():
    with pytest.raises(lab.CompileError, match="No STAR preset.*Lab DeckLayout"):
        lab.compile(
            build_assembly(CLONING_ASSEMBLIES, name="sbol-loop-assembly"),
            deck=assembly_deck(),
            liquid_handler=LiquidHandler.STAR,
        )


def test_chained_plan_compiles_and_conserves_volume():
    bundle = lab.compile(golden_gate(CLONING_ASSEMBLIES, CLONING_STRAINS), Manual())
    initial = sum(fill.volume for resource in bundle.protocol.resources for fill in resource.fills)
    assert sum(volume for _location, volume in bundle.final_volumes) == initial
    transferred = [
        step.volume for step in bundle.protocol.steps if getattr(step, "volume", None) == 2
    ]
    assert transferred
