"""Assemble packaged templates and assets into one offline worksheet."""

from __future__ import annotations

from importlib.resources import files

from jinja2 import Environment, PackageLoader, StrictUndefined

import lab.compiler as compiler
from lab.documents.prepare import build_worksheet
from lab.model import encode
from lab.units import number

STYLESHEETS = (
    "worksheet.css",
    "inspector.css",
    "flow-profile.css",
    "preparation.css",
    "responsive.css",
    "print.css",
)
SCRIPTS = ("flow-profile.js", "transfer-motion.js", "preparation.js", "inspector.js")

environment = Environment(
    loader=PackageLoader("lab.documents", "templates"),
    autoescape=True,
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)
environment.filters.update(
    number=number,
    coordinate=lambda value: f"{value:.2f}",
    short_time=lambda value: f"{value:.3g}",
    encode=encode,
)


def render(compilation: compiler.Compilation) -> str:
    assets = files("lab.documents").joinpath("assets")
    stylesheet = "\n".join(
        assets.joinpath(name).read_text(encoding="utf-8") for name in STYLESHEETS
    )
    script = "\n".join(assets.joinpath(name).read_text(encoding="utf-8") for name in SCRIPTS)
    return environment.get_template("worksheet.html").render(
        document=build_worksheet(compilation),
        stylesheet=stylesheet,
        script=script,
    )
