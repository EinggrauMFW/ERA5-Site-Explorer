"""Nearest-grid-point analysis of CDS ERA5 NetCDF/ZIP output, for two separate routes.

* Option A, ``analyse_bulk``     ERA5 single levels / MARS surface fields. Integrated
  wave parameters; ``mwp`` is used as the energy period Te = Tm-1 for the flux.
* Option B, ``analyse_spectra``  ERA5 2D wave spectra, with every parameter and the
  finite-depth flux computed from E(f, theta).

Both return ``(payload, frame)``: a JSON-able summary for the browser and the full
per-record pandas frame (saved as ``timeseries.csv``). The routes are never blended;
``crosscheck.py`` compares their frames side by side.
"""

from __future__ import annotations

import tempfile
import warnings as _warnings
import zipfile
from pathlib import Path
from typing import NamedTuple

import numpy as np
import pandas as pd
import xarray as xr

import wavecalc as wc

ANALYSIS_VERSION = 7

class SpectraNodes(NamedTuple):
    node_data: dict
    months: np.ndarray
    note: str | None
    lats: np.ndarray | None
    lons: np.ndarray | None


HOURS_PER_YEAR = 8766.0           # 365.25 days
MIN_RECORD_YEARS = 10             # project target of the author, not a standard
MAX_DISPLAY_POINTS = 1500
SCATTER_HM0_BIN = 0.5             # m
SCATTER_PERIOD_BIN = 1.0          # s
ROSE_SECTORS = 16                 # 22.5 degree sectors centred on N, NNE, ...
ORDERING_TOLERANCE = 0.01         # relative; mine, to absorb GRIB packing rounding
COMPOSITION_TOLERANCE = 0.05      # |swh^2 - shww^2 - shts^2| / swh^2, mine
TE_TP_FLAG_RANGE = (0.7, 1.1)     # flag, not error; bimodal seas can leave it
BIMODAL_ENERGY_SHARE = 0.2        # second partition carrying >= 20% of m0; mine
MAX_PARTITION_STEPS = 3000
MAX_GAMMA_STEPS = 6000

WAVE_NAMES = {
    "swh", "mwp", "pp1d", "mwd", "shww", "shts", "mpww", "mpts", "mdww", "mdts",
    "mp1", "mp2", "wdw", "wsp", "hmax", "tmax",
}
WIND_FIELDS = ("u10", "v10")
CIRCULAR = {"mwd", "mdww", "mdts", "dm_from", "theta_j_from"}
OTHER_FIELDS = {
    "t2m": ("2 m temperature", "°C", lambda x: x - 273.15),
    "sst": ("Sea surface temperature", "°C", lambda x: x - 273.15),
    "tp": ("Total precipitation", "mm", lambda x: x * 1000.0),
    "msl": ("Mean sea level pressure", "hPa", lambda x: x / 100.0),
}

# short name -> (label, unit, advanced). Labels always carry the moment definition.
BULK_META = {
    "swh": ("Significant wave height Hm0 (swh)", "m", False),
    "mwp": ("Energy period Te = Tm-1 (mwp)", "s", False),
    "pp1d": ("Peak period Tp (pp1d)", "s", False),
    "j": ("Deep-water energy flux 0.4906·Hm0²·Te", "kW/m", False),
    "mwd": ("Mean wave direction, coming from (mwd)", "°", False),
    "wind": ("10 m wind speed", "m/s", False),
    "shww": ("Wind-sea Hm0 (shww)", "m", False),
    "shts": ("Total-swell Hm0 (shts)", "m", False),
    "mpww": ("Wind-sea Te = Tm-1 (mpww)", "s", False),
    "mpts": ("Total-swell Te = Tm-1 (mpts)", "s", False),
    "mdww": ("Wind-sea direction, from (mdww)", "°", True),
    "mdts": ("Total-swell direction, from (mdts)", "°", True),
    "mp1": ("Tm01 = m0/m1 (mp1)", "s", True),
    "mp2": ("Tm02 zero-crossing (mp2)", "s", True),
    "wdw": ("Directional width σθ (wdw), dimensionless", "", True),
    "wsp": ("Spectral peakedness Qp (wsp)", "", True),
    "hmax": ("Hmax, extremes only, not an energy parameter (hmax)", "m", True),
    "tmax": ("Period of Hmax, extremes only (tmax)", "s", True),
    "te_tp": ("Te/Tp diagnostic", "", True),
    "steepness": ("Steepness 2π·Hm0/(g·Tm02²)", "", True),
}
SPECTRA_META = {
    "hm0": ("Hm0 = 4√m0", "m", False),
    "te": ("Te = Tm-1 = m-1/m0", "s", False),
    "tp_parabolic": ("Tp, parabolic fit (ECMWF-style)", "s", False),
    "flux": ("Energy flux, finite depth", "kW/m", False),
    "dm_from": ("Mean direction, coming from", "°", False),
    "theta_j_from": ("Flux direction θJ, coming from", "°", False),
    "directionality": ("Flux directionality |J_vec|/J", "", False),
    "tm01": ("Tm01 = m0/m1", "s", True),
    "tm02": ("Tm02 = √(m0/m2)", "s", True),
    "tp_grid": ("Tp, grid-bin peak", "s", True),
    "eps0": ("Spectral width ε0 = √(m0·m-2/m-1² − 1)", "", True),
    "wdw": ("Directional width σθ (ECMWF eq. 10–12), dimensionless", "", True),
    "flux_deep": ("Deep-water formula 0.4906·Hm0²·Te", "kW/m", True),
    "te_tp": ("Te/Tp diagnostic", "", True),
    "gamma": ("JONSWAP γ fit, diagnostic only", "", True),
}


# --- grid and file helpers -------------------------------------------------------

def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))


def circular_mean(degrees) -> float | None:
    values = np.asarray(degrees, dtype=float)
    values = values[np.isfinite(values)]
    return float(wc.vector_mean_direction(values)) if values.size else None


def time_name_of(ds: xr.Dataset) -> str:
    return "valid_time" if "valid_time" in ds.coords else "time"


def pick_cell(ds: xr.Dataset, variable: str, latitude: float, longitude: float,
              first_step_only: bool = False) -> tuple[int, int, bool]:
    """Return (lat index, lon index, moved_to_ocean) of the nearest cell holding data.

    Land cells carry no ERA5 wave values, so the nearest cell with any finite
    value is chosen. ``first_step_only`` checks just the first time step, which
    avoids reading a whole spectra file.
    """
    lats, lons = ds["latitude"].values, ds["longitude"].values
    grid_lat, grid_lon = np.meshgrid(lats, lons, indexing="ij")
    distance = haversine_km(latitude, longitude, grid_lat, grid_lon)
    nearest = np.unravel_index(np.argmin(distance), distance.shape)
    data = ds[variable]
    time_name = time_name_of(ds)
    if first_step_only and time_name in data.dims:
        data = data.isel({time_name: 0})
    other = [dim for dim in data.dims if dim not in ("latitude", "longitude")]
    has_data = data.notnull().any(other) if other else data.notnull()
    has_data = has_data.transpose("latitude", "longitude").values
    if not has_data.any():
        raise ValueError("The downloaded data contain no ocean values in the requested area")
    masked = np.where(has_data, distance, np.inf)
    best = np.unravel_index(np.argmin(masked), masked.shape)
    return int(best[0]), int(best[1]), bool(best != nearest)


def cell_index(ds: xr.Dataset, coordinate: tuple[float, float]) -> tuple[int, int]:
    return (int(np.abs(ds["latitude"].values - coordinate[0]).argmin()),
            int(np.abs(ds["longitude"].values - coordinate[1]).argmin()))


def cell_coordinate(ds: xr.Dataset, cell: tuple[int, int]) -> tuple[float, float]:
    return float(ds["latitude"].values[cell[0]]), float(ds["longitude"].values[cell[1]])


def netcdf_paths(files: list[Path], temp_dir: Path) -> list[Path]:
    """Return plain NetCDF paths, unpacking any ZIP archives into ``temp_dir``."""
    paths: list[Path] = []
    for index, source in enumerate(files):
        if zipfile.is_zipfile(source):
            with zipfile.ZipFile(source) as archive:
                for member_index, member in enumerate(n for n in archive.namelist() if n.endswith(".nc")):
                    target = temp_dir / f"{index}-{member_index}-{Path(member).name}"
                    with archive.open(member) as incoming, target.open("wb") as outgoing:
                        while chunk := incoming.read(1024 * 1024):
                            outgoing.write(chunk)
                    paths.append(target)
        else:
            paths.append(source)
    return paths


def split_files(files: list[Path]) -> tuple[list[Path], list[Path]]:
    """Separate the time-invariant model-bathymetry download from the data files."""
    files = [Path(f) for f in files]
    bathymetry = [f for f in files if "bathymetry" in f.name]
    return [f for f in files if f not in bathymetry], bathymetry


def read_depth(bathymetry_files: list[Path], cell: tuple[float, float] | None) -> float | None:
    """Model water depth in metres at the wave cell, or None if unavailable."""
    if not bathymetry_files or cell is None:
        return None
    with tempfile.TemporaryDirectory(prefix="era5-depth-") as temp_name:
        for path in netcdf_paths(bathymetry_files, Path(temp_name)):
            with xr.open_dataset(path, engine="netcdf4") as ds:
                name = next((v for v in ds.data_vars if v in ("wmb", "dpth") or "bathy" in v.lower()), None)
                if name is None:
                    continue
                data = ds[name]
                extra = [d for d in data.dims if d not in ("latitude", "longitude")]
                if extra:
                    data = data.isel({d: 0 for d in extra})
                value = float(data.isel(latitude=cell_index(ds, cell)[0],
                                        longitude=cell_index(ds, cell)[1]).values)
                if np.isfinite(value) and value > 0:
                    return value
    return None


# --- Option A: integrated parameters ---------------------------------------------

def extract_point(ds: xr.Dataset, names: list[str], cell: tuple[int, int]) -> dict[str, pd.Series]:
    time_name = time_name_of(ds)
    point = ds[names].isel(latitude=cell[0], longitude=cell[1])
    if "expver" in point.dims:  # ERA5 / ERA5T are NaN-complementary along expver
        point = point.mean("expver", skipna=True)
    index = pd.to_datetime(np.atleast_1d(point[time_name].values))
    result = {}
    for name in names:
        data = point[name]
        extra = [dim for dim in data.dims if dim != time_name]
        if extra:
            data = data.isel({dim: 0 for dim in extra})
        result[name] = pd.Series(np.atleast_1d(data.values).astype(float), index=index)
    return result


def collect(files: list[Path], latitude: float, longitude: float) -> dict:
    """Read every NetCDF in ``files`` into one merged series per variable."""
    series: dict[str, list[pd.Series]] = {}
    attrs: dict[str, tuple[str, str]] = {}
    cells: dict[str, tuple[float, float]] = {}
    moved = False

    with tempfile.TemporaryDirectory(prefix="era5-analysis-") as temp_name:
        for path in netcdf_paths(files, Path(temp_name)):
            with xr.open_dataset(path, engine="netcdf4") as ds:
                names = [v for v in ds.data_vars if {"latitude", "longitude"} <= set(ds[v].dims)]
                if not names:
                    continue
                # The grid is a property of the file (wave stream vs oper stream), not of one variable.
                kind = "wave" if "swh" in names or any(v in WAVE_NAMES for v in names) else "atmos"
                if kind not in cells:
                    lead = "swh" if "swh" in names else names[0]
                    i, j, was_moved = pick_cell(ds, lead, latitude, longitude)
                    moved = moved or was_moved
                    cells[kind] = cell_coordinate(ds, (i, j))
                for name in names:
                    attrs.setdefault(name, (ds[name].attrs.get("long_name", name),
                                            ds[name].attrs.get("units", "")))
                for name, values in extract_point(ds, names, cell_index(ds, cells[kind])).items():
                    series.setdefault(name, []).append(values)

    if not series:
        raise ValueError("No gridded variables were found in the downloaded files")
    merged = {}
    for name, parts in series.items():
        joined = pd.concat(parts)
        merged[name] = joined[~joined.index.duplicated()].sort_index()
    return {"series": merged, "cells": cells, "moved": moved, "attrs": attrs}


# --- shared summarising helpers --------------------------------------------------

def finite(values) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    return values[np.isfinite(values)]


def summarise(values: np.ndarray, circular: bool) -> dict:
    values = finite(values)
    if not values.size:
        return {"mean": None, "p95": None, "maximum": None}
    if circular:  # percentiles and maxima of a bearing are meaningless
        return {"mean": round(circular_mean(values), 1), "p95": None, "maximum": None}
    return {
        "mean": round(float(values.mean()), 3),
        "p95": round(float(np.percentile(values, 95)), 3),
        "maximum": round(float(values.max()), 3),
    }


def to_list(values) -> list:
    return [round(float(v), 3) if np.isfinite(v) else None for v in values]


def downsample(values: np.ndarray, stride: int, circular: bool) -> tuple[list, list | None]:
    """Block mean (circular mean for bearings) plus block max for the chart."""
    if stride == 1:
        return to_list(values), None
    buckets = np.arange(len(values)) // stride
    if circular:
        radians = np.radians(values)
        sin = pd.Series(np.sin(radians)).groupby(buckets).mean()
        cos = pd.Series(np.cos(radians)).groupby(buckets).mean()
        return to_list((np.degrees(np.arctan2(sin, cos)) % 360).values), None
    grouped = pd.Series(values).groupby(buckets)
    return to_list(grouped.mean().values), to_list(grouped.max().values)


def ordered(names, circular: set[str], priority: list[str]) -> list[str]:
    names = list(names)
    first = [n for n in priority if n in names]
    rest = [n for n in names if n not in first and n not in circular]
    return first + rest + [n for n in names if n in circular and n not in first]


# Which tab of the analysis page a section belongs to (the browser shows one group at a time).
SECTION_GROUPS = (
    ("distributions", ("scatter", "wave rose", "cumulative")),
    ("direction", ("direction:", "sector flux", "partitions")),
    ("quality", ("quality control", "validity", "composition", "shape diagnostics")),
)


def section_group(title: str) -> str:
    lowered = title.lower()
    for group, keywords in SECTION_GROUPS:
        if any(keyword in lowered for keyword in keywords):
            return group
    return "overview"


def row(label, value, note=None) -> dict:
    return {"label": label, "value": value, "note": note}


def fmt(value, digits=3, unit="") -> str:
    if value is None or not np.isfinite(value):
        return "—"
    return f"{value:.{digits}f}{(' ' + unit) if unit else ''}"


def timestep_hours(index: pd.DatetimeIndex) -> float | None:
    if len(index) < 2:
        return None
    return float(np.median(np.diff(index.to_numpy()).astype("timedelta64[s]").astype(float)) / 3600.0)


def record_years(index: pd.DatetimeIndex) -> float:
    return float((index[-1] - index[0]).total_seconds() / (365.25 * 86400)) if len(index) > 1 else 0.0


def percentile_rows(values, unit, digits=2) -> list[dict]:
    values = finite(values)
    if not values.size:
        return []
    return [row(f"P{p}", fmt(float(np.percentile(values, p)), digits, unit)) for p in (10, 50, 90, 95)]


def monthly_section(frame: pd.DataFrame, columns: dict[str, tuple[str, int]], title: str) -> dict:
    """Climatology by calendar month. ``columns`` maps frame column -> (heading, digits)."""
    rows = []
    for month in range(1, 13):
        part = frame[frame.index.month == month]
        if not len(part):
            continue
        rows.append([f"{month:02d}"] + [
            fmt(float(np.nanmean(part[c])), d) if part[c].notna().any() else "—"
            for c, (_, d) in columns.items()] + [str(len(part))])
    return {"kind": "table", "title": title,
            "columns": ["Month"] + [h for h, _ in columns.values()] + ["Records"], "rows": rows}


def rose_index(angle_from: np.ndarray, sectors: int = ROSE_SECTORS) -> np.ndarray:
    width = 360.0 / sectors
    return np.floor(((angle_from + width / 2) % 360.0) / width).astype(int) % sectors


def rose_labels(sectors: int = ROSE_SECTORS) -> list[float]:
    return [k * 360.0 / sectors for k in range(sectors)]


def scatter_section(title, x_label, y_label, x, y, weights, x_bin, y_bin, weight_label) -> dict:
    x, y, weights = (np.asarray(a, dtype=float) for a in (x, y, weights))
    ok = np.isfinite(x) & np.isfinite(y)
    x_top = np.ceil(x[ok].max() / x_bin) * x_bin + x_bin if ok.any() else x_bin
    y_top = np.ceil(y[ok].max() / y_bin) * y_bin + y_bin if ok.any() else y_bin
    x_edges = np.arange(0.0, x_top + x_bin / 2, x_bin)
    y_edges = np.arange(0.0, y_top + y_bin / 2, y_bin)
    table = wc.scatter_table(x, y, weights, x_edges, y_edges)
    inside = max(table["inside"], 1)
    total_weight = table["weights"].sum()
    hours = 100.0 * table["counts"] / inside
    energy = 100.0 * table["weights"] / total_weight if total_weight > 0 else np.zeros_like(hours)
    occupied = np.sort(energy[table["counts"] > 0])[::-1]
    cumulative = np.cumsum(occupied)
    bins_90 = int(np.searchsorted(cumulative, 90.0) + 1) if occupied.size else 0
    return {
        "kind": "scatter", "title": title, "x_label": x_label, "y_label": y_label,
        "x_edges": x_edges.tolist(), "y_edges": y_edges.tolist(),
        "hours_pct": np.round(hours, 3).tolist(), "energy_pct": np.round(energy, 3).tolist(),
        "weight_label": weight_label,
        "note": (f"Bins are half-open [lower, upper): {x_bin:g} {x_label.split('(')[-1].strip(') ')} × "
                 f"{y_bin:g} {y_label.split('(')[-1].strip(') ')}. {table['inside']:,} records in the table, "
                 f"{table['outside']:,} outside it, {table['invalid']:,} invalid. "
                 f"{bins_90} of {int((table['counts'] > 0).sum())} occupied bins carry 90% of the energy."),
    }


def rose_section(title, labels, series, note=None, unit="%") -> dict:
    return {"kind": "rose", "title": title, "sector_labels": [round(v, 1) for v in labels],
            "series": series, "unit": unit, "note": note}


def build_payload(frame: pd.DataFrame, specs: list[tuple[str, str, str, bool, bool]], *,
                  latitude, longitude, wave_cell, atmos_cell, moved, route, sections, notes,
                  warnings, primary, priority) -> dict:
    """Downsample and summarise the chart columns, and attach the section list."""
    index = frame.index
    count = len(index)
    stride = max(1, -(-count // MAX_DISPLAY_POINTS))
    stamps = index.to_numpy().astype("datetime64[s]")
    times = [str(stamps[i]) for i in range(0, count, stride)]

    series = {}
    for name, label, unit, circular, advanced in specs:
        values = frame[name].to_numpy(dtype=float)
        mean_values, max_values = downsample(values, stride, circular)
        series[name] = {"label": label, "unit": unit, "circular": circular, "advanced": advanced,
                        "values": mean_values, "max": max_values, **summarise(values, circular)}

    for section in sections:
        section.setdefault("group", section_group(section["title"]))
    cell = wave_cell or atmos_cell
    distance = float(haversine_km(latitude, longitude, *cell))
    primary_values = frame[primary].to_numpy(dtype=float)
    return {
        "route": route,
        "requested_coordinate": {"latitude": latitude, "longitude": longitude},
        "grid_coordinate": {"latitude": cell[0], "longitude": cell[1]},
        "atmos_grid_coordinate": (
            {"latitude": atmos_cell[0], "longitude": atmos_cell[1]} if atmos_cell and wave_cell else None),
        "grid_distance_km": round(distance, 1),
        "moved_to_ocean": bool(moved),
        "warnings": warnings,
        "notes": notes,
        "coverage": round(float(np.isfinite(primary_values).mean()), 4),
        "points": count,
        "displayed_points": len(times),
        "stride": stride,
        "start": str(stamps[0]),
        "end": str(stamps[-1]),
        "times": times,
        "order": ordered(series, CIRCULAR, priority),
        "series": series,
        "sections": sections,
    }


def record_section(frame: pd.DataFrame, step_hours, route_note: str) -> tuple[dict, list[str]]:
    years = record_years(frame.index)
    warnings = []
    if years < MIN_RECORD_YEARS:
        warnings.append(
            f"The record is {years:.2f} years. Under {MIN_RECORD_YEARS} years (the project target, not a "
            "standard) it is a demonstration, not a resource estimate; interannual variability "
            "(ENSO/IOD at Indonesian sites) is not sampled.")
    rows = [
        row("Period", f"{frame.index[0]:%Y-%m-%d %H:%M} to {frame.index[-1]:%Y-%m-%d %H:%M} UTC"),
        row("Records", f"{len(frame):,}"),
        row("Time step", f"{step_hours:g} h" if step_hours else "—"),
        row("Record length", f"{years:.2f} years"),
        row("Route", route_note),
    ]
    return {"kind": "kv", "title": "Record and sampling", "rows": rows}, warnings


def flux_section(flux: np.ndarray, title: str, method: str, hm0=None) -> dict:
    values = finite(flux)
    if not values.size:
        return {"kind": "kv", "title": title, "rows": [row("Energy flux", "not available")]}
    mean = float(values.mean())
    rows = [
        row("Mean J (each record first, then averaged)", fmt(mean, 2, "kW/m"), method),
        *percentile_rows(values, "kW/m"),
        row("Maximum", fmt(float(values.max()), 1, "kW/m")),
        row("Coefficient of variation (std/mean)", fmt(float(values.std() / mean), 2),
            "J is nonlinear in Hm0: never compute it from mean Hm0 and mean Te"),
        row("Mean annual energy per metre of crest",
            fmt(mean * HOURS_PER_YEAR / 1000.0, 1, "MWh/m/yr"), f"mean J × {HOURS_PER_YEAR:g} h"),
    ]
    if hm0 is not None:
        mean_hm0 = float(finite(hm0).mean())
        rows.append(row("Mean Hm0", fmt(mean_hm0, 2, "m")))
    return {"kind": "kv", "title": title, "rows": rows}


def common_limits() -> list[str]:
    return [
        "ERA5 is a model reanalysis, not a measurement. Validate against in-situ buoys before quoting a resource.",
        "The wave model runs on a reduced grid of about 0.36° and is delivered on a 0.5° regular grid. "
        "It does not resolve shoaling, refraction, islands or the nearshore: a coastal site is represented by "
        "an offshore condition.",
        "ERA5 assimilates wave observations, so comparing it with altimeter wave heights is not an "
        "independent validation. (Assimilation of altimeter data is not verified in this app; check the "
        "ECMWF ERA5 documentation.)",
    ]


# --- Option A: analysis ----------------------------------------------------------

def analyse_bulk(files: list[Path], latitude: float, longitude: float,
                 node: tuple[float, float] | None = None) -> tuple[dict, pd.DataFrame]:
    data_files, bathymetry = split_files(files)
    data = collect(data_files, *(node or (latitude, longitude)))
    frame = pd.DataFrame(data["series"]).sort_index()
    cells = data["cells"]
    depth = read_depth(bathymetry, cells.get("wave"))

    warnings: list[str] = []
    if "swh" in frame and "mwp" in frame:
        frame["j"] = wc.deep_water_flux(frame["swh"].to_numpy(float), frame["mwp"].to_numpy(float))
    elif "swh" in frame:
        warnings.append("No mean_wave_period (mwp = Te = Tm-1) in the download, so the energy flux is not "
                        "computed. The peak period pp1d is deliberately not used in the flux formula.")
    if set(WIND_FIELDS) <= set(frame.columns):
        frame["wind"] = np.hypot(frame["u10"].to_numpy(float), frame["v10"].to_numpy(float))
    if {"mp2", "swh"} <= set(frame.columns):
        frame["steepness"] = wc.steepness(frame["swh"].to_numpy(float), frame["mp2"].to_numpy(float))
    if {"mwp", "pp1d"} <= set(frame.columns):
        with np.errstate(divide="ignore", invalid="ignore"):
            frame["te_tp"] = frame["mwp"].to_numpy(float) / frame["pp1d"].to_numpy(float)
    for name, (label, unit, convert) in OTHER_FIELDS.items():
        if name in frame.columns:
            frame[name] = convert(frame[name].to_numpy(float))

    if frame.empty or not any(np.isfinite(frame[c].to_numpy(float)).any() for c in frame.columns):
        raise ValueError("The downloaded variables contain no valid values")

    step = timestep_hours(frame.index)
    record, record_warnings = record_section(
        frame, step, "Option A · ERA5 single levels: integrated parameters, mwp used as Te")
    warnings += record_warnings
    sections = [record]
    notes = [
        "Energy flux uses ERA5 mwp, which ECMWF defines as Tm-1 = m-1/m0 (the energy period, param 140232). "
        "No Tp→Te ratio is applied. J = ρg²/(64π)·Hm0²·Te is the deep-water relation.",
        "mwd is an energy-weighted mean over all frequencies, not the direction of energy flux. "
        "When swell and wind sea arrive from different directions the flux direction differs; "
        "only Option B can resolve that.",
    ] + common_limits()

    has_flux = "j" in frame
    if has_flux:
        flux_note = "J = 0.4906·Hm0²·Te per record, Te = mwp (Tm-1)"
        sections.append(flux_section(frame["j"].to_numpy(float), "Deep-water energy flux (Hm0, Te)",
                                     flux_note, frame["swh"].to_numpy(float)))
        sections.append(monthly_section(frame, {
            "swh": ("Mean Hm0 (m)", 2), "mwp": ("Mean Te (s)", 1), "j": ("Mean J (kW/m)", 1)},
            "Monthly climatology"))
        valid = frame[["swh", "mwp"]].notna().all(axis=1)
        sections.append(_swap_scatter_axes(scatter_section(
            "Scatter diagram Hm0 vs Te (mwp)", "Te (s)", "Hm0 (m)",
            frame.loc[valid, "mwp"], frame.loc[valid, "swh"], frame.loc[valid, "j"],
            SCATTER_PERIOD_BIN, SCATTER_HM0_BIN, "Share of energy (J)")))

        if "mwd" in frame:
            direction = frame["mwd"].to_numpy(float)
            ok = np.isfinite(direction) & frame["j"].notna().to_numpy()
            sector = rose_index(direction[ok])
            hours = np.bincount(sector, minlength=ROSE_SECTORS) / max(ok.sum(), 1) * 100
            energy = np.bincount(sector, weights=frame["j"].to_numpy(float)[ok], minlength=ROSE_SECTORS)
            energy = energy / max(energy.sum(), 1e-12) * 100
            sections.append(rose_section(
                "Wave rose, mwd (coming from)", rose_labels(),
                [{"label": "Share of hours", "values": np.round(hours, 2).tolist()},
                 {"label": "Share of energy (J)", "values": np.round(energy, 2).tolist()}],
                f"{ROSE_SECTORS} sectors of {360 / ROSE_SECTORS:g}° centred on true north, clockwise."))

    sections += _composition_sections(frame, warnings)
    sections += _quality_sections(frame, depth, has_flux, warnings, step)

    sampling = _sampling_check(frame, step)
    if sampling:
        sections.append(sampling)

    specs = []
    for name in frame.columns:
        if name in ("u10", "v10"):
            continue
        if name in BULK_META:
            label, unit, advanced = BULK_META[name]
        elif name in OTHER_FIELDS:
            label, unit, advanced = OTHER_FIELDS[name][0], OTHER_FIELDS[name][1], False
        else:
            label, unit = data["attrs"].get(name, (name, ""))
            advanced = True
        specs.append((name, label, unit, name in CIRCULAR, advanced))

    payload = build_payload(
        frame, specs, latitude=latitude, longitude=longitude, wave_cell=cells.get("wave"),
        atmos_cell=cells.get("atmos"), moved=data["moved"], route="single-levels",
        sections=sections, notes=notes, warnings=warnings,
        primary="swh" if "swh" in frame else frame.columns[0],
        priority=["swh", "mwp", "pp1d", "j", "wind", "shww", "shts", "mpww", "mpts"])
    payload["depth_m"] = depth
    payload["node_selected"] = node is not None
    return payload, frame


def _swap_scatter_axes(section: dict) -> dict:
    """scatter_section is (x rows, y columns); the browser draws rows = Hm0, columns = period."""
    for key in ("hours_pct", "energy_pct"):
        section[key] = np.asarray(section[key]).T.tolist()
    section["x_edges"], section["y_edges"] = section["y_edges"], section["x_edges"]
    section["x_label"], section["y_label"] = section["y_label"], section["x_label"]
    return section


def _composition_sections(frame: pd.DataFrame, warnings: list[str]) -> list[dict]:
    if not {"shww", "shts"} <= set(frame.columns):
        return []
    wind_sea, swell = frame["shww"].to_numpy(float), frame["shts"].to_numpy(float)
    ok = np.isfinite(wind_sea) & np.isfinite(swell)
    rows = [row("Swell-dominated hours (shts² > shww²)",
                fmt(float((swell[ok] ** 2 > wind_sea[ok] ** 2).mean() * 100), 1, "%"))]
    if {"mpww", "mpts"} <= set(frame.columns):
        pw, ps = frame["mpww"].to_numpy(float), frame["mpts"].to_numpy(float)
        share = swell**2 * ps / (wind_sea**2 * pw + swell**2 * ps)
        rows.append(row("Mean swell share of energy", fmt(float(finite(share).mean() * 100), 1, "%"),
                        "shts²·mpts / (shww²·mpww + shts²·mpts). ECMWF defines mpww and mpts as Tm-1 "
                        "(params 140236, 140239), so these weights are energy-consistent"))
    if "swh" in frame:
        swh = frame["swh"].to_numpy(float)
        good = ok & np.isfinite(swh) & (swh > 0)
        error = np.abs(swh[good] ** 2 - wind_sea[good] ** 2 - swell[good] ** 2) / swh[good] ** 2
        if error.size:
            within = float((error <= COMPOSITION_TOLERANCE).mean() * 100)
            rows.append(row("QC: swh² ≈ shww² + shts²",
                            f"median error {np.median(error) * 100:.2f}%, P95 {np.percentile(error, 95) * 100:.2f}%, "
                            f"{within:.1f}% of records within {COMPOSITION_TOLERANCE * 100:g}%",
                            "tolerance chosen by this app, not a standard"))
            if within < 95:
                warnings.append("Fewer than 95% of records satisfy swh² ≈ shww² + shts²: check the variables "
                                "and units before trusting the wind-sea/swell split.")
    return [{"kind": "kv", "title": "Sea-state composition", "rows": rows}]


def _quality_sections(frame: pd.DataFrame, depth: float | None, has_flux: bool,
                      warnings: list[str], step) -> list[dict]:
    rows = []
    if {"mp2", "mp1", "mwp"} <= set(frame.columns):
        bad, checked, _ = wc.ordering_violations(frame["mp2"], frame["mp1"], frame["mwp"], ORDERING_TOLERANCE)
        rows.append(row("QC: Tm02 ≤ Tm01 ≤ Te (mp2 ≤ mp1 ≤ mwp)", f"{bad:,} violations in {checked:,} records",
                        f"{ORDERING_TOLERANCE * 100:g}% tolerance (mine); a violation means a wrong variable, units or decoding"))
        if bad:
            warnings.append(f"{bad:,} records break Tm02 ≤ Tm01 ≤ Te. Check the variable mapping and units.")
    elif has_flux:
        rows.append(row("QC: Tm02 ≤ Tm01 ≤ Te", "not checked", "request the QC group (mp1, mp2) to enable"))
    if "te_tp" in frame:
        ratio = finite(frame["te_tp"])
        if ratio.size:
            low, high = TE_TP_FLAG_RANGE
            outside = float(((ratio < low) | (ratio > high)).mean() * 100)
            rows.append(row("Te/Tp", f"mean {ratio.mean():.2f}, {outside:.1f}% outside {low}–{high}",
                            "about 0.90 for JONSWAP γ = 3.3 and 0.86 for Pierson–Moskowitz; outside the range "
                            "is a flag for inspection (bimodal seas), not an error"))
    if "steepness" in frame:
        values = finite(frame["steepness"])
        if values.size:
            rows.append(row("Steepness 2π·Hm0/(g·Tm02²)", f"mean {values.mean():.3f}, max {values.max():.3f}"))
    if "wdw" in frame:
        values = finite(frame["wdw"])
        if values.size:
            rows.append(row("Directional width σθ (wdw)", f"mean {values.mean():.2f} (0 = unidirectional, √2 = uniform)"))
    sections = [{"kind": "kv", "title": "Quality control and spectral shape", "rows": rows}] if rows else []

    depth_rows = []
    if depth is not None and "mwp" in frame:
        te = frame["mwp"].to_numpy(float)
        half_wavelength = wc.deep_water_wavelength(te) / 2
        shallow = float(np.nanmean(depth < half_wavelength[np.isfinite(half_wavelength)]) * 100)
        depth_rows = [
            row("Model depth at the grid cell (wmb)", fmt(depth, 0, "m")),
            row("Hours with depth < L0/2", fmt(shallow, 1, "%"), "L0 = g·Te²/(2π); the deep-water flux is an approximation there"),
        ]
        if shallow > 0:
            warnings.append(f"Depth {depth:.0f} m is below L0/2 for {shallow:.1f}% of hours: the deep-water flux "
                            "is an approximation there, and its error does not have a fixed sign.")
    elif has_flux:
        depth_rows = [row("Model depth", "not in the download",
                          "request the QC group to get model_bathymetry; deep-water validity is unchecked")]
    if depth_rows:
        sections.append({"kind": "kv", "title": "Deep-water validity", "rows": depth_rows})
    return sections


def _sampling_check(frame: pd.DataFrame, step) -> dict | None:
    """Compare hourly statistics with the same record sampled every 6 hours."""
    if step is None or abs(step - 1.0) > 0.01 or "swh" not in frame:
        return None
    subset = frame[frame.index.hour % 6 == 0]
    columns = [c for c in ("swh", "mwp", "j") if c in frame]
    rows = []
    for column in columns:
        full, sampled = float(np.nanmean(frame[column])), float(np.nanmean(subset[column]))
        rows.append(row(f"Mean {column}", f"hourly {full:.3f} · 6-hourly {sampled:.3f}",
                        f"difference {100 * (sampled - full) / full:+.2f}%"))
    return {"kind": "kv", "title": "Sampling check (hourly vs every 6 h, this record only)", "rows": rows}


# --- Option B: 2D spectra --------------------------------------------------------

def spectra_variable(ds: xr.Dataset):
    dir_dim = next((d for d in ("directionNumber", "direction") if d in ds.dims), None)
    freq_dim = next((d for d in ("frequencyNumber", "frequency") if d in ds.dims), None)
    if not (dir_dim and freq_dim):
        return None
    name = next((v for v in ds.data_vars if dir_dim in ds[v].dims and freq_dim in ds[v].dims), None)
    return (name, dir_dim, freq_dim) if name else None


def partition_spectra(density, freqs, dfreq, dtheta, dir_to, depth, parts=3):
    """Watershed partitioning with wavespectra PTM3 (no wind needed, no wind-sea/swell labels).

    Returns a list (one per partition) of bulk-parameter dicts, or None if wavespectra is
    not installed. Systems are ordered by decreasing Hm0, not classified as sea or swell.
    """
    try:
        import warnings as _warnings

        import wavespectra  # noqa: F401
    except ImportError:
        return None
    steps = density.shape[0]
    dir_from = wc.to_from(dir_to)
    # wavespectra expects efth in m2/(Hz deg) with coming-from directions.
    efth = xr.DataArray(
        np.transpose(density, (0, 2, 1)) * np.pi / 180.0,
        dims=("time", "freq", "dir"),
        coords={"time": np.arange(steps), "freq": freqs, "dir": dir_from},
        name="efth",
    )
    with _warnings.catch_warnings():
        _warnings.simplefilter("ignore")
        dset = xr.Dataset({"efth": efth})
        result = dset.spec.partition.ptm3(parts=parts).compute()
    out = []
    for p in range(result.sizes["part"]):
        part_density = np.transpose(result.isel(part=p).values, (0, 2, 1)) * 180.0 / np.pi
        out.append(wc.spectral_bulk(np.nan_to_num(part_density), freqs, dfreq, dtheta, dir_to, depth))
    return out


def analyse_spectra(files: list[Path], latitude: float, longitude: float,
                    partition: bool = True,
                    node: tuple[float, float] | None = None) -> tuple[dict, pd.DataFrame]:
    data_files, bathymetry = split_files(files)
    freqs, dfreq, dtheta, dir_to, dir_from = wc.spectra_axes()
    cell_coord = None
    moved = False
    depth = None
    frames: list[pd.DataFrame] = []
    flux_f_sum = np.zeros(len(freqs))
    flux_f_count = 0
    partition_parts: dict[int, dict[str, list]] = {}
    partition_total: list[np.ndarray] = []
    min_density = np.inf
    missing_share = []
    time_stride = 1

    with tempfile.TemporaryDirectory(prefix="era5-analysis-") as temp_name:
        for path in netcdf_paths(data_files, Path(temp_name)):
            with xr.open_dataset(path, engine="netcdf4") as ds:
                found = spectra_variable(ds)
                if found is None:
                    continue
                variable, dir_dim, freq_dim = found
                if ds.sizes[dir_dim] != len(dir_to) or ds.sizes[freq_dim] != len(freqs):
                    raise ValueError(
                        f"Unexpected spectral grid {ds.sizes[dir_dim]} directions × {ds.sizes[freq_dim]} "
                        f"frequencies; this reader assumes the ERA5 24 × 30 grid")
                if cell_coord is None:
                    i, j, moved = pick_cell(ds, variable, *(node or (latitude, longitude)), first_step_only=True)
                    cell_coord = cell_coordinate(ds, (i, j))
                    depth = read_depth(bathymetry, cell_coord)
                lat_i, lon_j = cell_index(ds, cell_coord)
                time_name = time_name_of(ds)
                point = ds[variable].isel(latitude=lat_i, longitude=lon_j)
                if "expver" in point.dims:
                    point = point.mean("expver", skipna=True)
                point = point.transpose(time_name, dir_dim, freq_dim)
                raw = np.asarray(point.values, dtype=float)
                index = pd.to_datetime(np.atleast_1d(point[time_name].values))

                present = raw[np.isfinite(raw)]
                if present.size:
                    min_density = min(min_density, float(10.0 ** present.min()))
                missing_share.append(float(np.isnan(raw).mean()))
                density = wc.decode_log10(raw)
                bulk = wc.spectral_bulk(density, freqs, dfreq, dtheta, dir_to, depth)

                e_f = density.sum(axis=1) * dtheta
                stride = max(1, -(-len(index) // MAX_GAMMA_STEPS))
                gamma = np.full(len(index), np.nan)
                gamma[::stride] = wc.fit_gamma(e_f[::stride], freqs, bulk["tp_parabolic"][::stride] ** -1)
                with np.errstate(divide="ignore", invalid="ignore"):
                    te_tp = bulk["te"] / bulk["tp_parabolic"]

                columns = {k: v for k, v in bulk.items()
                           if k not in ("flux_dir", "flux_f", "m0")}
                columns.update({"te_tp": te_tp, "gamma": gamma, "m0": bulk["m0"]})
                for d in range(len(dir_to)):
                    columns[f"flux_dir_{d:02d}"] = bulk["flux_dir"][:, d]
                frames.append(pd.DataFrame(columns, index=index))

                good = np.isfinite(bulk["flux"])
                flux_f_sum += np.nansum(bulk["flux_f"][good], axis=0)
                flux_f_count += int(good.sum())

                if partition:
                    time_stride = max(1, -(-len(index) // MAX_PARTITION_STEPS))
                    sel = slice(None, None, time_stride)
                    parts = partition_spectra(density[sel], freqs, dfreq, dtheta, dir_to, depth)
                    if parts is not None:
                        partition_total.append(bulk["m0"][sel])
                        for p, bulk_p in enumerate(parts):
                            store = partition_parts.setdefault(p, {})
                            for key in ("hm0", "tp_parabolic", "te", "dm_from", "flux", "m0"):
                                store.setdefault(key, []).append(bulk_p[key])

    if not frames:
        raise ValueError("No 2D wave-spectra variable was found in the downloaded files")
    frame = pd.concat(frames)
    frame = frame[~frame.index.duplicated()].sort_index()
    if not np.isfinite(frame["hm0"].to_numpy()).any():
        raise ValueError("The wave spectra contain no valid values at this location")

    step = timestep_hours(frame.index)
    record, warnings = record_section(
        frame, step, "Option B · ERA5 2D wave spectra: every parameter computed from E(f, θ)")
    sections = [record]
    notes = [
        "Hm0, Te, Tm01, Tm02 and the flux are integrated from the spectrum, so no period is estimated. "
        "Frequencies are 0.03453·1.1ⁿ Hz (n = 0..29), directions are 24 bins of 15° stored as going-to and "
        "reported here as coming-from.",
        "The spectrum stops at 0.548 Hz (1.83 s). ECMWF's integrated parameters use an f⁻⁵ tail beyond the last "
        "bin, so Hm0 from the 30 bins is slightly below ERA5 swh. No tail is added here.",
        "Frequency bin widths are np.gradient of the frequency axis (the wavespectra convention); exact "
        "geometric bin edges would change Hm0 by about 0.06%.",
        "Directions are resolved to 15° bins: do not quote direction better than that, and treat σθ as approximate.",
        "Missing spectral bins are set to 0. The ECMWF page gives the encoding floor as approximately 1e-4, "
        f"but this file holds values down to {min_density:.1e}, so the floor is not exactly 1e-4. "
        f"{np.mean(missing_share) * 100:.0f}% of bins at the chosen cell are missing.",
    ] + common_limits()
    if depth is None:
        notes.append("No model bathymetry in the download: group velocity is deep-water, so the flux is the "
                     "deep-water value. Download the spectra product again (it requests model_bathymetry) for "
                     "a finite-depth flux.")
    else:
        notes.append(f"Finite-depth group velocity uses the model depth {depth:.0f} m at the grid cell. "
                     "Depth only changes cg here; there is no shoaling or refraction.")

    flux = frame["flux"].to_numpy(float)
    flux_rows = flux_section(
        flux, "Energy flux (finite-depth integral)" if depth else "Energy flux (deep-water integral)",
        "J = ρg ∫∫ cg(f, h) E(f, θ) df dθ" if depth else "J = ρg ∫∫ cg(f) E(f, θ) df dθ, deep water",
        frame["hm0"].to_numpy(float))
    deep_ratio = finite(flux / frame["flux_deep"].to_numpy(float))
    if deep_ratio.size:
        flux_rows["rows"].append(row("Finite-depth flux / 0.4906·Hm0²·Te", f"mean {deep_ratio.mean():.4f}",
                                     "1 in deep water; departs as the depth falls below L0/2"))
    sections.append(flux_rows)
    sections.append(monthly_section(frame, {
        "hm0": ("Mean Hm0 (m)", 2), "te": ("Mean Te (s)", 1), "tp_parabolic": ("Mean Tp (s)", 1),
        "flux": ("Mean J (kW/m)", 1)}, "Monthly climatology"))

    valid = frame[["hm0", "te"]].notna().all(axis=1)
    sections.append(_swap_scatter_axes(scatter_section(
        "Scatter diagram Hm0 vs Te (primary)", "Te (s)", "Hm0 (m)", frame.loc[valid, "te"],
        frame.loc[valid, "hm0"], frame.loc[valid, "flux"].fillna(0), SCATTER_PERIOD_BIN, SCATTER_HM0_BIN,
        "Share of energy (J)")))
    valid = frame[["hm0", "tp_parabolic"]].notna().all(axis=1)
    sections.append(_swap_scatter_axes(scatter_section(
        "Scatter diagram Hm0 vs Tp (to test Tp-indexed power matrices)", "Tp (s)", "Hm0 (m)",
        frame.loc[valid, "tp_parabolic"], frame.loc[valid, "hm0"], frame.loc[valid, "flux"].fillna(0),
        SCATTER_PERIOD_BIN, SCATTER_HM0_BIN, "Share of energy (J)")))

    flux_cols = [f"flux_dir_{d:02d}" for d in range(len(dir_to))]
    mean_flux_dir = np.nanmean(frame[flux_cols].to_numpy(float), axis=0)
    order = np.argsort(dir_from)  # file bins re-labelled coming-from, in compass order
    centres = dir_from[order]
    ok = np.isfinite(frame["dm_from"].to_numpy(float))
    sector = (np.floor(frame["dm_from"].to_numpy(float)[ok] / 15.0).astype(int)) % 24
    hours = np.bincount(sector, minlength=24) / max(ok.sum(), 1) * 100
    flux_share = mean_flux_dir[order] / max(np.nansum(mean_flux_dir), 1e-12) * 100
    sections.append(rose_section(
        "Wave rose by 15° direction bin (coming from)", centres.tolist(),
        [{"label": "Share of hours (energy-weighted mean direction)", "values": np.round(hours, 2).tolist()},
         {"label": "Share of flux", "values": np.round(flux_share, 2).tolist()}],
        "24 bins of 15°, labelled by the bin centre (coming from). Flux is binned in the file's own direction bins."))

    flux_by_f = flux_f_sum / max(flux_f_count, 1)
    periods = (1 / freqs)[::-1]
    share = np.cumsum(flux_by_f[::-1]) / max(flux_by_f.sum(), 1e-12) * 100
    sections.append({
        "kind": "line", "title": "Cumulative energy flux vs period", "x_label": "Period T = 1/f (s)",
        "x": np.round(periods, 3).tolist(),
        "y": [{"label": "Share of mean flux from periods ≤ T (%)", "values": np.round(share, 2).tolist()},
              {"label": "Mean flux per frequency bin (kW/m)", "values": np.round(flux_by_f[::-1], 4).tolist()}],
        "note": "Shows which periods carry the energy, i.e. how much falls inside a device response band."})

    theta_j = frame["theta_j_from"].to_numpy(float)
    dm = frame["dm_from"].to_numpy(float)
    both = np.isfinite(theta_j) & np.isfinite(dm)
    direction_rows = [
        row("Mean direction, energy-weighted (coming from)", fmt(circular_mean(dm), 1, "°")),
        row("Flux direction θJ (coming from)", fmt(circular_mean(theta_j), 1, "°"),
            "flux weights long periods more than mwd does"),
        row("Mean |θJ − mean direction|", fmt(float(np.abs(wc.angular_difference(theta_j[both], dm[both])).mean()), 1, "°")),
        row("Mean flux directionality |J_vec|/J", fmt(float(finite(frame["directionality"]).mean()), 3),
            "1 = unidirectional. The IEC TS 62600-101 name for this ratio is unverified"),
    ]
    sections.append({"kind": "kv", "title": "Direction: energy mean vs flux", "rows": direction_rows})

    sections += _partition_sections(partition_parts, partition_total, time_stride, partition)
    shape_rows = [
        row("Te/Tp (parabolic Tp)", f"mean {finite(frame['te_tp']).mean():.2f}" if finite(frame["te_tp"]).size else "—",
            "diagnostic: about 0.90 for JONSWAP γ = 3.3"),
        row("JONSWAP γ fit, median", fmt(float(np.nanmedian(frame["gamma"])) if finite(frame["gamma"]).size else None, 2),
            "diagnostic only; limited by the 10% frequency steps"),
    ]
    bad, checked, _ = wc.ordering_violations(frame["tm02"], frame["tm01"], frame["te"], ORDERING_TOLERANCE)
    shape_rows.append(row("QC: Tm02 ≤ Tm01 ≤ Te", f"{bad:,} violations in {checked:,} records"))
    if bad:
        warnings.append(f"{bad:,} records break Tm02 ≤ Tm01 ≤ Te: a decoding or unit error is likely.")
    sections.append({"kind": "kv", "title": "Spectral shape diagnostics", "rows": shape_rows})

    specs = [(name, label, unit, name in CIRCULAR, advanced) for name, (label, unit, advanced) in SPECTRA_META.items()
             if name in frame.columns]
    payload = build_payload(
        frame, specs, latitude=latitude, longitude=longitude, wave_cell=cell_coord, atmos_cell=None,
        moved=moved, route="wave-spectra", sections=sections, notes=notes, warnings=warnings,
        primary="hm0", priority=["hm0", "te", "tp_parabolic", "flux", "dm_from", "theta_j_from", "directionality"])
    payload["depth_m"] = depth
    payload["node_selected"] = node is not None
    return payload, frame


def _partition_sections(parts: dict[int, dict[str, list]], totals: list[np.ndarray], time_stride: int,
                        requested: bool) -> list[dict]:
    if not requested:
        return []
    if not parts:
        return [{"kind": "kv", "title": "Partitioning", "rows": [
            row("Not run", "wavespectra is not installed", "pip install wavespectra to enable PTM3 partitioning")]}]
    total_m0 = np.concatenate(totals)
    systems = {p: {k: np.concatenate(v) for k, v in store.items()} for p, store in sorted(parts.items())}
    table, shares = [], []
    # A system that never appears is all-NaN: averaging it is expected, not worth a warning.
    with np.errstate(divide="ignore", invalid="ignore"), _warnings.catch_warnings():
        _warnings.simplefilter("ignore", RuntimeWarning)
        for p, bulk in systems.items():
            share = np.where(total_m0 > 0, bulk["m0"] / total_m0, np.nan)
            shares.append(share)
            table.append([
                f"System {p + 1}", fmt(float(np.nanmean(bulk["hm0"])), 2),
                fmt(float(np.nanmean(bulk["tp_parabolic"])), 1), fmt(float(np.nanmean(bulk["te"])), 1),
                fmt(circular_mean(bulk["dm_from"]), 0), fmt(float(np.nanmean(bulk["flux"])), 2),
                fmt(float(np.nanmean(share) * 100), 1)])
        bimodal = float(np.nanmean(shares[1] >= BIMODAL_ENERGY_SHARE) * 100) if len(shares) > 1 else 0.0
        unassigned = float(np.nanmean(1 - np.nansum(np.vstack(shares), axis=0)) * 100)
    note = (f"Method: wavespectra PTM3 watershed partitioning, up to 3 systems, ordered by decreasing Hm0 "
            f"(not classified as wind sea or swell; it needs no wind). {len(total_m0):,} spectra"
            f"{f' (every {time_stride}th record)' if time_stride > 1 else ''}. "
            f"Multi-system hours (second system carries at least {BIMODAL_ENERGY_SHARE * 100:g}% of m0, threshold mine): "
            f"{bimodal:.1f}%. Energy not assigned to the 3 kept systems: {unassigned:.2f}%. "
            "These systems will not match the ECMWF swell partitions of Option A.")
    return [{"kind": "table", "title": "Spectral partitions (means over records)",
             "columns": ["System", "Hm0 (m)", "Tp (s)", "Te (s)", "Dir from (°)", "Flux J (kW/m)", "Share of m0 (%)"],
             "rows": table, "note": note}]


def sector_section(frame: pd.DataFrame, heading_from: float, half_width: float) -> dict:
    """Flux inside a heading sector, from the spectrum: the fixed-heading device view."""
    _, _, _, _, dir_from = wc.spectra_axes()
    flux_cols = [f"flux_dir_{d:02d}" for d in range(len(dir_from))]
    if not set(flux_cols) <= set(frame.columns):
        raise ValueError("Sector analysis needs the wave-spectra route")
    flux_dir = frame[flux_cols].to_numpy(float)
    fraction = wc.sector_fraction(flux_dir, dir_from, heading_from, half_width)
    total = np.nansum(flux_dir.sum(axis=1))
    in_sector = np.nansum(fraction * np.nansum(flux_dir, axis=1))
    by_flux = float(in_sector / total * 100) if total > 0 else float("nan")
    dm = frame["dm_from"].to_numpy(float)
    by_mwd = float(np.nanmean(np.abs(wc.angular_difference(dm, heading_from)) <= half_width) * 100)
    energy_by_mwd = float(np.nansum(np.where(np.abs(wc.angular_difference(dm, heading_from)) <= half_width,
                                             frame["flux"].to_numpy(float), 0)) / np.nansum(frame["flux"].to_numpy(float)) * 100)
    return {"kind": "kv", "title": f"Sector flux: heading {heading_from:g}° ± {half_width:g}° (coming from)", "rows": [
        row("Flux inside the sector (from the spectrum)", fmt(by_flux, 1, "%"),
            "the energy a fixed-heading device could see; bins on the edge are weighted by overlap, assuming "
            "flux is uniform within a 15° bin"),
        row("Flux a device forfeits (outside the sector)", fmt(100 - by_flux, 1, "%")),
        row("Hours with mean direction inside the sector", fmt(by_mwd, 1, "%"),
            "the filter-by-bulk-direction alternative"),
        row("Flux share of those hours", fmt(energy_by_mwd, 1, "%"),
            "differs from the first row because swell and wind sea can straddle the sector"),
    ]}


def analyse(files: list[Path], latitude: float, longitude: float, product: str = "single-levels",
            node: tuple[float, float] | None = None) -> tuple[dict, pd.DataFrame]:
    """Analyse the nearest ocean cell to the site, or the grid ``node`` if one is chosen."""
    if product == "wave-spectra":
        return analyse_spectra(files, latitude, longitude, node=node)
    return analyse_bulk(files, latitude, longitude, node=node)


# --- grid nodes ------------------------------------------------------------------

def read_depth_grid(bathymetry_files: list[Path]):
    """Model depth as a 2-D DataArray (latitude, longitude) in metres, or None."""
    if not bathymetry_files:
        return None
    with tempfile.TemporaryDirectory(prefix="era5-depth-") as temp_name:
        for path in netcdf_paths(bathymetry_files, Path(temp_name)):
            with xr.open_dataset(path, engine="netcdf4") as ds:
                name = next((v for v in ds.data_vars if v in ("wmb", "dpth") or "bathy" in v.lower()), None)
                if name is None:
                    continue
                data = ds[name]
                extra = [d for d in data.dims if d not in ("latitude", "longitude")]
                if extra:
                    data = data.isel({d: 0 for d in extra})
                return data.load()
    return None


def _depth_lookup(grid, lat: float, lon: float) -> float | None:
    if grid is None:
        return None
    value = float(grid.sel(latitude=lat, longitude=lon, method="nearest").values)
    return value if np.isfinite(value) and value > 0 else None


def _nan_mean(total: np.ndarray, count: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(count > 0, total / count, np.nan)


def sampling_note(stride: int, total_records: int) -> str | None:
    if stride <= 1:
        return None
    sampled = -(-total_records // stride)
    return f"Spectra summary uses one record in every {stride} ({sampled:,} of {total_records:,} records)."


MAX_NODE_RECORDS = 500000  # Budget for exact node summary (takes ~9.5 s wall time).
CHUNK_SIZE = 500  # Bounds memory during exact computation (peak ~244 MB).

def compute_spectra_nodes(paths: list[Path], depth_grid, total_records: int) -> SpectraNodes:
    """Return SpectraNodes (node_data, months, note, lats, lons).

    node_data is keyed by (i, j) with a dict of 'hm0', 'te', 'flux' arrays.
    """
    freqs, dfreq, dtheta, dir_to, _ = wc.spectra_axes()

    # First find lats and lons
    lats = lons = None
    for path in paths:
        with xr.open_dataset(path, engine="netcdf4") as ds:
            if spectra_variable(ds):
                lats = ds["latitude"].values
                lons = ds["longitude"].values
                break

    if lats is None:
        return SpectraNodes({}, np.array([]), None, None, None)

    n_nodes = len(lats) * len(lons)
    stride = max(1, -(-(total_records * n_nodes) // MAX_NODE_RECORDS))
    note = sampling_note(stride, total_records)

    node_lists = {(i, j): {"hm0": [], "te": [], "flux": []} for i in range(len(lats)) for j in range(len(lons))}
    all_months = []

    for path in paths:
        with xr.open_dataset(path, engine="netcdf4") as ds:
            found = spectra_variable(ds)
            if found is None:
                continue
            variable, dir_dim, freq_dim = found
            time_name = time_name_of(ds)

            raw_full = ds[variable].isel({time_name: slice(None, None, stride)})
            if "expver" in raw_full.dims:
                raw_full = raw_full.mean("expver", skipna=True)

            n_strided = raw_full.sizes[time_name]
            times = pd.DatetimeIndex(ds[time_name].values[::stride])
            all_months.append(times.month.to_numpy())

            for start in range(0, n_strided, CHUNK_SIZE):
                end = min(start + CHUNK_SIZE, n_strided)
                chunk = raw_full.isel({time_name: slice(start, end)})
                chunk = chunk.transpose(time_name, dir_dim, freq_dim, "latitude", "longitude").values.astype(float)

                for i, lat in enumerate(lats):
                    for j, lon in enumerate(lons):
                        column = chunk[:, :, :, i, j]
                        if not np.isfinite(column).any():
                            nans = np.full(end - start, np.nan)
                            for name in ("hm0", "te", "flux"):
                                node_lists[(i, j)][name].append(nans)
                            continue

                        depth = _depth_lookup(depth_grid, lat, lon)
                        bulk = wc.spectral_bulk(wc.decode_log10(column), freqs, dfreq, dtheta, dir_to, depth)
                        for name in ("hm0", "te", "flux"):
                            node_lists[(i, j)][name].append(bulk[name])

    months = np.concatenate(all_months) if all_months else np.array([])
    node_data = {
        key: {
            name: np.concatenate(lists[name]) if lists[name] else np.array([])
            for name in ("hm0", "te", "flux")
        }
        for key, lists in node_lists.items()
    }

    return SpectraNodes(node_data, months, note, lats, lons)


def node_summary(files: list[Path], product: str, latitude: float, longitude: float) -> dict:
    """Every grid node in the download with a quick summary, to pick one for analysis.

    Wave nodes carry mean Hm0, Te and flux (flux uses mwp for Option A and the spectrum for
    Option B). A node with no data is land (or ice): it is listed but cannot be selected. The
    spectra summary uses at most ``NODE_SAMPLE_STEPS`` records spread over the whole period.
    """
    data_files, bathymetry = split_files(files)
    depth_grid = read_depth_grid(bathymetry)
    lats = lons = None
    sums: dict[str, np.ndarray] = {}
    counts: dict[str, np.ndarray] = {}
    kind, note = "wave", None
    value_label = None

    def add(name, array):  # array: (time, lat, lon)
        finite_values = np.isfinite(array)
        total = np.where(finite_values, array, 0.0).sum(axis=0)
        count = finite_values.sum(axis=0)
        sums[name] = sums.get(name, 0) + total
        counts[name] = counts.get(name, 0) + count

    with tempfile.TemporaryDirectory(prefix="era5-nodes-") as temp_name:
        paths = netcdf_paths(data_files, Path(temp_name))
        if product == "wave-spectra":
            lengths = []
            for path in paths:
                with xr.open_dataset(path, engine="netcdf4") as ds:
                    found = spectra_variable(ds)
                    lengths.append(ds.sizes[time_name_of(ds)] if found else 0)
            total_records = sum(lengths)

            if total_records > 0:
                res = compute_spectra_nodes(paths, depth_grid, total_records)
                note = res.note
                lats = res.lats
                lons = res.lons

                if lats is not None:
                    shape = (len(lats), len(lons))
                    for name in ("hm0", "te", "flux"):
                        sums.setdefault(name, np.zeros(shape))
                        counts.setdefault(name, np.zeros(shape))

                    for i in range(len(lats)):
                        for j in range(len(lons)):
                            for name in ("hm0", "te", "flux"):
                                values = res.node_data[(i, j)][name]
                                good = np.isfinite(values)
                                sums[name][i, j] += values[good].sum() if good.any() else 0.0
                                counts[name][i, j] += good.sum()
        else:
            generic_done = False
            for path in paths:
                with xr.open_dataset(path, engine="netcdf4") as ds:
                    names = [v for v in ds.data_vars if {"latitude", "longitude"} <= set(ds[v].dims)]
                    if not names:
                        continue
                    wave = "swh" in names
                    if not wave and (generic_done or "hm0" in sums):
                        continue
                    if "expver" in ds.dims:
                        ds = ds.mean("expver", skipna=True)
                    time_name = time_name_of(ds)

                    def field(name, ds=ds, time_name=time_name):
                        data = ds[name]
                        extra = [d for d in data.dims if d not in (time_name, "latitude", "longitude")]
                        if extra:
                            data = data.isel({d: 0 for d in extra})
                        return data.transpose(time_name, "latitude", "longitude").values.astype(float)

                    if wave:
                        lats, lons = ds["latitude"].values, ds["longitude"].values
                        swh = field("swh")
                        add("hm0", swh)
                        if "mwp" in names:
                            mwp = field("mwp")
                            add("te", mwp)
                            add("flux", wc.deep_water_flux(swh, mwp))
                    elif "hm0" not in sums:
                        kind = "generic"
                        lats, lons = ds["latitude"].values, ds["longitude"].values
                        lead = names[0]
                        values = field(lead)
                        if lead in OTHER_FIELDS:
                            label, unit, convert = OTHER_FIELDS[lead]
                            values, value_label = convert(values), f"{label} ({unit})"
                        else:
                            long_name = ds[lead].attrs.get("long_name", lead)
                            value_label = f"{long_name} ({ds[lead].attrs.get('units', '')})"
                        add("value", values)
                        generic_done = True

    if lats is None:
        raise ValueError("No gridded variables were found in the downloaded files")
    means = {name: _nan_mean(sums[name], counts[name]) for name in sums}
    primary = "hm0" if "hm0" in means else "value"
    nodes = []
    for i, lat in enumerate(lats):
        for j, lon in enumerate(lons):
            valid = bool(np.isfinite(means[primary][i, j])) if primary in means else False
            entry = {"lat": float(lat), "lon": float(lon), "valid": valid,
                     "distance_km": round(float(haversine_km(latitude, longitude, lat, lon)), 1),
                     "depth": _depth_lookup(depth_grid, lat, lon)}
            for name in ("hm0", "te", "flux", "value"):
                if name in means:
                    v = means[name][i, j]
                    entry[name] = round(float(v), 3) if np.isfinite(v) else None
            nodes.append(entry)
    ocean = [n for n in nodes if n["valid"]]
    default = min(ocean, key=lambda n: n["distance_km"]) if ocean else None
    return {
        "kind": kind, "product": product, "value_label": value_label, "note": note,
        "requested_coordinate": {"latitude": latitude, "longitude": longitude},
        "default": {"lat": default["lat"], "lon": default["lon"]} if default else None,
        "nodes": nodes, "n_ocean": len(ocean), "n_land": len(nodes) - len(ocean),
        "lat_step": round(float(abs(np.median(np.diff(lats)))), 4) if len(lats) > 1 else None,
        "lon_step": round(float(abs(np.median(np.diff(lons)))), 4) if len(lons) > 1 else None,
    }


def find_node(summary: dict, lat: float, lon: float) -> dict | None:
    """Return the listed node at (lat, lon) within a small tolerance, or None."""
    for node in summary["nodes"]:
        if abs(node["lat"] - lat) < 1e-3 and abs(node["lon"] - lon) < 1e-3:
            return node
    return None
