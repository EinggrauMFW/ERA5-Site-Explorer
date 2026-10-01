"""Format analysis payloads into markdown reports and tabular CSV exports."""

import csv
import datetime
import io
import math
import re
import xml.sax.saxutils

def slug(label_with_unit: str) -> str:
    """Convert a label to a snake_case slug, translating units like % and °."""
    s = str(label_with_unit).lower()
    s = s.replace('%', 'pct')
    s = s.replace('/', '_per_')
    s = s.replace('°', 'deg')
    s = re.sub(r'[^a-z0-9_]+', '_', s)
    s = s.strip('_')
    s = re.sub(r'_+', '_', s)
    return s

def fmt_number(value) -> str:
    """Format a number for display, usually to 3 decimal places."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    try:
        v = float(value)
        # 3 significant decimals means e.g. .3f or something else? 
        # The CONTRACT says "Round for display (3 significant decimals)" but also mentions "Thousands separators none".
        # Actually it says "use `report.fmt_number` (3 significant decimals for display, thousands separators none) consistently"
        # Wait, if the value is 1000, 3 sig decimals usually means `1000.`? No, probably `.3f`.
        # "3 significant decimals" usually means 3 decimal places in this codebase.
        # Let's use `{value:.3f}` but strip trailing zeros? No, the browser uses `toFixed(1)` or similar. 
        # I'll use `{v:.3f}` or maybe formatting it to 3 decimal places.
        # But for integers it might be different. Let's stick to `{v:.3f}`. 
        return f"{v:.3f}"
    except (ValueError, TypeError):
        return "—"

def escape(s) -> str:
    """Escape text for XML injection."""
    return xml.sax.saxutils.escape(str(s))

def find_section(payload, title_substring):
    for sec in payload.get("sections", []):
        if title_substring.lower() in sec.get("title", "").lower():
            return sec
    return None

def scatter_csv(payload, which) -> tuple[str, str] | None:
    """Return (filename, csv_text) for the scatter diagram, or None if missing."""
    if which == "hm0-te":
        section = find_section(payload, "Hm0 vs Te")
        period_col = "te"
    elif which == "hm0-tp":
        section = find_section(payload, "Hm0 vs Tp")
        period_col = "tp"
    else:
        return None

    if not section or section.get("kind") != "scatter":
        return None

    x_edges = section.get("x_edges", [])
    y_edges = section.get("y_edges", [])
    hours_pct = section.get("hours_pct", [])
    energy_pct = section.get("energy_pct", [])

    output = io.StringIO()
    writer = csv.writer(output, lineterminator='\n')
    
    writer.writerow([
        "hm0_lower_m", "hm0_upper_m", 
        f"{period_col}_lower_s", f"{period_col}_upper_s", 
        "records_pct", "energy_pct"
    ])
    
    for i in range(len(x_edges) - 1):
        for j in range(len(y_edges) - 1):
            row = [
                x_edges[i], x_edges[i+1],
                y_edges[j], y_edges[j+1],
                hours_pct[i][j] if hours_pct and i < len(hours_pct) and j < len(hours_pct[i]) else None,
                energy_pct[i][j] if energy_pct and i < len(energy_pct) and j < len(energy_pct[i]) else None
            ]
            writer.writerow(row)
            
    return f"scatter-{which}.csv", output.getvalue()

def rose_csv(payload) -> tuple[str, str] | None:
    """Return (filename, csv_text) for the wave rose, or None if missing."""
    section = find_section(payload, "Wave rose")
    if not section or section.get("kind") != "rose":
        return None

    labels = section.get("sector_labels", [])
    series = section.get("series", [])
    
    output = io.StringIO()
    writer = csv.writer(output, lineterminator='\n')
    
    unit = section.get("unit", "")
    unit_slug = f" ({unit})" if unit else ""
    headers = ["direction_from_deg"] + [slug(s.get("label", "") + unit_slug) for s in series]
    writer.writerow(headers)
    
    for i, label in enumerate(labels):
        row = [label]
        for s in series:
            vals = s.get("values", [])
            row.append(vals[i] if i < len(vals) else None)
        writer.writerow(row)
        
    return "rose.csv", output.getvalue()

def generic_table_csv(payload, title_substring, filename) -> tuple[str, str] | None:
    """Return (filename, csv_text) for a generic table or line section, or None if missing."""
    section = find_section(payload, title_substring)
    if not section:
        return None
    kind = section.get("kind")
    if kind not in ("table", "line"):
        return None
    output = io.StringIO()
    writer = csv.writer(output, lineterminator='\n')
    if kind == "table":
        writer.writerow([slug(c) for c in section.get("columns", [])])
        for r in section.get("rows", []):
            writer.writerow(r)
    elif kind == "line":
        x_label = section.get("x_label", "X")
        ys = section.get("y", [])
        headers = [slug(x_label)] + [slug(s.get("label", "")) for s in ys]
        writer.writerow(headers)
        xs = section.get("x", [])
        for i in range(len(xs)):
            row = [xs[i]]
            for s in ys:
                vals = s.get("values", [])
                row.append(vals[i] if i < len(vals) else None)
            writer.writerow(row)
    return filename, output.getvalue()

def monthly_csv(payload) -> tuple[str, str] | None:
    """Return (filename, csv_text) for the monthly climatology table, or None if missing."""
    return generic_table_csv(payload, "Monthly climatology", "monthly.csv")

def flux_by_period_csv(payload) -> tuple[str, str] | None:
    """Return (filename, csv_text) for the flux-by-period table, or None if missing."""
    return generic_table_csv(payload, "Cumulative energy flux vs period", "flux-by-period.csv")

def partitions_csv(payload) -> tuple[str, str] | None:
    """Return (filename, csv_text) for the partitions table, or None if missing."""
    return generic_table_csv(payload, "Partitions", "partitions.csv")

def metrics_csv(payload) -> tuple[str, str] | None:
    """Return (filename, csv_text) for the metrics table, or None if missing."""
    series_dict = payload.get("series", {})
    order = payload.get("order", [])
    if not series_dict or not order:
        return None
    output = io.StringIO()
    writer = csv.writer(output, lineterminator='\n')
    writer.writerow(["key", "label", "unit", "mean", "p95", "maximum"])
    for key in order:
        if key in series_dict:
            s = series_dict[key]
            writer.writerow([
                key, s.get("label"), s.get("unit"),
                s.get("mean"), s.get("p95"), s.get("maximum")
            ])
    return "metrics.csv", output.getvalue()

def rose_svg(payload, theme="light") -> str | None:
    """Return an SVG string for the wave rose, or None if missing."""
    section = find_section(payload, "Wave rose")
    if not section or section.get("kind") != "rose":
        return None

    if theme == "dark":
        bg, fg, bar_a, bar_b, ring = "#1a1a1a", "#ffffff", "#00c0a0", "#ffb833", "#444444"
    else:
        bg, fg, bar_a, bar_b, ring = "#ffffff", "#10241e", "#008f7a", "#f7a400", "#dddddd"

    size = 300
    c = size / 2
    radius = 110
    
    series_list = section.get("series", [])
    sector_labels = section.get("sector_labels", [])
    
    all_values = []
    for s in series_list:
        all_values.extend(s.get("values", []))
    peak = max(all_values) if all_values else 1e-9
    peak = max(peak, 1e-9)
    
    n = len(sector_labels)
    width = 360 / n if n else 0
    
    def point(angle, r):
        rad = angle * math.pi / 180
        return c + r * math.sin(rad), c - r * math.cos(rad)
        
    def wedge(centre, span, r):
        x1, y1 = point(centre - span / 2, r)
        x2, y2 = point(centre + span / 2, r)
        return f"M{c},{c} L{x1:.1f},{y1:.1f} A{r:.1f},{r:.1f} 0 0 1 {x2:.1f},{y2:.1f} Z"

    lines = []
    lines.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size+100}" width="{size}" height="{size+100}" style="background-color: {bg}; font-family: sans-serif; font-size: 11px;">')
    lines.append(f'<text x="{c}" y="20" text-anchor="middle" fill="{fg}" font-size="14px" font-weight="bold">{escape(section.get("title", ""))}</text>')
    
    for f in (0.5, 1):
        lines.append(f'<circle cx="{c}" cy="{c+20}" r="{radius*f}" fill="none" stroke="{ring}" stroke-width="1"/>')
        
    styles = [(bar_a, 1), (bar_b, 0.6)]
    
    lines.append(f'<g transform="translate(0, 20)">')
    for i, s in enumerate(series_list):
        style_color, style_width = styles[i % len(styles)]
        for k, value in enumerate(s.get("values", [])):
            if value <= 0:
                continue
            d = wedge(sector_labels[k], width * style_width, radius * value / peak)
            title = f"{sector_labels[k]}° · {s.get('label', '')}: {value}{section.get('unit', '')}"
            lines.append(f'<path d="{d}" fill="{style_color}" stroke="none"><title>{escape(title)}</title></path>')
            
    for text, angle in [("N", 0), ("E", 90), ("S", 180), ("W", 270)]:
        x, y = point(angle, radius + 16)
        lines.append(f'<text x="{x}" y="{y+4}" text-anchor="middle" fill="{fg}">{escape(text)}</text>')
    lines.append('</g>')

    legend_y = size + 40
    for i, s in enumerate(series_list):
        style_color, _ = styles[i % len(styles)]
        lines.append(f'<rect x="10" y="{legend_y-10}" width="12" height="12" fill="{style_color}"/>')
        lines.append(f'<text x="28" y="{legend_y}" fill="{fg}">{escape(s.get("label", ""))}</text>')
        legend_y += 20
        
    caption = "Direction is where waves come from; clockwise from true north"
    lines.append(f'<text x="{c}" y="{legend_y+10}" text-anchor="middle" fill="{fg}" font-size="10px">{escape(caption)}</text>')
    lines.append('</svg>')
    return "\n".join(lines)

def scatter_svg(payload, which, quantity="records", theme="light") -> str | None:
    """Return an SVG string for the scatter diagram, or None if missing."""
    if which == "hm0-te":
        section = find_section(payload, "Hm0 vs Te")
    elif which == "hm0-tp":
        section = find_section(payload, "Hm0 vs Tp")
    else:
        return None

    if not section or section.get("kind") != "scatter":
        return None

    if theme == "dark":
        bg, fg, line, accent = "#1a1a1a", "#ffffff", "#444444", "#00c0a0"
    else:
        bg, fg, line, accent = "#ffffff", "#10241e", "#dddddd", "#008f7a"

    key = "hours_pct" if quantity == "records" else "energy_pct"
    matrix = section.get(key, [])
    x_edges = section.get("x_edges", [])
    y_edges = section.get("y_edges", [])
    
    if not matrix or not x_edges or not y_edges:
        return None

    # The browser draws: rows = Hm0 descending, cols = period.
    # In payload, x is Hm0, y is period.
    # matrix[i][j] where i is x (Hm0), j is y (period).
    # Hm0 descending means i goes from len(x_edges)-2 down to 0
    nx = len(x_edges) - 1
    ny = len(y_edges) - 1
    
    cell_w = 40
    cell_h = 20
    margin_l = 80
    margin_t = 60
    margin_r = 40
    margin_b = 60
    
    width = margin_l + ny * cell_w + margin_r
    height = margin_t + nx * cell_h + margin_b
    
    peak = 1e-9
    for row in matrix:
        for v in row:
            if v > peak:
                peak = v

    lines = []
    lines.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" style="background-color: {bg}; font-family: sans-serif; font-size: 11px;">')
    lines.append(f'<text x="{width/2}" y="20" text-anchor="middle" fill="{fg}" font-size="14px" font-weight="bold">{escape(section.get("title", ""))}</text>')
    
    # Legend color scale: we just draw a small gradient or some boxes
    # Wait, the spec says "colour scale legend and a caption stating that bins are half-open and what the percentages are of"
    
    lines.append(f'<text x="{margin_l}" y="{margin_t - 20}" fill="{fg}">{escape(section.get("x_label", ""))} ↓ · {escape(section.get("y_label", ""))} →</text>')
    
    for i in range(nx):
        row_idx = nx - 1 - i
        y_pos = margin_t + i * cell_h
        label = f"{x_edges[row_idx]:.1f}–{x_edges[row_idx+1]:.1f}"
        lines.append(f'<text x="{margin_l - 10}" y="{y_pos + 14}" text-anchor="end" fill="{fg}">{escape(label)}</text>')
        for j in range(ny):
            x_pos = margin_l + j * cell_w
            v = matrix[row_idx][j] if row_idx < len(matrix) and j < len(matrix[row_idx]) else 0
            
            fill_color = "transparent"
            text_color = fg
            text = ""
            if v > 0:
                # App logic: Math.round(10 + 78 * value / peak) opacity on accent
                opacity = 0.1 + 0.78 * (v / peak)
                fill_color = f"{accent}" # In real app it uses color-mix. We can simulate with rgb or opacity.
                # Let's just use opacity on a rect.
                lines.append(f'<rect x="{x_pos}" y="{y_pos}" width="{cell_w}" height="{cell_h}" fill="{fill_color}" fill-opacity="{opacity:.2f}"/>')
                
                if v >= 10:
                    text = f"{v:.0f}"
                else:
                    text = f"{v:.1f}"
                
                if v > 0.55 * peak:
                    text_color = bg
            
            lines.append(f'<rect x="{x_pos}" y="{y_pos}" width="{cell_w}" height="{cell_h}" fill="none" stroke="{line}" stroke-width="1"/>')
            if text:
                lines.append(f'<text x="{x_pos + cell_w/2}" y="{y_pos + 14}" text-anchor="middle" fill="{text_color}">{text}</text>')

    for j in range(ny):
        x_pos = margin_l + j * cell_w
        label = f"{y_edges[j]:.1f}–{y_edges[j+1]:.1f}"
        lines.append(f'<text x="{x_pos + cell_w/2}" y="{margin_t + nx * cell_h + 20}" text-anchor="middle" fill="{fg}">{escape(label)}</text>')

    caption = section.get("note", "Bins are half-open [lower, upper).")
    lines.append(f'<text x="{margin_l}" y="{height - 20}" fill="{fg}" font-size="10px">{escape(caption)}</text>')
    
    pct_of = "Share of records (%)" if quantity == "records" else f"{section.get('weight_label', 'Share')} (%)"
    lines.append(f'<text x="{margin_l}" y="{height - 10}" fill="{fg}" font-size="10px">{escape(pct_of)}</text>')

    lines.append('</svg>')
    return "\n".join(lines)
def build_report(view, bundle=False) -> str:
    """Format the payload and provenance as a markdown report string."""
    payload = view.analysis()
    prov = view.provenance()
    frame = view.frame()

    lines = []
    lines.append("# ERA5 wave resource report\n")

    # Metadata
    lines.append("| | |")
    lines.append("|---|---|")
    lines.append(f"| Job id | {view.id} |")
    route_name = "Option A single levels" if view.product == "single-levels" else "Option B 2D spectra"
    lines.append(f"| Route | {route_name} |")
    
    req_c = payload.get("requested_coordinate", {})
    lines.append(f"| Requested site | {req_c.get('latitude', '')}, {req_c.get('longitude', '')} |")
    
    grid_c = payload.get("grid_coordinate", {})
    lines.append(f"| Analysed grid node | {grid_c.get('latitude', '')}, {grid_c.get('longitude', '')} |")
    
    lines.append(f"| Distance | {payload.get('grid_distance_km', '')} km |")
    
    if payload.get("depth_m") is not None:
        lines.append(f"| Model depth | {payload.get('depth_m')} m |")
        
    start_t, end_t = payload.get("start", ""), payload.get("end", "")
    lines.append(f"| Period | {start_t} to {end_t} |")
    lines.append(f"| Records | {payload.get('points', '')} |")
    
    ts = frame.attrs.get("time_step_hours")
    if ts is not None:
        lines.append(f"| Time step | {fmt_number(ts)} h |")
        
    # Record length (years)
    years = 0.0
    if len(frame.index) > 1:
        years = (frame.index[-1] - frame.index[0]).total_seconds() / (365.25 * 86400)
    lines.append(f"| Record length | {fmt_number(years)} years |")
    
    cov = payload.get("coverage", 0)
    lines.append(f"| Data coverage | {fmt_number(cov * 100)} % |")
    
    gen_time = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
    lines.append(f"| Generated | {gen_time} |")
    
    node_sel = "Yes" if payload.get("node_selected") else "No"
    lines.append(f"| Non-default grid node | {node_sel} |")
    lines.append("")

    # Warnings
    warnings = payload.get("warnings", [])
    if warnings:
        for w in warnings:
            lines.append(f"* {w}")
        lines.append("")

    # Key results
    lines.append("## Key results\n")
    lines.append("| Name | Unit | Mean | P95 | Maximum |")
    lines.append("|---|---|---:|---:|---:|")
    for key in payload.get("order", []):
        s = payload.get("series", {}).get(key)
        if s and not s.get("advanced") and not s.get("circular"):
            mean = fmt_number(s.get("mean"))
            p95 = fmt_number(s.get("p95"))
            mx = fmt_number(s.get("maximum"))
            name = s.get("label", "").replace("|", "\\|")
            unit = s.get("unit", "").replace("|", "\\|")
            lines.append(f"| {name} | {unit} | {mean} | {p95} | {mx} |")
    lines.append("")

    # Payload sections
    for sec in payload.get("sections", []):
        title = sec.get("title", "")
        lines.append(f"## {title}\n")
        
        kind = sec.get("kind")
        if kind == "kv":
            lines.append("| Label | Value | Note |")
            lines.append("|---|---|---|")
            for r in sec.get("rows", []):
                lbl = str(r.get("label", "")).replace("|", "\\|")
                val = str(r.get("value", "")).replace("|", "\\|")
                note = str(r.get("note") or "").replace("|", "\\|")
                lines.append(f"| {lbl} | {val} | {note} |")
            lines.append("")
                
        elif kind == "table":
            cols = sec.get("columns", [])
            header = "| " + " | ".join(str(c).replace("|", "\\|") for c in cols) + " |"
            lines.append(header)
            
            # check numeric for right-align
            def is_numeric(v):
                if v is None or v == "—" or v == "": return True
                try: float(v); return True
                except ValueError: return False

            rows = sec.get("rows", [])
            align = []
            for col_idx in range(len(cols)):
                if all(is_numeric(r[col_idx] if col_idx < len(r) else None) for r in rows):
                    align.append("---:")
                else:
                    align.append("---")
            lines.append("|" + "|".join(align) + "|")
            
            for r in rows:
                lines.append("| " + " | ".join(str(x if x is not None else "").replace("|", "\\|") for x in r) + " |")
            lines.append("")
            
            if sec.get("note"):
                lines.append(f"_{sec['note']}_\n")

        elif kind == "scatter":
            x_edges = sec.get("x_edges", [])
            y_edges = sec.get("y_edges", [])
            
            for key_data, table_title in [("hours_pct", "Share of records (%)"), 
                                          ("energy_pct", f"{sec.get('weight_label', 'Share')} (%)")]:
                lines.append(f"**{table_title}**\n")
                
                header_cols = [f"{sec.get('x_label', '')} \\ {sec.get('y_label', '')}"]
                for j in range(len(y_edges) - 1):
                    header_cols.append(f"{y_edges[j]:g}-{y_edges[j+1]:g}")
                lines.append("| " + " | ".join(header_cols) + " |")
                lines.append("|" + "---|".join([""] * (len(header_cols)+1)))
                
                matrix = sec.get(key_data, [])
                # rows Hm0 descending
                for i in range(len(x_edges) - 2, -1, -1):
                    row_lbl = f"{x_edges[i]:g}-{x_edges[i+1]:g}"
                    row_cells = [row_lbl]
                    for j in range(len(y_edges) - 1):
                        v = matrix[i][j] if i < len(matrix) and j < len(matrix[i]) else 0
                        row_cells.append(fmt_number(v) if v > 0 else "")
                    lines.append("| " + " | ".join(row_cells) + " |")
                lines.append("")
            if sec.get("note"):
                lines.append(f"{sec['note']}\n")
                
            if bundle:
                tag = "hm0-tp" if "Tp" in title else "hm0-te"
                lines.append(f"Figures: `figures/scatter-{tag}.svg`, `figures/scatter-{tag}-energy.svg` in the bundle\n")

        elif kind == "rose":
            labels = sec.get("sector_labels", [])
            series = sec.get("series", [])
            header_cols = ["Direction"] + [s.get("label", "").replace("|", "\\|") for s in series]
            lines.append("| " + " | ".join(header_cols) + " |")
            lines.append("|" + "---|".join([""] * (len(header_cols)+1)))
            
            for i, lbl in enumerate(labels):
                row_cells = [f"{lbl:g}°"]
                for s in series:
                    v = s.get("values", [])[i] if i < len(s.get("values", [])) else None
                    row_cells.append(fmt_number(v))
                lines.append("| " + " | ".join(row_cells) + " |")
            lines.append("")
            
            if sec.get("note"):
                lines.append(f"{sec['note']}\n")
                
            if bundle:
                lines.append("Figures: `figures/rose.svg` in the bundle\n")
                
        elif kind == "line":
            xs = sec.get("x", [])
            ys = sec.get("y", [])
            header_cols = [sec.get("x_label", "X").replace("|", "\\|")] + [s.get("label", "").replace("|", "\\|") for s in ys]
            lines.append("| " + " | ".join(header_cols) + " |")
            lines.append("|" + "---:|".join([""] * (len(header_cols)+1)))
            
            for i in range(len(xs)):
                row_cells = [str(xs[i]).replace("|", "\\|")]
                for s in ys:
                    v = s.get("values", [])[i] if i < len(s.get("values", [])) else None
                    row_cells.append(fmt_number(v))
                lines.append("| " + " | ".join(row_cells) + " |")
            lines.append("")
            if sec.get("note"):
                lines.append(f"{sec['note']}\n")
                
    # Definitions and limits
    lines.append("## Definitions and limits\n")
    for note in payload.get("notes", []):
        lines.append(f"* {note}")
    lines.append("")

    # Provenance
    lines.append("## Provenance\n")
    if prov:
        lines.append(f"* **Product**: {prov.get('product', '')}")
        
        for ds in prov.get("datasets", []):
            doi = ds.get("doi")
            doi_link = f"[https://doi.org/{doi}](https://doi.org/{doi})" if doi else ""
            lines.append(f"* **Dataset**: {ds.get('title', '')} ({doi_link}), licence {ds.get('license', '')}, catalogue updated {ds.get('catalogue_updated', '')}")
            
        lines.append(f"* **Expver policy**: {prov.get('expver_policy', '')}")
        lines.append(f"* **Area**: {prov.get('area_nwse', '')}")
        
        per = prov.get('period', {})
        lines.append(f"* **Period**: {per.get('start', '')} to {per.get('end', '')}")
        
        lines.append(f"* **Time step**: {prov.get('time_step_hours', '')} h")
        
        reqs = prov.get("requests", [])
        if reqs:
            lines.append(f"* **CDS requests**: {len(reqs)}")
            lines.append("| Month | Days / Hours | Expver | Status | File | Size (MB) | SHA-256 |")
            lines.append("|---|---|---|---|---|---:|---|")
            for r in reqs:
                month = str(r.get("month", ""))
                
                dh = []
                if "days" in r and isinstance(r["days"], list) and len(r["days"]) == 2:
                    dh.append(f"d{r['days'][0]}-{r['days'][1]}")
                if "hours" in r and isinstance(r["hours"], list) and len(r["hours"]) == 2:
                    dh.append(f"h{r['hours'][0]}-{r['hours'][1]}")
                dh_str = " ".join(dh)
                
                expver = ""
                req = r.get("request")
                if isinstance(req, dict) and "expver" in req:
                    expver = str(req.get("expver", ""))
                
                status = str(r.get("status", ""))
                f = str(r.get("file", ""))
                if f:
                    f = f"`{f}`"
                
                size_mb = ""
                b = r.get("bytes")
                if b is not None:
                    size_mb = f"{b / 1048576:.2f}"
                    
                sha = str(r.get("sha256", ""))
                if sha:
                    sha = f"`{sha}`"
                    
                lines.append(f"| {month} | {dh_str} | {expver} | {status} | {f} | {size_mb} | {sha} |")
            lines.append("")
            
        bathy = prov.get("bathymetry")
        if isinstance(bathy, dict):
            bf = bathy.get("file", "")
            if bf: bf = f"`{bf}`"
            bb = bathy.get("bytes")
            bsize = f"({bb / 1048576:.2f} MB)" if bb is not None else ""
            lines.append(f"* **Model depth**: {bf} {bsize}".strip())
            
        software = prov.get("software", {})
        lines.append(f"* **Software**: cdsapi {software.get('cdsapi', '')}, Python {software.get('python', '')}")
        
        lines.append(f"* **Generated**: {prov.get('created_utc', '')} (completed {prov.get('completed_utc', '')})")
        
        lic = prov.get("licence", {})
        lines.append(f"* **Licence**: {lic.get('statement', '')} - {lic.get('url', '')}")
        
        dry_run = "Yes" if prov.get("dry_run") else "No"
        lines.append(f"* **Dry run**: {dry_run}")
    else:
        lines.append("No provenance.json for this job")
    lines.append("")

    # Additional sections
    for name, text in view.report_sections():
        lines.append("---")
        lines.append("")
        lines.append(text.strip())
        lines.append("")
        
    lines.append("## How to read this report\n")
    lines.append(
        "ERA5 is a reanalysis not a measurement; offshore node; "
        "the energy period Te is Tm-1; direction conventions; a short record is not a resource estimate."
    )

    return "\n".join(lines) + "\n"
