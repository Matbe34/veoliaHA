# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[SemVer](https://semver.org/).

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
