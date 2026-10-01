"""Sample identities, logical locations, and planned outputs."""

from dataclasses import dataclass

from lab.inventory import MaterialForm
from lab.provenance import Component, Implementation, Ref


@dataclass(frozen=True)
class Location:
    """A logical well shared by operations, sample placements, and target bindings."""

    resource: str
    well: str

    def __str__(self) -> str:
        return f"{self.resource}:{self.well}"


@dataclass(frozen=True, slots=True, kw_only=True)
class Sample:
    """Material at a location; parent_ids are contributions, not genetic ancestry."""

    id: str
    material_identity: str
    label: str
    parent_ids: tuple[str, ...] = ()
    replicate: int | None = None
    role: str = "material"
    source_sample_id: str | None = None
    source_protocol_id: str | None = None
    contents: tuple[str, ...] = ()
    dilution: int | None = None
    design: Ref[Component] | None = None
    implementation: Ref[Implementation] | None = None
    form: MaterialForm | None = None
    count: int | None = None

    def __post_init__(self) -> None:
        if self.form is not None and not isinstance(self.form, MaterialForm):
            raise TypeError("Sample form must be a MaterialForm")
        if self.form is not None and self.form.counted:
            if type(self.count) is not int or self.count < 1:
                raise ValueError("Counted samples need a positive integer count")
        elif self.count is not None:
            raise ValueError("Only counted material forms have a count")
        if self.design is not None and (
            not isinstance(self.design, Ref) or self.material_identity != self.design.identity
        ):
            raise ValueError("Sample material identity must match its design reference")
        if self.implementation is not None and not isinstance(self.implementation, Ref):
            raise TypeError("Sample implementation must be a reference")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (self.id, self.material_identity, self.label, self.role)
        ):
            raise ValueError("Sample identity, label, and role must be nonempty text.")
        if not isinstance(self.parent_ids, tuple) or not isinstance(self.contents, tuple):
            raise TypeError("Sample parents and contents must be tuples.")
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (*self.parent_ids, *self.contents)
        ):
            raise ValueError("Sample parents and contents must be nonempty text.")
        if len(set(self.parent_ids)) != len(self.parent_ids):
            raise ValueError("Sample parent ids must be unique.")
        if (self.source_sample_id is None) != (self.source_protocol_id is None):
            raise ValueError("An imported sample needs both source protocol and sample ids.")
        if self.source_sample_id is not None and (
            not self.source_sample_id.strip()
            or not self.source_protocol_id
            or not self.source_protocol_id.strip()
        ):
            raise ValueError("Source protocol and sample ids must be nonempty text.")


@dataclass(frozen=True, slots=True, kw_only=True)
class SamplePlacement:
    sample_id: str
    location: Location


@dataclass(frozen=True, slots=True, kw_only=True)
class OutputManifest:
    """Planned outputs of one stage. The next stage consumes this, not a well-name dict."""

    protocol_id: str
    samples: tuple[Sample, ...]
    placements: tuple[SamplePlacement, ...]

    def __post_init__(self) -> None:
        if not self.protocol_id.strip():
            raise ValueError("A manifest needs a protocol id.")
        if not isinstance(self.samples, tuple) or not isinstance(self.placements, tuple):
            raise TypeError("Manifest samples and placements must be tuples.")
        ids = {sample.id for sample in self.samples}
        if (
            len(ids) != len(self.samples)
            or len(self.placements) != len(ids)
            or {placement.sample_id for placement in self.placements} != ids
        ):
            raise ValueError("Manifest samples need unique ids and exactly one placement each.")
        if len({placement.location for placement in self.placements}) != len(self.placements):
            raise ValueError("Manifest sample locations must be unique.")

    def to_dict(self) -> dict[str, object]:
        locations = {placement.sample_id: placement.location for placement in self.placements}
        return {
            "schema_version": "1.0",
            "protocol_id": self.protocol_id,
            "state": "planned",
            "outputs": [
                {
                    "sample_id": sample.id,
                    "material_identity": sample.material_identity,
                    "design": None if sample.design is None else sample.design.identity,
                    "implementation": (
                        None if sample.implementation is None else sample.implementation.identity
                    ),
                    "label": sample.label,
                    "parent_sample_ids": list(sample.parent_ids),
                    "replicate": sample.replicate,
                    "role": sample.role,
                    "form": None if sample.form is None else sample.form.value,
                    "count": sample.count,
                    "dilution": sample.dilution,
                    "source_sample_id": sample.source_sample_id,
                    "source_protocol_id": sample.source_protocol_id,
                    "contents": list(sample.contents),
                    "container_id": locations[sample.id].resource,
                    "well_name": locations[sample.id].well,
                }
                for sample in self.samples
            ],
        }
