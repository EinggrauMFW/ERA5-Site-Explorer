"""Plugin: long-term statistics (variability, interannual, extremes).

Registers ``GET /api/jobs/<id>/longterm`` which computes climatology, variability indices,
interannual variation, and peaks-over-threshold extreme Hm0 return levels.
"""

from __future__ import annotations

import json

from flask import Flask, request

import longterm
import plugins


LONGTERM_VERSION = 2

def register(app: Flask, ctx: plugins.PluginContext) -> None:

    @app.route("/api/jobs/<job_id>/longterm")
    def longterm_route(job_id: str):
        # --- parse and validate parameters ----------------------------------------
        threshold_pct = _float_param("threshold_pct", 95.0, 50.0, 99.9)
        decluster_hours = _float_param("decluster_hours", 48.0, 1.0, 720.0)
        bootstrap = int(_float_param("bootstrap", 300.0, 0.0, 1000.0))
        seed = int(_float_param("seed", 1.0, 0.0, 2**31))

        raw = request.args.get("return_years", "1,10,50,100")
        try:
            return_years = [float(x.strip()) for x in raw.split(",")]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"return_years must be comma-separated numbers, got: {raw!r}") from exc
        if len(return_years) > 8:
            raise ValueError("return_years: at most 8 values")
        for val in return_years:
            if not (0.5 <= val <= 1000):
                raise ValueError(f"return_years values must be 0.5–1000, got {val}")

        view = ctx.job(job_id)
        # --- cache key ------------------------------------------------------------
        cache_name = f"longterm_{longterm.params_hash(LONGTERM_VERSION, threshold_pct, decluster_hours, return_years, bootstrap, seed, view.node)}.json"
        cache_path = view.results_path(cache_name)

        # Check if cache is newer than all data files
        if cache_path.is_file():
            cache_mtime = cache_path.stat().st_mtime
            data_fresh = all(f.stat().st_mtime <= cache_mtime for f in view.files if f.is_file())
            if data_fresh:
                result = json.loads(cache_path.read_text(encoding="utf-8"))
                view.save_report_section("40_longterm", longterm.report_markdown(result))
                return result

        frame = view.frame()

        # --- compute --------------------------------------------------------------
        result = longterm.compute_longterm(
            frame,
            threshold_pct=threshold_pct,
            decluster_hours=decluster_hours,
            return_years=return_years,
            bootstrap=bootstrap,
            seed=seed,
        )

        # --- persist --------------------------------------------------------------
        view.save_json(cache_name, result)
        view.save_report_section("40_longterm", longterm.report_markdown(result))

        return result


def _float_param(name: str, default: float, lo: float, hi: float) -> float:
    raw = request.args.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number, got: {raw!r}") from exc
    if not (lo <= value <= hi):
        raise ValueError(f"{name} must be between {lo} and {hi}, got {value}")
    return value
