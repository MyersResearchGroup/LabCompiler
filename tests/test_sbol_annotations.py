from dataclasses import replace

import pytest
import sbol3

import lab
from examples.cloning import ASSEMBLIES, STRAINS
from examples.sbol_provenance import inputs
from lab.experiments.cloning import AssemblyRequest, PlatingRequest, TransformationRequest
from lab.samples import Sample


def test_native_sbol_provenance_and_compiled_annotations(tmp_path):
    document, protocol = inputs()
    assert not document.validate().errors
    design = document.find("https://example.org/aliquot/design")
    material = sbol3.Implementation("https://example.org/aliquot/stock", built=design)
    document.add(material)
    sample = replace(protocol.snapshot().samples[0], implementation=material.identity)
    assert sample.implementation == material.identity
    result = lab.compile(protocol, to=None)
    output = result.manifest.to_dict()["outputs"][0]
    assert output["design"] == design.identity
    assert output["implementation"] is None
    assert output["parent_sample_ids"] == ["source"]

    document.write(str(tmp_path / "designs.ttl"), sbol3.TURTLE)
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


def test_cloning_preserves_design_iris_across_stage_manifests():
    assembly = lab.compile(AssemblyRequest(id="assembly", assemblies=ASSEMBLIES[:1]), to=None)
    assert assembly.manifest.samples[0].design == ASSEMBLIES[0].product.iri
    assert any(s.design == ASSEMBLIES[0].backbone.iri for s in assembly.protocol.samples)
    transformation = lab.compile(
        TransformationRequest(id="transformation", transformations=STRAINS[:1]),
        inputs=assembly.manifest,
        to=None,
    )
    assert all(s.design == STRAINS[0].strain.iri for s in transformation.manifest.samples)
    imported = [s for s in transformation.protocol.samples if s.source_sample_id]
    assert imported[0].design == ASSEMBLIES[0].product.iri
    plated = lab.compile(
        PlatingRequest(
            id="plating", sample_ids=tuple(s.id for s in transformation.manifest.samples)
        ),
        inputs=transformation.manifest,
        to=None,
    )
    assert all(s.design == STRAINS[0].strain.iri for s in plated.manifest.samples)
    assert all(s.implementation is None for s in plated.manifest.samples)


@pytest.mark.parametrize("field", ["design", "implementation"])
@pytest.mark.parametrize("identity", ["relative", "https://", "https://example.org/has space", 42])
def test_sample_annotations_require_absolute_identity_strings(field, identity):
    with pytest.raises(ValueError, match="absolute IRI"):
        Sample(id="s", label="DNA", material_identity="DNA", **{field: identity})


def test_sample_design_must_match_material_identity():
    with pytest.raises(ValueError, match="match its design"):
        Sample(id="s", label="DNA", material_identity="DNA", design="https://example.org/design")
