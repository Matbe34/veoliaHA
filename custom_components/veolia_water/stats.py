"""Backfill long-term statistics into HA's recorder.

Uses `homeassistant.components.recorder.statistics.async_import_statistics`
directly — no WS dance, no auth. The recorder schedules the import; the call
itself returns immediately.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timezone
from typing import Iterable, Optional

from homeassistant.components.recorder.statistics import (
    StatisticData,
    StatisticMetaData,
    async_import_statistics,
)
from homeassistant.core import HomeAssistant

from .const import STATS_SOURCE

_LOGGER = logging.getLogger(__name__)


def import_daily_series(
    hass: HomeAssistant,
    statistic_id: str,
    name: str,
    unit_of_measurement: str,
    daily_values: Iterable[tuple[date, Optional[float]]],
    *,
    has_sum: bool = False,
) -> bool:
    """Queue a (date, value) series. Returns False when there's nothing to import."""
    cleaned = sorted(
        ((d, float(v)) for d, v in daily_values if v is not None),
        key=lambda p: p[0],
    )
    if not cleaned:
        return False

    stats: list[StatisticData] = []
    running_sum = 0.0
    for d, v in cleaned:
        entry: StatisticData = {
            "start": datetime.combine(d, time(0, 0), tzinfo=timezone.utc),
            "state": v,
        }
        if has_sum:
            running_sum += v
            entry["sum"] = round(running_sum, 6)
        else:
            # statistics-graph reads mean/min/max for has_mean=True series.
            entry["mean"] = v
            entry["min"] = v
            entry["max"] = v
        stats.append(entry)

    metadata: StatisticMetaData = {
        "has_mean": not has_sum,
        "has_sum": has_sum,
        "name": name,
        "source": STATS_SOURCE,
        "statistic_id": statistic_id,
        "unit_of_measurement": unit_of_measurement,
    }
    async_import_statistics(hass, metadata, stats)
    _LOGGER.info("Queued %d daily points for %s", len(stats), statistic_id)
    return True
