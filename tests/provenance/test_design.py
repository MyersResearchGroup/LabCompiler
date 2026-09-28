from dataclasses import replace

import pytest

from lab.provenance import (
    Attachment,
    Collection,
    CombinatorialDerivation,
    Component,
    ComponentReference,
    Constraint,
    Cut,
    Document,
    EntireSequence,
    Experiment,
    ExperimentalData,
    ExternallyDefined,
    Implementation,
    Interaction,
    Interface,
    LocalSubComponent,
    Measure,
    Model,
    Participation,
    Range,
    Ref,
    Sequence,
    SequenceFeature,
    SubComponent,
    VariableFeature,
)
from lab.provenance.vocabulary import DNA, INLINE, IUPAC_DNA, OM, PRECEDES, SBOL

NS = "https://example.org/design"


def test_structured_design_locations_interactions_and_derivations_round_trip():
    document = Document(namespace=NS)
    sequence = Sequence(identity=document.iri("sequence"), elements="ACGTACGT", encoding=IUPAC_DNA)
    inner_feature = LocalSubComponent(identity=document.iri("part/feature"), types=(DNA,))
    part = Component(identity=document.iri("part"), types=(DNA,), features=(inner_feature,))
    sub = SubComponent(
        identity=document.iri("design/sub"),
        instance_of=part.ref,
        role_integration=SBOL + "mergeRoles",
        roles=(NS + "/role",),
        locations=(Range(sequence=sequence.ref, start=1, end=4, orientation=INLINE, order=1),),
        source_locations=(EntireSequence(sequence=sequence.ref),),
    )
    feature = SequenceFeature(
        identity=document.iri("design/feature"), locations=(Cut(sequence=sequence.ref, at=4),)
    )
    reference = ComponentReference(in_child_of=sub.ref, refers_to=inner_feature.ref)
    external = ExternallyDefined(types=(DNA,), definition="https://example.org/catalog/item")
    model = Model(
        identity=document.iri("model"),
        source="https://example.org/model.xml",
        language="https://identifiers.org/edam:format_2585",
        framework=NS + "/framework",
    )
    design = Component(
        identity=document.iri("design"),
        types=(DNA,),
        sequences=(sequence.ref,),
        features=(sub, feature, reference, external),
        models=(model.ref,),
        constraints=(Constraint(restriction=PRECEDES, subject=sub.ref, object=feature.ref),),
        interactions=(
            Interaction(
                types=(NS + "/interaction",),
                participations=(Participation(roles=(NS + "/participant",), participant=sub.ref),),
            ),
        ),
        interface=Interface(inputs=(sub.ref,), outputs=(feature.ref,)),
        measures=(Measure(value=4.0, unit=OM + "nanogram"),),
    )
    collection = Collection(identity=document.iri("collection"), members=(part.ref,))
    variants = CombinatorialDerivation(
        identity=document.iri("variants"),
        template=design.ref,
        strategy=SBOL + "enumerate",
        variable_features=(
            VariableFeature(
                cardinality=SBOL + "one",
                variable=sub.ref,
                variants=(part.ref,),
                variant_collections=(collection.ref,),
                variant_measures=(Measure(value=2.0, unit=OM + "one"),),
            ),
        ),
    )
    outer_variants = CombinatorialDerivation(
        identity=document.iri("outer_variants"),
        template=design.ref,
        variable_features=(
            VariableFeature(
                cardinality=SBOL + "one", variable=sub.ref, variant_derivations=(variants.ref,)
            ),
        ),
    )
    attachment = Attachment(
        identity=document.iri("attachment"),
        source="https://example.org/data.csv",
        format="https://identifiers.org/edam:format_3752",
        size=4,
        hash="abcd",
        hash_algorithm="sha256",
    )
    data = ExperimentalData(identity=document.iri("data"), attachments=(attachment.ref,))
    experiment = Experiment(identity=document.iri("experiment"), members=(data.ref,))
    implementation = Implementation(identity=document.iri("implementation"), built=design.ref)
    document.add(
        sequence,
        part,
        design,
        model,
        collection,
        variants,
        outer_variants,
        attachment,
        data,
        experiment,
        implementation,
    )
    frozen = document.freeze()
    native = frozen.to_sbol3()
    assert not native.validate().errors
    assert Document.from_sbol3(native).freeze() == frozen
    assert Document.from_turtle(frozen.to_turtle()).freeze() == frozen
    native_sub = native.find(sub.identity)
    assert native_sub.role_integration == SBOL + "mergeRoles"
    assert list(native_sub.roles) == [NS + "/role"]
    assert native.find(feature.identity).locations[0].at == 4


@pytest.mark.parametrize(
    "location",
    [
        Range(sequence=Ref(NS + "/sequence"), start=0, end=2),
        Range(sequence=Ref(NS + "/sequence"), start=3, end=2),
        Range(sequence=Ref(NS + "/sequence"), start=1, end=5),
        Cut(sequence=Ref(NS + "/sequence"), at=-1),
        Cut(sequence=Ref(NS + "/sequence"), at=5),
    ],
)
def test_locations_are_checked_against_the_referenced_sequence(location):
    document = Document(namespace=NS)
    sequence = Sequence(identity=document.iri("sequence"), elements="ACGT", encoding=IUPAC_DNA)
    document.add(
        sequence,
        Component(
            identity=document.iri("design"),
            types=(DNA,),
            features=(SequenceFeature(locations=(location,)),),
        ),
    )
    with pytest.raises(ValueError, match="sequence-(range|cut)"):
        document.freeze()


def test_constraint_references_must_belong_to_the_containing_component():
    document = Document(namespace=NS)
    first = LocalSubComponent(identity=document.iri("first/feature"), types=(DNA,))
    other = LocalSubComponent(identity=document.iri("other/feature"), types=(DNA,))
    document.add(
        Component(identity=document.iri("other"), types=(DNA,), features=(other,)),
        Component(
            identity=document.iri("first"),
            types=(DNA,),
            features=(first,),
            constraints=(Constraint(restriction=PRECEDES, subject=first.ref, object=other.ref),),
        ),
    )
    with pytest.raises(ValueError, match="feature-scope"):
        document.freeze()


def test_one_owned_object_cannot_belong_to_two_components():
    document = Document(namespace=NS)
    feature = LocalSubComponent(identity=document.iri("shared"), types=(DNA,))
    first = Component(identity=document.iri("first"), types=(DNA,), features=(feature,))
    document.add(first, replace(first, identity=document.iri("second")))
    with pytest.raises(ValueError, match="duplicate-identity"):
        document.freeze()


def test_component_containment_cannot_be_recursive():
    document = Document(namespace=NS)
    document.add(
        Component(
            identity=document.iri("design"),
            types=(DNA,),
            features=(SubComponent(instance_of=Ref(document.iri("design"))),),
        )
    )
    with pytest.raises(ValueError, match="dependency-cycle"):
        document.freeze()


@pytest.mark.parametrize(
    "obj",
    [
        Component(identity=NS + "/a", types=()),
        Component(identity=NS + "/a", types=(DNA, DNA)),
        Sequence(identity=NS + "/a", elements=123, encoding=IUPAC_DNA),
        Component(identity=NS + "/a", types=(DNA,), features=("invalid",)),
        Component(
            identity=NS + "/a",
            types=(DNA,),
            measures=(Measure(value=float("nan"), unit=OM + "one"),),
        ),
    ],
)
def test_invalid_model_field_values_fail_before_serialization(obj):
    with pytest.raises(ValueError):
        Document(namespace=NS).add(obj)
