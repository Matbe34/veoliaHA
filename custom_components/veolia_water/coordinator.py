"""DataUpdateCoordinator — owns the fetch cycle and the statistics import."""
from __future__ import annotations

import logging
from datetime import date, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_PORTAL_SITE,
    CONF_PORTAL_URL,
    CONF_SCAN_INTERVAL_HOURS,
    DEFAULT_SCAN_INTERVAL_HOURS,
    DOMAIN,
    STATS_SOURCE,
)
from .cycle import configured_contracts, run_cycle
from .models import Snapshot
from .parser import ParseError
from .portal import DEFAULT_BASE_URL, apply_overrides, profile_for_url
from .stats import import_daily_series
from .veolia_client import (
    CDNBlockedError,
    LoginError,
    VeoliaClient,
    VeoliaError,
)

_LOGGER = logging.getLogger(__name__)


class VeoliaCoordinator(DataUpdateCoordinator[dict[str, Snapshot]]):
    """Runs one cycle (login + fetch + parse + derive) on a configurable cadence.

    `data` maps each contract number read in the last cycle to its snapshot.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        opts = {**entry.data, **entry.options}
        interval_h = int(opts.get(CONF_SCAN_INTERVAL_HOURS, DEFAULT_SCAN_INTERVAL_HOURS))
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(hours=max(1, interval_h)),
        )
        self.hass = hass
        self.entry = entry
        self._username: str = opts[CONF_USERNAME]
        self._password: str = opts[CONF_PASSWORD]
        self._base_url: str = (opts.get(CONF_PORTAL_URL) or DEFAULT_BASE_URL).rstrip("/")
        # Empty: read whichever contract the portal opens on.
        self.contracts: list[str] = configured_contracts(opts)
        self._profile = apply_overrides(
            profile_for_url(self._base_url), {"site": opts.get(CONF_PORTAL_SITE)}
        )

    async def _async_update_data(self) -> dict[str, Snapshot]:
        try:
            async with VeoliaClient(
                self._username, self._password,
                base_url=self._base_url, profile=self._profile,
                contract=self.contracts[0] if self.contracts else None,
            ) as client:
                result = await run_cycle(client, self.contracts)
        except LoginError as e:
            raise UpdateFailed(f"login: {e}") from e
        except CDNBlockedError as e:
            raise UpdateFailed(f"cdn blocked: {e}") from e
        except (ParseError, VeoliaError) as e:
            raise UpdateFailed(f"portal: {e}") from e

        if not result.snapshots:
            number, error = next(iter(result.errors.items()))
            raise UpdateFailed(f"portal: contract {number}: {error}")

        # The history push is a nice-to-have: never let a recorder hiccup discard
        # snapshots we already fetched successfully (or fail the initial setup).
        for snapshot in result.snapshots.values():
            try:
                self._import_statistics(snapshot)
            except Exception:  # noqa: BLE001 — best-effort history import
                _LOGGER.exception(
                    "Statistics import failed for contract %s; continuing without it.",
                    snapshot.contract.contract_number,
                )

        return result.snapshots

    def _import_statistics(self, snap: Snapshot) -> None:
        """Push the fetched history into HA's recorder — every cycle, not once.

        Re-pushing the whole window is idempotent: `async_add_external_statistics`
        overwrites points sharing a `start` and leaves older ones alone.
        """
        cnum = snap.contract.contract_number
        if not (snap.caudales or snap.daily or snap.monthly):
            return

        slug = _slug(cnum)
        if snap.caudales:
            import_daily_series(
                self.hass,
                statistic_id=f"{STATS_SOURCE}:flow_qmax_{slug}",
                name=f"Veolia {cnum} daily peak flow",
                unit_of_measurement="m³/h",
                daily_values=[(c.fecha, c.q_max_m3h) for c in snap.caudales],
            )
        if snap.daily:
            import_daily_series(
                self.hass,
                statistic_id=f"{STATS_SOURCE}:daily_consumption_{slug}",
                name=f"Veolia {cnum} daily consumption",
                unit_of_measurement="m³",
                daily_values=[(d.fecha, d.consumo_m3) for d in snap.daily],
            )
            import_daily_series(
                self.hass,
                statistic_id=f"{STATS_SOURCE}:meter_index_{slug}",
                name=f"Veolia {cnum} meter index",
                unit_of_measurement="m³",
                daily_values=[(d.fecha, d.lectura_m3) for d in snap.daily],
            )
        if snap.monthly:
            import_daily_series(
                self.hass,
                statistic_id=f"{STATS_SOURCE}:monthly_consumption_{slug}",
                name=f"Veolia {cnum} monthly consumption",
                unit_of_measurement="m³",
                daily_values=[(date(m.year, m.month, 1), m.consumo_m3) for m in snap.monthly],
            )


def _slug(contract_number: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in contract_number).lower() or "unknown"
