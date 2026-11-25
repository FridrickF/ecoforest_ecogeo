from datetime import timedelta

DOMAIN = "ecoforest_ecogeo_plus"
MANUFACTURER = "Ecoforest"

CONF_SCAN_INTERVAL = "scan_interval"
DEFAULT_SCAN_INTERVAL = 30  # seconds, same as the original integration
MIN_SCAN_INTERVAL = 5
MAX_SCAN_INTERVAL = 600
SLOW_REFRESH_SECONDS = 300  # diagnostic blocks are read about every 5 minutes whatever the interval

POLLING_INTERVAL = timedelta(seconds=DEFAULT_SCAN_INTERVAL)
