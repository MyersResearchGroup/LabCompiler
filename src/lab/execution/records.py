"""Explicit observations; no observation is inferred from generated code."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from lab.provenance import Attachment, Component, Implementation, Ref
from lab.provenance.types import require_iri


def timestamp(value: datetime) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Observation timestamps require a timezone")


class RunMode(StrEnum):
    PHYSICAL = "physical"
    SIMULATED = "simulated"


class Outcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True, kw_only=True)
class Observation:
    name: str
    value: Decimal
    unit: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Observation name is required")
        if not isinstance(self.value, Decimal) or not self.value.is_finite():
            raise ValueError("Observation value must be a finite Decimal")
        require_iri(self.unit)


@dataclass(frozen=True, kw_only=True)
class StepRecord:
    step: str
    outcome: Outcome
    started_at: datetime
    ended_at: datetime
    attempt: int = 1
    observations: tuple[Observation, ...] = ()
    evidence: tuple[Ref[Attachment], ...] = ()
    note: str = ""

    def __post_init__(self) -> None:
        require_iri(self.step)
        timestamp(self.started_at)
        timestamp(self.ended_at)
        if self.ended_at < self.started_at:
            raise ValueError("Step end precedes its start")
        if not isinstance(self.outcome, Outcome):
            raise TypeError("Pass an Outcome")
        if type(self.attempt) is not int or self.attempt < 1:
            raise ValueError("Attempt numbers must be positive integers")
        if not isinstance(self.observations, tuple) or not all(
            isinstance(item, Observation) for item in self.observations
        ):
            raise TypeError("Observations must be an immutable tuple")
        if not isinstance(self.evidence, tuple) or not all(
            isinstance(item, Ref) for item in self.evidence
        ):
            raise TypeError("Evidence must be an immutable tuple of attachment references")


@dataclass(frozen=True, kw_only=True)
class OutputRecord:
    """An explicitly observed output; built is an optional structural assertion."""

    planned: Ref[Implementation]
    identity: str
    observed_at: datetime
    built: Ref[Component] | None = None
    volume_ul: Decimal | None = None
    count: int | None = None
    evidence: tuple[Ref[Attachment], ...] = ()

    def __post_init__(self) -> None:
        require_iri(self.identity)
        timestamp(self.observed_at)
        if not isinstance(self.planned, Ref):
            raise TypeError("An output must refer to a planned implementation")
        if self.identity == self.planned.identity:
            raise ValueError("An observed output requires a distinct identity")
        if self.volume_ul is not None and (
            not isinstance(self.volume_ul, Decimal)
            or not self.volume_ul.is_finite()
            or self.volume_ul < 0
        ):
            raise ValueError("Observed output volume must be a nonnegative finite Decimal")
        if self.count is not None and (
            type(self.count) is not int or self.count < 0 or self.volume_ul is not None
        ):
            raise ValueError("Observed counts must be nonnegative integers, separate from volumes")
        if self.built is not None and not isinstance(self.built, Ref):
            raise TypeError("Built structure must be a component reference")
        if not isinstance(self.evidence, tuple) or not all(
            isinstance(item, Ref) for item in self.evidence
        ):
            raise TypeError("Output evidence must be an immutable tuple")


def step_activity(identity: str, index: int, attempt: int) -> str:
    return identity + f"/step_{index + 1}_attempt_{attempt}"
