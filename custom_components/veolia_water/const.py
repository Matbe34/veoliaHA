"""Constants for the Veolia Water integration."""
from __future__ import annotations

DOMAIN = "veolia_water"
PLATFORMS = ["sensor", "button"]

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

# The portal stamps hourly readings in local time with no offset.
PORTAL_TIMEZONE = "Europe/Madrid"

# The portal silently drops rows beyond ~1000 per query, whatever the paging,
# and returns nothing for windows that are too long. Measured limits:
# hourly <= 40 days (24 h * 40 = 960 rows), daily/monthly <= ~365 days.
HOURLY_WINDOW_DAYS = 40
DAILY_WINDOW_DAYS = 360
CONSUMOS_PAGE_SIZE = 500

# How far back to look: everything the portal keeps on the first poll after
# start-up, then just enough overlap to cover missed polls.
BACKFILL_DAYS = 730
HOURLY_REFRESH_DAYS = 7
DAILY_REFRESH_DAYS = 100
MONTHLY_REFRESH_DAYS = 360
