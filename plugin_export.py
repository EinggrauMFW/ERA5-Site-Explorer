"""Export endpoints for reports, tables, figures and the combined zip bundle."""

import datetime
import io
import json
import zipfile
import flask

from report import (
    build_report,
    flux_by_period_csv,
    metrics_csv,
    monthly_csv,
    partitions_csv,
    rose_csv,
    rose_svg,
    scatter_csv,
    scatter_svg,
)

MAX_BUNDLE_BYTES = 200 * 1024 * 1024

def _available_tables(view) -> list[dict]:
    payload = view.analysis()
    avail = []
    
    def has(title):
        for sec in payload.get("sections", []):
            if title.lower() in sec.get("title", "").lower(): return True
        return False
        
    if has("hm0-te") or has("hm0 vs te"):
        avail.append({"name": "scatter-hm0-te", "filename": "scatter-hm0-te.csv", "description": "Scatter diagram of Hm0 vs Te"})
    if has("hm0-tp") or has("hm0 vs tp"):
        avail.append({"name": "scatter-hm0-tp", "filename": "scatter-hm0-tp.csv", "description": "Scatter diagram of Hm0 vs Tp"})
    if has("wave rose"):
        avail.append({"name": "rose", "filename": "rose.csv", "description": "Wave rose direction table"})
    if has("monthly climatology"):
        avail.append({"name": "monthly", "filename": "monthly.csv", "description": "Monthly climatology table"})
    if has("cumulative energy flux"):
        avail.append({"name": "flux-by-period", "filename": "flux-by-period.csv", "description": "Cumulative energy flux vs period"})
    if has("partitions"):
        avail.append({"name": "partitions", "filename": "partitions.csv", "description": "Partitions table"})
    if payload.get("series") and payload.get("order"):
        avail.append({"name": "metrics", "filename": "metrics.csv", "description": "Key metrics summary (mean, P95, max)"})
    return avail

def _available_figures(view) -> list[dict]:
    payload = view.analysis()
    avail = []
    
    def has(title):
        for sec in payload.get("sections", []):
            if title.lower() in sec.get("title", "").lower(): return True
        return False
        
    if has("wave rose"):
        avail.append({"name": "rose", "title": "Wave rose SVG", "variants": [{"theme": "light"}, {"theme": "dark"}]})
    if has("hm0-te") or has("hm0 vs te"):
        avail.append({"name": "scatter-hm0-te", "title": "Hm0 vs Te SVG", "variants": [{"theme": "light", "quantity": "records"}, {"theme": "dark", "quantity": "records"}, {"theme": "light", "quantity": "energy"}, {"theme": "dark", "quantity": "energy"}]})
    if has("hm0-tp") or has("hm0 vs tp"):
        avail.append({"name": "scatter-hm0-tp", "title": "Hm0 vs Tp SVG", "variants": [{"theme": "light", "quantity": "records"}, {"theme": "dark", "quantity": "records"}, {"theme": "light", "quantity": "energy"}, {"theme": "dark", "quantity": "energy"}]})
    return avail

def _get_table_csv(view, name):
    payload = view.analysis()
    if name == "scatter-hm0-te":
        return scatter_csv(payload, "hm0-te")
    elif name == "scatter-hm0-tp":
        return scatter_csv(payload, "hm0-tp")
    elif name == "rose":
        return rose_csv(payload)
    elif name == "monthly":
        return monthly_csv(payload)
    elif name == "flux-by-period":
        return flux_by_period_csv(payload)
    elif name == "partitions":
        return partitions_csv(payload)
    elif name == "metrics":
        return metrics_csv(payload)
    return None

def register(app, ctx):
    @app.route("/api/jobs/<job_id>/export/report.md", methods=["GET"])
    def export_report_md(job_id):
        view = ctx.job(job_id)
        content = build_report(view)
        
        filename = f"report_{job_id}.md"
        node = view.node
        if node:
            filename = f"report_{job_id}_{node[0]}_{node[1]}.md"
            
        return flask.Response(
            content,
            mimetype="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
        
    @app.route("/api/jobs/<job_id>/export/tables/<name>.csv", methods=["GET"])
    def export_table_csv(job_id, name):
        view = ctx.job(job_id)
        res = _get_table_csv(view, name)
        if not res:
            avail = _available_tables(view)
            avail_names = [t["name"] for t in avail]
            return flask.jsonify({"error": f"Table not found. Available: {', '.join(avail_names)}"}), 404
            
        filename = f"{name}_{job_id}.csv"
        node = view.node
        if node:
            filename = f"{name}_{job_id}_{node[0]}_{node[1]}.csv"
            
        return flask.Response(
            res[1],
            mimetype="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )

    @app.route("/api/jobs/<job_id>/export/figures/<name>.svg", methods=["GET"])
    def export_figure_svg(job_id, name):
        view = ctx.job(job_id)
        theme = flask.request.args.get("theme", "light")
        quantity = flask.request.args.get("quantity", "records")
        if theme not in ("light", "dark"):
            return flask.jsonify({"error": "theme must be 'light' or 'dark'"}), 422
        if quantity not in ("records", "energy"):
            return flask.jsonify({"error": "quantity must be 'records' or 'energy'"}), 422
            
        payload = view.analysis()
        
        svg = None
        if name == "rose":
            svg = rose_svg(payload, theme)
        elif name == "scatter-hm0-te":
            svg = scatter_svg(payload, "hm0-te", quantity, theme)
        elif name == "scatter-hm0-tp":
            svg = scatter_svg(payload, "hm0-tp", quantity, theme)
            
        if not svg:
            return flask.jsonify({"error": "Figure not found or not available"}), 404
            
        filename = f"{name}_{job_id}.svg"
        node = view.node
        if node:
            filename = f"{name}_{job_id}_{node[0]}_{node[1]}.svg"
            
        return flask.Response(
            svg,
            mimetype="image/svg+xml",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )

    @app.route("/api/jobs/<job_id>/export", methods=["GET"])
    def export_manifest(job_id):
        view = ctx.job(job_id)
        return flask.jsonify({
            "tables": _available_tables(view),
            "figures": _available_figures(view),
            "report": "report.md",
            "bundle": "bundle.zip"
        })

    @app.route("/api/jobs/<job_id>/export/bundle.zip", methods=["GET"])
    def export_bundle_zip(job_id):
        view = ctx.job(job_id)
        payload = view.analysis()
        
        # Prepare all contents
        bundle_report = build_report(view, bundle=True).encode("utf-8")
        
        tables = _available_tables(view)
        table_data = {}
        for t in tables:
            res = _get_table_csv(view, t["name"])
            if res:
                table_data[res[0]] = res[1].encode("utf-8")
                
        svgs = {}
        svg_rose = rose_svg(payload, theme="light")
        if svg_rose: svgs["rose.svg"] = svg_rose.encode("utf-8")
        for hm0_var in ["hm0-te", "hm0-tp"]:
            svg_rec = scatter_svg(payload, hm0_var, "records", "light")
            if svg_rec: svgs[f"scatter-{hm0_var}.svg"] = svg_rec.encode("utf-8")
            svg_ene = scatter_svg(payload, hm0_var, "energy", "light")
            if svg_ene: svgs[f"scatter-{hm0_var}-energy.svg"] = svg_ene.encode("utf-8")
            
        prov_file = view.directory / "provenance.json"
        prov_data = None
        if prov_file.exists():
            prov_data = prov_file.read_bytes()
            
        analysis_data = json.dumps(payload, indent=2).encode("utf-8")
        
        frame = view.raw_frame()
        csv_buf = io.StringIO()
        frame.to_csv(csv_buf, index_label="time")
        timeseries_data = csv_buf.getvalue().encode("utf-8")
        
        # Build README
        readme_lines = [
            "ERA5 Wave Resource Export Bundle",
            "=================================",
            "",
            f"Generation time: {datetime.datetime.now(datetime.timezone.utc).isoformat()}",
            "",
            "Files included:",
            "- report.md",
        ]
        if prov_data:
            readme_lines.append("- provenance.json")
        readme_lines.extend([
            "- timeseries.csv",
            "- analysis.json",
        ])
        for t in table_data: readme_lines.append(f"- tables/{t}")
        for s in svgs: readme_lines.append(f"- figures/{s}")
        readme_lines.append("- README_export.txt")
        readme_lines.extend([
            "",
            "Tables use half-open bins [lower, upper).",
            "Direction convention: Direction is where waves come from, clockwise from true north.",
            "Units are specified in the column names or report.",
        ])
        readme_data = ("\n".join(readme_lines) + "\n").encode("utf-8")
        
        total_size = len(bundle_report) + (len(prov_data) if prov_data else 0) + len(analysis_data) + len(timeseries_data) + len(readme_data)
        total_size += sum(len(d) for d in table_data.values())
        total_size += sum(len(d) for d in svgs.values())
        
        if total_size > MAX_BUNDLE_BYTES:
            return flask.jsonify({"error": f"Bundle exceeds {MAX_BUNDLE_BYTES // 1048576}MB limit"}), 413
            
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("report.md", bundle_report)
            if prov_data:
                zf.writestr("provenance.json", prov_data)
            zf.writestr("analysis.json", analysis_data)
            zf.writestr("timeseries.csv", timeseries_data)
            zf.writestr("README_export.txt", readme_data)
            for name, data in table_data.items():
                zf.writestr(f"tables/{name}", data)
            for name, data in svgs.items():
                zf.writestr(f"figures/{name}", data)
                
        filename = f"bundle_{job_id}.zip"
        node = view.node
        if node:
            filename = f"bundle_{job_id}_{node[0]}_{node[1]}.zip"
            
        return flask.Response(
            buf.getvalue(),
            mimetype="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
