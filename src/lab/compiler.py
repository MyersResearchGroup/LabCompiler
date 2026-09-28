"""Snapshot, validate, prepare a target, and emit a self-contained bundle."""

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from importlib.resources import files as resource_files
from pathlib import Path
from typing import Any, overload
from typing import Protocol as Interface

import lab.documents as documents
import lab.methods as methods
from lab._version import __version__
from lab.artifacts import write_bundle
from lab.deck import Deck
from lab.experiment import ExperimentPlan
from lab.experiments.cloning.stages.assembly import build_assembly
from lab.experiments.cloning.stages.plating import build_plating
from lab.experiments.cloning.stages.transformation import build_transformation
from lab.experiments.cloning.types import (
    Assembly,
    AssemblyRequest,
    PlatingRequest,
    Transformation,
    TransformationRequest,
)
from lab.labop import export as export_labop
from lab.model import RecordedProtocol, encode
from lab.operations import Distribute, Mix, Transfer
from lab.protocol import Protocol
from lab.samples import Location, OutputManifest
from lab.target import TargetPlan
from lab.targets.liquid_handler import LiquidHandler
from lab.targets.lower import lower_deck
from lab.targets.manual import Manual
from lab.validation import CompileError, count_trace, logical_bindings, validate


class Target(Interface):
    def prepare(self, protocol: RecordedProtocol) -> TargetPlan: ...


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


@dataclass(frozen=True)
class Compilation:
    protocol: RecordedProtocol
    target: TargetPlan
    final_volumes: tuple[tuple[Location, Decimal], ...]
    directory: Path | None = None

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
                "final_counts": [
                    {"location": encode(location), "count": count}
                    for location, count in count_trace(self.protocol)[-1].items()
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
    def source_map(self) -> tuple[dict[str, object], ...]:
        """One-based inclusive generated line spans for semantic operations."""
        source = self.target.source
        if source is None:
            return ()
        lines = source.splitlines()
        markers = [
            (index + 1, line.strip().removeprefix("# lab:step "))
            for index, line in enumerate(lines)
            if line.strip().startswith("# lab:step ")
        ]
        ends = {
            line.strip().removeprefix("# lab:end "): index + 1
            for index, line in enumerate(lines)
            if line.strip().startswith("# lab:end ")
        }
        expected = tuple(step.identity for step in self.protocol.steps)
        if tuple(identity for _, identity in markers) != expected or set(ends) != set(expected):
            raise CompileError("Generated source does not map every protocol step exactly once")
        return tuple(
            {
                "step": identity,
                "file": "protocol.py",
                "start_line": start,
                "end_line": ends[identity],
            }
            for start, identity in markers
        )

    @property
    def files(self) -> dict[str, str]:
        result = {
            "plan.json": self.plan_json,
            "protocol.html": documents.render(self),
            "protocol.json": self.protocol.semantic_json,
            "source-map.json": canonical_json(self.source_map),
        }
        if self.protocol.output_sample_ids:
            result["manifest.json"] = canonical_json(self.manifest.to_dict())
        if self.target.source is not None:
            result["protocol.py"] = self.target.source
        return result

    def write(self, directory: str | Path) -> Path:
        """Write a bundle. Refuse to replace any different existing artifact."""
        return write_bundle(directory, self.files)


def _compile_protocol(
    protocol: Protocol | RecordedProtocol,
    hardware: Target | Deck,
    *,
    liquid_handler: LiquidHandler | None = None,
) -> Compilation:
    """Compile offline for one piece of hardware.

    A ``Deck`` contains shared requirements and optional Lab-owned layouts. The
    selected backend validates and translates its layout or supported preset.
    Concrete backend targets are also accepted for low-level integrations.
    A document target such as ``Manual()`` has no robot, so ``liquid_handler`` is omitted.
    """
    recorded = protocol.snapshot() if isinstance(protocol, Protocol) else protocol
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


@dataclass(frozen=True, kw_only=True)
class ExperimentCompilation:
    experiment: ExperimentPlan
    stages: tuple[Compilation, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.stages, tuple) or len(self.stages) != len(self.experiment.stages):
            raise ValueError("Compile every experiment stage into an immutable tuple")
        if any(
            compilation.protocol != stage.protocol
            for compilation, stage in zip(self.stages, self.experiment.stages, strict=True)
        ):
            raise ValueError("Stage compilations must use the frozen experiment's protocols")

    @property
    def files(self) -> dict[str, str]:
        files = {
            "experiment.json": self.experiment.plan_json,
            "provenance.ttl": self.experiment.provenance.to_turtle(),
            "protocol.labop.ttl": export_labop(self.experiment).text,
            "methods.md": methods.render(self.experiment),
        }
        files.update({f"inputs/{item.name}": item.text for item in self.experiment.inputs})
        for name in ("lab.ttl", "labop.ttl", "uml.ttl", "upstream.json", "LICENSE.txt"):
            files[f"schemas/{name}"] = (
                resource_files("lab.labop")
                .joinpath(
                    "resources",
                    name,
                )
                .read_text(encoding="utf-8")
            )
        for index, compilation in enumerate(self.stages, 1):
            files.update(
                {f"stages/{index}/{name}": text for name, text in compilation.files.items()}
            )
        if self.experiment.build_json is not None:
            files["build.json"] = self.experiment.build_json
        checksums = {
            name: hashlib.sha256(text.encode()).hexdigest() for name, text in sorted(files.items())
        }
        files["bundle.json"] = canonical_json(
            {
                "format": "lab.bundle.v1",
                "experiment": self.experiment.identity,
                "semantic_sha256": self.experiment.digest,
                "sha256": checksums,
                "stages": [
                    {
                        "protocol": compilation.protocol.identity,
                        "target": compilation.target.name,
                        "plan_sha256": compilation.digest,
                    }
                    for compilation in self.stages
                ],
            }
        )
        return files

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.files["bundle.json"].encode()).hexdigest()

    def write(self, directory: str | Path) -> Path:
        return write_bundle(directory, self.files)


class _DefaultOutput:
    """Marks compile's default output directory."""


_DEFAULT_OUTPUT = _DefaultOutput()
_BUNDLE_FILES = (
    "protocol.html",
    "plan.json",
    "manifest.json",
    "protocol.py",
    "protocol.json",
    "source-map.json",
)


def _segment(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("A compilation directory needs a protocol and target name.")
    cleaned = value.replace("/", "_").replace("\\", "_").replace("\x00", "")
    if cleaned in {"", ".", ".."}:
        raise ValueError("A compilation directory needs a protocol and target name.")
    return cleaned


def _output_directory(
    compilation: Compilation, to: str | Path | None | _DefaultOutput
) -> Path | None:
    if to is None:
        return None
    if isinstance(to, _DefaultOutput):
        configured = os.environ.get("LAB_HOME")
        root = Path(configured).expanduser() if configured else Path.home() / ".lab"
        return root / _segment(compilation.protocol.name) / _segment(compilation.target.name)
    return Path(to)


def _write_output(directory: Path, files: dict[str, str]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (directory / name).write_text(text, encoding="utf-8")
    for name in _BUNDLE_FILES:
        path = directory / name
        if name not in files and path.is_file():
            path.unlink()


@overload
def compile(
    protocol: Protocol | RecordedProtocol | Assembly | AssemblyRequest,
    target: Target | None = None,
    *,
    deck: Deck | None = None,
    liquid_handler: LiquidHandler | None = None,
    inputs: None = None,
    to: str | Path | None = ...,
) -> Compilation: ...


@overload
def compile(
    protocol: Transformation | TransformationRequest,
    target: Target | None = None,
    *,
    deck: Deck | None = None,
    liquid_handler: LiquidHandler | None = None,
    inputs: OutputManifest | None = None,
    to: str | Path | None = ...,
) -> Compilation: ...


@overload
def compile(
    protocol: PlatingRequest,
    target: Target | None = None,
    *,
    deck: Deck | None = None,
    liquid_handler: LiquidHandler | None = None,
    inputs: OutputManifest,
    to: str | Path | None = ...,
) -> Compilation: ...


@overload
def compile(
    protocol: ExperimentPlan,
    target: Target | LiquidHandler | Mapping[str, Target | Deck],
    *,
    liquid_handler: LiquidHandler | None = None,
    to: str | Path | None = ...,
) -> ExperimentCompilation: ...


def compile(
    protocol: (
        Protocol
        | RecordedProtocol
        | ExperimentPlan
        | Assembly
        | AssemblyRequest
        | Transformation
        | TransformationRequest
        | PlatingRequest
    ),
    target: Target | LiquidHandler | Mapping[str, Target | Deck] | None = None,
    *,
    deck: Deck | None = None,
    liquid_handler: LiquidHandler | None = None,
    inputs: OutputManifest | None = None,
    to: str | Path | None | _DefaultOutput = _DEFAULT_OUTPUT,
) -> Compilation | ExperimentCompilation:
    """Compile a protocol, an experiment, or a cloning request.

    One assembly uses its id as the protocol name. An assembly request names a
    protocol that holds every assembly. One transformation uses its id as the
    protocol name. A transformation or plating request takes ``inputs`` as the
    upstream manifest. Leave out ``deck`` and ``liquid_handler`` for a manual
    document. Pass ``deck`` with a liquid handler to compile for a robot. The
    bundle is written to ``~/.lab/<protocol>/<target>/``, or to ``to``.
    ``LAB_HOME`` replaces ``~/.lab``. ``to=None`` skips the write. A deck
    contains shared requirements and optional Lab-owned layouts. The selected
    backend validates and translates its layout or supported preset. Concrete
    backend targets are also accepted for low-level integrations. An experiment
    accepts a handler, one document target, or an exact stage-to-target/deck
    mapping; use its compilation's write() method to save the complete bundle.
    """
    if inputs is not None and not isinstance(
        protocol, (Transformation, TransformationRequest, PlatingRequest)
    ):
        raise TypeError("Pass inputs with a transformation or plating request.")
    if isinstance(protocol, ExperimentPlan):
        if deck is not None or isinstance(target, Deck):
            raise TypeError("Map stage identities to decks when compiling an experiment")
        experiment_target = target if target is not None else Manual()
        if isinstance(experiment_target, Mapping) and set(experiment_target) != {
            stage.identity for stage in protocol.stages
        }:
            raise ValueError("Hardware mapping must cover exactly the experiment's stages")
        stages = []
        for stage in protocol.stages:
            stage_target = (
                stage.deck
                if isinstance(experiment_target, LiquidHandler)
                else experiment_target[stage.identity]
                if isinstance(experiment_target, Mapping)
                else experiment_target
            )
            handler = (
                experiment_target
                if isinstance(experiment_target, LiquidHandler)
                else liquid_handler
            )
            if stage.external:
                if isinstance(experiment_target, Mapping) and not isinstance(stage_target, Manual):
                    raise ValueError("Map external preparation stages to Manual targets")
                stage_target = Manual()
                handler = None
            stages.append(_compile_protocol(stage.protocol, stage_target, liquid_handler=handler))
        experiment_compilation = ExperimentCompilation(experiment=protocol, stages=tuple(stages))
        if to is not None and not isinstance(to, _DefaultOutput):
            experiment_compilation.write(to)
        return experiment_compilation
    if isinstance(target, (LiquidHandler, Mapping)):
        raise TypeError("A single protocol requires a target or a Deck")
    if isinstance(target, Deck):
        raise TypeError("Pass a deck with deck=.")
    if deck is not None and target is not None:
        raise TypeError("Pass a target or a deck.")
    if deck is None and target is None and liquid_handler is not None:
        raise TypeError("Pass deck= with liquid_handler.")
    hardware = deck if deck is not None else target if target is not None else Manual()
    work: Protocol | RecordedProtocol
    if isinstance(protocol, Assembly):
        work = build_assembly(AssemblyRequest(id=protocol.id, assemblies=(protocol,)))
    elif isinstance(protocol, AssemblyRequest):
        work = build_assembly(protocol)
    elif isinstance(protocol, Transformation):
        work = build_transformation(
            TransformationRequest(id=protocol.id, transformations=(protocol,)),
            inputs=inputs,
        )
    elif isinstance(protocol, TransformationRequest):
        work = build_transformation(protocol, inputs=inputs)
    elif isinstance(protocol, PlatingRequest):
        work = build_plating(protocol, inputs=inputs)
    else:
        work = protocol
    compilation = _compile_protocol(work, hardware, liquid_handler=liquid_handler)
    directory = _output_directory(compilation, to)
    if directory is not None:
        _write_output(directory, compilation.files)
        compilation = replace(compilation, directory=directory)
    return compilation
