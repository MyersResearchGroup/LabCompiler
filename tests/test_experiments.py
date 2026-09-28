import builtins
import hashlib
import json
from dataclasses import replace
from decimal import Decimal
from io import StringIO

import pytest

import lab
from lab import Protocol, seconds, uL
from lab.experiments import cloning
from lab.experiments.cloning.decks import assembly_deck
from lab.labop import export
from lab.operations import Origin
from lab.provenance import Document, EvidenceState, Implementation
from lab.targets import LiquidHandler, Manual
from tests.planning_fixture import multilevel_case, planning_case


def test_build_freezes_protocol_and_material_identity_before_hardware_selection(tmp_path):
    request, inputs = planning_case()
    experiment = cloning.build(cloning.plan(request, **inputs))
    artifact = lab.compile(experiment, Manual())
    assert all(
        sample.design and sample.implementation for sample in artifact.stages[0].protocol.samples
    )
    assert all(step.identity for step in artifact.stages[0].protocol.steps)
    files = artifact.files
    build_data = json.loads(files["build.json"])
    assert (
        hashlib.sha256(files["inputs/build-provenance.ttl"].encode()).hexdigest()
        == (build_data["provenance_sha256"])
    )
    manifest = json.loads(files["bundle.json"])
    for name, checksum in manifest["sha256"].items():
        assert hashlib.sha256(files[name].encode()).hexdigest() == checksum
    path = artifact.write(tmp_path)
    assert Document.read(path / "provenance.ttl").freeze().digest == experiment.provenance.digest
    assert artifact.write(tmp_path) == path
    assert "Planned methods" in files["methods.md"]
    outputs = tuple(
        obj
        for obj in experiment.provenance.objects
        if isinstance(obj, Implementation) and obj.evidence_state is EvidenceState.PLANNED
    )
    assert outputs and all(obj.built is None for obj in outputs)


def test_multilevel_handoffs_use_remaining_physical_volume():
    request, inputs = multilevel_case()
    experiment = cloning.build(cloning.plan(request, **inputs))
    assert len(experiment.stages) == 3
    first, second, third = experiment.stages
    assert second.depends_on == third.depends_on == (first.identity,)
    assert second.handoffs[0].implementation == third.handoffs[0].implementation
    assert second.handoffs[0].volume_ul == Decimal(5)
    assert third.handoffs[0].volume_ul == Decimal(4)
    with pytest.raises(ValueError, match="requires an upstream"):
        replace(experiment, stages=(first, replace(second, handoffs=()), third))
    with pytest.raises(ValueError, match="volume"):
        replace(
            experiment,
            stages=(
                first,
                second,
                replace(third, handoffs=(replace(third.handoffs[0], volume_ul=Decimal(5)),)),
            ),
        )
    assert export(experiment).text == export(experiment).text


def test_missing_inputs_block_executable_generation():
    request, inputs = planning_case(stock_volume="0")
    with pytest.raises(ValueError, match="not ready"):
        cloning.build(cloning.plan(request, **inputs))


def test_semantic_identity_is_independent_of_authoring_paths():
    p = Protocol("Same meaning")
    p.wait(1 * seconds)
    original = p.snapshot()
    moved = replace(
        original,
        steps=tuple(
            replace(
                step,
                origin=Origin("/another/machine/experiment.py", 800),
            )
            for step in original.steps
        ),
    )
    assert moved.digest == original.digest
    assert moved.steps[0].identity == original.steps[0].identity
    p.wait(2 * seconds)
    assert original.digest != p.snapshot().digest
    with pytest.raises(ValueError, match="unique"):
        replace(original, steps=(*original.steps, *original.steps))


@pytest.mark.integration
@pytest.mark.parametrize("handler", [LiquidHandler.OT2, LiquidHandler.FLEX])
def test_assembly_robot_code_runs_in_official_simulator(handler):
    simulator = pytest.importorskip("opentrons.simulate")
    request, inputs = planning_case()
    experiment = cloning.build(cloning.plan(request, **inputs))
    artifact = lab.compile(experiment, handler)
    compilation = artifact.stages[0]
    log, _ = simulator.simulate(StringIO(compilation.files["protocol.py"]))
    assert any("Aspirating 1.0" in event["payload"]["text"] for event in log)
    assert [item["step"] for item in compilation.source_map] == [
        step.identity for step in compilation.protocol.steps
    ]
    manual = lab.compile(experiment, Manual())
    for name in ("experiment.json", "protocol.labop.ttl", "provenance.ttl", "methods.md"):
        assert artifact.files[name] == manual.files[name]


@pytest.mark.integration
async def test_assembly_star_preview_matches_planned_final_volumes():
    backend_module = pytest.importorskip("pylabrobot.liquid_handling.backends")
    resources = pytest.importorskip("pylabrobot.resources")
    events = []

    class Recorder(backend_module.SerializingBackend):
        async def send_command(self, command, data=None):
            events.append(command)

    async def thermal(plate, *, profile, cycles, lid_temperature):
        assert profile == [(25, 1)]
        assert plate.get_item("A1").tracker.get_used_volume() == 5
        events.append("thermal")

    request, inputs = planning_case()
    experiment = cloning.build(cloning.plan(request, **inputs))
    artifact = lab.compile(experiment, LiquidHandler.STAR)
    compilation = artifact.stages[0]
    namespace = {"__name__": "_generated"}
    exec(builtins.compile(compilation.files["protocol.py"], "preview.py", "exec"), namespace)
    resources.set_volume_tracking(True)
    try:
        await namespace["run"](Recorder(num_channels=8), thermocycle=thermal)
    finally:
        resources.set_volume_tracking(False)
    assert events[-2:] == ["thermal", "stop"]
    assert len(compilation.source_map) == len(compilation.protocol.steps)


@pytest.mark.integration
def test_flex_switches_volume_modes_before_picking_up_fresh_tips():
    simulator = pytest.importorskip("opentrons.simulate")

    p = Protocol("Mixed volumes")
    source = p.plate("reagents", shape=(4, 6), capacity=1500 * uL)
    target = p.plate("products", capacity=100 * uL)
    p.load(source["A1"], "water", volume=200 * uL)
    for index, volume in enumerate((1, 4, 5, 30, 50), 1):
        p.transfer(source["A1"], target[f"A{index}"], volume=volume * uL)
    artifact = lab.compile(p, deck=assembly_deck(), liquid_handler=LiquidHandler.FLEX)
    source_text = artifact.files["protocol.py"]
    assert source_text.count("configure_for_volume") == 5
    simulator.simulate(StringIO(source_text))
