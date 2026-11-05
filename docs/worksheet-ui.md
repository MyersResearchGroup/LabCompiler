# Protocol worksheet UI

`Compilation.files["protocol.html"]` is a standalone document. Python renders the complete worksheet, then embeds the packaged CSS and JavaScript. It opens directly from disk, needs no network access, and has a print layout. JavaScript adds inspection and chart selection to content that already exists in the document.

The implementation lives in `src/lab/documents/`:

| File or directory | Responsibility |
| --- | --- |
| `models.py` | Typed presentation records for resources, wells, references, procedure rows, and liquid classes. |
| `prepare.py` | Derive those records from a compilation, including initial loads, sample identity, physical bindings, incoming transfers, and calculated final volumes. |
| `flow.py` | Calculate the numeric phases and coordinates of idealized flow profiles. |
| `preparation.py` | Turn the compiler's volume trace into plate geometry and a snapshot after each operation. |
| `templates/worksheet.html` | Compose the page and embed its assets. |
| `templates/sections/` | Advisories, materials, preparation preview, ordered procedure, final volumes, and reference details. |
| `templates/components/` | Reusable references, tables, headings, fields, detail cards, plate maps, inspector, and flow charts. |
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

`<lab-preparation>` displays the logical resource grids and lets the reader play, step, or scrub through the procedure. It uses `volume_trace()` from the compiler's validator; JavaScript selects precomputed snapshots and does no volume accounting. A snapshot is the state after one complete protocol operation, including all destinations of a distribution. Mixes, waits, temperature operations, and operator instructions retain the recorded liquid volumes. Source and destination outlines identify transfers; a dashed outline identifies mixing and temperature operations. Filled well area is proportional to volume divided by the smaller of declared and bound capacities.

The maps represent logical row and column coordinates. They do not infer robot trajectories, physical deck geometry, tip use, mixing completeness, or chemical composition. Initial reagent labels and sample identities come directly from the protocol. Well details include physical bindings. Playback uses one continuous clock for the pipette, dispensing drops, and position slider, allowing more time for distributions and repeated mixes. Between operations, the empty pipette travels to the next source in a short transition. Pausing preserves the current position; scrubbing selects a fractional position within an operation, and keyboard arrows step through whole operations. Playback starts only on request and pauses when the preview leaves view, the tab is hidden, or the reader inspects a well. Reduced-motion mode disables fill and pipette animations.

`<lab-transfer-motion>` draws directional paths between the rendered wells and animates a schematic pipette, aspiration, and dispensing drops. A distribution connects its source and ordered destinations; mixing repeats its recorded cycles in place. Temperature operations have no pipette motion. The component measures the actual displayed well positions, redraws on resize or plate scrolling, and leaves a static route available while paused or in reduced-motion mode. The animation illustrates the selected operation; the numeric snapshot remains explicitly labeled as its after-state. It is independent of hardware choreography and duration: a target may refill the tip between destinations.

For the Manual target, a distribution represents a separate measured aspiration and complete dispense for each destination. The animation returns to the source empty between wells and the flow profile uses the same alternating aspiration/dispense phases. The grouped procedure step and its total delivered volumes remain unchanged. This avoids depicting repeated partial strokes from a single large load with an ordinary manual micropipette.

The Advisories section lists `Compilation.diagnostics` with links to the procedure steps and wells they name; it is omitted when there are none.

The overview counts one fresh tip for each `Transfer`, `Distribute`, or `Mix`, matching the current target policy. A distribution reuses its tip across its destinations. The concentration-series recipe explicitly groups identical source/dose additions into distributions; the document renderer does not reorder operations.

Each preview change publishes `lab:previewchange` inside its worksheet. `<lab-well-volume>` uses that event to display the selected state's amount in reagent lists and well-detail cards, including cloned inspector cards. The preview also exposes `currentFrame` so a newly connected volume display can read the selected snapshot. This keeps preview-specific behavior out of the inspector. Procedure step numbers link back to the matching snapshot.

The initial plate maps and loads remain visible without JavaScript. Printing selects the initial state and hides playback controls; closing print preview restores the selected state. Each plate is a keyboard stop: arrow keys move between used wells in a row or column, Home/End move to the first/last used well, and Enter opens its details.

## Making changes

For a content change, update the relevant view record and preparation code, then its template. For a visual change, edit the component template and stylesheet. For a new interaction, give the component its own behavior instead of adding chart or card logic to the page renderer. Add any new asset to the ordered manifests in `render.py`; there is no separate frontend build or runtime asset fetch.

Run `uv run pytest tests/test_documents.py tests/test_preparation_view.py` for operation fidelity, escaping, references, flow calculations, and plate snapshots. Regenerate the concentration-series example to check the complete worksheet:

```sh
uv run python -m examples.concentration_series \
  --csv examples/concentration_series/layout.csv \
  --config examples/concentration_series/config.toml --target manual
```

Open `build/concentration_series/csv/manual/protocol.html`. Check hover and click inspection, nested references, Tab and Escape, operation selection and chart phase readouts, a narrow viewport, print preview, and reference navigation with JavaScript disabled. Templates and assets ship inside the Python package, so packaging changes also need a render check from the built wheel.
