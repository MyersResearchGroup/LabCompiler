"""Observed SBOL projection from supplied run evidence."""

from datetime import datetime

from lab.compiler import ExperimentCompilation
from lab.execution.records import Outcome, OutputRecord, RunMode, StepRecord, step_activity
from lab.provenance import (
    Activity,
    Agent,
    Association,
    Attachment,
    Document,
    DocumentSnapshot,
    EvidenceState,
    Implementation,
    Measure,
    Plan,
)
from lab.provenance.vocabulary import LAB, OM


def project(
    compilation: ExperimentCompilation,
    *,
    identity: str,
    mode: RunMode,
    agent: Agent,
    started_at: datetime,
    ended_at: datetime,
    steps: tuple[StepRecord, ...],
    outputs: tuple[OutputRecord, ...],
    attachments: tuple[Attachment, ...],
) -> DocumentSnapshot:
    document = Document.from_snapshot(compilation.experiment.provenance)
    document.add(agent, *attachments)
    evidence_state = EvidenceState.RECORDED if mode is RunMode.PHYSICAL else EvidenceState.SIMULATED
    plan = Plan(
        identity=identity + "/specification", protocol=compilation.experiment.identity + "/labop"
    )
    run = Activity(
        identity=identity,
        types=(LAB + "run",),
        evidence_state=evidence_state,
        start_time=started_at,
        end_time=ended_at,
        association=(Association(agent=agent.ref, plan=plan.ref),),
        description=f"{mode.value} run of compilation {compilation.digest}.",
    )
    document.add(plan, run)
    ordered = [step for stage in compilation.stages for step in stage.protocol.steps]
    indices = {step.identity: index for index, step in enumerate(ordered)}
    for index, stage in enumerate(compilation.stages, 1):
        stage_steps = {step.identity for step in stage.protocol.steps}
        observations = tuple(record for record in steps if record.step in stage_steps)
        if not observations:
            continue
        specification = Plan(
            identity=identity + f"/stage_{index}/specification",
            protocol=stage.protocol.identity,
        )
        activity = Activity(
            identity=identity + f"/stage_{index}",
            types=(LAB + "stageExecution",),
            evidence_state=evidence_state,
            start_time=min(record.started_at for record in observations),
            end_time=max(record.ended_at for record in observations),
            association=(Association(agent=agent.ref, plan=specification.ref),),
            description="Stage observation window, aggregated from supplied step records.",
        )
        document.add(specification, activity)
    for record in steps:
        specification = Plan(
            identity=step_activity(identity, indices[record.step], record.attempt)
            + "/specification",
            protocol=record.step,
        )
        activity = Activity(
            identity=step_activity(identity, indices[record.step], record.attempt),
            types=(
                LAB + "stepObservation",
                LAB + record.outcome.value,
                *((LAB + "stepExecution",) if record.outcome is not Outcome.SKIPPED else ()),
            ),
            evidence_state=evidence_state,
            start_time=record.started_at,
            end_time=record.ended_at,
            association=(Association(agent=agent.ref, plan=specification.ref),),
            attachments=record.evidence,
            description=record.note or None,
            measures=tuple(
                Measure(name=item.name, value=float(item.value), unit=item.unit)
                for item in record.observations
            ),
        )
        document.add(specification, activity)
    for output in outputs:
        planned = document.resolve(output.planned)
        receipt = Activity(
            identity=output.identity + "/observation",
            types=(LAB + "outputObservation",),
            evidence_state=evidence_state,
            end_time=output.observed_at,
            attachments=output.evidence,
            association=(Association(agent=agent.ref, plan=plan.ref),),
            informed_by=(run.ref,),
        )
        material = Implementation(
            identity=output.identity,
            derived_from=(planned.ref, *planned.derived_from),
            built=output.built,
            evidence_state=evidence_state,
            generated_by=(receipt.ref,),
            attachments=output.evidence,
            measures=(
                Measure(
                    name="Observed volume", value=float(output.volume_ul), unit=OM + "microlitre"
                ),
            )
            if output.volume_ul is not None
            else (Measure(name="Observed count", value=float(output.count), unit=OM + "one"),)
            if output.count is not None
            else (),
        )
        document.add(receipt, material)
    return document.freeze()
