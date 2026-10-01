#!/usr/bin/env python3
"""Download ERA5 data for one site from Copernicus CDS, in small monthly requests.

Three download products are supported, following the request styles in
``cdsapidata_retrieval.py`` from WaveSpectrum-ERA-5:

* ``single-levels``  Option A. ``reanalysis-era5-single-levels`` with chosen variable
  groups (integrated wave parameters in three tiers, wind, extremes, weather).
* ``mars-surface``   ``reanalysis-era5-complete`` (raw MARS) surface analysis fields
  selected by GRIB code.
* ``wave-spectra``   Option B. ``reanalysis-era5-complete`` 2D wave spectra
  (param 251.140, 24 directions x 30 frequencies).

``area`` is always ``[North, West, South, East]``. The script is run by ``app.py`` as
a subprocess, one process per job, and can be used on its own:

    python fetch_era5_waves.py --latitude -2.05 --longitude 99.4 \
        --start 2022-01-01 --end 2022-12-31 --output downloads/sipora \
        --product wave-spectra --dry-run

Output is one NetCDF per month plus ``era5_bathymetry.nc`` (model depth, requested once
for the single-levels QC group and for the spectra product) and ``provenance.json``
(exact requests, dataset metadata, file hashes, software versions). CDS may return a
ZIP or a plain NetCDF; both are saved as-is and handled by ``analysis.py``.
"""

from __future__ import annotations

import argparse
import calendar
import datetime as dt
import hashlib
import json
import logging
import math
import platform
import re
import sys
import urllib.request
from pathlib import Path

PRODUCTS = {
    "single-levels": "Option A · ERA5 single levels (integrated parameters)",
    "wave-spectra": "Option B · ERA5 MARS 2D wave spectra",
    "mars-surface": "ERA5 MARS · surface fields",
}
DEFAULT_PRODUCT = "single-levels"

SINGLE_LEVELS_DATASET = "reanalysis-era5-single-levels"
MARS_DATASET = "reanalysis-era5-complete"
CATALOGUE = "https://cds.climate.copernicus.eu/api/catalogue/v1/collections/"

# CDS names confirmed against the live catalogue form (reanalysis-era5-single-levels).
VARIABLE_GROUPS = {
    # Tier 1: core. mwp is Te = Tm-1 (ECMWF), the period for flux and Hm0-Te matrices.
    "waves": [
        "significant_height_of_combined_wind_waves_and_swell",
        "mean_wave_period",
        "peak_wave_period",
        "mean_wave_direction",
    ],
    # Tier 2: sea-state composition (wind sea, total swell, three swell partitions).
    "composition": [
        "significant_height_of_wind_waves",
        "significant_height_of_total_swell",
        "mean_period_of_wind_waves",
        "mean_period_of_total_swell",
        "mean_direction_of_wind_waves",
        "mean_direction_of_total_swell",
        "significant_wave_height_of_first_swell_partition",
        "mean_wave_direction_of_first_swell_partition",
        "mean_wave_period_of_first_swell_partition",
        "significant_wave_height_of_second_swell_partition",
        "mean_wave_direction_of_second_swell_partition",
        "mean_wave_period_of_second_swell_partition",
        "significant_wave_height_of_third_swell_partition",
        "mean_wave_direction_of_third_swell_partition",
        "mean_wave_period_of_third_swell_partition",
    ],
    # Tier 3: QC and interpretation aids. model_bathymetry is fetched separately (time-invariant).
    "qc": [
        "mean_wave_period_based_on_first_moment",
        "mean_zero_crossing_wave_period",
        "wave_spectral_directional_width",
        "wave_spectral_peakedness",
    ],
    "wind": ["10m_u_component_of_wind", "10m_v_component_of_wind"],
    # Extremes screening only: not energy parameters.
    "extremes": [
        "maximum_individual_wave_height",
        "period_corresponding_to_maximum_individual_wave_height",
    ],
    "temperature": ["2m_temperature"],
    "precipitation": ["total_precipitation"],
}
GROUP_LABELS = {
    "waves": "Tier 1 · core: Hm0, Te (mwp), Tp, mean direction",
    "composition": "Tier 2 · wind sea, swell and swell partitions",
    "qc": "Tier 3 · QC: Tm01, Tm02, directional width, peakedness (+ model depth)",
    "wind": "10 m wind",
    "extremes": "Extremes only (Hmax, Tmax; not energy parameters)",
    "temperature": "2 m temperature",
    "precipitation": "Total precipitation",
}
DEFAULT_GROUPS = ("waves", "composition", "qc", "wind")
BATHYMETRY_VARIABLE = "model_bathymetry"
BATHYMETRY_FILE = "era5_bathymetry.nc"

# GRIB codes for analysis ('an') fields in the oper stream.
MARS_SURFACE_PARAMS = {
    "167.128": "2 m temperature",
    "165.128": "10 m u wind",
    "166.128": "10 m v wind",
    "151.128": "Mean sea level pressure",
    "34.128": "Sea surface temperature",
}
DEFAULT_MARS_PARAMS = ("165.128", "166.128")
PARAM_PATTERN = re.compile(r"^\d{1,3}\.\d{3}$")

SPECTRA_DIRECTIONS = 24
SPECTRA_FREQUENCIES = 30

TIME_STEPS = (1, 3, 6, 12, 24)
DEFAULT_TIME_STEP = {"single-levels": 1, "mars-surface": 6, "wave-spectra": 6}

WAVE_GRID_DEG = 0.5      # delivered ERA5 wave grid
ATMOS_GRID_DEG = 0.25
EARLIEST = dt.date(1940, 1, 1)
LAG_DAYS = 6             # ERA5T trails real time by about five days
FINAL_LAG_DAYS = 100     # ERA5 final (expver 1) trails by roughly three months
MAX_RANGE_DAYS = {"single-levels": 366 * 5, "mars-surface": 366 * 5, "wave-spectra": 366 * 2}
MAX_BUFFER = {"single-levels": 10.0, "mars-surface": 10.0, "wave-spectra": 2.0}

LICENCE_NOTE = (
    "Contains modified Copernicus Climate Change Service information. Use of the data is subject to the "
    "Copernicus licence, which you accept on the CDS website before downloading."
)
LICENCE_URL = "https://cds.climate.copernicus.eu/licences"


def latest_available(today: dt.date | None = None) -> dt.date:
    return (today or dt.date.today()) - dt.timedelta(days=LAG_DAYS)


def validate_period(start: dt.date, end: dt.date, today: dt.date | None = None,
                    product: str = DEFAULT_PRODUCT) -> None:
    if start > end:
        raise ValueError("Start date must be on or before end date")
    if start < EARLIEST:
        raise ValueError(f"ERA5 starts on {EARLIEST.isoformat()}")
    latest = latest_available(today)
    if end > latest:
        raise ValueError(
            f"ERA5 is only available up to about {LAG_DAYS} days ago; "
            f"the latest usable end date is {latest.isoformat()}"
        )
    limit = MAX_RANGE_DAYS[product]
    if (end - start).days + 1 > limit:
        raise ValueError(f"Request at most {limit // 366} years at a time for this product")


def normalise_options(product: str | None, groups=None, params=None,
                      time_step: int | None = None) -> dict:
    """Validate download options and fill in per-product defaults."""
    product = product or DEFAULT_PRODUCT
    if product not in PRODUCTS:
        raise ValueError(f"Unknown product '{product}'")
    step = DEFAULT_TIME_STEP[product] if time_step in (None, "") else time_step
    try:
        step = int(step)
    except (TypeError, ValueError) as exc:
        raise ValueError("Time step must be a number of hours") from exc
    if step not in TIME_STEPS:
        raise ValueError(f"Time step must be one of {', '.join(map(str, TIME_STEPS))} hours")

    result = {"product": product, "time_step": step, "groups": [], "params": []}
    if product == "single-levels":
        chosen = list(groups) if groups else list(DEFAULT_GROUPS)
        unknown = [name for name in chosen if name not in VARIABLE_GROUPS]
        if unknown:
            raise ValueError(f"Unknown variable group: {', '.join(unknown)}")
        result["groups"] = [name for name in VARIABLE_GROUPS if name in chosen]
    elif product == "mars-surface":
        if isinstance(params, str):
            params = [p for p in params.replace("/", ",").split(",") if p.strip()]
        chosen = [p.strip() for p in (params or DEFAULT_MARS_PARAMS)]
        bad = [p for p in chosen if not PARAM_PATTERN.match(p)]
        if bad or not chosen:
            raise ValueError("Parameters must be GRIB codes such as 167.128")
        result["params"] = list(dict.fromkeys(chosen))
    return result


def wants_bathymetry(options: dict) -> bool:
    return options["product"] == "wave-spectra" or (
        options["product"] == "single-levels" and "qc" in options["groups"])


def build_area(latitude: float, longitude: float, buffer: float) -> list[float]:
    """Return ``[N, W, S, E]`` snapped outward to the 0.5 degree wave grid.

    Snapping guarantees the nearest wave-grid nodes are inside the request even
    with a tiny buffer, and avoids the irregular coordinates CDS returns for boxes
    that are not aligned to the native grid. The box is clipped at +-180 rather than
    wrapped, so a site near the date line gets a slightly one-sided box.
    """
    def down(value: float) -> float:
        return math.floor(value / WAVE_GRID_DEG) * WAVE_GRID_DEG

    def up(value: float) -> float:
        return math.ceil(value / WAVE_GRID_DEG) * WAVE_GRID_DEG

    north = min(90.0, up(latitude + buffer))
    south = max(-90.0, down(latitude - buffer))
    west = max(-180.0, down(longitude - buffer))
    east = min(180.0, up(longitude + buffer))
    return [north, west, south, east]


def month_chunks(start: dt.date, end: dt.date) -> list[tuple[int, int, list[int]]]:
    """Split ``start..end`` into ``(year, month, [days])`` tuples."""
    chunks = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        last_day = calendar.monthrange(year, month)[1]
        first = start.day if (year, month) == (start.year, start.month) else 1
        last = end.day if (year, month) == (end.year, end.month) else last_day
        chunks.append((year, month, list(range(first, last + 1))))
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return chunks


def hours_for(step: int) -> list[int]:
    return list(range(0, 24, step))


def auto_expver(last_day: dt.date, today: dt.date | None = None) -> str:
    """ERA5 final is expver 1; the most recent months are preliminary ERA5T (expver 5)."""
    cutoff = (today or dt.date.today()) - dt.timedelta(days=FINAL_LAG_DAYS)
    return "1" if last_day < cutoff else "5"


def build_request(year: int, month: int, days: list[int], area: list[float],
                  options: dict | None = None, expver: str = "1") -> tuple[str, dict]:
    """Return ``(dataset, request)`` for one month of the chosen product."""
    options = options or normalise_options(DEFAULT_PRODUCT)
    product, hours = options["product"], hours_for(options["time_step"])

    if product == "single-levels":
        variables = [v for group in options["groups"] for v in VARIABLE_GROUPS[group]]
        return SINGLE_LEVELS_DATASET, {
            "product_type": ["reanalysis"],
            "variable": variables,
            "year": [f"{year:04d}"],
            "month": [f"{month:02d}"],
            "day": [f"{day:02d}" for day in days],
            "time": [f"{hour:02d}:00" for hour in hours],
            "area": area,
            "data_format": "netcdf",
            "download_format": "unarchived",
        }

    first, last = dt.date(year, month, days[0]), dt.date(year, month, days[-1])
    request = {
        "class": "ea",
        "date": f"{first.isoformat()}/to/{last.isoformat()}",
        "expver": expver,
        "time": "/".join(f"{hour:02d}" for hour in hours),
        "type": "an",
        "area": area,
        "format": "netcdf",
    }
    if product == "mars-surface":
        request.update({
            "levtype": "sfc",
            "param": "/".join(options["params"]),
            "stream": "oper",
            "grid": "0.25/0.25",
        })
    else:  # wave-spectra
        request.update({
            "direction": "/".join(str(i) for i in range(1, SPECTRA_DIRECTIONS + 1)),
            "domain": "g",
            "frequency": "/".join(str(i) for i in range(1, SPECTRA_FREQUENCIES + 1)),
            "param": "251.140",
            "stream": "wave",
            "grid": "0.5/0.5",
        })
    return MARS_DATASET, request


def build_bathymetry_request(year: int, month: int, day: int, area: list[float]) -> tuple[str, dict]:
    """Model depth is time-invariant: one hour on one day is enough."""
    return SINGLE_LEVELS_DATASET, {
        "product_type": ["reanalysis"],
        "variable": [BATHYMETRY_VARIABLE],
        "year": [f"{year:04d}"],
        "month": [f"{month:02d}"],
        "day": [f"{day:02d}"],
        "time": ["00:00"],
        "area": area,
        "data_format": "netcdf",
        "download_format": "unarchived",
    }


def output_name(product: str, year: int, month: int, days: list[int] | None = None,
                whole_month: bool = True) -> str:
    prefix = {"single-levels": "era5", "mars-surface": "era5-mars", "wave-spectra": "era5-spectra"}
    suffix = "" if whole_month or not days else f"_d{days[0]:02d}-{days[-1]:02d}"
    return f"{prefix[product]}_{year:04d}-{month:02d}{suffix}.nc"


# CDS rejects MARS requests above a cost limit counted in fields (date x time x direction x
# frequency x parameter), not in area. The 6-hourly month in WaveSpectrum-ERA-5 (30 x 4 x 720 =
# 86,400 fields) is known to work, so spectra requests are split to stay within that. The exact
# CDS limit is not published here; if a request is still refused it is halved and retried.
MAX_FIELDS = {"wave-spectra": 86_400}


def fields_per_day(options: dict) -> int:
    steps = len(hours_for(options["time_step"]))
    if options["product"] == "wave-spectra":
        return steps * SPECTRA_DIRECTIONS * SPECTRA_FREQUENCIES
    if options["product"] == "mars-surface":
        return steps * len(options["params"])
    return steps * sum(len(VARIABLE_GROUPS[g]) for g in options["groups"])


def split_days(days: list[int], per_day: int, max_fields: int | None) -> list[list[int]]:
    """Split a month's days into runs that stay within ``max_fields`` (at least one day each)."""
    if not max_fields:
        return [days]
    return split_into(days, max_fields // max(per_day, 1))


def estimate_cost(client, dataset: str, request: dict):
    """Ask CDS what a request costs without submitting it; returns the reply or an error dict."""
    # cdsapi.Client() is a LegacyClient that wraps the modern client as ``.client``.
    modern = getattr(client, "client", client)
    try:
        return modern.estimate_costs(dataset, request)
    except Exception as exc:  # old-style key, endpoint unavailable, or CDS refuses to estimate
        return {"error": f"{type(exc).__name__}: {exc}"}


def probe_variants(options: dict, area: list[float], year: int, month: int, day: int,
                   configured_expver: str) -> list[tuple[str, str, dict]]:
    """Small test requests that separate the usual causes of a cost refusal."""
    other = "1" if configured_expver == "5" else "5"
    cases = [
        (f"1 day, {options['time_step']} h step, expver {configured_expver} (as configured)",
         options, year, month, day, configured_expver),
        ("1 day, 1 h step (hourly)", {**options, "time_step": 1}, year, month, day, configured_expver),
        ("1 day, 6 h step", {**options, "time_step": 6}, year, month, day, configured_expver),
        (f"1 day, 6 h step, expver {other}", {**options, "time_step": 6}, year, month, day, other),
        ("1 day, 6 h step, 2020-04-01, expver 1 (old final data)", {**options, "time_step": 6}, 2020, 4, 1, "1"),
    ]
    return [(label, *build_request(y, m, [d], area, opts, ev)) for label, opts, y, m, d, ev in cases]


def run_probe(client, variants) -> list[tuple[str, str]]:
    """Submit each variant and delete it at once if CDS accepts it; nothing is downloaded."""
    modern = getattr(client, "client", client)
    results = []
    for label, dataset, request in variants:
        try:
            remote = modern.submit(dataset, request)
        except Exception as exc:
            reason = " ".join(str(exc).split())
            results.append((label, f"REFUSED: {reason[:240]}"))
            continue
        try:
            remote.delete()
            results.append((label, "accepted (cancelled straight away, nothing downloaded)"))
        except Exception as exc:
            results.append((label, f"accepted but could not be cancelled ({exc}); remove it from your CDS "
                                   "requests page"))
    return results


def cost_limits(reply) -> list[tuple[str, float, float]]:
    """Find every (name, cost, limit) triple in a CDS cost reply, whatever its nesting."""
    found: list[tuple[str, float, float]] = []

    def numeric(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    def walk(node, name="cost"):
        if isinstance(node, dict):
            if numeric(node.get("cost")) and numeric(node.get("limit")):
                found.append((str(node.get("id", name)), float(node["cost"]), float(node["limit"])))
            for key, value in node.items():
                walk(value, str(key))
        elif isinstance(node, list):
            for value in node:
                walk(value, name)

    walk(reply)
    return found


def split_into(days: list[int], size: int) -> list[list[int]]:
    """Near-equal runs of at most ``size`` days."""
    size = max(1, size)
    parts = -(-len(days) // size)
    base, extra = divmod(len(days), parts)
    runs, start = [], 0
    for k in range(parts):
        length = base + (1 if k < extra else 0)
        runs.append(days[start:start + length])
        start += length
    return runs


def is_cost_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "cost limit" in text or "too large" in text or "reduce your selection" in text


def grid_nodes(area: list[float], step: float) -> tuple[int, int]:
    north, west, south, east = area
    return int(round((north - south) / step)) + 1, int(round((east - west) / step)) + 1


def estimate_size_mb(options: dict, area: list[float], days: int) -> float:
    """Rough uncompressed float32 size of one month, to show in the preview."""
    steps = days * (24 // options["time_step"])
    if options["product"] == "wave-spectra":
        n_lat, n_lon = grid_nodes(area, WAVE_GRID_DEG)
        values = n_lat * n_lon * steps * SPECTRA_DIRECTIONS * SPECTRA_FREQUENCIES
    elif options["product"] == "mars-surface":
        n_lat, n_lon = grid_nodes(area, ATMOS_GRID_DEG)
        values = n_lat * n_lon * steps * len(options["params"])
    else:
        wave_vars = sum(len(VARIABLE_GROUPS[g]) for g in options["groups"]
                        if g in ("waves", "composition", "qc", "extremes"))
        other_vars = sum(len(VARIABLE_GROUPS[g]) for g in options["groups"]
                         if g in ("wind", "temperature", "precipitation"))
        w_lat, w_lon = grid_nodes(area, WAVE_GRID_DEG)
        a_lat, a_lon = grid_nodes(area, ATMOS_GRID_DEG)
        values = steps * (wave_vars * w_lat * w_lon + other_vars * a_lat * a_lon)
    return values * 4 / 1048576


# --- catalogue checks and provenance ---------------------------------------------

def fetch_json(url: str, timeout: int = 30):
    request = urllib.request.Request(url, headers={"User-Agent": "ecmwf-map-app"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def catalogue_variables(dataset: str) -> set[str] | None:
    """Variable names the live CDS form accepts for ``dataset``, or None if unreachable."""
    try:
        form = fetch_json(f"{CATALOGUE}{dataset}/form.json")
    except Exception:
        return None
    names: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            if "values" in node and "labels" in node:
                names.update(node["values"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for widget in form:
        if widget.get("name") == "variable":
            walk(widget.get("details", {}))
    return names or None


def dataset_metadata(dataset: str) -> dict:
    """Title, DOI, licence id and catalogue update date, or a note if CDS is unreachable."""
    try:
        record = fetch_json(f"{CATALOGUE}{dataset}")
    except Exception as exc:
        return {"dataset": dataset, "note": f"catalogue not reachable: {exc}"}
    return {"dataset": dataset, "title": record.get("title"), "doi": record.get("sci:doi"),
            "license": record.get("license"), "catalogue_updated": record.get("updated")}


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def software_versions() -> dict:
    try:
        from importlib.metadata import version
        cds = version("cdsapi")
    except Exception:
        cds = None
    return {"python": platform.python_version(), "platform": platform.platform(), "cdsapi": cds}


def write_provenance(path: Path, record: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, indent=2), encoding="utf-8")
    temporary.replace(path)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--latitude", type=float, required=True)
    parser.add_argument("--longitude", type=float, required=True)
    parser.add_argument("--start", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--end", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--buffer", type=float, default=0.5, help="degrees around the site")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--product", choices=list(PRODUCTS), default=DEFAULT_PRODUCT)
    parser.add_argument("--groups", default="", help="single-levels groups, comma separated: "
                        + ",".join(VARIABLE_GROUPS))
    parser.add_argument("--params", default="", help="mars-surface GRIB codes, comma separated")
    parser.add_argument("--time-step", type=int, default=None, help="hours between time steps")
    parser.add_argument("--expver", choices=["auto", "1", "5"], default="auto",
                        help="MARS experiment version: 1 = ERA5, 5 = preliminary ERA5T")
    parser.add_argument("--dry-run", action="store_true", help="print requests, do not contact CDS")
    parser.add_argument("--probe", action="store_true",
                        help="submit a few one-day test requests, report which CDS accepts, and cancel "
                             "the accepted ones immediately (nothing is downloaded)")
    parser.add_argument("--estimate", action="store_true",
                        help="ask CDS for the cost of the first request of each product and exit; "
                             "nothing is submitted or downloaded")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        options = normalise_options(
            args.product,
            [g for g in args.groups.split(",") if g],
            [p for p in args.params.split(",") if p],
            args.time_step,
        )
        validate_period(args.start, args.end, product=args.product)
        if not 0 <= args.buffer <= MAX_BUFFER[args.product]:
            raise ValueError(f"Buffer must be between 0 and {MAX_BUFFER[args.product]:g} degrees "
                             f"for {args.product}")
    except ValueError as exc:
        print(f"error: {exc}", flush=True)
        return 2

    area = build_area(args.latitude, args.longitude, args.buffer)
    chunks = month_chunks(args.start, args.end)
    per_day = fields_per_day(options)
    plan = {(y, m): split_days(d, per_day, MAX_FIELDS.get(args.product)) for y, m, d in chunks}
    print(
        f"{PRODUCTS[args.product]} · site {args.latitude:.5f}, {args.longitude:.5f} · "
        f"area [N, W, S, E] = {area} · {options['time_step']} h step · "
        f"{len(chunks)} month(s), {sum(len(subs) for subs in plan.values())} CDS request(s)",
        flush=True,
    )
    total_mb = sum(estimate_size_mb(options, area, len(days)) for _, _, days in chunks)
    print(f"Estimated size ≈ {total_mb:,.1f} MB uncompressed (float32); files on disk are usually smaller.",
          flush=True)

    def request_for(year: int, month: int, days: list[int]) -> tuple[str, dict]:
        expver = args.expver
        if expver == "auto":
            expver = auto_expver(dt.date(year, month, days[-1]))
        return build_request(year, month, days, area, options, expver)

    first_year, first_month, first_days = chunks[0]
    bathymetry = build_bathymetry_request(first_year, first_month, first_days[0], area) \
        if wants_bathymetry(options) else None

    provenance = {
        "schema": 1,
        "tool": "ecmwf-map-app fetch_era5_waves.py",
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "completed_utc": None,
        "dry_run": args.dry_run,
        "product": args.product,
        "options": options,
        "site": {"latitude": args.latitude, "longitude": args.longitude, "buffer_deg": args.buffer},
        "area_nwse": area,
        "period": {"start": args.start.isoformat(), "end": args.end.isoformat()},
        "time_step_hours": options["time_step"],
        "expver_policy": args.expver,
        "datasets": [],
        "requests": [],
        "bathymetry": None,
        "software": software_versions(),
        "licence": {"statement": LICENCE_NOTE, "url": LICENCE_URL},
        "notes": ["The full licence text is on the CDS website; only the attribution statement and URL are "
                  "stored here."],
    }

    if args.dry_run:
        for year, month, days in chunks:
            subs = plan[(year, month)]
            for number, sub in enumerate(subs):
                dataset, request = request_for(year, month, sub)
                split = len(subs) > 1
                where = f" days {sub[0]:02d}-{sub[-1]:02d}" if split else ""
                print(f"--- {year:04d}-{month:02d}{where} · {dataset} ---", flush=True)
                if number == 0:  # one example per month keeps long runs readable
                    print(json.dumps(request, indent=2), flush=True)
                    if split:
                        print(f"(this month is split into {len(subs)} requests of at most "
                              f"{MAX_FIELDS[args.product]:,} fields; the others differ only in 'date')", flush=True)
                provenance["requests"].append({"month": f"{year:04d}-{month:02d}", "days": [sub[0], sub[-1]],
                                               "dataset": dataset, "request": request, "status": "dry-run"})
        if bathymetry:
            print(f"--- model depth · {bathymetry[0]} ---", flush=True)
            print(json.dumps(bathymetry[1], indent=2), flush=True)
            provenance["bathymetry"] = {"dataset": bathymetry[0], "request": bathymetry[1], "status": "dry-run"}
        args.output.mkdir(parents=True, exist_ok=True)
        provenance["completed_utc"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        write_provenance(args.output / "provenance.json", provenance)
        print("Dry run complete; nothing was sent to CDS.", flush=True)
        return 0

    try:
        import cdsapi
    except ImportError:
        print("error: install cdsapi (pip install cdsapi) and configure ~/.cdsapirc", flush=True)
        return 2

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    args.output.mkdir(parents=True, exist_ok=True)

    datasets_used = sorted({request_for(y, m, d)[0] for y, m, d in chunks} |
                           ({bathymetry[0]} if bathymetry else set()))
    provenance["datasets"] = [dataset_metadata(name) for name in datasets_used]
    if args.product == "single-levels":
        accepted = catalogue_variables(SINGLE_LEVELS_DATASET)
        wanted = [v for g in options["groups"] for v in VARIABLE_GROUPS[g]]
        if bathymetry:
            wanted.append(BATHYMETRY_VARIABLE)
        if accepted is None:
            print("warning: could not read the CDS catalogue to check variable names; CDS will reject "
                  "any it does not accept.", flush=True)
        else:
            rejected = [v for v in wanted if v not in accepted]
            if rejected:
                print("error: CDS does not list these variable names: " + ", ".join(rejected), flush=True)
                return 2
            print(f"Checked {len(wanted)} variable names against the live CDS catalogue: all accepted.",
                  flush=True)
    write_provenance(args.output / "provenance.json", provenance)
    client = cdsapi.Client(progress=False)

    if args.probe:
        year, month, days_ = chunks[0]
        expver_now = args.expver if args.expver != "auto" else auto_expver(dt.date(year, month, days_[-1]))
        print("Probing CDS with one-day test requests (accepted ones are cancelled at once):", flush=True)
        for label, outcome in run_probe(client, probe_variants(options, area, year, month, days_[0], expver_now)):
            print(f"  {label}\n    -> {outcome}", flush=True)
        return 0

    # Ask CDS what the first request costs before submitting anything. The reply format is CDS's;
    # whatever it says is printed so the real limit is visible instead of guessed.
    first_days = plan[(chunks[0][0], chunks[0][1])][0]
    probe_dataset, probe_request = request_for(chunks[0][0], chunks[0][1], first_days)
    reply = estimate_cost(client, probe_dataset, probe_request)
    print(f"CDS cost estimate for {len(first_days)} day(s) of {args.product}: "
          f"{json.dumps(reply, default=str)[:600]}", flush=True)
    triples = cost_limits(reply)
    provenance["cost_estimate"] = {"days": len(first_days), "reply": reply}
    if args.estimate:
        for name, cost, limit in triples:
            print(f"  {name}: cost {cost:g} of limit {limit:g} ({cost / limit:.0%})", flush=True)
        if not triples:
            print("  (no cost/limit pair found in the reply above)", flush=True)
        return 0
    worst = max((cost / limit for _, cost, limit in triples if limit > 0), default=0.0)
    if worst > 1.0:
        per_day = worst / len(first_days)
        fit = int(1.0 / per_day)
        if fit < 1:
            print(f"error: even one day is estimated at {per_day:.0%} of a CDS cost limit, so no split of "
                  "the period can work. Reduce the request itself: a larger time step (3 or 6 h), fewer "
                  "variables or parameters, or a different expver.", flush=True)
            write_provenance(args.output / "provenance.json", provenance)
            return 1
        print(f"CDS estimates the first request at {worst:.0%} of its limit; using runs of at most {fit} "
              "day(s).", flush=True)
        plan = {key: split_into(days_, fit) for key, days_ in
                ((k, sum(v, [])) for k, v in plan.items())}

    def retrieve(dataset: str, request: dict, target: Path, label: str) -> dict:
        record = {"dataset": dataset, "request": request, "file": target.name}
        if target.exists() and target.stat().st_size > 0:
            print(f"{label}: already downloaded, skipping", flush=True)
            record["status"] = "skipped (existing file)"
        else:
            partial = target.with_name(target.name + ".part")
            print(f"{label}: requesting from CDS (this may queue for a while)…", flush=True)
            client.retrieve(dataset, request, str(partial))
            partial.replace(target)
            print(f"{label}: saved {target.name} ({target.stat().st_size / 1048576:.1f} MB)", flush=True)
            record["status"] = "downloaded"
        record["bytes"] = target.stat().st_size
        record["sha256"] = sha256_of(target)
        return record

    def download_days(year, month, days, month_days, label) -> list[dict]:
        """Download ``days``; if CDS refuses it as too costly, halve it and retry."""
        whole = days == month_days
        dataset, request = request_for(year, month, days)
        target = args.output / output_name(args.product, year, month, days, whole)
        tag = label if whole else f"{label} days {days[0]:02d}-{days[-1]:02d}"
        try:
            record = retrieve(dataset, request, target, tag)
        except Exception as exc:  # cdsapi raises plain Exceptions for auth/licence/queue errors
            target.with_name(target.name + ".part").unlink(missing_ok=True)
            if is_cost_error(exc) and len(days) > 1:
                middle = len(days) // 2
                print(f"{tag}: CDS cost limit exceeded; splitting into {middle} + {len(days) - middle} "
                      "days and retrying", flush=True)
                first_half = download_days(year, month, days[:middle], month_days, label)
                if any(str(r["status"]).startswith("failed") for r in first_half):
                    return first_half  # the smaller request failed too: do not keep sending requests
                return first_half + download_days(year, month, days[middle:], month_days, label)
            if is_cost_error(exc):
                print(f"error: CDS refused a request of a single day ({tag}) as too costly, so shortening "
                      "the period cannot fix it. Something else in the request is too large for CDS: try a "
                      "larger --time-step (3 or 6 h, as in the WaveSpectrum-ERA-5 example), --expver 1 for "
                      "older months or --expver 5 for recent ones, and run with --estimate to see the "
                      "limits CDS reports.", flush=True)
            print(f"error: CDS request for {tag} failed: {exc}", flush=True)
            record = {"dataset": dataset, "request": request, "file": target.name, "status": f"failed: {exc}"}
        record["month"] = f"{year:04d}-{month:02d}"
        record["days"] = [days[0], days[-1]]
        return [record]

    failure = False
    for index, (year, month, days) in enumerate(chunks, start=1):
        label = f"[{index}/{len(chunks)}] {year:04d}-{month:02d}"
        first_request = request_for(year, month, plan[(year, month)][0])[1]
        if "expver" in first_request:
            kind = "ERA5 final" if first_request["expver"] == "1" else "preliminary ERA5T"
            print(f"{label}: using expver {first_request['expver']} ({kind})", flush=True)
        whole_target = args.output / output_name(args.product, year, month)
        subs = [days] if whole_target.exists() and whole_target.stat().st_size > 0 else plan[(year, month)]
        if len(subs) > 1:
            print(f"{label}: split into {len(subs)} requests to stay under the CDS cost limit", flush=True)
        for sub in subs:
            records = download_days(year, month, sub, days, label)
            provenance["requests"].extend(records)
            write_provenance(args.output / "provenance.json", provenance)
            if any(str(r["status"]).startswith("failed") for r in records):
                failure = True
                break
        if failure:
            break

    if bathymetry and not failure:
        try:
            provenance["bathymetry"] = retrieve(bathymetry[0], bathymetry[1],
                                                args.output / BATHYMETRY_FILE, "model depth")
        except Exception as exc:  # depth is an aid: the analysis falls back to deep water
            (args.output / (BATHYMETRY_FILE + ".part")).unlink(missing_ok=True)
            print(f"warning: model_bathymetry was not downloaded ({exc}); the analysis will assume "
                  "deep water.", flush=True)
            provenance["bathymetry"] = {"dataset": bathymetry[0], "request": bathymetry[1],
                                        "status": f"failed: {exc}"}

    provenance["completed_utc"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    write_provenance(args.output / "provenance.json", provenance)
    if failure:
        return 1
    print("All requested months downloaded.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
