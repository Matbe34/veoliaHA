"""Domain dataclasses — pure Python, no HA imports."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Optional


@dataclass
class Contract:
    contract_number: str
    address: Optional[str] = None
    smart_metering: bool = False
    point_of_service_id: Optional[str] = None
    last_invoice_status_code: Optional[str] = None


@dataclass
class Reading:
    contract_number: str
    meter_index_m3: Optional[float] = None
    consumption_period_m3: Optional[float] = None
    consumption_daily_l: Optional[int] = None  # period-average L/day from inicio
    consumption_monthly_m3: Optional[float] = None
    last_reading_date: Optional[date] = None
    reading_type: Optional[str] = None  # "real" | "estimated"
    period_days: Optional[int] = None
    period_year: Optional[int] = None
    period_number: Optional[int] = None
    # Derived from the daily smart-meter series:
    latest_daily_consumption_m3: Optional[float] = None
    latest_daily_consumption_l: Optional[int] = None
    rolling_7d_avg_l: Optional[int] = None
    month_to_date_m3: Optional[float] = None


@dataclass
class Invoice:
    contract_number: str
    invoice_number: Optional[str] = None
    amount_eur: Optional[float] = None
    status: Optional[str] = None
    status_code: Optional[str] = None
    issue_date: Optional[date] = None
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    current_period_end_estimate: Optional[date] = None
    next_invoice_date_estimate: Optional[date] = None


@dataclass
class Caudal:
    """One day of smart-meter flow telemetry. q values are m³/h."""
    fecha: date
    q_min_m3h: Optional[float] = None
    q_max_m3h: Optional[float] = None
    hora_min: Optional[time] = None
    hora_max: Optional[time] = None


@dataclass
class DailyConsumption:
    """One day from buscarConsumosDiaria."""
    fecha: date
    hora: Optional[time] = None
    lectura_m3: Optional[float] = None   # cumulative meter reading
    consumo_m3: Optional[float] = None   # delta vs. previous reading
    is_estimated: bool = False


@dataclass
class MonthlyConsumption:
    """One month from buscarConsumosMensual."""
    year: int
    month: int
    consumo_m3: Optional[float] = None
    is_estimated: bool = False


@dataclass
class FlowSummary:
    """Per-cycle digest of a Caudal list."""
    latest_date: Optional[date] = None
    q_max_today_m3h: Optional[float] = None
    q_min_today_m3h: Optional[float] = None
    q_max_time_today: Optional[time] = None
    possible_leak: Optional[bool] = None  # True when today's q_min > 0
    record_count: int = 0


@dataclass
class Snapshot:
    """Everything we pull from the portal in one cycle for a single contract."""
    contract: Contract
    reading: Reading = field(default_factory=lambda: Reading(contract_number=""))
    invoice: Invoice = field(default_factory=lambda: Invoice(contract_number=""))
    history: list[dict] = field(default_factory=list)
    flow: FlowSummary = field(default_factory=FlowSummary)
    caudales: list[Caudal] = field(default_factory=list)
    daily: list[DailyConsumption] = field(default_factory=list)
    monthly: list[MonthlyConsumption] = field(default_factory=list)


def serialize(value):
    """JSON-safe coercion for date/datetime/time values."""
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    return value
