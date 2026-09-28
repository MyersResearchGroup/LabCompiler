"""Presentation helpers for the reporter-reference notebook.

These functions inspect ordinary Lab objects and emitted files. They do not
construct plans, change RDF assertions, or generate execution observations.
"""

import hashlib
import json
from collections.abc import Iterable, Mapping
from html import escape
from os.path import relpath
from pathlib import Path
from tempfile import mkdtemp

from IPython.display import HTML, SVG, Code, Markdown, display
from rdflib import RDF, Graph, Namespace, URIRef
from rdflib.term import Node

from lab.compiler import Compilation
from lab.experiments.cloning.planning import BuildPlan
from lab.inventory import Inventory
from lab.provenance import DocumentSnapshot

EXAMPLE = Path(__file__).resolve().parent
ROOT = EXAMPLE.parents[1]
CASE = EXAMPLE / "data/reporter_reference"
EX = Namespace("https://example.org/reporter/")
SBOL = Namespace("http://sbols.org/v3#")
PROV = Namespace("http://www.w3.org/ns/prov#")
LAB = Namespace("https://the-lab-compiler.github.io/lab-py/ns#")
UML = Namespace("http://bioprotocols.org/uml#")
LABOP = Namespace("http://bioprotocols.org/labop#")
OM = Namespace("http://www.ontology-of-units-of-measure.org/resource/om-2/")


def fresh_output() -> Path:
    parent = ROOT / "build/cloning-notebook"
    parent.mkdir(parents=True, exist_ok=True)
    return Path(mkdtemp(prefix="reporter-", dir=parent))


def table(headers: Iterable[object], rows: Iterable[Iterable[object]]) -> None:
    head = "".join(f"<th>{escape(str(value))}</th>" for value in headers)
    body = "".join(
        "<tr>"
        + "".join(
            f'<td style="text-align:left;overflow-wrap:anywhere">{escape(str(value))}</td>'
            for value in row
        )
        + "</tr>"
        for row in rows
    )
    display(HTML(f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"))


def inventory(inventory: Inventory, document: DocumentSnapshot) -> None:
    table(
        ("Material record", "Intended design", "Form", "Available", "Location"),
        (
            (
                document.resolve(stock.implementation).name,
                document.resolve(stock.design).name,
                stock.form.value,
                f"{stock.amount} {'unit' if stock.form.counted else 'µL'}",
                f"{stock.location.container} / {stock.location.position}"
                if stock.location
                else "not assigned",
            )
            for stock in inventory.stocks
            if stock.form.value in {"bacterial_stab", "dna", "competent_cells"}
        ),
    )


def plan(plan: BuildPlan) -> None:
    labels = {
        "preparation": "Prepare vector DNA",
        "assembly": "Assemble reporter design",
        "transformation": "Introduce it into host H1",
        "plating": "Deposit a sample",
    }
    table(
        ("Order", "Task", "Expected material", "Responsible system"),
        (
            (
                index,
                labels[task.kind.value],
                f"{task.output_form.value}: "
                + (
                    f"{task.output_count} sample"
                    if task.output_form.counted
                    else f"{task.output_volume_ul} µL"
                ),
                "External procedure P01" if task.kind.value == "preparation" else "Liquid handler",
            )
            for index, task in enumerate(plan.tasks, 1)
        ),
    )
    positions = [(16, 66), (254, 66), (492, 66), (730, 8), (730, 126)]
    if len(plan.tasks) != len(positions):
        return
    numbers = {task.identity: index for index, task in enumerate(plan.tasks)}
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 210" '
        'role="img" aria-label="Preparation, assembly, transformation, and two deposited samples">',
        '<rect width="960" height="210" fill="#f7f9fc" rx="12"/>',
        '<defs><marker id="reporter-arrow" markerWidth="8" markerHeight="8" '
        'refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8" '
        'fill="#53677d"/></marker></defs>',
    ]
    for index, task in enumerate(plan.tasks):
        x, y = positions[index]
        for parent in task.depends_on:
            px, py = positions[numbers[parent]]
            parts.append(
                f'<path d="M{px + 210},{py + 34} L{x - 7},{y + 34}" '
                'stroke="#53677d" stroke-width="2" marker-end="url(#reporter-arrow)"/>'
            )
        parts.extend(
            [
                f'<rect x="{x}" y="{y}" width="210" height="68" rx="9" '
                'fill="white" stroke="#b7c8da"/>',
                f'<text x="{x + 12}" y="{y + 27}" fill="#162f49" '
                f'font-family="sans-serif" font-size="16">{index + 1}. '
                f"{escape(task.kind.value.title())}</text>",
                f'<text x="{x + 12}" y="{y + 49}" fill="#53677d" '
                f'font-family="sans-serif" font-size="13">{escape(task.output_form.value)}</text>',
            ]
        )
    parts.append("</svg>")
    display(SVG("".join(parts)))


def _excerpt(source: Graph, selected: Graph, prefixes: Mapping[str, str]) -> str:
    for name, namespace in {
        "ex": str(EX),
        "sbol": str(SBOL),
        "prov": str(PROV),
        "lab": str(LAB),
        "uml": str(UML),
        "labop": str(LABOP),
        "om": str(OM),
        "build": str(EX.build) + "/",
        **prefixes,
    }.items():
        selected.bind(name, Namespace(namespace), replace=True)
    # Keep typed numeric literals explicit. RDFLib's compact Turtle writer can
    # change a decimal's lexical spelling (for example "1" to 1.0).
    used = set()

    def term(node: Node) -> str:
        rendered = node.n3(namespace_manager=selected.namespace_manager)
        for prefix, _ in selected.namespaces():
            if rendered.startswith(prefix + ":") or "^^" + prefix + ":" in rendered:
                used.add(prefix)
        return rendered

    blocks = []
    for subject in sorted(set(selected.subjects()), key=str):
        statements = []
        for predicate in sorted(
            set(selected.predicates(subject)), key=lambda value: (value != RDF.type, str(value))
        ):
            objects = ", ".join(
                term(obj) for obj in sorted(selected.objects(subject, predicate), key=str)
            )
            statements.append(f"{'a' if predicate == RDF.type else term(predicate)} {objects}")
        blocks.append(term(subject) + " " + " ;\n    ".join(statements) + " .")
    declarations = [
        f"@prefix {prefix}: <{namespace}> ."
        for prefix, namespace in selected.namespaces()
        if prefix in used
    ]
    text = "\n".join(declarations) + "\n\n" + "\n\n".join(blocks) + "\n"
    # Excerpts must be real triples from the source, with only notation changed.
    assert len(selected) and set(selected) <= set(source)
    assert set(Graph().parse(data=text, format="turtle")) == set(selected)
    return text


def turtle(
    path: Path,
    subjects: Iterable[str],
    *,
    predicates: Iterable[str] | None = None,
    follow: Iterable[str] = (),
    prefixes: Mapping[str, str] | None = None,
) -> None:
    """Show an actual RDF subset using compact Turtle notation.

    Housekeeping identifiers, redundant SBOL base classes, and embedded semantic
    JSON are omitted. Explicit subject and predicate selection keeps each view
    small; the original artifact is never modified.
    """
    source = Graph().parse(path, format="turtle")
    selected = Graph()
    allowed = None if predicates is None else {URIRef(value) for value in predicates}
    follow_edges = {URIRef(value) for value in follow}
    pending = [URIRef(value) for value in subjects]
    visited = set()
    excluded = {SBOL.displayId, SBOL.hasNamespace, LAB.semanticStep, LAB.planDigest}
    while pending:
        subject = pending.pop()
        if subject in visited:
            continue
        visited.add(subject)
        for triple in source.triples((subject, None, None)):
            _, predicate, obj = triple
            if predicate in excluded or (allowed is not None and predicate not in allowed):
                continue
            if predicate == RDF.type and obj in {SBOL.TopLevel, SBOL.Identified}:
                continue
            selected.add(triple)
            if predicate in follow_edges:
                pending.append(obj)
    display(Code(_excerpt(source, selected, prefixes or {}), language="turtle"))


def labop_amount(path: Path, step: str) -> None:
    """Follow an action's amount pin into its measured value in the real RDF."""
    source = Graph().parse(path, format="turtle")
    action = URIRef(step)
    pin = URIRef(step + "/input_amount")
    value = source.value(pin, UML.value, any=False)
    if value is None:
        raise ValueError("Select an action with an explicit amount parameter")
    measure = source.value(value, UML.identifiedValue, any=False)
    if measure is None:
        raise ValueError("Select an action with an explicit amount parameter")
    selected = Graph()
    for subject, predicates in (
        (action, (RDF.type, UML.behavior)),
        (pin, (RDF.type, SBOL.name, UML.value)),
        (value, (RDF.type, UML.identifiedValue)),
        (measure, (RDF.type, OM.hasNumericalValue, OM.hasUnit)),
    ):
        for predicate in predicates:
            for triple in source.triples((subject, predicate, None)):
                if predicate != RDF.type or triple[2] != SBOL.Identified:
                    selected.add(triple)
    selected.add((action, UML.input, pin))
    protocol = step.rsplit("/", 1)[0]
    display(
        Code(
            _excerpt(
                source,
                selected,
                {
                    "stage": protocol + "/",
                    "step": step + "/",
                    "pin": str(pin) + "/",
                    "amount": str(value) + "/",
                    "liquid": "https://bioprotocols.org/labop/primitives/liquid_handling/",
                },
            ),
            language="turtle",
        )
    )


def source(stage: Compilation, step: str) -> None:
    span = next(row for row in stage.source_map if row["step"] == step)
    if stage.target.source is None:
        raise ValueError("Select an automated stage")
    lines = stage.target.source.splitlines()
    display(Markdown(f"`protocol.py`, lines **{span['start_line']}–{span['end_line']}**"))
    display(Code("\n".join(lines[span["start_line"] - 1 : span["end_line"]]), language="python"))


def links(directory: Path, paths: Mapping[str, str]) -> None:
    relative = Path(relpath(directory, start=EXAMPLE)).as_posix()
    display(Markdown("\n".join(f"- [{label}]({relative}/{path})" for label, path in paths.items())))


def check_bundle(directory: Path) -> int:
    checksums = json.loads((directory / "bundle.json").read_text())["sha256"]
    for name, expected in checksums.items():
        assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == expected
    return len(checksums)
