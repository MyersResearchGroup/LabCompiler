# Protocol worksheet UI

`Compilation.files["protocol.html"]` is a standalone document. Python renders the complete worksheet, then embeds the packaged CSS and JavaScript. It opens directly from disk, needs no network access, and has a print layout. JavaScript adds inspection and chart selection to content that already exists in the document.

The implementation lives in `src/lab/documents/`:

| File or directory | Responsibility |
| --- | --- |
| `models.py` | Typed presentation records for resources, wells, references, procedure rows, and liquid classes. |
| `prepare.py` | Derive those records from a compilation, including initial loads, sample identity, physical bindings, incoming transfers, and calculated final volumes. |
| `flow.py` | Calculate the numeric phases and coordinates of idealized flow profiles. |
| `templates/worksheet.html` | Compose the page and embed its assets. |
| `templates/sections/` | Materials, ordered procedure, final volumes, and reference details. |
| `templates/components/` | Reusable references, tables, headings, fields, detail cards, inspector, and flow charts. |
| `assets/` | Page and component styles, responsive and print rules, and interactive components. |
| `render.py` | Configure Jinja, load packaged assets in a fixed order, and render the page. |
| `descriptions.py` | Plain-text operation descriptions shared with robot backends. |

## Data and rendering

The recorded protocol and target plan supply the data. `build_worksheet()` prepares frozen dataclasses containing text, quantities, and references. It does not produce HTML. Flow profiles use nominal volume divided by rate; they do not represent measured device traces.

Jinja templates own HTML and SVG. Shared macros in `components/ui.html` render links, tables, detail fields, and card frames; the sections and detail-card components compose them. A reference has one stable anchor and a label, so the same well can be linked from materials, procedure steps, final volumes, and other cards.

Autoescaping applies to every template, including attributes and SVG. Missing template fields raise an error. Only the repository's static CSS and JavaScript use `safe`; protocol text must always pass through normal autoescaping. Keep HTML out of the Python view data and compiler models.

## Interactive components

`<lab-inspector>` owns the preview panel, pinning, positioning, dismissal, and focus handling. It handles references inside its enclosing `<lab-worksheet>` and clones the corresponding reference card into its panel. The original cards remain available in the reference section. Without JavaScript, links navigate directly to those cards.

`<lab-flow-profile>` owns its operation selector and phase readout. It works both in the reference section and in an inspector card. Its `selectOperation(stepId)` method lets the inspector choose the operation associated with a clicked procedure row. The inspector does not calculate chart data or handle chart controls.

Both components use light DOM, so the templates, stylesheet, browser accessibility tree, and print rules share the same markup. Each instance owns its state and removes listeners and observers when disconnected.

## Making changes

For a content change, update the relevant view record and preparation code, then its template. For a visual change, edit the component template and stylesheet. For a new interaction, give the component its own behavior instead of adding chart or card logic to the page renderer. Add any new asset to the ordered manifests in `render.py`; there is no separate frontend build or runtime asset fetch.

Run `uv run pytest tests/test_documents.py` for operation fidelity, escaping, references, and flow calculations. Regenerate the concentration-series example to check the complete worksheet:

```sh
uv run python -m examples.concentration_series \
  --csv examples/concentration_series/layout.csv \
  --config examples/concentration_series/config.toml --target manual
```

Open `build/concentration_series/csv/manual/protocol.html`. Check hover and click inspection, nested references, Tab and Escape, operation selection and chart phase readouts, a narrow viewport, print preview, and reference navigation with JavaScript disabled. Templates and assets ship inside the Python package, so packaging changes also need a render check from the built wheel.
