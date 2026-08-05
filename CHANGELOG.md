# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[SemVer](https://semver.org/).

## [1.0.2] — 2026-08-05

### Fixed
- Setup no longer fails with `portal: inicio page is missing the
  `miUltimoConsumo` block`. Only `contrato` is required now (its absence still
  means the page rendered as guest); `miUltimoConsumo`, `miUltimaFactura` and
  `listadoConsumosImportes` are optional and degrade to zero / unknown.
- Missing consumption values report 0 instead of failing: period consumption,
  period average daily, and derived monthly. Meter index, reading dates and
  period identifiers stay unknown — a fabricated 0 on a `total_increasing`
  meter would look like a reset and double-count the index in statistics.
- `contrato` blocks without a `number` fall back to `miUltimaFactura`'s
  `numeroContrato`; blocks wrapped in a one-element array are accepted.
- Non-string `fechaConsumo` / `consumptionClass` values in the consumption
  endpoints no longer raise `AttributeError`.
- A failing long-term-statistics backfill is logged instead of discarding an
  otherwise-good cycle (and failing initial setup).

## [1.0.1] — 2026-05-21

### Fixed
- Statistics import: switched from `async_import_statistics` (internal,
  entity-id-keyed) to `async_add_external_statistics` (for the
  `<source>:<key>` IDs we use). Previously caused setup to fail with
  `Invalid statistic_id` on first cycle.

## [1.0.0] — 2026-05-21

Initial release.

### Added
- HACS-installable custom integration for Veolia / Sorea water.
- Config flow + options flow (credentials, portal URL, poll interval, contract filter).
- `DataUpdateCoordinator`-driven cycle: login → fetch → parse → derive.
- ~20 sensors per contract: meter index, period / daily / monthly consumption,
  rolling 7-day average, month-to-date, invoice info, peak / min flow telemetry,
  possible-leak indicator, sync diagnostics.
- Long-term statistics backfill on first cycle: 90 days of daily peak flow,
  daily consumption, daily meter index, and 24 months of monthly consumption.
- Spanish + Catalan locale-aware date and number parsing
  (including elision forms like `d'abr.`).
- Configurable portal base URL — defaults to `https://sorea.veolia.cat`.
- 45 unit tests covering the parser.
