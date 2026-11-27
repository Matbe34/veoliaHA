# Veolia Water — Home Assistant integration

[![HACS Custom][hacs-shield]][hacs]

Tracks Veolia / Sorea water consumption directly inside Home Assistant —
meter readings, billing-period totals, the last invoice, and (for smart
meters) per-day flow telemetry and hourly, daily and monthly historical
statistics imported into HA's recorder.

Verified against [`sorea.veolia.cat`](https://sorea.veolia.cat) (Veolia
Catalonia) and [`viaqua.veolia.gal`](https://viaqua.veolia.gal) (Viaqua,
Galicia). Other Veolia-group portals run the same Liferay application under
a different site name, which the integration discovers at login — set the
portal base URL and try it. If the landing page can't be found, run
`scripts/probe_portal.py` (see [Unsupported portals](#unsupported-portals)).

## Install via HACS

1. In Home Assistant, open **HACS → Integrations → ⋮ → Custom repositories**.
2. Add `https://github.com/Matbe34/veoliaHA` with category **Integration**.
3. Install **Veolia Water** from the HACS list, then **restart Home Assistant**.
4. **Settings → Devices & Services → Add Integration → Veolia Water**.
5. Enter your portal username and password. If your account has several
   contracts, tick the ones to add.

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

## Several contracts

If your account has more than one contract, setup lists them after login
and you tick the ones to add. Each one gets its own device with the full set
of sensors and its own history. Change the selection later under
**Configure**: unticking a contract removes its device but keeps its imported
history.

Each poll logs in once and reads the contracts one after another, switching
between them the same way the portal's own contract list does. If one can't
be read, only its sensors go unavailable.

## Long-term statistics

For smart meters, the first time a series is imported (when the recorder holds
none of it yet) it gets everything the portal keeps, up to two years. Stored
history is never fetched again: later polls refresh only the recent window
(7 days hourly, 100 days daily, 11 months monthly). A first import that fails
part-way imports nothing and is retried in full on the next poll.
Re-importing is idempotent.

- `veolia_water:hourly_consumption_<slug>` — hourly, cumulative meter reading
  in m³ (`sum`), usable as a water source in the **Energy** dashboard
- `veolia_water:meter_index_<slug>` — cumulative meter, daily
- `veolia_water:daily_consumption_<slug>` — daily volume
- `veolia_water:monthly_consumption_<slug>` — monthly volume
- `veolia_water:flow_qmax_<slug>` — daily peak flow

They show up in **History** and `statistics-graph` cards immediately.

The portal data arrives in batches, about 5 hours behind real time, and it
silently drops rows past ~1000 per query, so requests are cut into windows
(40 days for hourly, 360 for daily, 11 whole months for monthly). The first
import can take a couple of minutes.

## What's *not* available

The portal does not publish per-day cumulative meter readings for non-smart
meters, only period-level (~90 d) totals. The cumulative `meter_index`
therefore updates on billing-period boundaries for manual meters. For smart
meters (`telelectura`) it refreshes daily.

## Unsupported portals

Each tenant runs the same application under its own Liferay *site name* — the
`soreaonline` in `/group/soreaonline/inicio`. That name is detected from the
post-login redirect, so most portals need nothing but the base URL. If
detection fails, find the name in your browser's address bar after logging in
and set **Portal site name** in the integration options.

To report a portal that still doesn't work:

```bash
VEOLIA_USER='you@example.com' VEOLIA_PASSWORD='...' \
    python scripts/probe_portal.py https://your-portal.example
```

It prints paths, portlet ids and which JSON blocks the page carries — key
names only, never values — so the output is safe to paste into an issue.

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
