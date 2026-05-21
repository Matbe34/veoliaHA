"""Sensor entities for the Veolia Water integration."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Optional

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CURRENCY_EURO, EntityCategory, UnitOfTime, UnitOfVolume
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import VeoliaCoordinator
from .models import Snapshot, serialize

UNIT_M3H = "m³/h"


@dataclass(frozen=True, kw_only=True)
class VeoliaSensorDescription(SensorEntityDescription):
    """A sensor description plus a callable that pulls the value from a Snapshot."""

    value_fn: Callable[[Snapshot], Any]


SENSORS: tuple[VeoliaSensorDescription, ...] = (
    VeoliaSensorDescription(
        key="meter_index",
        translation_key="meter_index",
        name="Meter index",
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=3,
        icon="mdi:counter",
        value_fn=lambda s: s.reading.meter_index_m3,
    ),
    VeoliaSensorDescription(
        key="consumption_period",
        translation_key="consumption_period",
        name="Period consumption",
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=3,
        icon="mdi:water",
        value_fn=lambda s: s.reading.consumption_period_m3,
    ),
    VeoliaSensorDescription(
        key="period_avg_daily",
        translation_key="period_avg_daily",
        name="Period avg daily",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        icon="mdi:water-outline",
        value_fn=lambda s: s.reading.consumption_daily_l,
    ),
    VeoliaSensorDescription(
        key="latest_daily_consumption",
        translation_key="latest_daily_consumption",
        name="Latest daily consumption",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        icon="mdi:cup-water",
        value_fn=lambda s: s.reading.latest_daily_consumption_l,
    ),
    VeoliaSensorDescription(
        key="rolling_7d_avg",
        translation_key="rolling_7d_avg",
        name="7-day rolling average",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        icon="mdi:chart-line-variant",
        value_fn=lambda s: s.reading.rolling_7d_avg_l,
    ),
    VeoliaSensorDescription(
        key="month_to_date",
        translation_key="month_to_date",
        name="Month-to-date consumption",
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=3,
        icon="mdi:calendar-month",
        value_fn=lambda s: s.reading.month_to_date_m3,
    ),
    VeoliaSensorDescription(
        key="last_reading_date",
        translation_key="last_reading_date",
        name="Last reading date",
        device_class=SensorDeviceClass.DATE,
        icon="mdi:calendar-clock",
        value_fn=lambda s: s.reading.last_reading_date,
    ),
    VeoliaSensorDescription(
        key="reading_type",
        translation_key="reading_type",
        name="Reading type",
        icon="mdi:check-decagram-outline",
        value_fn=lambda s: s.reading.reading_type,
    ),
    VeoliaSensorDescription(
        key="period_days",
        translation_key="period_days",
        name="Period days",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.DAYS,
        icon="mdi:timer-sand",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.reading.period_days,
    ),

    VeoliaSensorDescription(
        key="last_invoice_amount",
        translation_key="last_invoice_amount",
        name="Last invoice amount",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        icon="mdi:cash-multiple",
        value_fn=lambda s: s.invoice.amount_eur,
    ),
    VeoliaSensorDescription(
        key="last_invoice_status",
        translation_key="last_invoice_status",
        name="Last invoice status",
        icon="mdi:invoice-text-check-outline",
        value_fn=lambda s: s.invoice.status,
    ),
    VeoliaSensorDescription(
        key="last_invoice_period_end",
        translation_key="last_invoice_period_end",
        name="Last invoice period end",
        device_class=SensorDeviceClass.DATE,
        icon="mdi:calendar-end",
        value_fn=lambda s: s.invoice.period_end,
    ),
    VeoliaSensorDescription(
        key="last_invoice_issue_date",
        translation_key="last_invoice_issue_date",
        name="Last invoice issue date",
        device_class=SensorDeviceClass.DATE,
        icon="mdi:calendar",
        value_fn=lambda s: s.invoice.issue_date,
    ),
    VeoliaSensorDescription(
        key="current_period_end_estimate",
        translation_key="current_period_end_estimate",
        name="Current period ends (estimate)",
        device_class=SensorDeviceClass.DATE,
        icon="mdi:calendar-end-outline",
        value_fn=lambda s: s.invoice.current_period_end_estimate,
    ),
    VeoliaSensorDescription(
        key="next_invoice_date_estimate",
        translation_key="next_invoice_date_estimate",
        name="Next invoice date (estimate)",
        device_class=SensorDeviceClass.DATE,
        icon="mdi:calendar-clock-outline",
        value_fn=lambda s: s.invoice.next_invoice_date_estimate,
    ),

    VeoliaSensorDescription(
        key="flow_qmax_today",
        translation_key="flow_qmax_today",
        name="Peak flow today",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UNIT_M3H,
        suggested_display_precision=3,
        icon="mdi:waves-arrow-up",
        value_fn=lambda s: s.flow.q_max_today_m3h,
    ),
    VeoliaSensorDescription(
        key="flow_qmin_today",
        translation_key="flow_qmin_today",
        name="Min flow today",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UNIT_M3H,
        suggested_display_precision=3,
        icon="mdi:waves-arrow-right",
        value_fn=lambda s: s.flow.q_min_today_m3h,
    ),
    VeoliaSensorDescription(
        key="flow_qmax_time_today",
        translation_key="flow_qmax_time_today",
        name="Peak flow time today",
        icon="mdi:clock-time-four-outline",
        value_fn=lambda s: serialize(s.flow.q_max_time_today),
    ),
    VeoliaSensorDescription(
        key="flow_data_date",
        translation_key="flow_data_date",
        name="Latest flow data date",
        device_class=SensorDeviceClass.DATE,
        icon="mdi:calendar-today",
        value_fn=lambda s: s.flow.latest_date,
    ),
    VeoliaSensorDescription(
        key="possible_leak",
        translation_key="possible_leak",
        name="Possible leak",
        icon="mdi:water-alert-outline",
        value_fn=lambda s: s.flow.possible_leak,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: VeoliaCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(VeoliaSensor(coordinator, entry, desc) for desc in SENSORS)


class VeoliaSensor(CoordinatorEntity[VeoliaCoordinator], SensorEntity):
    """Entity backed by the coordinator's Snapshot."""

    entity_description: VeoliaSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: VeoliaCoordinator,
        entry: ConfigEntry,
        description: VeoliaSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        contract_number = (coordinator.data.contract.contract_number
                           if coordinator.data else entry.entry_id)
        self._attr_unique_id = f"{contract_number}_{description.key}"

        snap = coordinator.data
        contract = snap.contract if snap else None
        device_name = f"Veolia Water — {contract.address}" if (contract and contract.address) else f"Veolia Water — {contract_number}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, contract_number)},
            name=device_name,
            manufacturer="Veolia",
            model="Water meter (telelectura)" if (contract and contract.smart_metering) else "Water meter",
            configuration_url=coordinator._base_url,
        )

    @property
    def native_value(self) -> Optional[Any]:
        snap = self.coordinator.data
        if snap is None:
            return None
        return self.entity_description.value_fn(snap)

    @property
    def extra_state_attributes(self) -> Optional[dict[str, Any]]:
        # Expose the per-period history on a single sensor so it's discoverable
        # without polluting every entity. Attaches to meter_index.
        if self.entity_description.key != "meter_index":
            return None
        snap = self.coordinator.data
        if snap is None:
            return None
        return {"history": snap.history}

    @callback
    def _handle_coordinator_update(self) -> None:
        # The default CoordinatorEntity handles this, but we override to be
        # explicit and to clean up if the snapshot is suddenly None.
        self.async_write_ha_state()
