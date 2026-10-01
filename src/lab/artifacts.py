"""Deterministic artifact encoding and non-destructive bundle writes."""

import hashlib
import json
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any

from lab.units import number


@dataclass(frozen=True, kw_only=True)
class SourceArtifact:
    name: str
    text: str

    def __post_init__(self) -> None:
        relative = PurePosixPath(self.name)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError("Source artifacts require a relative bundle path")
        if not isinstance(self.text, str):
            raise TypeError("Source artifact content must be text")

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()


def encode(value: Any) -> Any:
    if isinstance(value, Decimal):
        return number(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: encode(getattr(value, field.name))
            for field in fields(value)
            if not field.name.startswith("_")
        }
    if isinstance(value, (tuple, list)):
        return [encode(item) for item in value]
    if isinstance(value, dict):
        return {key: encode(item) for key, item in value.items()}
    return value


def canonical_json(value: Any) -> str:
    return (
        json.dumps(encode(value), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n"
    )


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def write_bundle(directory: str | Path, files: dict[str, str]) -> Path:
    directory = Path(directory)
    for name, content in files.items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError(f"Invalid bundle path {name!r}")
        path = directory / name
        if path.exists() and path.read_text(encoding="utf-8") != content:
            raise FileExistsError(f"{path} already contains a different artifact")
    for name, content in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return directory
