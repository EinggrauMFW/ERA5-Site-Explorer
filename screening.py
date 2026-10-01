"""Site screening computations: per-node and seasonal statistics.

Computes mean wave parameters (Hm0, Te) and energy flux (kW/m) over the dataset's duration for all nodes in the bounding box.
For single-levels, flux uses deep-water approximation. For wave-spectra, it computes bulk parameters integrating the spectra over finite depth.
"""

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

import analysis
import wavecalc as wc

MAX_RECORDS = 1500

def _node_stats(hm0_arr, te_arr, flux_arr, months_arr):
    # Arrays are 1D, length = records
    hm0_arr = np.asarray(hm0_arr, dtype=float)
    te_arr = np.asarray(te_arr, dtype=float)
    flux_arr = np.asarray(flux_arr, dtype=float)
    months_arr = np.asarray(months_arr, dtype=int)
    
    valid = np.isfinite(flux_arr)
    if not valid.any():
        return {
            "hm0_mean_m": None, "te_mean_s": None, "flux_mean_kw_m": None,
            "flux_p95_kw_m": None, "flux_cov": None,
            "monthly_flux_kw_m": [None]*12, "season_flux_kw_m": {"DJF": None, "MAM": None, "JJA": None, "SON": None},
            "seasonality_ratio": None, "n_records": 0
        }
        
    f_valid = flux_arr[valid]
    m_valid = months_arr[valid]
    
    mean_flux = float(f_valid.mean())
    cov = float(f_valid.std() / mean_flux) if mean_flux > 0 else None
    p95 = float(np.percentile(f_valid, 95))
    
    monthly_flux = [None] * 12
    for m in range(1, 13):
        idx = m_valid == m
        if idx.any():
            monthly_flux[m - 1] = float(f_valid[idx].mean())
            
    # Seasons: DJF (12, 1, 2), MAM (3, 4, 5), JJA (6, 7, 8), SON (9, 10, 11)
    seasons = {
        "DJF": [12, 1, 2],
        "MAM": [3, 4, 5],
        "JJA": [6, 7, 8],
        "SON": [9, 10, 11]
    }
    season_flux = {}
    for s_name, s_months in seasons.items():
        idx = np.isin(m_valid, s_months)
        if idx.any():
            season_flux[s_name] = float(f_valid[idx].mean())
        else:
            season_flux[s_name] = None
            
    s_vals = [v for v in season_flux.values() if v is not None]
    if len(s_vals) == 4 and min(s_vals) > 0:
        seasonality_ratio = float(max(s_vals) / min(s_vals))
    else:
        seasonality_ratio = None
        
    v_hm0 = hm0_arr[np.isfinite(hm0_arr)]
    v_te = te_arr[np.isfinite(te_arr)]
    
    return {
        "hm0_mean_m": float(v_hm0.mean()) if len(v_hm0) > 0 else None,
        "te_mean_s": float(v_te.mean()) if len(v_te) > 0 else None,
        "flux_mean_kw_m": mean_flux,
        "flux_p95_kw_m": p95,
        "flux_cov": cov,
        "monthly_flux_kw_m": monthly_flux,
        "season_flux_kw_m": season_flux,
        "seasonality_ratio": seasonality_ratio,
        "n_records": int(valid.sum())
    }

def compute_screening(files: list[Path], product: str, latitude: float, longitude: float) -> dict:
    """Return site screening map payload (stats and units like kw_m) for a bounding box over all valid time records."""
    data_files, bathymetry = analysis.split_files(files)
    depth_grid = analysis.read_depth_grid(bathymetry)
    
    with tempfile.TemporaryDirectory(prefix="era5-screening-") as temp_name:
        paths = analysis.netcdf_paths(data_files, Path(temp_name))
        
        # 1. Get total record times
        timestamps = []
        for path in paths:
            with xr.open_dataset(path, engine="netcdf4") as ds:
                if product == "wave-spectra":
                    found = analysis.spectra_variable(ds)
                    if found is None:
                        continue
                timestamps.append(ds[analysis.time_name_of(ds)].values)
        if not timestamps:
            raise ValueError("No valid gridded variables found in the downloaded files.")
        
        index = pd.DatetimeIndex(np.concatenate(timestamps)).sort_values().drop_duplicates()
        n_tot = len(index)
        if n_tot > 1:
            step_hours = float(np.median(np.diff(index.to_numpy()).astype("timedelta64[s]").astype(float)) / 3600.0)
            years = float((index[-1] - index[0]).total_seconds() / (365.25 * 86400))
        else:
            step_hours = None
            years = 0.0
            
        record_dict = {
            "start": index[0].isoformat(),
            "end": index[-1].isoformat(),
            "years": years,
            "step_hours": step_hours,
            "records": n_tot
        }
        
        warnings = []
        if years < analysis.MIN_RECORD_YEARS:
            warnings.append(
                f"The record is {years:.2f} years. Under {analysis.MIN_RECORD_YEARS} years (the project target, not a "
                "standard) it is a demonstration, not a resource estimate; interannual variability "
                "(ENSO/IOD at Indonesian sites) is not sampled.")
        if len(np.unique(index.month)) < 12:
            warnings.append("Seasonal statistics from a partial year are biased: the record does not cover all 12 months.")
            
        season_def = "Calendar seasons by month: DJF = December, January, February; MAM = March-May; JJA = June-August; SON = September-November. Records are grouped by month only, so a record spanning several years pools all its Decembers with all its Januaries and Februaries."
        
        if product == "wave-spectra":
            res = _compute_spectra(paths, depth_grid, latitude, longitude, n_tot)
        else:
            res = _compute_bulk(paths, depth_grid, latitude, longitude)
            
        nodes = res["nodes"]
        default_node = None
        min_dist = np.inf
        for n in nodes:
            if n["valid"] and n["distance_km"] < min_dist:
                min_dist = n["distance_km"]
                default_node = {"lat": n["lat"], "lon": n["lon"]}
                
        n_ocean = sum(1 for n in nodes if n["valid"])
        n_land = len(nodes) - n_ocean
        
        if res.get("no_mwp"):
            warnings.append("No mean_wave_period (mwp = Te = Tm-1) in the download, so the energy flux is not computed. The peak period pp1d is deliberately not used in the flux formula.")
            
        return {
            "product": product,
            "route": product,
            "requested_coordinate": {"latitude": latitude, "longitude": longitude},
            "record": record_dict,
            "nodes": nodes,
            "default_node": default_node,
            "n_ocean": n_ocean,
            "n_land": n_land,
            "season_definition": season_def,
            "sampling_note": res.get("sampling_note"),
            "warnings": warnings
        }

def _compute_bulk(paths: list[Path], depth_grid, site_lat: float, site_lon: float) -> dict:
    # Option A: vectorise over nodes (arrays are small)
    all_swh = []
    all_mwp = []
    all_months = []
    lats = lons = None
    
    no_mwp = False
    
    for path in paths:
        with xr.open_dataset(path, engine="netcdf4") as ds:
            names = [v for v in ds.data_vars if {"latitude", "longitude"} <= set(ds[v].dims)]
            if not names:
                continue
            if "swh" not in names:
                continue
            if "expver" in ds.dims:
                ds = ds.mean("expver", skipna=True)
                
            time_name = analysis.time_name_of(ds)
            lats = ds["latitude"].values
            lons = ds["longitude"].values
            
            times = pd.DatetimeIndex(ds[time_name].values)
            all_months.append(times.month.to_numpy())
            
            def field(name):
                data = ds[name]
                extra = [d for d in data.dims if d not in (time_name, "latitude", "longitude")]
                if extra:
                    data = data.isel({d: 0 for d in extra})
                return data.transpose(time_name, "latitude", "longitude").values.astype(float)
                
            swh = field("swh")
            all_swh.append(swh)
            if "mwp" in names:
                all_mwp.append(field("mwp"))
            else:
                all_mwp.append(np.full_like(swh, np.nan))
                no_mwp = True
                
    if lats is None:
        raise ValueError("No wave variables found")
        
    swh = np.concatenate(all_swh, axis=0) # (time, lat, lon)
    mwp = np.concatenate(all_mwp, axis=0)
    months = np.concatenate(all_months, axis=0)
    
    flux = wc.deep_water_flux(swh, mwp)
    
    nodes = []
    for i, lat in enumerate(lats):
        for j, lon in enumerate(lons):
            h_node = swh[:, i, j]
            t_node = mwp[:, i, j]
            f_node = flux[:, i, j]
            
            # Check if any finite data exists for this node
            valid = bool(np.isfinite(h_node).any())
            dist = round(float(analysis.haversine_km(site_lat, site_lon, lat, lon)), 1)
            depth = analysis._depth_lookup(depth_grid, lat, lon)
            
            node_dict = {
                "lat": float(lat), "lon": float(lon), "valid": valid,
                "depth_m": depth, "distance_km": dist
            }
            if valid:
                stats = _node_stats(h_node, t_node, f_node, months)
                node_dict.update(stats)
            else:
                node_dict.update({
                    "hm0_mean_m": None, "te_mean_s": None, "flux_mean_kw_m": None,
                    "flux_p95_kw_m": None, "flux_cov": None,
                    "monthly_flux_kw_m": [None]*12,
                    "season_flux_kw_m": {"DJF": None, "MAM": None, "JJA": None, "SON": None},
                    "seasonality_ratio": None, "n_records": 0
                })
            nodes.append(node_dict)
            
    return {"nodes": nodes, "no_mwp": no_mwp}

def _compute_spectra(paths: list[Path], depth_grid, site_lat: float, site_lon: float, total_records: int) -> dict:
    stride = max(1, -(-total_records // MAX_RECORDS))
    sampling_note = None
    if stride > 1:
        sampled = -(-total_records // stride)
        sampling_note = f"Spectra summary uses every {stride}th record ({sampled:,} of {total_records:,})."
        
    freqs, dfreq, dtheta, dir_to, _ = wc.spectra_axes()
    
    lats = lons = None
    
    # Iterate over files, collect data per node
    # Since we can only load one file at a time, we'll accumulate node data in memory
    # But wait, memory can hold subsampled arrays per node.
    node_h = {}
    node_t = {}
    node_f = {}
    
    all_months = []
    
    for path in paths:
        with xr.open_dataset(path, engine="netcdf4") as ds:
            found = analysis.spectra_variable(ds)
            if not found:
                continue
            variable, dir_dim, freq_dim = found
            time_name = analysis.time_name_of(ds)
            lats = ds["latitude"].values
            lons = ds["longitude"].values
            
            raw = ds[variable].isel({time_name: slice(None, None, stride)})
            if "expver" in raw.dims:
                raw = raw.mean("expver", skipna=True)
                
            times = pd.DatetimeIndex(ds[time_name].values[::stride])
            all_months.append(times.month.to_numpy())
            
            raw = raw.transpose(time_name, dir_dim, freq_dim, "latitude", "longitude").values.astype(float)
            
            for i, lat in enumerate(lats):
                for j, lon in enumerate(lons):
                    key = (i, j)
                    if key not in node_h:
                        node_h[key] = []
                        node_t[key] = []
                        node_f[key] = []
                        
                    column = raw[:, :, :, i, j]
                    if not np.isfinite(column).any():
                        # land cell
                        node_h[key].append(np.full(len(times), np.nan))
                        node_t[key].append(np.full(len(times), np.nan))
                        node_f[key].append(np.full(len(times), np.nan))
                        continue
                        
                    depth = analysis._depth_lookup(depth_grid, lat, lon)
                    bulk = wc.spectral_bulk(wc.decode_log10(column), freqs, dfreq, dtheta, dir_to, depth)
                    node_h[key].append(bulk["hm0"])
                    node_t[key].append(bulk["te"])
                    node_f[key].append(bulk["flux"])
                    
    months = np.concatenate(all_months)
    nodes = []
    
    for i, lat in enumerate(lats):
        for j, lon in enumerate(lons):
            key = (i, j)
            h_node = np.concatenate(node_h[key])
            t_node = np.concatenate(node_t[key])
            f_node = np.concatenate(node_f[key])
            
            valid = bool(np.isfinite(h_node).any())
            dist = round(float(analysis.haversine_km(site_lat, site_lon, lat, lon)), 1)
            depth = analysis._depth_lookup(depth_grid, lat, lon)
            
            node_dict = {
                "lat": float(lat), "lon": float(lon), "valid": valid,
                "depth_m": depth, "distance_km": dist
            }
            if valid:
                stats = _node_stats(h_node, t_node, f_node, months)
                node_dict.update(stats)
            else:
                node_dict.update({
                    "hm0_mean_m": None, "te_mean_s": None, "flux_mean_kw_m": None,
                    "flux_p95_kw_m": None, "flux_cov": None,
                    "monthly_flux_kw_m": [None]*12,
                    "season_flux_kw_m": {"DJF": None, "MAM": None, "JJA": None, "SON": None},
                    "seasonality_ratio": None, "n_records": 0
                })
            nodes.append(node_dict)
            
    return {"nodes": nodes, "sampling_note": sampling_note}
