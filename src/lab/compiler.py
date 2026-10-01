"""Snapshot, validate, prepare a target, and emit a self-contained bundle."""

import hashlib
import json
import os
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any
from typing import Protocol as Interface

import lab.documents as documents
from lab._version import __version__
from lab.deck import Deck
from lab.labop import export as export_labop
from lab.model import Distribute, Mix, RecordedProtocol, TargetPlan, Transfer, encode
from lab.protocol import Protocol
from lab.samples import Location, OutputManifest
from lab.targets.liquid_handler import LiquidHandler
from lab.targets.lower import lower_deck
from lab.targets.manual import Manual
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
        result = {
            "plan.json": self.plan_json,
            "protocol.html": documents.render(self),
            "protocol.labop.ttl": export_labop(self.protocol).text,
        }
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


class _DefaultOutput:
    """Marks compile's default output directory."""


_DEFAULT_OUTPUT = _DefaultOutput()
_BUNDLE_FILES = ("protocol.html", "plan.json", "manifest.json", "protocol.py", "protocol.labop.ttl")


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


def compile(
    protocol: Protocol,
    target: Target | None = None,
    *,
    deck: Deck | None = None,
    liquid_handler: LiquidHandler | None = None,
    to: str | Path | None | _DefaultOutput = _DEFAULT_OUTPUT,
) -> Compilation:
    """Snapshot and compile a protocol for a manual document or a robot.

    Experiment builders produce the protocol before target selection. Pass
    ``deck`` with a liquid handler for a robot, or omit both for a manual document.
    Bundles are written to ``~/.lab/<protocol>/<target>/``; ``LAB_HOME`` changes
    that root, ``to`` selects a directory, and ``to=None`` skips writing.
    """
    if not isinstance(protocol, Protocol):
        raise TypeError("Compile a Protocol; use an experiment builder to turn designs into one.")
    if isinstance(target, Deck):
        raise TypeError("Pass a deck with deck=.")
    if deck is not None and target is not None:
        raise TypeError("Pass a target or a deck.")
    if deck is None and target is None and liquid_handler is not None:
        raise TypeError("Pass deck= with liquid_handler.")
    hardware: Target | Deck = (
        deck if deck is not None else target if target is not None else Manual()
    )
    recorded = protocol.snapshot()
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
    compilation = Compilation(recorded, prepared, tuple(volumes.items()))
    directory = _output_directory(compilation, to)
    if directory is not None:
        _write_output(directory, compilation.files)
        compilation = replace(compilation, directory=directory)
    return compilation
