"""
Core numerical module for assessing wave-energy device performance.

Provides the `PowerMatrix` dataclass, CSV parsing for power matrices, interpolation
and lookup methods, and the main `assess_device` routine to evaluate energy
production against wave resource records.
"""

import csv
import io
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.interpolate import RegularGridInterpolator

import analysis

@dataclass
class PowerMatrix:
    hm0_edges: np.ndarray
    period_edges: np.ndarray
    power_kw: np.ndarray
    hm0_centres: np.ndarray
    period_centres: np.ndarray


def _make_edges(values, convention):
    if len(values) < 2:
        raise ValueError("Need at least 2 bins.")
    if convention == "centres":
        diffs = np.diff(values)
        interior = values[:-1] + diffs / 2.0
        first = values[0] - diffs[0] / 2.0
        last = values[-1] + diffs[-1] / 2.0
        return np.concatenate(([first], interior, [last]))
    elif convention == "lower_edges":
        diffs = np.diff(values)
        last = values[-1] + diffs[-1]
        return np.concatenate((values, [last]))
    else:
        raise ValueError(f"Unknown bin_convention: {convention}")


def _make_centres(values, convention):
    if convention == "centres":
        return values
    elif convention == "lower_edges":
        diffs = np.diff(values)
        last_spacing = diffs[-1]
        spacings = np.concatenate((diffs, [last_spacing]))
        return values + spacings / 2.0
    else:
        raise ValueError(f"Unknown bin_convention: {convention}")


def parse_power_matrix(csv_text: str, bin_convention: str = "centres") -> PowerMatrix:
    """Parses a CSV string into a PowerMatrix with power in kW and edges in m and s."""
    if csv_text.startswith("\ufeff"):
        csv_text = csv_text[1:]
    
    csv_text = csv_text.strip()
    if not csv_text:
        raise ValueError("Empty power matrix.")

    lines = [line.strip() for line in csv_text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("Empty power matrix.")
        
    first_line = lines[0]
    if "\t" in first_line:
        delimiter = "\t"
    elif ";" in first_line:
        delimiter = ";"
    else:
        delimiter = ","
        
    reader = csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter)
    rows = list(reader)
    if len(rows) < 3:
        raise ValueError("Power matrix must have at least 2 wave heights (3 rows total).")
    
    header = rows[0]
    if len(header) < 3:
        raise ValueError("Power matrix must have at least 2 periods.")
        
    try:
        raw_periods = np.array([float(x.replace(',', '.')) if delimiter != ',' else float(x) for x in header[1:]])
    except ValueError:
        raise ValueError("Invalid number in periods header (row 1). Use decimal points, not commas.")

    n_period = len(raw_periods)
    if n_period < 2:
        raise ValueError("Need at least 2 periods.")
    if n_period > 200:
        raise ValueError("At most 200 periods allowed.")
        
    hm0_list = []
    power_list = []
    
    for row_idx, row in enumerate(rows[1:], start=2):
        if len(row) == 0:
            continue
        if len(row) != len(header):
            msg = f"Row {row_idx} has {len(row)} columns, expected {len(header)}."
            if delimiter == "," and len(row) > len(header):
                msg += " A decimal comma in a comma-delimited file is not allowed."
            raise ValueError(msg)
        
        try:
            val = float(row[0].replace(',', '.')) if delimiter != ',' else float(row[0])
            hm0_list.append(val)
        except ValueError:
            raise ValueError(f"Invalid wave height on row {row_idx}.")
            
        p_row = []
        for cell in row[1:]:
            cell = cell.strip()
            if cell in ("", "-", "NaN", "nan"):
                p_row.append(np.nan)
            else:
                try:
                    val = float(cell.replace(',', '.')) if delimiter != ',' else float(cell)
                except ValueError:
                    if delimiter == ',' and ',' in cell:
                        raise ValueError(f"Invalid numeric value '{cell}' on row {row_idx}. A decimal comma in a comma-delimited file is not allowed.")
                    raise ValueError(f"Invalid numeric value '{cell}' on row {row_idx}.")
                if val < 0:
                    raise ValueError(f"Power cannot be negative (row {row_idx}).")
                p_row.append(val)
        power_list.append(p_row)
        
    raw_hm0 = np.array(hm0_list)
    power_kw = np.array(power_list)
    
    n_hm0 = len(raw_hm0)
    if n_hm0 < 2:
        raise ValueError("Need at least 2 wave heights.")
    if n_hm0 > 200:
        raise ValueError("At most 200 wave heights allowed.")
        
    if not np.all(np.diff(raw_periods) > 0):
        raise ValueError("Periods must be strictly increasing.")
    if not np.all(np.diff(raw_hm0) > 0):
        raise ValueError("Wave heights must be strictly increasing.")
        
    hm0_edges = _make_edges(raw_hm0, bin_convention)
    period_edges = _make_edges(raw_periods, bin_convention)
    hm0_centres = _make_centres(raw_hm0, bin_convention)
    period_centres = _make_centres(raw_periods, bin_convention)
    
    return PowerMatrix(hm0_edges, period_edges, power_kw, hm0_centres, period_centres)


def lookup_power(matrix: PowerMatrix, hm0: np.ndarray, period: np.ndarray, method: str) -> tuple[np.ndarray, np.ndarray]:
    """Looks up power for wave states, returning (power_kw, inside_mask)."""
    hm0 = np.asarray(hm0, dtype=float)
    period = np.asarray(period, dtype=float)
    
    valid = np.isfinite(hm0) & np.isfinite(period)
    power = np.zeros_like(hm0)
    inside = np.zeros_like(hm0, dtype=bool)
    
    if method == "bin":
        idx_h = np.searchsorted(matrix.hm0_edges, hm0, side="right") - 1
        idx_p = np.searchsorted(matrix.period_edges, period, side="right") - 1
        
        in_extent = (
            valid &
            (idx_h >= 0) & (idx_h < len(matrix.hm0_centres)) &
            (idx_p >= 0) & (idx_p < len(matrix.period_centres)) &
            (hm0 < matrix.hm0_edges[-1]) & (period < matrix.period_edges[-1])
        )
        defined = np.zeros_like(in_extent)
        if in_extent.any():
            cell_vals = matrix.power_kw[idx_h[in_extent], idx_p[in_extent]]
            def_mask = np.isfinite(cell_vals)
            in_ext_indices = np.where(in_extent)[0]
            defined[in_ext_indices[def_mask]] = True
            power[in_ext_indices[def_mask]] = cell_vals[def_mask]
            
        inside = in_extent & defined

    elif method == "bilinear":
        interp = RegularGridInterpolator(
            (matrix.hm0_centres, matrix.period_centres),
            matrix.power_kw,
            method="linear",
            bounds_error=False,
            fill_value=np.nan
        )
        pts = np.column_stack((hm0, period))
        with np.errstate(invalid='ignore'):
            res = interp(pts)
            
        in_extent = (
            valid &
            (hm0 >= matrix.hm0_centres[0]) & (hm0 <= matrix.hm0_centres[-1]) &
            (period >= matrix.period_centres[0]) & (period <= matrix.period_centres[-1])
        )
        
        is_def = np.isfinite(res)
        inside = in_extent & is_def
        power[inside] = res[inside]

    else:
        raise ValueError(f"Unknown method: {method}")
        
    return power, inside


def _evaluate_single_method(matrix: PowerMatrix, hm0, period, flux, method: str, month, rated_kw, width_m, min_flux_kw_m):
    power, inside = lookup_power(matrix, hm0, period, method)
    valid = np.isfinite(hm0) & np.isfinite(period) & np.isfinite(flux)
    
    n_valid = int(valid.sum())
    if n_valid == 0:
        res = {
            "records_valid": 0,
            "records_inside": None,
            "share_inside_pct": None,
            "share_outside_pct": None,
            "flux_outside_support_pct": None,
            "mean_power_kw": None,
            "aep_mwh": None,
            "capacity_factor_pct": None,
            "capture_width_energy_weighted_m": None,
            "capture_width_mean_of_ratios_m": None,
            "capture_width_mean_of_ratios_records": None
        }
        if width_m is not None:
            res["capture_width_energy_weighted_ratio"] = None
            res["capture_width_mean_of_ratios_ratio"] = None
        return res
        
    hm0_v = hm0[valid]
    period_v = period[valid]
    flux_v = flux[valid]
    power_v = power[valid]
    inside_v = inside[valid]
    
    records_inside = int(inside_v.sum())
    share_inside = (records_inside / n_valid * 100) if n_valid > 0 else 0.0
    share_outside = 100.0 - share_inside
    
    flux_total = flux_v.sum()
    flux_outside = flux_v[~inside_v].sum()
    flux_outside_pct = (flux_outside / flux_total * 100) if flux_total > 0 else 0.0
    
    mean_power = power_v.mean() if n_valid > 0 else 0.0
    aep_mwh = mean_power * 8766.0 / 1000.0
    cf = (mean_power / rated_kw * 100) if rated_kw else None
    
    flux_pos = flux_v > 0
    cw_ew = power_v[flux_pos].sum() / flux_v[flux_pos].sum() if flux_pos.any() else 0.0
    
    ratio_mask = flux_v >= min_flux_kw_m
    cw_mr = (power_v[ratio_mask] / flux_v[ratio_mask]).mean() if ratio_mask.any() else 0.0
    cw_mr_count = int(ratio_mask.sum())
    
    res = {
        "records_valid": n_valid,
        "records_inside": records_inside,
        "share_inside_pct": share_inside,
        "share_outside_pct": share_outside,
        "flux_outside_support_pct": flux_outside_pct,
        "mean_power_kw": mean_power,
        "aep_mwh": aep_mwh,
        "capacity_factor_pct": cf,
        "capture_width_energy_weighted_m": cw_ew,
        "capture_width_mean_of_ratios_m": cw_mr,
        "capture_width_mean_of_ratios_records": cw_mr_count
    }
    if width_m is not None:
        res["capture_width_energy_weighted_ratio"] = cw_ew / width_m if width_m else None
        res["capture_width_mean_of_ratios_ratio"] = cw_mr / width_m if width_m else None
        
    return res


def _evaluate_indexing(matrix: PowerMatrix, hm0, period, flux, month, rated_kw, width_m, min_flux_kw_m):
    valid = np.isfinite(hm0) & np.isfinite(period) & np.isfinite(flux)
    
    res_bin = _evaluate_single_method(matrix, hm0, period, flux, "bin", month, rated_kw, width_m, min_flux_kw_m)
    res_bilinear = _evaluate_single_method(matrix, hm0, period, flux, "bilinear", month, rated_kw, width_m, min_flux_kw_m)
    
    power, inside = lookup_power(matrix, hm0, period, "bin")
    
    hm0_v = hm0[valid & inside]
    period_v = period[valid & inside]
    flux_v = flux[valid & inside]
    
    idx_h = np.searchsorted(matrix.hm0_edges, hm0_v, side="right") - 1
    idx_p = np.searchsorted(matrix.period_edges, period_v, side="right") - 1
    
    shape = matrix.power_kw.shape
    occupancy = np.zeros(shape)
    energy = np.zeros(shape)
    
    if len(idx_h) > 0:
        np.add.at(occupancy, (idx_h, idx_p), 1.0)
        np.add.at(energy, (idx_h, idx_p), flux_v)
    
    n_valid = valid.sum()
    occ_pct = occupancy / n_valid * 100 if n_valid > 0 else occupancy
    flux_total = flux[valid].sum()
    en_pct = energy / flux_total * 100 if flux_total > 0 else energy
    
    power_v = power[valid]
    month_v = month[valid]
    
    monthly = []
    annual_energy_sample = power_v.sum() if n_valid > 0 else 0.0
    for m in range(1, 13):
        mask = month_v == m
        m_count = int(mask.sum())
        if n_valid == 0:
            m_mean = None
            m_share = None
        else:
            m_mean = power_v[mask].mean() if m_count > 0 else 0.0
            m_energy = power_v[mask].sum() if m_count > 0 else 0.0
            m_share = (m_energy / annual_energy_sample * 100) if annual_energy_sample > 0 else 0.0
        monthly.append({
            "month": m,
            "records": m_count,
            "mean_power_kw": m_mean,
            "energy_share_pct": m_share
        })
        
    return {
        "bin": res_bin,
        "bilinear": res_bilinear,
        "occupancy_pct": occ_pct.tolist(),
        "energy_pct": en_pct.tolist(),
        "hm0_edges": matrix.hm0_edges.tolist(),
        "period_edges": matrix.period_edges.tolist(),
        "monthly": monthly
    }


def assess_device(frame: pd.DataFrame, matrix: PowerMatrix, *, name: str, rated_kw: float = None, width_m: float = None, period_type: str = "unknown", min_flux_kw_m: float = 1.0) -> dict:
    """Evaluates device performance over a dataset, returning metrics in kW, MWh, m, and %."""
    month = frame.index.month.to_numpy()
    hm0 = frame["hm0"].to_numpy()
    flux = frame["flux"].to_numpy()
    te = frame["te"].to_numpy()
    tp = frame["tp"].to_numpy()
    
    indexings = {}
    if period_type in ("te", "unknown"):
        indexings["te"] = _evaluate_indexing(matrix, hm0, te, flux, month, rated_kw, width_m, min_flux_kw_m)
    if period_type in ("tp", "unknown"):
        indexings["tp"] = _evaluate_indexing(matrix, hm0, tp, flux, month, rated_kw, width_m, min_flux_kw_m)
        
    warnings = []
    notes = [
        "ERA5 is a reanalysis at an offshore node.",
        "Power matrices are device- and site-specific.",
        "The flux used is the route's own flux (Option A deep-water proxy from mwp, Option B spectrum integral)."
    ]
    if width_m is not None:
        notes.append("The definition of 'characteristic width' is the user's.")
        
    notes.append("Records are equally weighted in AEP calculation.")
    
    period_labels = {}
    if "te" in indexings:
        period_labels["te"] = "Te = Tm-1 (energy period)"
    if "tp" in indexings:
        period_labels["tp"] = "Tp (peak period)"
        
    for key, eval_data in indexings.items():
        if eval_data["bin"].get("records_valid", 0) > 0:
            if eval_data["bin"]["share_outside_pct"] > 20.0:
                warnings.append(f"More than 20% of valid records are outside the matrix for {key} indexing.")
        else:
            label_name = "peak" if key == "tp" else "energy"
            warnings.append(f"No valid records for {key.capitalize()} indexing: the download has no {label_name} period.")

    if len(frame) > 1:
        span_days = (frame.index[-1] - frame.index[0]).total_seconds() / 86400.0
        years = span_days / 365.25
        steps = np.diff(frame.index.to_numpy()).astype("timedelta64[s]").astype(float)
        median_step_hours = np.median(steps) / 3600.0
        expected_records = (span_days * 24.0) / median_step_hours + 1
        coverage_pct = (len(frame) / expected_records * 100.0) if expected_records > 0 else 0.0
    elif len(frame) == 1:
        years = 0.0
        coverage_pct = 100.0
        median_step_hours = frame.attrs.get("time_step_hours", 6.0)
    else:
        years = 0.0
        coverage_pct = 0.0
        median_step_hours = frame.attrs.get("time_step_hours", 6.0)

    if years > 0 and years < analysis.MIN_RECORD_YEARS:
        warnings.append("AEP from a partial record is not an annual estimate.")
        
    if len(frame) > 0 and len(np.unique(month)) < 12:
        warnings.append("Fewer than 12 calendar months present; results may have seasonal bias.")
        
    max_matrix_power = np.nanmax(matrix.power_kw)
    if rated_kw is not None and rated_kw < max_matrix_power:
        warnings.append(f"Rated power ({rated_kw} kW) is smaller than the matrix maximum power ({max_matrix_power} kW).")
        
    if len(frame) > 1:
        if np.any(steps > 1.5 * (median_step_hours * 3600.0)):
            warnings.append("Time step is not constant; gaps larger than 1.5x the median step exist.")
    
    res = {
        "name": name,
        "period_type": period_type,
        "period_labels": period_labels,
        "indexings": indexings,
        "matrix": {
            "n_hm0": len(matrix.hm0_centres),
            "n_period": len(matrix.period_centres),
            "hm0_range": [float(matrix.hm0_centres[0]), float(matrix.hm0_centres[-1])],
            "period_range": [float(matrix.period_centres[0]), float(matrix.period_centres[-1])],
            "power_max_kw": float(max_matrix_power) if np.isfinite(max_matrix_power) else 0.0
        },
        "record": {
            "start": frame.index[0].isoformat() if len(frame) > 0 else None,
            "end": frame.index[-1].isoformat() if len(frame) > 0 else None,
            "years": years,
            "coverage_pct": coverage_pct,
            "step_hours": median_step_hours,
            "records": len(frame)
        },
        "warnings": warnings,
        "notes": notes,
        "unverified": []
    }
    
    if period_type == "unknown" and "te" in indexings and "tp" in indexings:
        if indexings["te"]["bin"].get("records_valid", 0) > 0 and indexings["tp"]["bin"].get("records_valid", 0) > 0:
            aep_te = indexings["te"]["bin"]["aep_mwh"]
            aep_tp = indexings["tp"]["bin"]["aep_mwh"]
            res["ambiguity"] = {
                "aep_mwh_min": min(aep_te, aep_tp),
                "aep_mwh_max": max(aep_te, aep_tp),
                "aep_ratio_tp_over_te": aep_tp / aep_te if aep_te > 0 else None
            }
        
    return res


def example_matrix_csv() -> str:
    """Returns a synthetic power matrix as a CSV string, with power in kW, Hm0 in m, Te in s."""
    hm0 = np.arange(0.5, 6.5, 0.5)
    te = np.arange(4.0, 17.0, 1.0)
    rated = 100.0
    
    lines = ["Hm0\\Te," + ",".join(f"{t:.1f}" for t in te)]
    for h in hm0:
        row = [f"{h:.1f}"]
        for t in te:
            if h < 0.75:
                p = 0.0
            else:
                p = min(rated, 0.2 * 0.4906 * h**2 * t * 8.0)
            row.append(f"{p:.2f}")
        lines.append(",".join(row))
    return "\n".join(lines) + "\n"
