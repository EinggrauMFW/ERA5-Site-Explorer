"""Side-by-side comparison of Option A (single levels) and Option B (2D spectra).

Both frames come from ``analysis``; nothing is blended. Records are matched on
timestamp, so a 6-hourly spectra record is compared with the hourly single-levels
record at the same instant.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import wavecalc as wc

# Defaults for "agree". Tolerances are this app's configurable defaults, not standards.
TOLERANCES = {"hm0": 0.05, "te": 0.05, "tp": 0.10, "tm01": 0.05, "tm02": 0.05, "flux": 0.05,
              "direction": 15.0}
MAX_SCATTER_POINTS = 500
WORST = 10

# key, label, unit, A column, B column, kind, expectation
ROWS = [
    ("hm0", "Hm0", "m", "swh", "hm0", "rel",
     "Close; Option B slightly lower because the 30 bins stop at 0.548 Hz and no tail is added"),
    ("te", "Te = Tm-1", "s", "mwp", "te", "rel",
     "Agree closely. A large systematic gap means a decoding or unit error: fix before trusting anything"),
    ("tp", "Tp", "s", "pp1d", "tp_parabolic", "rel",
     "Differ at bin-resolution level; ECMWF uses a parabolic fit around the 2D maximum, Option B around the 1D peak"),
    ("tm01", "Tm01", "s", "mp1", "tm01", "rel", "Agree closely"),
    ("tm02", "Tm02", "s", "mp2", "tm02", "rel", "Agree closely"),
    ("direction", "Mean direction (coming from)", "°", "mwd", "dm_from", "circ",
     "Agree within about the 15° bin width. A near-180° offset reveals a convention error"),
    ("flux", "Energy flux J", "kW/m", "j", "flux", "rel",
     "Equal in deep water; the difference grows as the model depth falls below L0/2"),
]


def _worst(index, a, b, diff, relative) -> list[dict]:
    score = np.abs(diff) / np.where(a != 0, np.abs(a), np.nan) if relative else np.abs(diff)
    score = np.where(np.isfinite(score), score, -1)
    top = np.argsort(score)[::-1][:WORST]
    return [{"time": f"{index[i]:%Y-%m-%d %H:%M}", "a": round(float(a[i]), 3), "b": round(float(b[i]), 3),
             "diff": round(float(diff[i]), 3)} for i in top if score[i] >= 0]


def _ordering(frame, low, mid, high) -> dict | None:
    if not {low, mid, high} <= set(frame.columns):
        return None
    bad, checked, _ = wc.ordering_violations(frame[low], frame[mid], frame[high], 0.01)
    return {"violations": bad, "checked": checked}


def compare(frame_a: pd.DataFrame, frame_b: pd.DataFrame, tolerances: dict | None = None,
            cell_a=None, cell_b=None, depth_m=None) -> dict:
    tolerances = {**TOLERANCES, **(tolerances or {})}
    index = frame_a.index.intersection(frame_b.index)
    if not len(index):
        raise ValueError("The two downloads share no timestamps. Use the same period, and a time step "
                         "that the spectra record divides (hourly single levels vs 6-hourly spectra works).")
    a_all, b_all = frame_a.loc[index], frame_b.loc[index]
    rows, warnings = [], []
    if cell_a and cell_b and (abs(cell_a[0] - cell_b[0]) > 1e-6 or abs(cell_a[1] - cell_b[1]) > 1e-6):
        warnings.append(f"The two routes used different grid cells (A {cell_a[0]:.2f}, {cell_a[1]:.2f}; "
                        f"B {cell_b[0]:.2f}, {cell_b[1]:.2f}). Differences include a spatial difference.")

    for key, label, unit, col_a, col_b, kind, expectation in ROWS:
        if col_a not in a_all.columns or col_b not in b_all.columns:
            rows.append({"key": key, "label": label, "unit": unit, "available": False,
                         "expectation": expectation,
                         "missing": [c for c, f in ((col_a, a_all), (col_b, b_all)) if c not in f.columns]})
            continue
        a = a_all[col_a].to_numpy(float)
        b = b_all[col_b].to_numpy(float)
        ok = np.isfinite(a) & np.isfinite(b)
        if not ok.any():
            rows.append({"key": key, "label": label, "unit": unit, "available": False,
                         "expectation": expectation, "missing": ["no finite pairs"]})
            continue
        a, b, stamps = a[ok], b[ok], index[ok]
        relative = kind == "rel"
        diff = wc.angular_difference(b, a) if kind == "circ" else b - a
        tolerance = tolerances[key]
        within = (np.abs(diff) <= tolerance * np.abs(a)) if relative else (np.abs(diff) <= tolerance)
        # Direction differences are averaged as vectors: near +-180 degrees an arithmetic mean cancels out.
        bias = float(wc.angular_difference(wc.vector_mean_direction(diff), 0.0)) if kind == "circ" else float(np.mean(diff))
        # A constant series has no defined correlation; None keeps the JSON valid (NaN is not JSON).
        correlation = None
        if relative and len(a) > 2 and np.std(a) > 0 and np.std(b) > 0:
            correlation = round(float(np.corrcoef(a, b)[0, 1]), 4)
        result = {
            "key": key, "label": label, "unit": unit, "available": True, "expectation": expectation,
            "n": int(ok.sum()), "bias": round(bias, 4), "rmse": round(float(np.sqrt(np.mean(diff**2))), 4),
            "bias_pct": round(100 * bias / float(np.mean(a)), 2) if relative and np.mean(a) else None,
            "correlation": correlation,
            "tolerance": tolerance, "tolerance_unit": "relative" if relative else "degrees",
            "within_tolerance_pct": round(float(within.mean() * 100), 1),
            "abs_diff_p50": round(float(np.percentile(np.abs(diff), 50)), 4),
            "abs_diff_p95": round(float(np.percentile(np.abs(diff), 95)), 4),
            "worst": _worst(stamps, a, b, diff, relative),
        }
        if relative:
            step = max(1, -(-len(a) // MAX_SCATTER_POINTS))
            result["scatter"] = {"a": [round(float(v), 3) for v in a[::step]],
                                 "b": [round(float(v), 3) for v in b[::step]]}
        rows.append(result)
        if key == "te" and abs(bias / np.mean(a)) > tolerance:
            warnings.append("Te differs between the routes by more than the tolerance: a decoding, unit or "
                            "variable error is likely. Fix this before trusting any other number.")
        if key == "direction" and abs(bias) > 150:
            warnings.append("Direction differs by close to 180°: a coming-from/going-to convention error.")
        if key == "flux" and depth_m is not None and depth_m > 0:
            warnings.append(f"Model depth at the cell is {depth_m:.0f} m, so Option B's flux uses finite-depth "
                            "group velocity; part of the flux difference is depth, not error.")

    return {
        "overlap_records": int(len(index)),
        "start": f"{index[0]:%Y-%m-%d %H:%M}", "end": f"{index[-1]:%Y-%m-%d %H:%M}",
        "tolerances": tolerances, "rows": rows, "warnings": warnings,
        "ordering": {"A: mp2 ≤ mp1 ≤ mwp": _ordering(a_all, "mp2", "mp1", "mwp"),
                     "B: Tm02 ≤ Tm01 ≤ Te": _ordering(b_all, "tm02", "tm01", "te")},
    }
