---
name: plotly-browser-validation
description: "Use when rendering, inspecting, or validating Plotly plots in the VS Code integrated browser, including exported HTML, notebook figures, localhost previews, failed file:// loading, overlapping titles or legends, range sliders, and axis zoom interactions."
---

# Plotly Browser Validation

## Goal

Check the actual rendered figure, not just its Python layout configuration.
Export self-contained HTML, serve that one file over loopback HTTP, inspect it
with browser tools, and clean up the temporary preview afterward.

Direct `file://` opening failed during this repository's browser checks.
The working approach was inline Plotly.js plus a localhost HTTP URL. Prefer
that approach from the start instead of repeatedly retrying local-file URLs.

## Procedure

### 1. Prepare the exact figure

- Read the relevant plotting cells and preserve the user's dataset, parameters,
  output files, and unrelated notebook outputs.
- Use an existing figure when available. Otherwise run only the necessary
  setup/preparation/plotting in `transit_env`, preferably with trusted local
  snapshots and no downloads. Do not run model fitting just to preview a plot.
- For isolated `nbclient` execution, verify the kernel uses `transit_env`.
  Setting `PLOTLY_RENDERER=plotly_mimetype` in the validation process can make
  the figure JSON available as `application/vnd.plotly.v1+json`. Reconstruct
  it with `plotly.graph_objects.Figure` if needed. This is a validation setting,
  not a reason to change the notebook's permanent renderer.
- Export to a unique, explicitly tracked temporary HTML path:

  ```python
  figure.write_html(html_path, full_html=True, include_plotlyjs=True)
  ```

  `include_plotlyjs=True` bundles Plotly.js. Do not use a CDN for an offline
  browser check. Do not overwrite an existing user export.
  Inline JavaScript does not embed map tiles or URL-based images: check for
  external assets before calling a preview offline, and never expose tokens
  or credentials.

### 2. Serve only that HTML file

Run the bundled [single-file server](./scripts/serve_plotly_html.py) from the
repository root, replacing the example HTML path with the actual export:

```bash
conda run --no-capture-output -n transit_env python -u \
  .github/skills/plotly-browser-validation/scripts/serve_plotly_html.py \
  /absolute/path/to/plot.html
```

- Use the Bash tool's **attached async** mode for this temporary server.
  Retain its actual returned `shellId`; do not detach it, use shell background
  operators, or leave it running after a self-contained check.
- The script binds `127.0.0.1` and chooses an available port. Read its printed
  URL; never guess a port or reuse one from an earlier session. If startup
  output is not yet available, make one short `read_bash` readiness check.
- Request the printed URL with `curl` and require HTTP 200 before opening the
  browser. A running process alone is not readiness.
- The endpoint is `/plot.html`; every other path returns an error. The server
  does not expose a directory or the workspace. It serves the startup snapshot
  of the file, so regenerate and restart after changing the figure.

Only serve agent-generated or explicitly trusted figures. Loopback is not
permission to expose credentials, unrelated files, or restricted content.
Do not use HTTP to bypass access controls on arbitrary files.

### 3. Open and inspect the browser rendering

- Discover deferred browser tools before calling them. Prefer an existing
  shared page and navigate it to the printed HTTP URL. If its page ID is no
  longer available, open a new page and use the new returned ID.
- Confirm that the Plotly graph has rendered, not merely that the page opened.
  Check the console/snapshot for JavaScript errors and use the graph element
  `.js-plotly-plot` to verify its `data` and `_fullLayout` are populated.
- Use native browser tools for ordinary navigation, clicks, and snapshots.
  Use a small, purpose-specific `runPlaywrightCode` snippet when exact geometry
  or axis state cannot be checked with those tools.
- Access the DOM only through the provided `page`, for example:

  ```javascript
  return await page.evaluate(() => {
    const plot = document.querySelector('.js-plotly-plot');
    return {
      traces: plot.data.length,
      verticalZoomLocked: plot._fullLayout.yaxis.fixedrange
    };
  });
  ```

- Return only relevant counts, axis settings, ranges, and bounding rectangles.
  Do not dump the full figure JSON or large HTML into chat.

### 4. Verify the requested layout and interactions

**Layout**

- Check the required number/order of subplot rows, trace placement, labels,
  units, and shared-axis matching.
- Inspect `getBoundingClientRect()` for `.gtitle`, `.legend`, relevant subplot
  annotations, and `.rangeslider-container`. Rectangles must not overlap
  unintentionally. A legend below the plot also needs clearance from the time
  label/slider and must not be clipped by the figure boundary.
- Use a screenshot when visual appearance needs confirmation. Do not infer
  successful spacing merely from a changed legend coordinate or margin.

**Zoom**

- Inspect the browser's computed `_fullLayout`, not only the Python input.
  A visible x-axis range slider can default the perpendicular y-axis to
  `fixedrange=True`.
- Where vertical zoom is intended, set it explicitly. For an all-zoomable
  figure:

  ```python
  figure.update_yaxes(fixedrange=False)
  ```

  Preserve intentionally locked axes when the requested UX requires them.
- Capture the relevant x/y ranges, then use the native zoom control or a box
  drag and read the ranges again. Assert that the x range changes and the
  intended y span shrinks. Shared time axes must remain synchronized, and the
  slider must remain available.
- Wait for completed relayout/rendering rather than relying on a short fixed
  sleep, particularly for large figures or mixed SVG/WebGL traces.
- Synthetic mouse events and automated drag coordinates were unreliable for
  some checks. An automation timeout is not proof that the chart is broken:
  inspect computed axis locks and try a native **Zoom in** click with measured
  before/after ranges before concluding that zoom failed.
- For datetime comparisons, inspect representative trace values and types.
  Plotly may convert a timezone-aware pandas Series into local-wall-time
  `numpy.datetime64` values, while `.tolist()` or a DatetimeIndex can retain
  timezone-aware objects. Compare the intended clock correctly; do not change
  valid timestamps merely to make an incompatible type comparison pass.

### 5. Clean up and report evidence

- Stop the temporary server with `stop_bash` and its actual `shellId`, including
  when a browser check fails. Do not kill processes by name.
- Release an agent-owned preview page when no longer needed. Do not close or
  navigate away from an unrelated user page.
- Delete only the specific temporary HTML/files created for this check.
  Preserve user-requested exports and unrelated files; do not register scratch
  previews as deliverables.
- Report what was verified: HTTP readiness, successful rendering, relevant
  layout geometry, actual zoom changes, and retained shared-axis behavior.
  Distinguish browser rendering from Python construction time.
- If plotting helpers changed, tell the user when their existing notebook
  kernel needs a restart/reimport and rerun. Never restart their live kernel
  or discard notebook state solely for the validation preview.

## Maintaining the server helper

The server uses only Python's standard library. Its
[focused pytest tests](./tests/test_serve_plotly_html.py) check the served
content, loopback URL, unavailable paths, and invalid input files:

```bash
conda run -n transit_env python -m pytest \
  .github/skills/plotly-browser-validation/tests/test_serve_plotly_html.py -q
```
