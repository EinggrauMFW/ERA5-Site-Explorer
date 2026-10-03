"""
API plugin for device performance assessment.

Provides routes to upload a power matrix, compute AEP and capture width,
and manage saved results for the active job.
"""

import json
import os
import re

from flask import Response, abort, jsonify, request

from device import assess_device, example_matrix_csv, parse_power_matrix, power_matrix_from_arrays
from plugins import _json_default

def register(app, ctx):
    @app.route("/api/jobs/<job_id>/device", methods=["POST"])
    def assess(job_id):
        view = ctx.job(job_id)

        req = request.get_json(silent=True)
        if not isinstance(req, dict):
            raise ValueError("Missing or invalid JSON payload")

        name = req.get("name", "")
        if not isinstance(name, str):
            raise ValueError("Name must be a string.")

        name = re.sub(r'[\x00-\x1f\x7f-\x9f\n\r]', '', name).strip()
        if not name or not (1 <= len(name) <= 60):
            raise ValueError("Name must be 1 to 60 characters.")

        slug = re.sub(r'[^a-z0-9_-]+', '-', name.lower())
        if not slug or not slug.replace('-', ''):
            slug = "device"

        device_id = req.get("device_id")
        csv_text = req.get("csv_text", "")

        catalogue_dev = None
        if device_id:
            if csv_text:
                raise ValueError("Cannot provide both device_id and csv_text.")
            from devices_catalogue import find_device
            catalogue_dev = find_device(device_id)
            if not catalogue_dev:
                abort(404)
        elif not isinstance(csv_text, str):
            raise ValueError("CSV text must be a string.")
        elif len(csv_text.encode('utf-8')) > 200 * 1024:
            raise ValueError("CSV text exceeds 200 kB limit.")

        bin_convention = req.get("bin_convention")
        if not bin_convention:
            bin_convention = catalogue_dev.bin_convention if catalogue_dev else "centres"

        if bin_convention not in ("centres", "lower_edges"):
            raise ValueError("Invalid bin convention.")

        period_type = req.get("period_type", "unknown")
        if period_type not in ("te", "tp", "unknown"):
            raise ValueError("Invalid period type.")

        rated_kw = req.get("rated_kw")
        if rated_kw == "": rated_kw = None
        if rated_kw is not None:
            try:
                rated_kw = float(rated_kw)
                if rated_kw <= 0 or rated_kw != rated_kw or rated_kw == float('inf'):
                    raise ValueError()
            except Exception:
                raise ValueError("Rated power must be a finite positive number.")

        width_m = req.get("width_m")
        if width_m == "": width_m = None
        if width_m is not None:
            try:
                width_m = float(width_m)
                if width_m <= 0 or width_m != width_m or width_m == float('inf'):
                    raise ValueError()
            except Exception:
                raise ValueError("Characteristic width must be a finite positive number.")

        if catalogue_dev:
            matrix = power_matrix_from_arrays(catalogue_dev.hs_m, catalogue_dev.period_s, catalogue_dev.power_kw, bin_convention=bin_convention)
        else:
            matrix = parse_power_matrix(csv_text, bin_convention=bin_convention)

        frame = view.frame()

        result = assess_device(
            frame, matrix,
            name=name,
            rated_kw=rated_kw,
            width_m=width_m,
            period_type=period_type
        )

        if view.node:
            result["node"] = list(view.node)
        else:
            result["node"] = None

        if catalogue_dev:
            result["catalogue"] = {
                "id": catalogue_dev.id,
                "name": catalogue_dev.name,
                "source": catalogue_dev.source,
                "provenance": catalogue_dev.provenance,
                "notes": catalogue_dev.notes,
                "synthetic": catalogue_dev.synthetic
            }

        view.save_json(f"device_{slug}.json", result)

        lines = []
        node_text = f" at node {result['node'][0]:.3f}, {result['node'][1]:.3f}" if result["node"] else ""
        safe_name = name.replace('|', '\\|')
        lines.append(f"## Device Assessment: {safe_name}{node_text}")
        lines.append("")

        lines.append("| Indexing | Method | AEP (MWh/yr) | Capacity Factor (%) | Capture Width (m) | Share Outside (%) |")
        lines.append("|---|---|---|---|---|---|")
        for key, eval_data in result["indexings"].items():
            for method in ("bin", "bilinear"):
                m_data = eval_data[method]
                aep = f"{m_data['aep_mwh']:.1f}" if m_data.get('aep_mwh') is not None else "-"
                cf = f"{m_data['capacity_factor_pct']:.1f}" if m_data.get('capacity_factor_pct') is not None else "-"
                cw = f"{m_data['capture_width_energy_weighted_m']:.1f}" if m_data.get('capture_width_energy_weighted_m') is not None else "-"
                outside = f"{m_data['share_outside_pct']:.1f}" if m_data.get('share_outside_pct') is not None else "-"
                lines.append(f"| {key} | {method} | {aep} | {cf} | {cw} | {outside} |")

        lines.append("")
        if result["warnings"]:
            lines.append("**Warnings:**")
            for w in result["warnings"]:
                lines.append(f"- {w}")
            lines.append("")

        if result["notes"]:
            lines.append("**Notes:**")
            for n in result["notes"]:
                lines.append(f"- {n}")
            lines.append("")

        if catalogue_dev:
            prov_notes = []
            if catalogue_dev.provenance:
                prov_notes.append(catalogue_dev.provenance)
            prov_notes.extend(catalogue_dev.notes)
            source_text = " ".join(prov_notes).strip()
            if source_text:
                lines.append(f"Matrix source: {source_text}")
                lines.append("")

        view.save_report_section(f"30_device_{slug}", "\n".join(lines))

        return app.response_class(json.dumps(result, default=_json_default), mimetype="application/json")


    @app.route("/api/jobs/<job_id>/device", methods=["GET"])
    def list_devices(job_id):
        view = ctx.job(job_id)
        devices = []
        folder = view.directory / "results"
        if folder.is_dir():
            for p in folder.glob("device_*.json"):
                slug = p.stem[7:]
                res = view.load_json(p.name)

                aep_min = None
                aep_max = None
                if res["period_type"] == "unknown" and "ambiguity" in res:
                    aep_min = res["ambiguity"]["aep_mwh_min"]
                    aep_max = res["ambiguity"]["aep_mwh_max"]
                elif res.get("indexings"):
                    key = list(res["indexings"].keys())[0]
                    aep_min = res["indexings"][key]["bin"].get("aep_mwh")
                    aep_max = aep_min

                devices.append({
                    "slug": slug,
                    "name": res.get("name", slug),
                    "node": res.get("node"),
                    "aep_mwh_range": [aep_min, aep_max],
                    "saved": True
                })
        return jsonify({"devices": devices})

    @app.route("/api/jobs/<job_id>/device/<slug>", methods=["GET"])
    def get_device(job_id, slug):
        if not re.match(r'^[a-z0-9_-]{1,60}$', slug):
            return jsonify({"error": "Not found"}), 404
        view = ctx.job(job_id)
        res = view.load_json(f"device_{slug}.json")
        if res is None:
            abort(404)
        return jsonify(res)

    @app.route("/api/jobs/<job_id>/device/<slug>", methods=["DELETE"])
    def delete_device(job_id, slug):
        if not re.match(r'^[a-z0-9_-]{1,60}$', slug):
            return jsonify({"error": "Not found"}), 404
        view = ctx.job(job_id)

        json_path = view.results_path(f"device_{slug}.json")
        if json_path.is_file():
            json_path.unlink()

        rep_folder = view.directory / "report_sections"
        rep_path = rep_folder / f"30_device_{slug}.md"
        if rep_path.is_file():
            rep_path.unlink()

        return "", 204

    @app.route("/api/device/example.csv", methods=["GET"])
    def example_csv():
        csv = example_matrix_csv()
        return Response(csv, mimetype="text/csv")
