from dataclasses import FrozenInstanceError, replace
from typing import get_type_hints

import pytest
import sbol3

import lab.provenance as provenance
from lab.provenance import Activity, Component, Document, Ref, Sequence, Usage
from lab.provenance.vocabulary import DNA, IUPAC_DNA

NS = "https://example.org/provenance"


def test_freezing_assigns_owned_ids_without_mutating_authored_objects():
    document = Document(namespace=NS)
    component = Component(identity=document.iri("design"), types=(DNA,))
    activity = Activity(identity=document.iri("build"), usage=(Usage(entity=component.ref),))
    document.add(activity, component)
    frozen = document.freeze()
    owned = frozen.resolve(activity.ref).usage[0]
    assert owned.identity == NS + "/build/Usage1"
    assert activity.usage[0].identity is None
    with pytest.raises(ValueError, match="Assign an identity"):
        _ = activity.usage[0].ref
    assert frozen.resolve(owned.ref) == owned
    with pytest.raises(FrozenInstanceError):
        owned.name = "changed"
    assert document.freeze() == frozen
    document.add(Activity(identity=document.iri("later")))
    assert len(frozen.objects) == 2


def test_owned_identity_allocation_preserves_explicit_ids_and_skips_collisions():
    document = Document(namespace=NS)
    entity = Component(identity=document.iri("design"), types=(DNA,))
    activity = Activity(
        identity=document.iri("build"),
        usage=(
            Usage(entity=entity.ref),
            Usage(identity=document.iri("build/Usage1"), entity=entity.ref),
        ),
    )
    document.add(entity, activity)
    assert [usage.identity for usage in document.freeze().resolve(activity.ref).usage] == [
        NS + "/build/Usage1",
        NS + "/build/Usage2",
    ]


def test_add_is_atomic_and_conflicting_definitions_are_rejected():
    document = Document(namespace=NS)
    design = Component(identity=document.iri("design"), types=(DNA,))
    document.add(design, design)
    with pytest.raises(ValueError, match="Conflicting definition"):
        document.add(Activity(identity=document.iri("unused")), replace(design, name="different"))
    assert document.objects == (design,)
    with pytest.raises(ValueError, match="TopLevel"):
        document.add(Usage(entity=design.ref))


def test_reference_lookup_and_closure_are_explicit():
    document = Document(namespace=NS)
    sequence = Sequence(identity=document.iri("sequence"), elements="ACGT", encoding=IUPAC_DNA)
    design = Component(identity=document.iri("design"), types=(DNA,), sequences=(sequence.ref,))
    document.add(design)
    assert not document.validate().is_valid
    with pytest.raises(ValueError, match="unresolved-reference"):
        document.freeze()
    external = document.freeze(allow_external=True)
    with pytest.raises(KeyError):
        external.resolve(sequence.ref)
    document.add(sequence)
    frozen = document.freeze()
    assert frozen.get(sequence.identity, Sequence) == frozen.resolve(sequence.ref)
    with pytest.raises(TypeError, match="not Sequence"):
        frozen.get(design.identity, Sequence)
    assert Document.from_snapshot(frozen).freeze() == frozen


def test_no_process_global_namespace_and_native_documents_are_detached():
    previous = sbol3.get_namespace()
    document = Document(namespace=NS)
    design = Component(identity=document.iri("design"), types=(DNA,))
    document.add(design)
    frozen = document.freeze()
    native = frozen.to_sbol3()
    native.find(design.identity).name = "Modified elsewhere"
    assert frozen.resolve(design.ref).name is None
    assert document.to_sbol3().find(design.identity).name is None
    assert sbol3.get_namespace() == previous


def test_digest_is_independent_of_object_insertion_and_rdf_statement_order():
    first, second = Document(namespace=NS), Document(namespace=NS)
    a = Activity(identity=first.iri("a"))
    b = Activity(identity=first.iri("b"))
    first.add(a, b)
    second.add(b, a)
    assert first.freeze().digest == second.freeze().digest
    reversed_rdf = "\n".join(reversed(first.freeze().to_turtle().splitlines()))
    assert Document.from_turtle(reversed_rdf).freeze().digest == first.freeze().digest


def test_local_io_is_reproducible_and_refuses_accidental_replacement(tmp_path):
    document = Document(namespace=NS)
    document.add(Activity(identity=document.iri("a")))
    path = tmp_path / "nested/provenance.ttl"
    document.write(path)
    document.write(path)
    restored = Document.read(path)
    assert restored.freeze() == document.freeze()
    document.add(Activity(identity=document.iri("b")))
    with pytest.raises(FileExistsError):
        document.write(path)
    assert Document.read(path).freeze() == restored.freeze()


@pytest.mark.parametrize("value", ["relative", "", "https:///path", "https://example.org/bad name"])
def test_refs_require_absolute_iris(value):
    with pytest.raises(ValueError, match="absolute IRI"):
        Ref(value)


@pytest.mark.parametrize("value", ["bad-name", "../escape", "123", "a//b", "", "a#b"])
def test_document_iris_are_explicit_sbol_display_ids(value):
    with pytest.raises(ValueError, match="display IDs"):
        Document(namespace=NS).iri(value)


def test_every_public_model_has_runtime_resolvable_annotations():
    for name in provenance.__all__:
        cls = getattr(provenance, name)
        if isinstance(cls, type) and hasattr(cls, "__dataclass_fields__"):
            get_type_hints(cls)


def test_mutable_collections_are_rejected_at_construction():
    with pytest.raises(TypeError, match="immutable"):
        Component(identity=NS + "/design", types=[DNA])


def test_mixed_namespaces_need_an_explicit_authoring_namespace_on_import():
    document = Document(namespace=NS)
    document.add(Activity(identity=NS + "/a"), Activity(identity="https://elsewhere.org/b"))
    rdf = document.freeze().to_turtle()
    with pytest.raises(ValueError, match="multiple namespaces"):
        Document.from_turtle(rdf)
    assert Document.from_turtle(rdf, namespace=NS).freeze() == document.freeze()
