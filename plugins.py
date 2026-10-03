"""Plugin interface for resource tools (device performance, screening, long-term statistics, export).

A plugin is a module named ``plugin_<name>.py`` next to ``app.py`` that defines

    def register(app, ctx): ...

and adds Flask routes under ``/api/jobs/<job_id>/<feature>``. Routes obtain everything about a
finished job through ``ctx.job(job_id, product=None)``, which returns a :class:`JobView`. They do
not import ``app``. Errors: raise ``ValueError`` for bad input (becomes HTTP 422 JSON); Flask
``abort`` codes become JSON as well.

The canonical frame
-------------------
Option A (single levels) and Option B (spectra) store different column names. Plugins work on
``JobView.frame()``, which has the same columns for both routes:

    hm0       significant wave height, m          (swh | Hm0 = 4 sqrt(m0))
    te        energy period Te = Tm-1, s          (mwp | m-1/m0)
    tp        peak period Tp, s                   (pp1d | parabolic-fit peak)
    dir_from  mean direction, degrees, coming-from, 0-360   (mwd | energy-weighted mean)
    flux      energy flux J, kW/m                 (0.4906 Hm0^2 Te | finite-depth integral)

indexed by UTC time (a ``DatetimeIndex``), sorted, unique. A column is all-NaN if the download
did not contain it. ``frame.attrs`` holds ``product``, ``route``, ``time_step_hours``. Option B
frames also keep their ``flux_dir_00..23`` columns (flux per 15 degree going-to bin, kW/m).
"""

from __future__ import annotations

import importlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from netcdf_safety import atomic_write_text

LOG = logging.getLogger(__name__)
HOURS_PER_YEAR = 8766.0
PLUGIN_MODULES = ("plugin_device", "plugin_devices", "plugin_screening", "plugin_longterm", "plugin_export")

CANONICAL_COLUMNS = ("hm0", "te", "tp", "dir_from", "flux")
_BULK_MAP = {"hm0": "swh", "te": "mwp", "tp": "pp1d", "dir_from": "mwd", "flux": "j"}
_SPECTRA_MAP = {"hm0": "hm0", "te": "te", "tp": "tp_parabolic", "dir_from": "dm_from", "flux": "flux"}


def canonical_frame(frame: pd.DataFrame, product: str) -> pd.DataFrame:
    """Return the per-record frame with the canonical column names (see module docstring)."""
    mapping = _SPECTRA_MAP if product == "wave-spectra" else _BULK_MAP
    index = pd.DatetimeIndex(frame.index)
    out = pd.DataFrame(index=index)
    for name, source in mapping.items():
        out[name] = frame[source].to_numpy(dtype=float) if source in frame.columns else np.nan
    if product != "wave-spectra" and out["flux"].isna().all() and out["hm0"].notna().any() and out["te"].notna().any():
        out["flux"] = 0.4906 * out["hm0"] ** 2 * out["te"]  # same deep-water relation as analysis.py
    if product == "wave-spectra":
        for column in frame.columns:
            if column.startswith("flux_dir_"):
                out[column] = frame[column].to_numpy(dtype=float)
    out = out[~out.index.duplicated()].sort_index()
    step = float(np.median(np.diff(out.index.to_numpy()).astype("timedelta64[s]").astype(float)) / 3600.0) if len(out) > 1 else None
    out.attrs.update({"product": product, "route": "spectra" if product == "wave-spectra" else "integrated",
                      "time_step_hours": step})
    return out


@dataclass
class JobView:
    """Everything a plugin route needs to know about one finished, non-preview job."""

    id: str
    product: str                       # "single-levels" | "wave-spectra" | "mars-surface"
    directory: Path                    # the job folder (data files, analysis cache, provenance.json)
    latitude: float                    # requested site
    longitude: float
    files: list[Path]                  # NetCDF data files
    node: tuple[float, float] | None   # grid node chosen with ?node_lat=&node_lon=, else None (nearest ocean cell)
    _analysis: Callable[[], dict] = field(repr=False, default=lambda: {})
    _frame: Callable[[], pd.DataFrame] = field(repr=False, default=lambda: pd.DataFrame())
    _nodes: Callable[[], dict] = field(repr=False, default=lambda: {})

    def analysis(self) -> dict:
        """The cached analysis payload (the JSON the Analysis page shows) for this node."""
        return self._analysis()

    def frame(self) -> pd.DataFrame:
        """Canonical per-record frame for this node (see module docstring)."""
        return canonical_frame(self._frame(), self.product)

    def raw_frame(self) -> pd.DataFrame:
        """The route's own frame as saved in timeseries.csv, with its original column names."""
        return self._frame()

    def nodes(self) -> dict:
        """The grid-node summary (every node with mean Hm0, Te and flux) of this download."""
        return self._nodes()

    def provenance(self) -> dict:
        path = self.directory / "provenance.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    # --- per-job storage for plugin results --------------------------------------------------
    def results_path(self, name: str) -> Path:
        folder = self.directory / "results"
        folder.mkdir(exist_ok=True)
        return folder / name

    def save_json(self, name: str, payload: Any) -> Path:
        path = self.results_path(name)
        atomic_write_text(path, json.dumps(payload, indent=1, default=_json_default), encoding="utf-8")
        return path

    def load_json(self, name: str, default: Any = None) -> Any:
        path = self.directory / "results" / name
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default

    def save_report_section(self, name: str, markdown: str) -> Path:
        """Markdown fragment that plugin_export appends to report.md (sorted by file name)."""
        folder = self.directory / "report_sections"
        folder.mkdir(exist_ok=True)
        path = folder / f"{name}.md"
        atomic_write_text(path, markdown, encoding="utf-8")
        return path

    def report_sections(self) -> list[tuple[str, str]]:
        folder = self.directory / "report_sections"
        if not folder.is_dir():
            return []
        return [(p.stem, p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.md"))]


def _json_default(value):
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")


@dataclass
class PluginContext:
    """Passed to ``register(app, ctx)``."""

    job: Callable[..., JobView]        # ctx.job(job_id, product=None) -> JobView; aborts 404/409 as needed
    downloads: Path


def load_plugins(app, ctx: PluginContext) -> list[str]:
    """Import and register every plugin module that exists; a broken plugin is logged, not fatal."""
    loaded = []
    for module_name in PLUGIN_MODULES:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name == module_name:
                continue  # plugin not written yet
            LOG.exception("plugin %s failed to import", module_name)
            continue
        except Exception:
            LOG.exception("plugin %s failed to import", module_name)
            continue
        try:
            module.register(app, ctx)
            loaded.append(module_name)
        except Exception:
            LOG.exception("plugin %s failed to register", module_name)
    return loaded
