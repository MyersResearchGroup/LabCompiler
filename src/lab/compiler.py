"""Snapshot, validate, prepare a target, and emit a self-contained bundle."""

import hashlib
import json
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any, overload
from typing import Protocol as Interface

import lab.documents as documents
from lab._version import __version__
from lab.deck import Deck
from lab.experiments.cloning.stages.assembly import build_assembly
from lab.experiments.cloning.stages.plating import build_plating
from lab.experiments.cloning.stages.transformation import build_transformation
from lab.experiments.cloning.types import (
    Assembly,
    AssemblyRequest,
    PlatingRequest,
    TransformationRequest,
)
from lab.model import Distribute, Mix, RecordedProtocol, TargetPlan, Transfer, encode
from lab.protocol import Protocol
from lab.samples import Location, OutputManifest
from lab.targets.liquid_handler import LiquidHandler
from lab.targets.lower import lower_deck
from lab.validation import CompileError, logical_bindings, validate


class Target(Interface):
    def prepare(self, protocol: RecordedProtocol) -> TargetPlan: ...


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


@dataclass(frozen=True)
class Compilation:
    protocol: RecordedProtocol
    target: TargetPlan
    final_volumes: tuple[tuple[Location, Decimal], ...]

    @property
    def manifest(self) -> OutputManifest:
        """Planned outputs from the same snapshot that drives code and documents."""
        return self.protocol.output_manifest()

    @property
    def plan_json(self) -> str:
        return canonical_json(
            {
                "format": "lab.plan.v1",
                "compiler_version": __version__,
                "units": {"volume": "microliter", "duration": "second", "temperature": "celsius"},
                "protocol": encode(self.protocol),
                "target": {
                    "name": self.target.name,
                    "configuration": json.loads(self.target.configuration_json),
                    "bindings": encode(self.target.bindings),
                    "setup": list(self.target.setup),
                },
                "final_volumes": [
                    {"location": encode(location), "volume": encode(volume)}
                    for location, volume in self.final_volumes
                ],
                "source_sha256": (
                    hashlib.sha256(self.target.source.encode()).hexdigest()
                    if self.target.source is not None
                    else None
                ),
            }
        )

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.plan_json.encode()).hexdigest()

    @property
    def files(self) -> dict[str, str]:
        result = {"plan.json": self.plan_json, "protocol.html": documents.render(self)}
        if self.protocol.output_sample_ids:
            result["manifest.json"] = canonical_json(self.manifest.to_dict())
        if self.target.source is not None:
            result["protocol.py"] = self.target.source
        return result

    def write(self, directory: str | Path) -> Path:
        """Write a bundle. Refuse to replace any different existing artifact."""
        directory = Path(directory)
        files = self.files
        for name, text in files.items():
            path = directory / name
            if path.exists() and path.read_text(encoding="utf-8") != text:
                raise FileExistsError(f"{path} already contains a different artifact")
        directory.mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            (directory / name).write_text(text, encoding="utf-8")
        return directory


@overload
def compile(
    protocol: Protocol | Assembly | AssemblyRequest,
    hardware: Target | Deck,
    *,
    liquid_handler: LiquidHandler | None = None,
    inputs: None = None,
) -> Compilation: ...


@overload
def compile(
    protocol: TransformationRequest,
    hardware: Target | Deck,
    *,
    liquid_handler: LiquidHandler | None = None,
    inputs: OutputManifest | None = None,
) -> Compilation: ...


@overload
def compile(
    protocol: PlatingRequest,
    hardware: Target | Deck,
    *,
    liquid_handler: LiquidHandler | None = None,
    inputs: OutputManifest,
) -> Compilation: ...


def compile(
    protocol: Protocol | Assembly | AssemblyRequest | TransformationRequest | PlatingRequest,
    hardware: Target | Deck,
    *,
    liquid_handler: LiquidHandler | None = None,
    inputs: OutputManifest | None = None,
) -> Compilation:
    """Compile a protocol or a cloning request for one piece of hardware.

    One assembly uses its id as the protocol name. An assembly request names a
    protocol that holds every assembly. A transformation or plating request
    takes ``inputs`` as the upstream manifest. A ``Deck`` contains shared
    requirements and optional Lab-owned layouts. The selected backend validates
    and translates its layout or supported preset. Concrete backend targets are
    also accepted for low-level integrations. A document target such as
    ``Manual()`` has no robot, so ``liquid_handler`` is omitted.
    """
    if inputs is not None and not isinstance(protocol, (TransformationRequest, PlatingRequest)):
        raise TypeError("Pass inputs with a transformation or plating request.")
    work: Protocol
    if isinstance(protocol, Assembly):
        work = build_assembly(AssemblyRequest(id=protocol.id, assemblies=(protocol,)))
    elif isinstance(protocol, AssemblyRequest):
        work = build_assembly(protocol)
    elif isinstance(protocol, TransformationRequest):
        work = build_transformation(protocol, inputs=inputs)
    elif isinstance(protocol, PlatingRequest):
        work = build_plating(protocol, inputs=inputs)
    else:
        work = protocol
    recorded = work.snapshot()
    authored_deck = hardware if isinstance(hardware, Deck) else None
    if isinstance(hardware, Deck):
        if not isinstance(liquid_handler, LiquidHandler):
            raise TypeError(
                "Pass liquid_handler=LiquidHandler.OT2, LiquidHandler.FLEX, or LiquidHandler.STAR."
            )
        liquid = tuple(
            step.volume for step in recorded.steps if isinstance(step, (Transfer, Mix, Distribute))
        )
        requirements = {container.id: container.labware for container in hardware.containers}
        for resource in recorded.resources:
            spec = requirements.get(resource.name)
            if spec is None or (spec.rows, spec.columns) != (resource.rows, resource.columns):
                raise CompileError(
                    f"Deck requirements must match the protocol geometry for {resource.name}."
                )
            if resource.capacity > spec.capacity_ul:
                raise CompileError(f"Protocol capacity exceeds the deck limit for {resource.name}.")
        hardware = lower_deck(hardware, liquid_handler, liquid)
    declared = getattr(hardware, "liquid_handler", None)
    if isinstance(declared, LiquidHandler) and liquid_handler != declared:
        raise TypeError(
            f"This hardware is LiquidHandler.{declared.name}. "
            f"Pass liquid_handler=LiquidHandler.{declared.name}."
        )
    if liquid_handler is not None and not isinstance(declared, LiquidHandler):
        raise TypeError("This hardware does not name a LiquidHandler.")
    if liquid_handler is not None and not isinstance(liquid_handler, LiquidHandler):
        raise TypeError("Pass LiquidHandler.OT2, LiquidHandler.FLEX, or LiquidHandler.STAR.")
    validate(recorded, logical_bindings(recorded))
    prepared = hardware.prepare(recorded)
    if authored_deck is not None:
        configuration = json.loads(prepared.configuration_json)
        configuration["lab_deck"] = encode(authored_deck)
        prepared = replace(prepared, configuration_json=json.dumps(configuration, sort_keys=True))
    volumes = validate(recorded, prepared.bindings)
    return Compilation(recorded, prepared, tuple(volumes.items()))
