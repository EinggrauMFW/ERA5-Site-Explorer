# WP10: device picker dialog with a heatmap preview (front end)

Goal: in the Device tab, **Choose from catalogue** opens a dialog. The user browses the devices in the catalogue
(WP9), sees the power matrix as a heatmap with its provenance and caveats, and picks one. The pick fills the
Device form and runs the assessment. Everything is on the client; the API already exists.

## The API you build on (WP9, already merged into your working tree)

- `GET /api/devices` returns `{"configured", "directory", "devices": [...], "warnings": [...]}`. Each device
  summary has `id, name, rated_kw, width_m, period_type ("tp"|"te"|"unknown"), source, provenance, origin,
  synthetic, n_hm0, n_period, hm0_range, period_range, power_max_kw, defined_cells, total_cells, defined_pct,
  near_max_cells, n_notes`.
- `GET /api/devices/<id>` returns the summary plus `notes` (list of strings), `hs_m`, `period_s`, `power_kw`
  (rows; `null` = undefined), `bin_convention`, `hm0_edges` and `period_edges` (length n+1).
- `POST /api/jobs/<job id>/device` now accepts `device_id` instead of `csv_text`; the response and the saved
  result carry `catalogue: {id, name, source, provenance, notes, synthetic}` for catalogue devices.
- Use `EraExplorer.api(path, options)` for every call (it appends the selected grid node and throws an `Error`
  with the server message).

If anything in that contract is missing or different when you read the code, say so in your report instead of
working around it.

## Files you own

- `static/plugins/devices.js` and `static/plugins/devices.css` (new; the loader picks them up because the
  backend plugin `plugin_devices` exists; the script is wrapped in an IIFE).
- `static/plugins/device.js`: ONLY the button, the picked-device state, the assess payload, and showing the
  catalogue provenance in the result. Do not restructure the rest.
- `static/plugins/device.css` only if the button or chip needs a style that does not belong in `devices.css`.

No Python, no tests, no other files. A change you think is needed elsewhere goes under "Requests to the
orchestrator".

## Requirements

### 1. The dialog (`devices.js`)

Expose `EraExplorer.openDevicePicker({ initialId, onPick })`. It uses a native `<dialog>` with `showModal()`
(focus trap, Escape to close and the backdrop come with it), is created on first use, removed from the DOM
or reused on later opens, and restores focus to the button that opened it when it closes. Heading: "Choose a
device power matrix". A close button with an accessible name. `aria-labelledby` points at the heading.

**Layout.** Two panes at 900 px or wider: a device list on the left (about 280 px), the preview on the right.
Below 900 px the panes stack, and below 600 px the dialog fills the screen (no horizontal page scroll at 375 px;
the heatmap scales to the width). The dialog never exceeds the viewport height: the body scrolls inside it.

**Loading states.** While loading, a plain "Loading devices…" line. A failed request shows the server's message
in the existing `.form-error` style with a Retry button. If `configured` is false and the only device is the
synthetic example, show a short note: the catalogue folder is set with the `DEVICES_DIR` environment variable,
naming the variable and saying devices are read from `devices.json` and `*.csv` files in that folder. If
`warnings` is not empty, show them in a collapsed `<details>` titled "N files were skipped" with each message
as text.

**The list** (`role="listbox"`, items `role="option"`, `aria-selected`; arrow keys move the selection, Home and
End jump, Enter or double-click picks). Each item shows the name, a line `rated 750 kW · 15 × 17 cells` (rated
power only when known), and small badges: the period type as printed (`Tp`, `Te`, or `period axis not
stated`), and `synthetic` for the example or `unverified` when the provenance text contains the word
"unverified" (case-insensitive) or the entry is a CSV with no metadata. Do not invent other classifications.
The first device is selected, or `initialId` if given. Selecting loads the detail (`GET /api/devices/<id>`);
keep the previous preview until the new one arrives, and ignore a response for a device that is no longer
selected.

### 2. The heatmap (`devices.js`)

An inline SVG, one `<rect>` per cell, drawn from `hm0_edges` and `period_edges` (cells are the bins the
assessment uses, not points). Period increases to the right; Hm0 increases **upward**.

- **Colour.** Linear in power from 0 to the table maximum, using the same technique as the Screening heat
  table so both themes work: a `color-mix(in srgb, var(--chart-line) P%, var(--surface-2))` fill set through
  the element's style, with `P = 100 × kW / max`. Colours must come from the CSS variables only. Do not hard
  code any colour.
- **Undefined cells** (`null`) are drawn with a hatched SVG pattern (stroke from `var(--muted)` or similar), not
  as zero. They must be visibly different from a genuine 0 kW cell, which is the lowest colour.
- **Edges can be slightly negative.** Edges are extended by half a spacing at the ends, so CorPower's first
  period edge is -0.0005 s (the real file). Never draw a negative tick or label: start the axes at 0 when the
  first edge is below 0 by less than one spacing, and clip the first cell to 0.
- **Axes.** Ticks and labels with units: "Hm0 (m)" and the period label that matches the device (`Tp (s)`,
  `Te (s)`, or `Period (s)` when not stated), tick values rounded sensibly (at most 6 to 8 ticks per axis, no
  overlapping labels at 375 px). Use `EraExplorer.fmtNum` or `EraExplorer.fmt` for numbers.
- **Colour legend**: a horizontal gradient bar built with the same colour function, labelled `0` and the maximum
  in kW, plus a swatch with the hatch labelled "undefined in the source (counts as 0 kW in the assessment)".
  Only show the swatch when the matrix has undefined cells.
- **Readout.** Hovering a cell, or moving the keyboard cursor over it, shows a line below (and a tooltip
  next to the pointer): `Hm0 3.5 m · Tp 7.5 s → 440 kW` (with `(59% of rated)` when rated power is known, and
  `undefined` for a null cell). The readout element is `aria-live="polite"`. The SVG is one tab stop
  (`tabindex="0"`, `role="application"` or `img` with a clear `aria-label`): arrow keys move a visible
  cursor cell (outline from `var(--ink)`), Home and End go to the row ends. Provide the same information to
  screen readers through the label, which states the device, the size, and the maximum power.
- **Size.** The SVG uses a `viewBox` and scales to its container; cells never get narrower than the grid
  allows (a 50 × 48 matrix must still render quickly and legibly at 600 px: no per-cell DOM listeners, use one
  pointer handler on the SVG that maps the position to a cell).

### 3. The information panel

Under or beside the heatmap, in this order:

1. The device name, and `source` and `provenance` as plain text paragraphs.
2. **Caveats** from `notes`, as a list, with a heading "Read before using". If the device has no notes, do not
   show the heading. Show every note; do not truncate.
3. A small table (`table.kv`): matrix size (`15 × 17 cells`), Hm0 range (m), period range (s), maximum power
   (kW), defined cells (`N of M`, `P%`), and `Cells at 99% of the maximum or above: N`, with the hint "a
   plateau: rated power, or a cap on the source's colour scale".
4. **Settings used when picked**, editable and prefilled from the device: rated power (kW, optional), characteristic
   width (m, optional), and **Period axis**: a select with `Te`, `Tp`, `Unknown: evaluate both`, preselected to
   the device's `period_type` (`unknown` when not stated). When the device says Tp, show this exact sentence
   under the select: "The source labels this axis Tp. If the manufacturer's matrix really uses the energy period,
   choose Te or Unknown." (Do not assert which is right: it is unresolved.)

### 4. Picking, and the Device form (`device.js`)

- A **Choose from catalogue…** button in the Device form next to the CSV upload (`id="device_catalogue"`), which
  opens the picker.
- `onPick` receives `{ id, name, rated_kw, width_m, period_type, bin_convention }` (the edited settings). Then:
  fill the form fields (`device_name` with the name, `device_rated`, `device_width`, `device_period`,
  `device_bin`), remember the catalogue id, show a chip next to the CSV input reading `Matrix: <name>
  (catalogue)` with a button to clear it, clear `device_csv` and `panel.dataset.csvText`, and **run the
  assessment immediately**. The user can change fields and press Assess again.
- While a catalogue device is chosen the Assess click sends `device_id` and no `csv_text`. Choosing a CSV file,
  or the synthetic example link, or clearing the chip, clears the catalogue choice (one source at a time).
- A failed assessment shows the server's message in the form's existing error element, and keeps the form
  filled.
- In the result, when `catalogue` is present, show a short block before the numbers: "Matrix source", the
  provenance, and the notes in a `<details>` titled "Caveats from the source" (collapsed). Plain text only.

### 5. Styles (`devices.css`)

Use the CSS variables for colour, spacing consistent with `.card`/`.field`, and no inline styles for anything a
class can do (the SVG fills are the exception). Check both themes. Focus rings must be visible. Respect
`prefers-reduced-motion` (no animation is required).

## Acceptance

- `node --check` passes for every JavaScript file you touched.
- The app serves the new files (`curl` the script and the stylesheet) and the Device tab still works with a CSV
  upload (the code path for uploads is unchanged).
- Be exact in your report about what you could and could not check: you cannot judge appearance. List, for each
  numbered requirement above, the function or selector that implements it.
- The orchestrator will check the dialog in a real browser at desktop and phone width, in both themes, with the
  four real matrices (Pelamis has undefined cells, CorPower is 50 × 48), by keyboard and by mouse.
