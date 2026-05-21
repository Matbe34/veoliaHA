"""Constants for the Veolia Water integration."""
from __future__ import annotations

DOMAIN = "veolia_water"
PLATFORMS = ["sensor"]

# Configuration keys (set via config_flow and options_flow).
CONF_PORTAL_URL = "portal_url"
CONF_CONTRACT_NUMBER = "contract_number"
CONF_SCAN_INTERVAL_HOURS = "scan_interval_hours"

DEFAULT_SCAN_INTERVAL_HOURS = 6

# External-statistics source domain (anything that's not a HA integration name).
STATS_SOURCE = "veolia_water"

# Bump when the long-term-statistics shape changes so existing installs re-import.
STATS_IMPORT_VERSION = 1

# Persistent store (HA's Store helper).
STORAGE_KEY = "veolia_water_state"
STORAGE_VERSION = 1
