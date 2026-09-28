"""Record explicit run observations and write immutable evidence bundles."""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from lab.artifacts import canonical_json, write_bundle
from lab.compiler import ExperimentCompilation
from lab.documents import describe
from lab.execution import provenance
from lab.execution.records import Outcome, OutputRecord, RunMode, StepRecord, timestamp
from lab.labop import execution as labop_execution
from lab.provenance import Agent, Attachment, DocumentSnapshot, EvidenceState, Implementation
from lab.provenance.types import require_iri


@dataclass(frozen=True, kw_only=True)
class RunRecord:
    identity: str
    compilation: ExperimentCompilation
    mode: RunMode
    agent: Agent
    started_at: datetime
    ended_at: datetime
    steps: tuple[StepRecord, ...]
    outputs: tuple[OutputRecord, ...] = ()
    attachments: tuple[Attachment, ...] = ()

    def __post_init__(self) -> None:
        require_iri(self.identity)
        timestamp(self.started_at)
        timestamp(self.ended_at)
        if not isinstance(self.mode, RunMode) or not isinstance(self.agent, Agent):
            raise TypeError("Runs need an explicit mode and agent")
        if self.ended_at < self.started_at:
            raise ValueError("Run end precedes its start")
        if not all(
            isinstance(items, tuple) for items in (self.steps, self.outputs, self.attachments)
        ):
            raise TypeError("Run observations must be immutable tuples")
        known = {
            step.identity for stage in self.compilation.stages for step in stage.protocol.steps
        }
        attempts: dict[str, list[StepRecord]] = {}
        for record in self.steps:
            if not isinstance(record, StepRecord) or record.step not in known:
                raise ValueError("Observation refers to an unknown compiled step")
            if not self.started_at <= record.started_at <= record.ended_at <= self.ended_at:
                raise ValueError("Step observation lies outside the run interval")
            attempts.setdefault(record.step, []).append(record)
        for records in attempts.values():
            ordered = sorted(records, key=lambda record: record.attempt)
            if [record.attempt for record in ordered] != list(range(1, len(ordered) + 1)):
                raise ValueError("Attempts must be unique and contiguous, starting at one")
            for first, second in zip(ordered, ordered[1:], strict=False):
                if second.started_at < first.ended_at:
                    raise ValueError("Retry attempts must not overlap")
        declared = {
            sample.implementation: sample
            for stage in self.compilation.stages
            for sample in stage.manifest.samples
            if sample.implementation is not None
        }
        ids = [output.identity for output in self.outputs]
        if len(set(ids)) != len(ids) or len({output.planned for output in self.outputs}) != len(
            ids
        ):
            raise ValueError("Output identities and planned mappings must be unique")
        for output in self.outputs:
            if not isinstance(output, OutputRecord) or output.planned not in declared:
                raise ValueError("Observed output must refer to a declared planned output")
            counted = declared[output.planned].count is not None
            if (counted and output.volume_ul is not None) or (
                not counted and output.count is not None
            ):
                raise ValueError(
                    "Observed output quantity kind must match the planned material form"
                )
            if not self.started_at <= output.observed_at <= self.ended_at:
                raise ValueError("Output observation lies outside the run interval")
            planned = self.compilation.experiment.provenance.resolve(output.planned)
            if (
                not isinstance(planned, Implementation)
                or planned.evidence_state is not EvidenceState.PLANNED
            ):
                raise ValueError("Output observation must refer to a planned implementation")
            if output.built is not None:
                self.compilation.experiment.provenance.resolve(output.built)
        if not all(isinstance(item, Attachment) for item in self.attachments):
            raise TypeError("Evidence objects must be Attachments")
        # Resolve evidence and ownership now, not only when writing files.
        self.provenance.validate().raise_for_errors()

    @property
    def missing_steps(self) -> tuple[str, ...]:
        recorded = {record.step for record in self.steps}
        return tuple(
            str(step.identity)
            for stage in self.compilation.stages
            for step in stage.protocol.steps
            if step.identity not in recorded
        )

    @property
    def complete(self) -> bool:
        latest: dict[str, StepRecord] = {}
        for record in sorted(self.steps, key=lambda record: record.attempt):
            latest[record.step] = record
        ordered = [
            latest[str(step.identity)]
            for stage in self.compilation.stages
            for step in stage.protocol.steps
            if step.identity in latest
        ]
        return (
            not self.missing_steps
            and all(record.outcome is Outcome.SUCCEEDED for record in ordered)
            and all(
                first.ended_at <= second.started_at
                for first, second in zip(ordered, ordered[1:], strict=False)
            )
        )

    @property
    def record_json(self) -> str:
        return canonical_json(
            {
                "format": "lab.run.v1",
                "identity": self.identity,
                "compilation_sha256": self.compilation.digest,
                "experiment_sha256": self.compilation.experiment.digest,
                "mode": self.mode,
                "agent": self.agent.identity,
                "started_at": self.started_at,
                "ended_at": self.ended_at,
                "steps": self.steps,
                "outputs": self.outputs,
                "complete": self.complete,
                "missing_steps": self.missing_steps,
            }
        )

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.record_json.encode()).hexdigest()

    @property
    def provenance(self) -> DocumentSnapshot:
        return provenance.project(
            self.compilation,
            identity=self.identity,
            mode=self.mode,
            agent=self.agent,
            started_at=self.started_at,
            ended_at=self.ended_at,
            steps=self.steps,
            outputs=self.outputs,
            attachments=self.attachments,
        )

    @property
    def files(self) -> dict[str, str]:
        observed = self.provenance
        planned = {
            step.identity: step
            for stage in self.compilation.stages
            for step in stage.protocol.steps
        }
        details = []
        for record in self.steps:
            details.extend(
                [
                    f"## Step <{record.step}>, attempt {record.attempt}",
                    "",
                    f"Planned operation: {describe(planned[record.step])}",
                    "",
                    f"Recorded outcome: {record.outcome.value}; "
                    f"{record.started_at.isoformat()} to {record.ended_at.isoformat()}.",
                    "",
                ]
            )
            if record.note:
                details += [record.note, ""]
            for observation in record.observations:
                details.append(f"- {observation.name}: {observation.value} <{observation.unit}>.")
            for attachment in record.evidence:
                details.append(f"- Evidence: <{attachment.identity}>.")
            details.append("")
        details += ["## Observed outputs", ""]
        for output in self.outputs:
            amount = (
                f"count {output.count}"
                if output.count is not None
                else f"volume {output.volume_ul} µL"
                if output.volume_ul is not None
                else "quantity unmeasured"
            )
            details.append(
                f"- <{output.identity}>, observed at {output.observed_at.isoformat()}; "
                f"planned material <{output.planned.identity}>; "
                f"{amount}; "
                f"structural assertion {output.built.identity if output.built else 'unspecified'}."
            )
        details.append("")
        files = {
            "run.json": self.record_json,
            "provenance.ttl": observed.to_turtle(),
            "execution.labop.ttl": labop_execution.project(
                self.compilation,
                observed,
                identity=self.identity,
                records=self.steps,
                complete=self.complete,
            ),
            "methods.md": "\n".join(
                [
                    "# Recorded methods",
                    "",
                    f"Run: <{self.identity}>; mode: {self.mode.value}.",
                    "",
                    f"Compilation SHA-256: {self.compilation.digest}",
                    "",
                    f"Complete ordered step coverage: {self.complete}. "
                    f"Missing steps: {len(self.missing_steps)}.",
                    "",
                    "Only the following observations were supplied. Planned parameter values are "
                    "available in the referenced compilation; "
                    "they are not substituted for measurements.",
                    "",
                    *details,
                    "",
                ]
            ),
        }
        files["bundle.json"] = canonical_json(
            {
                "format": "lab.run.bundle.v1",
                "run": self.identity,
                "sha256": {
                    name: hashlib.sha256(text.encode()).hexdigest() for name, text in files.items()
                },
            }
        )
        return files

    def write(self, directory: str | Path) -> Path:
        return write_bundle(directory, self.files)


class Run:
    def __init__(
        self,
        compilation: ExperimentCompilation,
        *,
        identity: str,
        mode: RunMode,
        agent: Agent,
        started_at: datetime,
    ) -> None:
        require_iri(identity)
        timestamp(started_at)
        if not isinstance(compilation, ExperimentCompilation):
            raise TypeError("A run references an ExperimentCompilation")
        self.compilation = compilation
        self.identity = identity
        self.mode = mode
        self.agent = agent
        self.started_at = started_at
        self._steps: list[StepRecord] = []
        self._outputs: list[OutputRecord] = []
        self._attachments: list[Attachment] = []
        self._finished = False

    def _open(self) -> None:
        if self._finished:
            raise ValueError("This run has already been finalized")

    def record(self, observation: StepRecord) -> None:
        self._open()
        if not isinstance(observation, StepRecord):
            raise TypeError("Pass a StepRecord")
        self._steps.append(observation)

    def record_output(self, observation: OutputRecord) -> None:
        self._open()
        if not isinstance(observation, OutputRecord):
            raise TypeError("Pass an OutputRecord")
        self._outputs.append(observation)

    def attach(self, attachment: Attachment) -> None:
        self._open()
        if not isinstance(attachment, Attachment):
            raise TypeError("Pass an Attachment")
        self._attachments.append(attachment)

    def finalize(self, *, ended_at: datetime) -> RunRecord:
        self._open()
        result = RunRecord(
            identity=self.identity,
            compilation=self.compilation,
            mode=self.mode,
            agent=self.agent,
            started_at=self.started_at,
            ended_at=ended_at,
            steps=tuple(self._steps),
            outputs=tuple(self._outputs),
            attachments=tuple(self._attachments),
        )
        self._finished = True
        return result
