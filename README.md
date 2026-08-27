# ECMWF ERA5 Map App

A local Flask interface for selecting WEC study coordinates on a MapLibre map
and downloading monthly ERA5 wave and wind NetCDF files through Copernicus CDS.
The basemap uses OpenFreeMap with OpenStreetMap data and does not require a map
account or API token.

## Setup

1. Configure CDS credentials in `~/.cdsapirc` and accept the ERA5 licence.
2. Install and run:

```bash
cd ecmwf-map-app
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

Open <http://127.0.0.1:5000>. Click or drag the map marker to select the site.
Use **Preview request only** to test request generation without contacting CDS.

The map displays the exact requested point and buffered ERA5 bounding box before
submission. After a real download completes, the app automatically reads the
wave stream inside the CDS NetCDF/ZIP output and displays nearest-grid-point
wave-height, period, direction, and deep-water wave-power-proxy analysis.

Downloaded NetCDF files are written below `ecmwf-map-app/downloads/<job-id>/`.
Jobs are held in memory, so restarting the app clears the status list but does
not delete downloaded files.
