"""API plugin for site screening payload and CSV export."""
import io
import csv
import json
from flask import jsonify, send_file
import plugins
import screening

SCREENING_VERSION = 1

def _load_or_compute(view):
    cache_path = view.directory / "screening.json"
    
    mtime = 0
    for f in view.files:
        if f.is_file():
            mtime = max(mtime, f.stat().st_mtime)
            
    if cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("_version") == SCREENING_VERSION and cached.get("_mtime") >= mtime:
                return {k: v for k, v in cached.items() if not k.startswith("_")}
        except Exception:
            pass
            
    result = screening.compute_screening(view.files, view.product, view.latitude, view.longitude)
    
    to_cache = dict(result)
    to_cache["_version"] = SCREENING_VERSION
    to_cache["_mtime"] = mtime
    cache_path.write_text(json.dumps(to_cache, default=plugins._json_default), encoding="utf-8")
    
    return result

def register(app, ctx):
    """Register the screening API endpoints (JSON and CSV)."""
    @app.route("/api/jobs/<job_id>/screening")
    def screening_payload(job_id):
        view = ctx.job(job_id)
        result = _load_or_compute(view)
        return jsonify(result)
        
    @app.route("/api/jobs/<job_id>/screening.csv")
    def screening_csv(job_id):
        view = ctx.job(job_id)
        result = _load_or_compute(view)
            
        ocean_nodes = [n for n in result["nodes"] if n["valid"]]
        
        output = io.StringIO()
        writer = csv.writer(output)
        
        header = [
            "lat", "lon", "depth_m", "distance_km",
            "hm0_mean_m", "te_mean_s", "flux_mean_kw_m", "flux_p95_kw_m", "flux_cov", "seasonality_ratio",
            "flux_djf_kw_m", "flux_mam_kw_m", "flux_jja_kw_m", "flux_son_kw_m",
            "flux_jan_kw_m", "flux_feb_kw_m", "flux_mar_kw_m", "flux_apr_kw_m", "flux_may_kw_m", "flux_jun_kw_m",
            "flux_jul_kw_m", "flux_aug_kw_m", "flux_sep_kw_m", "flux_oct_kw_m", "flux_nov_kw_m", "flux_dec_kw_m"
        ]
        writer.writerow(header)
        
        for n in ocean_nodes:
            s_flux = n["season_flux_kw_m"]
            m_flux = n["monthly_flux_kw_m"]
            row = [
                n["lat"], n["lon"], n.get("depth_m"), n["distance_km"],
                n["hm0_mean_m"], n["te_mean_s"], n["flux_mean_kw_m"], n["flux_p95_kw_m"], n["flux_cov"], n["seasonality_ratio"],
                s_flux.get("DJF"), s_flux.get("MAM"), s_flux.get("JJA"), s_flux.get("SON")
            ] + m_flux
            writer.writerow(row)
            
        mem = io.BytesIO()
        mem.write(output.getvalue().encode('utf-8'))
        mem.seek(0)
        return send_file(mem, mimetype="text/csv", as_attachment=True, download_name="screening.csv")
