from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from rdflib import RDF, Graph, Literal, URIRef

import lab
from lab.execution import Observation, Outcome, OutputRecord, Run, RunMode, StepRecord
from lab.experiments import cloning
from lab.labop.primitives import LAB, LABOP
from lab.provenance import Agent, Document, EvidenceState
from lab.provenance.vocabulary import OM
from lab.targets import Manual
from tests.planning_fixture import NS, planning_case

START = datetime(2026, 9, 27, tzinfo=UTC)


def run_case(mode=RunMode.PHYSICAL):
    request, inputs = planning_case()
    compilation = lab.compile(cloning.build(cloning.plan(request, **inputs)), Manual())
    return Run(
        compilation,
        identity=NS + "/run",
        mode=mode,
        agent=Agent(identity=NS + "/operator"),
        started_at=START,
    )


def observation(run, index=0, *, attempt=1, outcome=Outcome.SUCCEEDED, offset=None):
    offset = index if offset is None else offset
    return StepRecord(
        step=run.compilation.stages[0].protocol.steps[index].identity,
        attempt=attempt,
        outcome=outcome,
        started_at=START + timedelta(seconds=offset),
        ended_at=START + timedelta(seconds=offset + 1),
    )


def test_incomplete_run_preserves_missing_observations_and_planned_outputs(tmp_path):
    run = run_case()
    record = observation(run)
    run.record(record)
    result = run.finalize(ended_at=START + timedelta(seconds=1))
    assert not result.complete and len(result.missing_steps) == 5
    output = run.compilation.stages[0].manifest.samples[0].implementation
    assert result.provenance.resolve(output).evidence_state is EvidenceState.PLANNED
    graph = Graph().parse(data=result.files["execution.labop.ttl"], format="turtle")
    activity = URIRef(NS + "/run/step_1_attempt_1")
    assert (activity, RDF.type, LABOP.BehaviorExecution) in graph
    assert (URIRef(NS + "/run"), LABOP.completedNormally, Literal(False)) in graph
    assert result.provenance.get(str(activity), lab.provenance.Activity).start_time == START
    result.write(tmp_path)
    assert Document.read(tmp_path / "provenance.ttl").freeze().digest == result.provenance.digest
    with pytest.raises(ValueError, match="finalized"):
        run.record(record)


@pytest.mark.parametrize(
    "mode,state",
    [
        (RunMode.PHYSICAL, EvidenceState.RECORDED),
        (RunMode.SIMULATED, EvidenceState.SIMULATED),
    ],
)
def test_complete_runs_only_realize_explicitly_observed_outputs(mode, state):
    run = run_case(mode)
    count = len(run.compilation.stages[0].protocol.steps)
    for index in range(count):
        run.record(observation(run, index))
    planned = run.compilation.stages[0].manifest.samples[0].implementation
    run.record_output(
        OutputRecord(
            planned=planned,
            identity=NS + "/observed_product",
            observed_at=START + timedelta(seconds=count),
            volume_ul=Decimal(4),
        )
    )
    result = run.finalize(ended_at=START + timedelta(seconds=count))
    assert result.complete
    realized = result.provenance.get(NS + "/observed_product", lab.provenance.Implementation)
    assert realized.evidence_state is state and realized.built is None
    assert realized.measures[0].value == 4
    assert result.provenance.resolve(planned).evidence_state is EvidenceState.PLANNED
    assert result.provenance.get(NS + "/run", lab.provenance.Activity).evidence_state is state


def test_retries_failures_and_skips_are_not_filled_with_successes():
    run = run_case()
    run.record(observation(run, outcome=Outcome.FAILED))
    run.record(observation(run, attempt=2, offset=1))
    run.record(observation(run, 1, offset=2, outcome=Outcome.SKIPPED))
    result = run.finalize(ended_at=START + timedelta(seconds=3))
    assert len(result.steps) == 3 and not result.complete
    graph = Graph().parse(data=result.files["execution.labop.ttl"], format="turtle")
    assert len(tuple(graph.subjects(RDF.type, LABOP.CallBehaviorExecution))) == 2
    assert len(tuple(graph.subjects(LAB.recordCoverage))) == 1
    with pytest.raises(ValueError, match="unique and contiguous"):
        replace(result, steps=(result.steps[1],))
    with pytest.raises(ValueError, match="outside"):
        replace(result, ended_at=START)


def test_out_of_order_successes_do_not_claim_normal_completion():
    run = run_case()
    count = len(run.compilation.stages[0].protocol.steps)
    for index in range(count):
        run.record(observation(run, index, offset=count - index))
    result = run.finalize(ended_at=START + timedelta(seconds=count + 1))
    assert not result.missing_steps and not result.complete


def test_measurements_are_separate_from_planned_parameters_and_preserved():
    run = run_case()
    measured = Observation(name="Actual volume", value=Decimal("0.95"), unit=OM + "microlitre")
    run.record(replace(observation(run), observations=(measured,)))
    result = run.finalize(ended_at=START + timedelta(seconds=1))
    activity = result.provenance.get(NS + "/run/step_1_attempt_1", lab.provenance.Activity)
    assert activity.measures[0].value == 0.95
    assert run.compilation.stages[0].protocol.steps[0].volume == 1
    assert "0.95" in result.record_json
