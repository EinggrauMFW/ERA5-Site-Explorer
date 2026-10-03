import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from device import parse_power_matrix, example_matrix_csv


@dataclass
class DeviceSummary:
    id: str
    name: str
    rated_kw: Optional[float]
    width_m: Optional[float]
    period_type: str
    bin_convention: str
    source: str
    provenance: str
    notes: list[str]
    origin: str
    synthetic: bool
    hs_m: list[float]
    period_s: list[float]
    power_kw: list[list[Optional[float]]]
    n_hm0: int
    n_period: int
    hm0_range: list[float]
    period_range: list[float]
    power_max_kw: float
    defined_cells: int
    total_cells: int
    defined_pct: float
    near_max_cells: int


@dataclass
class Catalogue:
    configured: bool
    directory: str
    devices: list[DeviceSummary]
    warnings: list[str]


def _strip_control_chars(s: str) -> str:
    return re.sub(r'[\x00-\x1f\x7f-\x9f\n\r]', '', s).strip()


def _parse_period_type(pt) -> str:
    if not isinstance(pt, str):
        return "unknown"
    pt = pt.lower()
    if pt in ("tp", "te"):
        return pt
    return "unknown"


def _is_finite_num(v) -> bool:
    try:
        return isinstance(v, (int, float)) and not isinstance(v, bool) and np.isfinite(v)
    except (TypeError, OverflowError, ValueError):
        return False


def _is_finite_pos_num(v) -> bool:
    return _is_finite_num(v) and v > 0


def catalogue_directory() -> tuple[Path, bool]:
    env_dir = os.environ.get("DEVICES_DIR")
    if env_dir:
        return Path(env_dir), True
    return Path(__file__).parent / "devices", False


def _build_device(
    dev_id: str,
    name: str,
    rated_kw: Optional[float],
    width_m: Optional[float],
    period_type: str,
    bin_convention: str,
    source: str,
    provenance: str,
    notes: list[str],
    origin: str,
    synthetic: bool,
    hs_m: list[float],
    period_s: list[float],
    power_kw: list[list[Optional[float]]]
) -> DeviceSummary:
    flat_cells = [c for r in power_kw for c in r if c is not None]
    if not flat_cells:
        raise ValueError(f"Device '{dev_id}': all cells undefined.")

    power_max = max(flat_cells)
    near_max_cells = sum(1 for c in flat_cells if c >= 0.99 * power_max)
    total_cells = len(hs_m) * len(period_s)
    defined_cells = len(flat_cells)

    return DeviceSummary(
        id=dev_id,
        name=name,
        rated_kw=rated_kw,
        width_m=width_m,
        period_type=period_type,
        bin_convention=bin_convention,
        source=source,
        provenance=provenance,
        notes=notes,
        origin=origin,
        synthetic=synthetic,
        hs_m=hs_m,
        period_s=period_s,
        power_kw=power_kw,
        n_hm0=len(hs_m),
        n_period=len(period_s),
        hm0_range=[hs_m[0], hs_m[-1]],
        period_range=[period_s[0], period_s[-1]],
        power_max_kw=power_max,
        defined_cells=defined_cells,
        total_cells=total_cells,
        defined_pct=(defined_cells / total_cells) * 100,
        near_max_cells=near_max_cells
    )


def _entry_to_device(dev_id: str, entry: dict) -> DeviceSummary:
    if not re.match(r'^[a-z0-9_-]{1,60}$', dev_id):
        raise ValueError(f"Invalid device id '{dev_id}' in devices.json.")

    if not isinstance(entry, dict):
        raise ValueError(f"Device '{dev_id}': is not an object.")

    name = entry.get("name")
    if not isinstance(name, str):
        raise ValueError(f"Device '{dev_id}': invalid name.")
    name = _strip_control_chars(name)
    if not (1 <= len(name) <= 60):
        raise ValueError(f"Device '{dev_id}': name length must be 1-60.")

    rated_kw = entry.get("rated_kw")
    if rated_kw is not None and not _is_finite_pos_num(rated_kw):
        raise ValueError(f"Device '{dev_id}': invalid rated_kw.")

    width_m = entry.get("width_m")
    if width_m is not None and not _is_finite_pos_num(width_m):
        raise ValueError(f"Device '{dev_id}': invalid width_m.")

    period_type = _parse_period_type(entry.get("period_type"))
    bin_convention = entry.get("bin_convention", "centres")
    if bin_convention not in ("centres", "lower_edges"):
        raise ValueError(f"Device '{dev_id}': bin_convention must be 'centres' or 'lower_edges'.")

    raw_notes = entry.get("notes")
    if isinstance(raw_notes, list):
        notes = [n for n in raw_notes if isinstance(n, str)]
    else:
        notes = []

    source = entry.get("source") if isinstance(entry.get("source"), str) else ""
    provenance = entry.get("provenance") if isinstance(entry.get("provenance"), str) else ""

    hs_m = entry.get("hs_m")
    period_s = entry.get("period_s")
    power_kw = entry.get("power_kw")

    if not isinstance(hs_m, list) or not isinstance(period_s, list) or not isinstance(power_kw, list):
        raise ValueError(f"Device '{dev_id}': missing or invalid axes/power arrays.")

    if not (2 <= len(hs_m) <= 200) or not all(_is_finite_num(v) for v in hs_m):
        raise ValueError(f"Device '{dev_id}': invalid hs_m.")
    if not all(hs_m[i] < hs_m[i+1] for i in range(len(hs_m)-1)):
        raise ValueError(f"Device '{dev_id}': hs_m not strictly increasing.")

    if not (2 <= len(period_s) <= 200) or not all(_is_finite_num(v) for v in period_s):
        raise ValueError(f"Device '{dev_id}': invalid period_s.")
    if not all(period_s[i] < period_s[i+1] for i in range(len(period_s)-1)):
        raise ValueError(f"Device '{dev_id}': period_s not strictly increasing.")

    if len(power_kw) != len(hs_m):
        raise ValueError(f"Device '{dev_id}': power_kw row count must match hs_m.")

    for r_idx, row in enumerate(power_kw):
        if not isinstance(row, list) or len(row) != len(period_s):
            raise ValueError(f"Device '{dev_id}': ragged or invalid row {r_idx}.")
        for cell in row:
            if cell is not None and (not _is_finite_num(cell) or cell < 0):
                raise ValueError(f"Device '{dev_id}': invalid cell value.")

    return _build_device(
        dev_id=dev_id,
        name=name,
        rated_kw=rated_kw,
        width_m=width_m,
        period_type=period_type,
        bin_convention=bin_convention,
        source=source,
        provenance=provenance,
        notes=notes,
        origin="json",
        synthetic=False,
        hs_m=hs_m,
        period_s=period_s,
        power_kw=power_kw,
    )


def load_catalogue(directory_path: Optional[str] = None) -> Catalogue:
    warnings = []
    devices = []
    seen_ids = set()

    if directory_path is None:
        directory, configured = catalogue_directory()
    else:
        directory = Path(directory_path)
        configured = True

    dir_name = directory.name

    csv_to_skip = set()

    def add_device(summary: DeviceSummary):
        if summary.id in seen_ids:
            warnings.append(f"Duplicate device id '{summary.id}' ignored.")
            return
        seen_ids.add(summary.id)
        devices.append(summary)

    if directory.is_dir():
        json_path = directory / "devices.json"
        if json_path.is_file():
            try:
                if json_path.stat().st_size > 5 * 1024 * 1024:
                    warnings.append("devices.json exceeds 5 MB limit.")
                else:
                    json_text = json_path.read_text(encoding="utf-8")
                    try:
                        json_data = json.loads(json_text)
                    except json.JSONDecodeError:
                        warnings.append("devices.json is not valid JSON.")
                        json_data = None

                    if isinstance(json_data, dict):
                        for dev_id, entry in json_data.items():
                            # The file an entry names is never a stray device, even if the entry is rejected:
                            # otherwise its matrix would load without the provenance and caveats.
                            file_name = entry.get("file") if isinstance(entry, dict) else None
                            if isinstance(file_name, str):
                                csv_to_skip.add(file_name)
                            try:
                                add_device(_entry_to_device(dev_id, entry))
                            except (ValueError, TypeError, OverflowError) as e:
                                warnings.append(str(e) if dev_id in str(e) else f"Device '{dev_id}': {e}")
                    elif json_data is not None:
                        warnings.append("devices.json is not an object.")
            except (OSError, ValueError) as e:
                warnings.append(f"Failed to read devices.json: {e}")

        for csv_path in sorted(directory.glob("*.csv")):
            if csv_path.name in csv_to_skip:
                continue

            dev_id = csv_path.stem
            if not re.match(r'^[a-z0-9_-]{1,60}$', dev_id):
                warnings.append(f"Invalid device id '{dev_id}' from CSV file name.")
                continue

            try:
                if csv_path.stat().st_size > 1 * 1024 * 1024:
                    warnings.append(f"Device '{dev_id}': CSV exceeds 1 MB limit.")
                    continue

                csv_text = csv_path.read_text(encoding="utf-8")
                matrix = parse_power_matrix(csv_text, "centres")

                hs_m = matrix.hm0_centres.tolist()
                period_s = matrix.period_centres.tolist()
                power_kw_array = matrix.power_kw.tolist()
                power_kw_clean = [[c if np.isfinite(c) else None for c in r] for r in power_kw_array]

                summary = _build_device(
                    dev_id=dev_id,
                    name=dev_id,
                    rated_kw=None,
                    width_m=None,
                    period_type="unknown",
                    bin_convention="centres",
                    source="",
                    provenance="No metadata entry",
                    notes=[],
                    origin="csv",
                    synthetic=False,
                    hs_m=hs_m,
                    period_s=period_s,
                    power_kw=power_kw_clean,
                )
                add_device(summary)
            except OSError as e:
                warnings.append(f"Failed to read {csv_path.name}: {e}")
            except (ValueError, TypeError, OverflowError) as e:
                warnings.append(f"Device '{dev_id}' ({csv_path.name}): {e}")

    example_matrix = parse_power_matrix(example_matrix_csv(), "centres")
    hs_m_ex = example_matrix.hm0_centres.tolist()
    period_s_ex = example_matrix.period_centres.tolist()
    power_kw_ex = example_matrix.power_kw.tolist()
    power_kw_clean_ex = [[c if np.isfinite(c) else None for c in r] for r in power_kw_ex]

    ex_summary = _build_device(
        dev_id="synthetic-example",
        name="Synthetic example",
        rated_kw=None,
        width_m=None,
        period_type="unknown",
        bin_convention="centres",
        source="Synthetic example bundled with the app; not a real device",
        provenance="Generated by the app for demonstration; not a real device",
        notes=[],
        origin="example",
        synthetic=True,
        hs_m=hs_m_ex,
        period_s=period_s_ex,
        power_kw=power_kw_clean_ex,
    )
    add_device(ex_summary)

    return Catalogue(
        configured=configured,
        directory=dir_name,
        devices=devices,
        warnings=warnings
    )


def find_device(device_id: str) -> Optional[DeviceSummary]:
    cat = load_catalogue()
    for d in cat.devices:
        if d.id == device_id:
            return d
    return None


def summary_dict(device: DeviceSummary) -> dict:
    return {
        "id": device.id,
        "name": device.name,
        "rated_kw": device.rated_kw,
        "width_m": device.width_m,
        "period_type": device.period_type,
        "bin_convention": device.bin_convention,
        "source": device.source,
        "provenance": device.provenance,
        "origin": device.origin,
        "synthetic": device.synthetic,
        "n_hm0": device.n_hm0,
        "n_period": device.n_period,
        "hm0_range": device.hm0_range,
        "period_range": device.period_range,
        "power_max_kw": device.power_max_kw,
        "defined_cells": device.defined_cells,
        "total_cells": device.total_cells,
        "defined_pct": device.defined_pct,
        "near_max_cells": device.near_max_cells,
        "n_notes": len(device.notes)
    }


def detail_dict(device: DeviceSummary, hm0_edges: list[float], period_edges: list[float]) -> dict:
    d = summary_dict(device)
    d.update({
        "notes": device.notes,
        "hs_m": device.hs_m,
        "period_s": device.period_s,
        "power_kw": device.power_kw,
        "hm0_edges": hm0_edges,
        "period_edges": period_edges
    })
    return d
