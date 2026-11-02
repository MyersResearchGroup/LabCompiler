"""Typed presentation data shared by the worksheet's template components.

These records contain text, quantities, and references, never rendered markup.
The recorded protocol remains the authority for operations and material identity.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import TypeAlias

from lab.documents.flow import FlowProfile
from lab.model import Fill, RecordedProtocol, Resource, TargetPlan
from lab.samples import Location, Sample

DetailFields: TypeAlias = tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Reference:
    anchor: str
    label: str


@dataclass(frozen=True)
class IncomingTransfer:
    step_number: int
    source: Reference
    volume: Decimal


@dataclass(frozen=True)
class WellView:
    reference: Reference
    location: Location
    fill: Fill | None
    physical: str
    final_volume: Decimal
    fields: DetailFields
    sample: Sample | None
    sample_heading: str
    sample_fields: DetailFields
    incoming: tuple[IncomingTransfer, ...]


@dataclass(frozen=True)
class ResourceView:
    reference: Reference
    resource: Resource
    geometry: str
    physical_summary: str
    fields: DetailFields
    wells: tuple[WellView, ...]
    loaded_wells: tuple[WellView, ...]
    final_wells: tuple[WellView, ...]


@dataclass(frozen=True)
class ProcedureRow:
    number: int
    action: str
    instruction: str = ""
    volume: Decimal | None = None
    volume_note: str = ""
    source: Reference | None = None
    destinations: tuple[Reference, ...] = ()
    liquid_class: Reference | None = None
    air_gap: Decimal | None = None


@dataclass(frozen=True)
class LiquidClassView:
    reference: Reference
    description: str
    fields: DetailFields
    notes: tuple[str, ...]
    profiles: tuple[FlowProfile, ...]


@dataclass(frozen=True)
class Worksheet:
    protocol: RecordedProtocol
    target: TargetPlan
    digest: str
    summary: str
    notes: str
    resources: tuple[ResourceView, ...]
    procedure: tuple[ProcedureRow, ...]
    liquid_classes: tuple[LiquidClassView, ...]
