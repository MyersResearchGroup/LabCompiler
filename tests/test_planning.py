import json
from dataclasses import replace
from decimal import Decimal

import pytest

from lab import uL
from lab.experiments.cloning.domestication import propose_edits
from lab.experiments.cloning.methods import CloningMethods
from lab.experiments.cloning.planning import (
    BuildTarget,
    PlanningPolicy,
    RequirementKind,
    TaskKind,
    plan,
)
from lab.experiments.cloning.sequences import calculate_assembly, digest_fragments
from lab.experiments.cloning.systems import CloningSystem, FragmentSelection
from lab.inventory import Inventory, MaterialForm, Stock
from lab.provenance import Component, Document, EvidenceState, Implementation, Sequence
from tests.planning_fixture import NS, planning_case


def test_explicit_fragments_calculate_product_and_computational_provenance():
    _, inputs = planning_case()
    recipe = inputs["system"].recipes[0]
    fragments = digest_fragments(
        recipe.fragments[1].component, document=inputs["document"], enzyme="EcoRI"
    )
    assert [
        (f.selection.left_cut, f.selection.right_cut, f.watson, f.crick, f.overhang)
        for f in fragments
    ] == [
        (None, 3, "GGG", "AATTCCC", 0),
        (3, 12, "AATTCCCCG", "AATTCGGGG", -4),
        (12, None, "AATTCGG", "CCG", -4),
    ]
    calculated = calculate_assembly(recipe, document=inputs["document"])
    assert calculated.sequence.elements == "AATTCTTTTAAAAGAATTCCCCG"
    assert calculated.activity.evidence_state is EvidenceState.RECORDED
    doc = Document.from_snapshot(inputs["document"])
    doc.add(*calculated.objects)
    assert not doc.to_sbol3().validate().errors


def test_planner_allocates_specific_implementations_and_emits_planned_outputs(tmp_path):
    request, inputs = planning_case()
    build = plan(request, **inputs)
    build.require_ready()
    assert len(build.tasks) == 1
    task = build.tasks[0]
    assert task.kind is TaskKind.ASSEMBLY
    assert len(task.inputs) == 4
    assert sum(item.volume_ul for item in task.inputs) == 5
    assert all(item.stock is not None for item in task.inputs)
    output = build.document.resolve(task.output)
    assert output.built is None and output.evidence_state is EvidenceState.PLANNED
    assert sum(item.volume_ul for item in build.products) == 3
    assert inputs["inventory"].stocks[0].quantity_ul == 10
    assert plan(request, **inputs).digest == build.digest
    path = build.write(tmp_path)
    assert (
        json.loads((path / "build.json").read_text())["provenance_sha256"] == build.document.digest
    )
    assert Document.read(path / "provenance.ttl").freeze().digest == build.document.digest


def test_inventory_is_reused_before_assembling():
    request, inputs = planning_case()
    doc = Document.from_snapshot(inputs["document"])
    implementation = Implementation(
        identity=NS + "/existing",
        derived_from=(request.targets[0].design,),
        evidence_state=EvidenceState.RECORDED,
    )
    doc.add(implementation)
    inventory = Inventory(
        identity=NS + "/available",
        stocks=(
            *inputs["inventory"].stocks,
            Stock(
                identity=NS + "/available_stock",
                implementation=implementation.ref,
                design=request.targets[0].design,
                form=MaterialForm.DNA,
                quantity=20 * uL,
            ),
        ),
    )
    build = plan(request, **{**inputs, "document": doc.freeze(), "inventory": inventory})
    assert build.ready and build.tasks == ()
    assert build.products[0].implementation == implementation.ref


def test_multiple_batches_do_not_double_count_inventory_or_reuse_output_identity():
    request, inputs = planning_case(volume="11", stock_volume="3")
    build = plan(request, **inputs)
    assert sum(task.kind is TaskKind.ASSEMBLY for task in build.tasks) == 3
    assert len({task.output for task in build.tasks}) == len(build.tasks)
    # Three batches require six microlitres of water; inventory contains three.
    water = [item for item in build.requirements if item.design.identity == NS + "/water"]
    assert sum(item.volume_ul for item in water) == 3
    for stock in inputs["inventory"].stocks:
        assert (
            sum(item.volume_ul for item in build.allocations if item.stock == stock.identity)
            <= stock.quantity_ul
        )
    with pytest.raises(ValueError, match="not ready"):
        build.require_ready()


def test_wrong_material_form_requires_preparation():
    request, inputs = planning_case()
    original = inputs["inventory"].stocks
    stocks = tuple(
        Stock(
            identity=stock.identity,
            implementation=stock.implementation,
            design=stock.design,
            form=MaterialForm.CULTURE if stock.design.identity == NS + "/vector" else stock.form,
            quantity=stock.quantity_ul * uL,
        )
        for stock in original
    )
    build = plan(
        request, **{**inputs, "inventory": Inventory(identity=NS + "/other", stocks=stocks)}
    )
    assert any(item.kind is RequirementKind.PREPARATION for item in build.requirements)


def test_invalid_fragment_selection_does_not_silently_choose_another_fragment():
    request, inputs = planning_case()
    recipe = inputs["system"].recipes[0]
    invalid = replace(
        recipe, fragments=(replace(recipe.fragments[0], left_cut=4), *recipe.fragments[1:])
    )
    build = plan(request, **{**inputs, "system": replace(inputs["system"], recipes=(invalid,))})
    assert not build.ready
    assert any("does not select exactly one" in item.message for item in build.requirements)


def test_route_tie_breaking_is_stable_and_search_limits_are_explicit():
    request, inputs = planning_case()
    recipe = inputs["system"].recipes[0]
    alternate = replace(recipe, identity=NS + "/z_recipe")
    system = replace(inputs["system"], recipes=(alternate, recipe))
    build = plan(request, **{**inputs, "system": system})
    assert build.tasks[0].recipe.identity == recipe.identity
    with pytest.raises(ValueError, match="max_states"):
        plan(request, **inputs, policy=PlanningPolicy(max_states=1))


def test_edits_are_proposals_until_explicitly_applied_to_a_new_design():
    _, inputs = planning_case()
    document = inputs["document"]
    component = document.get(NS + "/insert", Component)
    proposals = propose_edits(
        component.ref, document=document, enzyme="EcoRI", editable_positions=(3,)
    )
    assert proposals
    original = document.resolve(component.sequences[0])
    edited = proposals[0].apply(document, identity=NS + "/selected_edit")
    assert document.resolve(component.sequences[0]) == original
    assert edited.get(NS + "/selected_edit/sequence", Sequence).elements != original.elements
    assert edited.get(NS + "/selected_edit", Component).derived_from == (component.ref,)
    assert len(edited.objects) == len(document.objects) + 3


def test_inventory_round_trip_and_recorded_evidence_requirement(tmp_path):
    _, inputs = planning_case()
    inventory = inputs["inventory"]
    path = inventory.write(tmp_path / "inventory.json")
    assert Inventory.read(path, document=inputs["document"]) == inventory
    implementation = Implementation(identity=NS + "/planned", evidence_state=EvidenceState.PLANNED)
    doc = Document.from_snapshot(inputs["document"])
    doc.add(implementation)
    stock = Stock(
        identity=NS + "/stock",
        implementation=implementation.ref,
        design=inventory.stocks[0].design,
        form=MaterialForm.DNA,
        quantity=1 * uL,
    )
    with pytest.raises(ValueError, match="recorded"):
        Inventory(identity=NS + "/inventory", stocks=(stock,)).validate(doc.freeze())


def test_stock_quantities_are_normalized_and_detached_from_mutable_pint_values():
    _, inputs = planning_case()
    source = inputs["inventory"].stocks[0]
    quantity = 10 * uL
    stock = Stock(
        identity=source.identity,
        implementation=source.implementation,
        design=source.design,
        form=source.form,
        quantity=quantity,
    )
    quantity *= 2
    assert stock.quantity_ul == Decimal(10)


def test_multilevel_assembly_consumes_a_planned_intermediate():
    request, inputs = planning_case()
    doc = Document.from_snapshot(inputs["document"])
    intermediate = doc.get(NS + "/target", Component)
    final = replace(intermediate, identity=NS + "/final")
    doc.add(final)
    first = inputs["system"].recipes[0]
    second = replace(
        first,
        identity=NS + "/second_recipe",
        product=final.ref,
        fragments=(
            first.fragments[0],
            FragmentSelection(component=intermediate.ref, left_cut=14, right_cut=0),
        ),
    )
    request = replace(request, targets=(BuildTarget(design=final.ref, volume_ul=Decimal(3)),))
    build = plan(
        request,
        **{
            **inputs,
            "document": doc.freeze(),
            "system": replace(inputs["system"], recipes=(first, second)),
        },
    )
    build.require_ready()
    assert len(build.tasks) == 2
    assert build.tasks[1].depends_on == (build.tasks[0].identity,)
    assert any(item.implementation == build.tasks[0].output for item in build.tasks[1].inputs)
    assert sum(item.volume_ul for item in build.products) == 3


def test_route_search_preserves_scarce_inventory_for_another_target():
    request, inputs = planning_case()
    doc = Document.from_snapshot(inputs["document"])
    vector = doc.get(NS + "/vector", Component)
    other_vector = replace(vector, identity=NS + "/other_vector")
    other_target = replace(doc.get(NS + "/target", Component), identity=NS + "/other_target")
    stock_impl = Implementation(
        identity=NS + "/other_vector_impl",
        derived_from=(other_vector.ref,),
        evidence_state=EvidenceState.RECORDED,
    )
    doc.add(other_vector, other_target, stock_impl)
    stocks = tuple(
        Stock(
            identity=stock.identity,
            implementation=stock.implementation,
            design=stock.design,
            form=stock.form,
            quantity=(1 if stock.design == vector.ref else 10) * uL,
        )
        for stock in inputs["inventory"].stocks
    )
    alternate_stock = Stock(
        identity=NS + "/other_vector_stock",
        implementation=stock_impl.ref,
        design=other_vector.ref,
        form=MaterialForm.DNA,
        quantity=1 * uL,
    )
    first = inputs["system"].recipes[0]
    alternative = replace(
        first,
        identity=NS + "/z_alternative",
        fragments=(replace(first.fragments[0], component=other_vector.ref), first.fragments[1]),
    )
    second = replace(first, identity=NS + "/second", product=other_target.ref)
    build = plan(
        replace(
            request,
            targets=(*request.targets, BuildTarget(design=other_target.ref, volume_ul=Decimal(3))),
        ),
        **{
            **inputs,
            "document": doc.freeze(),
            "inventory": Inventory(identity=NS + "/scarce", stocks=(*stocks, alternate_stock)),
            "system": replace(inputs["system"], recipes=(first, alternative, second)),
        },
    )
    build.require_ready()
    assert build.tasks[0].recipe.identity == alternative.identity
    assert build.tasks[1].recipe.identity == second.identity


def test_typed_recipe_and_method_files_round_trip(tmp_path):
    _, inputs = planning_case()
    system = inputs["system"]
    methods = inputs["methods"]
    assert CloningSystem.read(system.write(tmp_path / "system.json")) == system
    assert CloningMethods.read(methods.write(tmp_path / "methods.json")) == methods


def test_authoring_can_add_frozen_objects_idempotently_and_explicitly_revise():
    _, inputs = planning_case()
    doc = Document.from_snapshot(inputs["document"])
    authored = Component(identity=NS + "/new", types=("https://example.org/type",))
    doc.add(authored)
    snapshot = doc.freeze()
    doc = Document.from_snapshot(snapshot)
    doc.add(authored)
    assert doc.freeze().digest == snapshot.digest
    revised = replace(authored, name="A deliberate revision")
    with pytest.raises(ValueError, match="Conflicting"):
        doc.add(revised)
    doc.replace(revised)
    assert doc.freeze().get(authored.identity, Component).name == revised.name
    assert snapshot.get(authored.identity, Component).name is None
