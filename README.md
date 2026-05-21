# Veolia Water — Home Assistant integration

[![HACS Custom][hacs-shield]][hacs]

Tracks Veolia / Sorea water consumption directly inside Home Assistant —
meter readings, billing-period totals, the last invoice, and (for smart
meters) per-day flow telemetry and 90 days of historical statistics imported
into HA's recorder.

Verified against [`sorea.veolia.cat`](https://sorea.veolia.cat) (Veolia
Catalonia). The portal base URL is configurable, so the integration can be
pointed at other Liferay-based Veolia portals that expose the same JSON shape.

## Install via HACS

1. In Home Assistant, open **HACS → Integrations → ⋮ → Custom repositories**.
2. Add `https://github.com/Matbe34/veoliaHA` with category **Integration**.
3. Install **Veolia Water** from the HACS list, then **restart Home Assistant**.
4. **Settings → Devices & Services → Add Integration → Veolia Water**.
5. Enter your portal username and password.

## Sensors

One HA device per contract with these entities:

**Period / billing**
- `meter_index` (m³, `total_increasing`) — cumulative meter reading.
- `consumption_period` (m³) — current billing-period total.
- `period_avg_daily` (L) — average L/day across the current period.
- `latest_daily_consumption` (L) — smart meters: yesterday's usage.
- `rolling_7d_avg` (L) — smart meters: rolling mean of the last 7 days.
- `month_to_date` (m³) — smart meters: this calendar month so far.
- `last_reading_date`, `reading_type` (`real` / `estimated`).
- `period_days` (diagnostic).

**Invoice**
- `last_invoice_amount` (EUR), `last_invoice_status`, `last_invoice_period_end`, `last_invoice_issue_date`.
- `current_period_end_estimate`, `next_invoice_date_estimate` — forecasts derived from the last invoice's cycle.

**Flow telemetry (smart meters only)**
- `flow_qmax_today` / `flow_qmin_today` (m³/h), `flow_qmax_time_today`.
- `flow_data_date` — most recent telemetry date.
- `possible_leak` — `true` when today's `qmin > 0` (no overnight quiet period).

## Long-term statistics

On the first cycle, 90 days of daily / 24 months of monthly history are
imported as external statistics:

- `veolia_water:meter_index_<slug>` — cumulative meter
- `veolia_water:daily_consumption_<slug>` — daily volume
- `veolia_water:monthly_consumption_<slug>` — monthly volume
- `veolia_water:flow_qmax_<slug>` — daily peak flow

They show up in **History** and `statistics-graph` cards immediately.

## What's *not* available

The portal does not publish per-day cumulative meter readings for non-smart
meters, only period-level (~90 d) totals. The cumulative `meter_index`
therefore updates on billing-period boundaries for manual meters. For smart
meters (`telelectura`) it refreshes daily.

## Privacy

- Credentials live only inside HA's encrypted config store.
- Cookies are kept in memory for one cycle; no session persistence on disk.
- The integration talks only to your configured portal URL and HA core.

## Local development

```bash
python -m venv .venv && . .venv/bin/activate
pip install pytest pytest-asyncio aiohttp
pytest tests/
```

The pure logic (`portal.py`, `veolia_client.py`, `parser.py`, `models.py`)
runs outside HA. Drop the package into a HA core dev environment to test the
config_flow / coordinator / sensor wiring.

## License

[MIT](LICENSE).

[hacs]: https://hacs.xyz/
[hacs-shield]: https://img.shields.io/badge/HACS-Custom-orange.svg
