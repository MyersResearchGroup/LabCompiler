from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from io import StringIO

import pytest

import lab
from lab.execution import OutputRecord, Run, RunMode
from lab.experiments import cloning
from lab.inventory import CountedStock, MaterialForm
from lab.operations import ExternalPreparation, Thermocycle, Transfer
from lab.provenance import Agent, Component, Document, EvidenceState, Implementation
from lab.targets import LiquidHandler, Manual
from tests.cloning_integration_fixture import integrated_case
from tests.planning_fixture import NS


def test_all_cloning_routes_share_material_and_evidence_accounting():
    request, inputs = integrated_case()
    planned = cloning.plan(request, **inputs)
    planned.require_ready()
    assert [task.kind.value for task in planned.tasks] == [
        "preparation",
        "assembly",
        "transformation",
        "plating",
        "plating",
    ]
    assert sum(product.count for product in planned.products) == 2
    assert all(
        product.volume_ul == 0 and product.form is MaterialForm.PLATED_SAMPLE
        for product in planned.products
    )
    experiment = cloning.build(planned)
    assert len(experiment.stages) == 5
    assert experiment.stages[0].external
    assert isinstance(experiment.stages[0].protocol.steps[0], ExternalPreparation)
    transformation = experiment.stages[2]
    assert (
        len([step for step in transformation.protocol.steps if isinstance(step, Thermocycle)]) == 2
    )
    first, second = experiment.stages[-2:]
    assert first.handoffs[0].implementation == second.handoffs[0].implementation
    assert (first.handoffs[0].volume_ul, second.handoffs[0].volume_ul) == (Decimal(5), Decimal(4))
    for task in planned.tasks:
        material = experiment.provenance.resolve(task.output)
        assert material.built is None and material.evidence_state is EvidenceState.PLANNED
    compilation = lab.compile(experiment, Manual())
    assert "Expected output" in compilation.files["methods.md"]


def test_all_method_recipe_and_stock_inputs_round_trip(tmp_path):
    request, inputs = integrated_case()
    assert (
        cloning.CloningMethods.read(inputs["methods"].write(tmp_path / "methods.json"))
        == inputs["methods"]
    )
    assert (
        cloning.CloningSystem.read(inputs["system"].write(tmp_path / "system.json"))
        == inputs["system"]
    )
    inventory = inputs["inventory"]
    assert (
        type(inventory).read(
            inventory.write(tmp_path / "inventory.json"), document=inputs["document"]
        )
        == inventory
    )


def test_missing_preparation_never_becomes_an_implicit_yield():
    request, inputs = integrated_case()
    system = replace(
        inputs["system"],
        recipes=tuple(
            recipe for recipe in inputs["system"].recipes if recipe.kind != "preparation"
        ),
    )
    planned = cloning.plan(request, **{**inputs, "system": system})
    assert not planned.ready
    assert any(requirement.kind.value == "preparation" for requirement in planned.requirements)
    with pytest.raises(ValueError, match="not ready"):
        cloning.build(planned)


def test_counted_stock_is_not_reused_when_repeating_external_preparation():
    _, inputs = integrated_case()
    vector = inputs["document"].get(NS + "/vector", Component)
    request = cloning.BuildRequest(
        identity=NS + "/repeat_preparation",
        targets=(cloning.BuildTarget(design=vector.ref, volume_ul=Decimal(11)),),
    )
    planned = cloning.plan(request, **inputs)
    assert not planned.ready
    assert (
        sum(
            allocation.count
            for allocation in planned.allocations
            if allocation.stock == NS + "/stab_stock"
        )
        == 1
    )
    assert sum(requirement.count for requirement in planned.requirements) == 1
    assert all(requirement.volume_ul == 0 for requirement in planned.requirements)
    assert planned.acquisitions[0].count == 1


def test_counted_output_handoff_can_feed_an_explicit_external_procedure():
    _, inputs = integrated_case()
    doc = Document.from_snapshot(inputs["document"])
    strain = doc.get(NS + "/strain", Component)
    product = Component(identity=NS + "/extracted", types=("https://example.org/dna",))
    doc.add(product)
    method = cloning.ExternalPreparationMethod(
        identity=NS + "/extract_method",
        procedure=NS + "/extract_sop",
        instructions="Apply the supplied external test procedure; record the output.",
        source_count=1,
        output_volume_ul=Decimal(5),
    )
    recipe = cloning.ExternalPreparationRecipe(
        identity=NS + "/extract_recipe",
        product=product.ref,
        source=strain.ref,
        source_form=MaterialForm.PLATED_SAMPLE,
        output_form=MaterialForm.DNA,
        method=method.identity,
    )
    request = cloning.BuildRequest(
        identity=NS + "/extract_request",
        targets=(cloning.BuildTarget(design=product.ref, volume_ul=Decimal(2)),),
    )
    planned = cloning.plan(
        request,
        **{
            **inputs,
            "document": doc.freeze(),
            "system": replace(inputs["system"], recipes=(*inputs["system"].recipes, recipe)),
            "methods": replace(
                inputs["methods"], preparations=(*inputs["methods"].preparations, method)
            ),
        },
    )
    planned.require_ready()
    experiment = cloning.build(planned)
    stage = experiment.stages[-1]
    assert stage.external and stage.handoffs[0].count == 1
    assert stage.handoffs[0].volume_ul == 0
    compilation = lab.compile(experiment, Manual())
    assert "1 unit(s)" in compilation.files["methods.md"]
    with pytest.raises(ValueError, match="volume|count"):
        replace(
            experiment,
            stages=(
                *experiment.stages[:-1],
                replace(stage, handoffs=(replace(stage.handoffs[0], count=2),)),
            ),
        )


@pytest.mark.integration
@pytest.mark.parametrize("handler", [LiquidHandler.OT2, LiquidHandler.FLEX])
def test_integrated_workflow_runs_each_robot_stage_in_official_simulator(handler):
    simulator = pytest.importorskip("opentrons.simulate")
    request, inputs = integrated_case(count=1)
    experiment = cloning.build(cloning.plan(request, **inputs))
    compilation = lab.compile(experiment, handler)
    assert compilation.stages[0].target.name == "Manual"
    for stage in compilation.stages[1:]:
        simulator.simulate(StringIO(stage.files["protocol.py"]))
        assert len(stage.source_map) == len(stage.protocol.steps)
    assert ".bottom(z=2)" in compilation.stages[-1].files["protocol.py"]
    assert (
        compilation.files["protocol.labop.ttl"]
        == lab.compile(experiment, Manual()).files["protocol.labop.ttl"]
    )


@pytest.mark.integration
async def test_integrated_star_preview_carries_temperature_and_spot_height():
    backends = pytest.importorskip("pylabrobot.liquid_handling.backends")
    resources = pytest.importorskip("pylabrobot.resources")
    events = []

    class Recorder(backends.SerializingBackend):
        async def send_command(self, command, data=None):
            events.append((command, data))

    async def thermal(plate, *, profile, cycles, lid_temperature):
        events.append(("thermal", profile))

    async def hold(plate, *, temperature):
        events.append(("hold", temperature))

    request, inputs = integrated_case(count=1)
    compilation = lab.compile(cloning.build(cloning.plan(request, **inputs)), LiquidHandler.STAR)
    resources.set_volume_tracking(True)
    try:
        for stage in compilation.stages[1:]:
            namespace = {"__name__": "_generated"}
            exec(compile(stage.files["protocol.py"], "preview.py", "exec"), namespace)
            await namespace["run"](
                Recorder(num_channels=8),
                **(
                    {"thermocycle": thermal, "set_temperature": hold}
                    if any(isinstance(step, Thermocycle) for step in stage.protocol.steps)
                    else {}
                ),
            )
    finally:
        resources.set_volume_tracking(False)
    assert ("hold", 25) in events
    spots = [
        data
        for command, data in events
        if command == "dispense"
        and any(channel.get("liquid_height") == 2 for channel in data["channels"])
    ]
    assert spots


def test_external_material_accounting_rejects_overdrawn_counts_and_yields():
    request, inputs = integrated_case(count=1)
    experiment = cloning.build(cloning.plan(request, **inputs))
    stage = experiment.stages[0]
    step = stage.protocol.steps[0]
    overdraw = replace(step, inputs=(replace(step.inputs[0], count=2),))
    with pytest.raises(ValueError, match="available material count"):
        replace(
            experiment,
            stages=(
                replace(stage, protocol=replace(stage.protocol, steps=(overdraw,))),
                *experiment.stages[1:],
            ),
        )
    overflow = replace(step, outputs=(replace(step.outputs[0], volume_ul=Decimal(101)),))
    with pytest.raises(ValueError, match="capacity"):
        replace(
            experiment,
            stages=(
                replace(stage, protocol=replace(stage.protocol, steps=(overflow,))),
                *experiment.stages[1:],
            ),
        )
    with pytest.raises(ValueError, match="positive volume or count"):
        replace(step.inputs[0], volume_ul=Decimal(1))
    pipetting = Transfer(step.inputs[0].location, step.outputs[0].location, Decimal(1), step.origin)
    with pytest.raises(ValueError, match="Counted material"):
        replace(
            experiment,
            stages=(
                replace(stage, protocol=replace(stage.protocol, steps=(pipetting,))),
                *experiment.stages[1:],
            ),
        )


def test_count_targets_reuse_recorded_inventory_without_claiming_colonies():
    request, inputs = integrated_case(count=2)
    doc = Document.from_snapshot(inputs["document"])
    design = request.targets[0].design
    material = Implementation(
        identity=NS + "/existing_spots",
        derived_from=(design,),
        evidence_state=EvidenceState.RECORDED,
    )
    doc.add(material)
    stock = CountedStock(
        identity=NS + "/spot_stock",
        design=design,
        implementation=material.ref,
        form=MaterialForm.PLATED_SAMPLE,
        count=2,
    )
    planned = cloning.plan(
        request,
        **{
            **inputs,
            "document": doc.freeze(),
            "inventory": replace(inputs["inventory"], stocks=(*inputs["inventory"].stocks, stock)),
        },
    )
    assert planned.ready and not planned.tasks
    assert planned.products[0].implementation == material.ref and planned.products[0].count == 2
    assert not doc.freeze().resolve(material.ref).built


def test_counted_outputs_require_explicit_observations_and_preserve_units():
    request, inputs = integrated_case(count=1)
    compilation = lab.compile(cloning.build(cloning.plan(request, **inputs)), Manual())
    planned = compilation.stages[-1].manifest.samples[0].implementation
    start = datetime(2026, 9, 27, tzinfo=UTC)
    person = Agent(identity=NS + "/operator")
    run = Run(
        compilation,
        identity=NS + "/count_run",
        mode=RunMode.PHYSICAL,
        agent=person,
        started_at=start,
    )
    output = OutputRecord(
        planned=planned, identity=NS + "/observed_spot", observed_at=start, count=1
    )
    run.record_output(output)
    record = run.finalize(ended_at=start)
    assert not record.complete
    observed = record.provenance.get(output.identity, Implementation)
    assert observed.built is None and observed.evidence_state is EvidenceState.RECORDED
    assert observed.measures[0].value == 1 and observed.measures[0].unit.endswith("/one")
    assert record.provenance.resolve(planned).evidence_state is EvidenceState.PLANNED
    assert "count 1" in record.files["methods.md"]
    other = Run(
        compilation,
        identity=NS + "/wrong_units",
        mode=RunMode.PHYSICAL,
        agent=person,
        started_at=start,
    )
    other.record_output(replace(output, count=None, volume_ul=Decimal(1)))
    with pytest.raises(ValueError, match="quantity kind"):
        other.finalize(ended_at=start)


def test_external_preparation_needs_matching_quantity_kinds_and_explicit_target():
    request, inputs = integrated_case(count=1)
    method = inputs["methods"].preparations[0]
    wrong = replace(method, source_count=0, source_volume_ul=Decimal(1))
    with pytest.raises(ValueError, match="quantity kinds"):
        cloning.plan(
            request, **{**inputs, "methods": replace(inputs["methods"], preparations=(wrong,))}
        )
    experiment = cloning.build(cloning.plan(request, **inputs))
    targets = {stage.identity: stage.deck for stage in experiment.stages}
    with pytest.raises(ValueError, match="Manual targets"):
        lab.compile(experiment, targets, liquid_handler=LiquidHandler.OT2)
