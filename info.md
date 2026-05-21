# Veolia Water

Tracks Veolia / Sorea water consumption in Home Assistant — meter readings,
billing-period totals, last invoice, and (for smart meters) daily flow
telemetry with 90 days of history imported into HA's recorder.

## What you get

- One HA device per Veolia contract.
- ~20 sensors: cumulative meter, period / monthly / daily consumption,
  rolling 7-day average, month-to-date, last-invoice info, peak-flow
  telemetry, possible-leak indicator, sync diagnostics.
- Long-term statistics backfilled on first cycle (90 days daily, 24 months
  monthly).
- Native HA config flow — no YAML, no MQTT broker required.

Default target: `https://sorea.veolia.cat` (Catalonia). The portal URL is
configurable for other Liferay-based Veolia portals.

See the [README](https://github.com/Matbe34/veoliaHA/blob/master/README.md)
for the full sensor list and configuration options.
