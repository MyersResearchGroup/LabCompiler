from dataclasses import replace

import pytest
import sbol3

import lab
from examples.cloning import ASSEMBLIES, STRAINS
from examples.cloning import designs as cloning_designs
from examples.sbol_provenance import inputs
from examples.transformation import designs as transformation_designs
from lab.experiments.cloning import (
    Assembly,
    Transformation,
    build_assembly,
    build_plating,
    build_transformation,
)
from lab.samples import Sample


def test_native_sbol_provenance_and_compiled_annotations(tmp_path):
    designs, protocol = inputs()
    assert not designs.validate().errors
    design = designs.find("https://example.org/aliquot/design")
    material = sbol3.Implementation("https://example.org/aliquot/stock", built=design)
    designs.add(material)
    sample = replace(protocol.snapshot().samples[0], implementation=material.identity)
    assert sample.implementation == material.identity
    result = lab.compile(protocol, to=None)
    output = result.manifest.to_dict()["outputs"][0]
    assert output["design"] == design.identity
    assert output["implementation"] is None
    assert output["parent_sample_ids"] == ["source"]

    designs.write(str(tmp_path / "designs.ttl"), sbol3.TURTLE)
    restored = sbol3.Document()
    restored.read(str(tmp_path / "designs.ttl"))
    assert not restored.validate().errors
    assert list(restored.find(design.identity).generated_by) == list(design.generated_by)
    activity = restored.find(design.generated_by[0])
    assert activity.usage[0].entity == design.sequences[0]
    assert activity.association[0].agent == "https://example.org/aliquot/author"
    assert restored.find(sample.implementation).built == design.identity

    plan = result.plan_json
    design.name = "Edited design metadata"
    material.built = "https://example.org/another_design"
    protocol.wait(1 * lab.seconds)
    assert result.plan_json == plan
    assert result.manifest.samples[0].design == design.identity


def test_cloning_uses_imported_sbol_designs_across_stage_manifests(tmp_path):
    cloning_designs.write(str(tmp_path / "designs.ttl"), sbol3.TURTLE)
    designs = sbol3.Document()
    designs.read(str(tmp_path / "designs.ttl"))
    assert not designs.validate().errors
    original = ASSEMBLIES[0]
    recipe = Assembly(
        product=designs.find(original.product.identity),
        backbone=designs.find(original.backbone.identity),
        parts=[designs.find(part.identity) for part in original.parts],
        restriction_enzyme=designs.find(original.restriction_enzyme.identity),
    )
    recipe.product.name = "Imported SBOL product name"
    protocol = build_assembly((recipe,))
    recipe.product.name = "Edited after building the protocol"
    assembly = lab.compile(protocol, to=None)
    assert assembly.manifest.samples[0].label == "Imported SBOL product name"
    assert assembly.manifest.samples[0].design == ASSEMBLIES[0].product.identity
    assert any(s.design == ASSEMBLIES[0].backbone.identity for s in assembly.protocol.samples)
    strain = STRAINS[0]
    transformation_recipe = Transformation(
        strain=designs.find(strain.strain.identity),
        chassis=designs.find(strain.chassis.identity),
        plasmids=[recipe.product],
    )
    protocol = build_transformation((transformation_recipe,), inputs=assembly.manifest)
    transformation = lab.compile(protocol, to=None)
    assert all(s.design == STRAINS[0].strain.identity for s in transformation.manifest.samples)
    imported = [s for s in transformation.protocol.samples if s.source_sample_id]
    assert imported[0].design == ASSEMBLIES[0].product.identity
    plated = lab.compile(
        build_plating(transformation.manifest),
        to=None,
    )
    assert all(s.design == STRAINS[0].strain.identity for s in plated.manifest.samples)
    assert all(s.implementation is None for s in plated.manifest.samples)
    for result in (assembly, transformation, plated):
        assert all(designs.find(s.design) is not None for s in result.manifest.samples)


@pytest.mark.parametrize("designs", [cloning_designs, transformation_designs])
def test_experiment_examples_use_valid_native_sbol_designs(designs):
    assert not designs.validate().errors


@pytest.mark.parametrize("field", ["design", "implementation"])
@pytest.mark.parametrize("identity", ["relative", "https://", "https://example.org/has space", 42])
def test_sample_annotations_require_absolute_identity_strings(field, identity):
    with pytest.raises(ValueError, match="absolute IRI"):
        Sample(id="s", label="DNA", material_identity="DNA", **{field: identity})


def test_sample_design_must_match_material_identity():
    with pytest.raises(ValueError, match="match its design"):
        Sample(id="s", label="DNA", material_identity="DNA", design="https://example.org/design")
