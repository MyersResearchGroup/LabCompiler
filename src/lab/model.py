"""The entire recorded protocol vocabulary. No callbacks or device objects."""

from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from enum import Enum
from typing import Any

from lab.artifacts import canonical_json, digest
from lab.operations import (
    Distribute,
    ExternalPreparation,
    ManualInstruction,
    Mix,
    Resource,
    SetTemperature,
    Step,
    Thermocycle,
    Transfer,
    Wait,
)
from lab.provenance.types import require_iri
from lab.samples import OutputManifest, Sample, SamplePlacement
from lab.units import number


@dataclass(frozen=True)
class RecordedProtocol:
    name: str
    description: str
    resources: tuple[Resource, ...]
    steps: tuple[Step, ...]
    samples: tuple[Sample, ...] = ()
    placements: tuple[SamplePlacement, ...] = ()
    input_sample_ids: tuple[str, ...] = ()
    output_sample_ids: tuple[str, ...] = ()
    identity: str | None = None

    def __post_init__(self) -> None:
        for field in (
            self.resources,
            self.steps,
            self.samples,
            self.placements,
            self.input_sample_ids,
            self.output_sample_ids,
        ):
            if not isinstance(field, tuple):
                raise TypeError("Recorded protocol collections must be immutable tuples")
        if any(not isinstance(resource.fills, tuple) for resource in self.resources):
            raise TypeError("Recorded initial fills must be immutable tuples")
        if any(
            isinstance(step, Thermocycle) and not isinstance(step.profile, tuple)
            for step in self.steps
        ):
            raise TypeError("Thermal profiles must be immutable tuples")
        if any(
            isinstance(step, Distribute) and not isinstance(step.destinations, tuple)
            for step in self.steps
        ):
            raise TypeError("Distribution destinations must be immutable tuples")
        identity = self.identity or "urn:lab:protocol:" + digest(semantic(self))
        require_iri(identity)
        object.__setattr__(self, "identity", identity)
        steps = tuple(
            replace(step, identity=step.identity or identity + f"/step_{index + 1}")
            for index, step in enumerate(self.steps)
        )
        if len({step.identity for step in steps}) != len(steps):
            raise ValueError("Step identities must be unique")
        for step in steps:
            require_iri(str(step.identity))
        object.__setattr__(self, "steps", steps)

    @property
    def semantic_json(self) -> str:
        return canonical_json(semantic(self))

    @property
    def digest(self) -> str:
        return digest(semantic(self))

    def output_manifest(self) -> OutputManifest:
        """Project declared outputs from this snapshot using logical locations."""
        samples = {sample.id: sample for sample in self.samples}
        placements = {placement.sample_id: placement for placement in self.placements}
        return OutputManifest(
            protocol_id=str(self.identity),
            samples=tuple(samples[sample_id] for sample_id in self.output_sample_ids),
            placements=tuple(placements[sample_id] for sample_id in self.output_sample_ids),
        )


def encode(value: Any) -> Any:
    """JSON-compatible data for the small, closed vocabulary."""
    if isinstance(value, Decimal):
        return number(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (tuple, list)):
        return [encode(item) for item in value]
    if isinstance(value, dict):
        return {key: encode(item) for key, item in value.items()}
    if isinstance(
        value,
        (
            Transfer,
            Distribute,
            Mix,
            Wait,
            Thermocycle,
            SetTemperature,
            ManualInstruction,
            ExternalPreparation,
        ),
    ):
        return {"kind": type(value).__name__, **encode(asdict(value))}
    if hasattr(value, "__dataclass_fields__"):
        # Do not recursively use asdict here: it would erase step discriminants.
        return {name: encode(getattr(value, name)) for name in value.__dataclass_fields__}
    return value


def semantic(value: Any) -> Any:
    """Encode meaning without developer source paths or line numbers."""

    def clean(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: clean(val) for key, val in item.items() if key != "origin"}
        if isinstance(item, list):
            return [clean(val) for val in item]
        return item

    return clean(encode(value))
