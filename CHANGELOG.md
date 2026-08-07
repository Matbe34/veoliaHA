# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[SemVer](https://semver.org/).

## [Unreleased]

### Security
- `probe_portal.py` now redacts log records too. Only its own output went
  through the scrubber; anything the integration logged reached the terminal by
  way of logging's last-resort handler, and `parser` logs the contract number
  when a portal serves no `contrato` block — the very portals the probe gets
  run against. Reported on issue #3, where the contract number had to be edited
  out by hand. The handler is installed on the root logger, so aiohttp and
  asyncio are covered as well, and it prints the message alone: a traceback
  would carry page text past the scrubber.

## [1.2.0] — 2026-08-06

### Added
- `data_date` attribute on the four flow sensors (peak flow, min flow, peak
  flow time, possible leak), carrying the day the figures actually describe.
  The caudales endpoint runs a couple of days behind, so a dashboard that
  wants to be honest about staleness now has something to show.

### Changed
- Those sensors are named "(latest day)" rather than "today", which they never
  were. Entity IDs are unchanged — `unique_id` is built from the description
  `key`, not the name.
- Daily derivations moved out of `coordinator.py` into a new `derive` module.
  The coordinator imports Home Assistant at module level and so can't be unit
  tested; `derive` is pure Python, like `parser`, `models`, `portal` and
  `veolia_client`.
- `extra_state_attributes` now dispatches through an `attrs_fn` on the sensor
  description, replacing the hardcoded check for the `meter_index` key.

### Fixed
- Period consumption and period average daily both read 0 on a contract that
  has never been billed. They come from the portal's `ultimo` block, which
  describes the last *closed* billing period; with no invoice yet the portal
  reports nothing there and the parser's zero-defaulting turned that absence
  into a hard 0. When the portal reports no period at all, the whole daily
  series is totalled instead — the only span that can honestly be summed. A
  real period is left alone, including a genuine 0 m³ one, so the fallback
  stops applying as soon as the first invoice lands.

## [1.1.2] — 2026-08-06

### Fixed
- Long-term statistics (daily consumption, meter index, monthly consumption,
  peak flow) were imported once at setup and never again, so every series froze
  at whatever the first cycle saw while the portal kept publishing new days.
  The import now runs on every coordinator cycle; re-pushing the fetched window
  is idempotent, since `async_add_external_statistics` overwrites points that
  share a `start`. Existing installs recover on the next cycle — no reinstall.

### Removed
- The persistent `Store` and `STATS_IMPORT_VERSION`, which existed only to hold
  the one-shot import flag.

## [1.1.1] — 2026-08-06

### Added
- Aigües de Barcelona (`agbar.veolia.cat`) profile, from the probe output on
  issue #3. The site (`sgab`) is left to discovery rather than pinned, so
  login keeps sending no `redirect` — the flow the probe actually exercised.
- `probe_portal.py` now runs the integration's own parser against the landing
  page and calls the three consumption endpoints, reporting record counts and
  response key names. Page structure alone couldn't show whether the data API
  answers in the shape the parser expects.

### Security
- `probe_portal.py` redaction hardened, since its output is meant for public
  issues: every line now routes through the scrubber (the linked-pages list
  bypassed it), and emails plus 6+ digit runs — contract numbers, DNI, meter
  serials — are stripped even when not supplied as credentials. The page-link
  regex no longer runs past the href and pulls surrounding page text, which
  could include any of the above.

### Fixed
- Landing pages without a `contrato` block (Agbar serves every other block but
  not that one) now take the contract number from `miUltimaFactura` instead of
  failing setup with `inicio page is missing the contrato block`.
- Smart-meter endpoints are probed when the portal doesn't report a
  `smartMetering` flag, rather than assumed absent, and an unavailable
  consumption page no longer fails the whole cycle.
- The **Portal site name** option accepts a pasted path or full URL
  (`/group/sgab/inicio`) as well as a bare site name; previously it produced
  `/group//group/sgab/inicio/inicio` and a 404.

## [1.1.0] — 2026-08-05

### Added
- `PortalProfile` per tenant (`portal.py`), replacing the module-level Sorea
  constants. Profiles for Sorea (confirmed), Agbar and Hidrogea.
- The Liferay site name is discovered from the post-login redirect, so a
  portal no longer has to be known in advance to be reachable.
- **Portal site name** override in the integration options for portals where
  detection fails.
- `scripts/probe_portal.py` — logs in and reports paths, portlet ids and JSON
  block key names, with credentials and values redacted.

### Fixed
- Login success and session-expiry are detected from the Liferay auth cookie
  (falling back to "no login form present") instead of matching the literal
  string `soreaonline` in the URL, which reported a successful login as
  `Authentication rejected` on every non-Sorea portal.

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
