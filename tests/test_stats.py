"""The hourly statistics series handed to the recorder."""
from datetime import datetime, timezone

import pytest

pytest.importorskip("homeassistant.components.recorder.statistics")

from custom_components.veolia_water import stats  # noqa: E402


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


@pytest.fixture
def captured(monkeypatch):
    calls = []
    monkeypatch.setattr(
        stats, "async_add_external_statistics",
        lambda hass, metadata, data: calls.append((metadata, data)),
    )
    return calls


def test_hourly_series_uses_the_meter_reading_as_state_and_sum(captured):
    points = [
        (_utc(2026, 11, 23, 8), 172.5),
        (_utc(2026, 11, 23, 7), 172.343),  # out of order on purpose
        (_utc(2026, 11, 23, 9), None),     # dropped
    ]
    assert stats.import_hourly_sum_series(
        object(), "veolia_water:hourly_consumption_1", "Veolia 1 hourly consumption", "m³", points,
    )
    (metadata, data), = captured
    assert metadata["has_sum"] is True
    assert metadata["statistic_id"] == "veolia_water:hourly_consumption_1"
    assert metadata["unit_of_measurement"] == "m³"
    assert metadata["unit_class"] == "volume"
    assert data == [
        {"start": _utc(2026, 11, 23, 7), "state": 172.343, "sum": 172.343},
        {"start": _utc(2026, 11, 23, 8), "state": 172.5, "sum": 172.5},
    ]


def test_hourly_series_with_no_values_imports_nothing(captured):
    assert not stats.import_hourly_sum_series(object(), "x:y", "n", "m³", [(_utc(2026, 1, 1), None)])
    assert captured == []
