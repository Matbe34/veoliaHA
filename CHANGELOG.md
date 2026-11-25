# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[SemVer](https://semver.org/).

## [Unreleased]

### Added
- A **Get data now** button on each contract's device that polls the portal
  immediately, so a long poll interval doesn't mean waiting for fresh data.
- Hourly statistics (`veolia_water:hourly_consumption_<slug>`) for smart
  meters: the meter's cumulative reading, hour by hour, ready to use as a
  water source in the Energy dashboard. The first poll after start-up imports
  all the history the portal keeps; later polls refresh the last 7 days.
- Spanish translation. `translations/en.json` now matches `strings.json`
  (it was missing the sensor names and one option description).
- Verified against Viaqua (`viaqua.veolia.gal`).

### Fixed
- The meter index sensor now takes the latest hourly reading when there is
  one. The landing page's reading lagged the hourly series by many hours.
- History requests failed on portals that reject long date ranges (Viaqua
  returns nothing above ~365 days). Daily and monthly requests are now cut
  into 360-day windows, and hourly ones into 40-day windows, because the
  portal silently drops rows past ~1000 per query.
- Some portals answer a valid login with a bounce back to `/login`, which made
  setup fail with "Don't know the landing-page path". The login is now retried
  (up to 5 attempts, with a clean cookie jar each time). A real password
  rejection still fails immediately.
- Paged requests treated the end index as exclusive; it is inclusive.
- Transient 5xx errors from the portal are retried instead of dropping the
  data for that poll.
- Daily history now goes back up to two years on the first poll instead of
  100 days; the sensors still use only the last 100.

## [1.3.1] - 2026-11-17

### Fixed
- Imported statistics now set `mean_type` and `unit_class`, so Home Assistant
  no longer logs a deprecation warning for them, and imports keep working once
  Home Assistant requires both fields (#11, thanks @gjohansson-ST).

### Changed
- Requires Home Assistant 2025.11.0 or newer. Older versions can't store
  statistics with `unit_class`.

## [1.3.0] - 2026-11-04

### Added
- Several contracts per account (issue #10). After login, setup lists the
  account's contracts and you tick the ones to add; each gets its own device
  and sensors. One login per poll reads them all, switching between them the
  way the portal's own contract list does, and a contract that can't be read
  only takes its own sensors offline. The options screen shows the same list,
  fetched live, so contracts can be added or removed later. Removing one
  deletes its device; its imported history stays in the recorder.

### Changed
- The free-text contract number field is gone from setup and options, replaced
  by the contract list. Entries that already have a number keep reading that
  contract until the selection is changed in the options. Entries without one
  keep reading whichever contract the portal opens on, with the same entity
  IDs as before.

## [1.2.2] - 2026-11-03

### Fixed
- The contract number option now switches the portal to that contract. It
  used to be only a check: an account with several contracts (issue #10) got
  whichever one the portal opened on, and setting the number of the other one
  just turned that into an error. When the portal opens on a different
  contract, the integration now sends the same `seleccionarContrato` request
  as the portal's own contract list, then reloads. The number also goes in
  the login form's `lastContract` field, which was always left empty. If the
  portal still returns a different contract, the error says so plainly
  instead of suggesting a switch on the portal, which does not help.

## [1.2.1] - 2026-10-12

1.2.0 was never published as a release, so its changes ship here as well.

### Fixed
- Setup no longer says "Wrong username or password" when the problem is the
  portal, not the password. Every login failure raised `LoginError`, and the
  config flow shows that as bad credentials, so a site with no Liferay login
  page (mel-ileo.fr, issue #9) or a server error sent people off resetting a
  password that worked. A missing `/login` page or `p_auth` token now raises
  `UnsupportedPortalError`, shown as an unsupported portal; HTTP errors from
  the login page or the login POST show as a connection problem. Only a portal
  that turns the credentials away still reports them as wrong.
- `probe_portal.py` reports those failures as `FAILED` instead of crashing with
  a traceback.
- `manifest.json` no longer lists `aiohttp` as a requirement. Home Assistant
  ships it, and hassfest rejects a custom integration that declares a core
  dependency, which had the Validate workflow failing.

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
