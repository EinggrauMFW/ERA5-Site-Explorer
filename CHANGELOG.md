# Changelog

All notable changes are listed here. The project follows [Semantic Versioning](https://semver.org/); while the
major version is 0, minor versions may change behaviour and numbers.

## [0.1.1] - 2026-10-05

Patch release. If you are on 0.1.0, update: it cannot start a download from the page.

### Fixed
- **Fetch ERA5 data failed with "latitude must be a number".** The request form sent its latitude, longitude,
  buffer and time step as text. The stricter request check added in 0.1.0 accepts only numbers, so every submit from
  the page was refused and no download could be started. The page now sends numbers. A request sent straight to
  the API with numbers was never affected.
- A Windows-only flake in the browser-test fixtures that made a full test run fail intermittently in setup.

### Changed
- Five new browser tests submit the real form (all three data products and a preview request). The earlier tests
  posted to the API directly and never pressed the button, which is why the bug was not caught. The suite is now
  725 tests, 75 of them in a browser.

## [0.1.0] - 2026-10-04

First tagged release. It is a screening aid, not a resource assessment: the numerics are tested on synthetic data
with known answers, and real-data validation is incomplete (see
[docs/verification.md](docs/verification.md) and "Known limits" in the release notes).

### What the app does
- Pick a site on a map, download ERA5 wave data from Copernicus CDS (Option A: integrated single-level
  parameters; Option B: 2D wave spectra; plus MARS surface fields) with progress, resume and a probe of what CDS
  accepts.
- Analyse any grid node of a download: energy flux, climatology, scatter tables, wave roses, spectral
  partitioning, quality checks, site screening, device performance from a power matrix (with a catalogue and
  heatmap picker), long-term statistics and peaks-over-threshold extremes, and a report export.
- A cross-check panel compares Option A and Option B without merging them.
- Methods are documented in [docs/numerics.md](docs/numerics.md).

### Fixed in this release (found by an independent review of the whole app)
**Server and data safety**
- The server no longer crashes when a job is opened: NetCDF reads are serialised (netCDF4 is not thread-safe)
  and cache files are written atomically.
- Host and Origin checks against DNS rebinding and cross-site requests; a second copy on the same port is
  refused; malformed JSON gives 400; error messages no longer contain file paths.
- Downloads: a failing `job.json` save no longer wedges a running job, an orphaned fetcher stops itself, Resume is
  blocked while the old fetcher lives, and a cancelled run cannot overwrite a resumed one. Missing CDS
  credentials and unaccepted licences give plain guidance.

**Numbers**
- Return levels no longer come out too low when the record has gaps: the event rate uses observed time.
- Seasonal shares add up to 100 % (they summed to about 400 %).
- A year counts as complete only when every month is at least 90 % covered.
- Overlapping files are counted once in the node table and screening (they were counted twice, +11 % flux in the
  review's case), and a surface download split over several files is read in full.
- Smaller fixes: spectra sampling is global across files with an exact note; sensitivity rows with too few peaks
  are not fitted; a warning for Hm0 sampled less often than hourly; hours without a direction are left out of the
  sector percentage; no return levels shorter than the mean time between events.

**Interface**
- State bugs: a failed analysis restores the previous node, the last request wins, the theme toggle no longer
  refetches or wipes input, charts are drawn once, one bad history record no longer blanks the list, refused
  Cancel/Delete are reported, a faulty plugin cannot break the tabs, and the Screening tab never shows another
  job's data. The closed device picker no longer covers the page on phones.
- Accessibility and readability: names for every control, keyboard control of the data-product choice,
  reduced-motion scrolling, WCAG AA text contrast in both themes, readable heat tables, charts that fit a phone
  screen, number formatting without negative zero.

### Changed
- Cached analysis, node, screening and long-term results are recomputed after upgrading (internal cache versions
  were bumped).
- The test suite grew from 232 to 720 tests, including 70 that drive the page in Chromium (Playwright, with a
  dedicated CI job).
