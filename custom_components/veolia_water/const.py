"""Constants for the Veolia Water integration."""
from __future__ import annotations

DOMAIN = "veolia_water"
PLATFORMS = ["sensor"]

# Configuration keys (set via config_flow and options_flow).
CONF_PORTAL_URL = "portal_url"
CONF_PORTAL_SITE = "portal_site"
# Entries from before the contract picker store one number here.
CONF_CONTRACT_NUMBER = "contract_number"
# Contract numbers to read; an empty list means the one the portal opens on.
CONF_CONTRACTS = "contracts"
CONF_SCAN_INTERVAL_HOURS = "scan_interval_hours"

DEFAULT_SCAN_INTERVAL_HOURS = 6

# External-statistics source domain (anything that's not a HA integration name).
STATS_SOURCE = "veolia_water"
