"""Constants for the LTAC METAR monitor."""

# LTAC identity / source mapping
AIRPORT_ICAO = "LTAC"
AIRPORT_NAME = "Ankara Esenboga"
AIRPORT_TIMEZONE = "Europe/Istanbul"
AIRPORT_LAT = 40.115921
AIRPORT_LON = 32.986827
AIRPORT_ELEVATION_M = 959

MGM_OBS_ISTNO = 17128
MGM_DAILY_FORECAST_ISTNO = 90615
MGM_SHAPE_FORECAST_ISTNO = 17130
ANKARA_PROVINCE_PLATE = 6

# Lightweight comparison ring around LTAC for the web dashboard.
# These are fetched together from the Ankara province bulk AWS feed and
# matched against the official district lookup for context forecasts/map data.
NEIGHBOR_RING_STATIONS = (
    {"station_id": 17128, "label": "ESENBOGA"},
    {"station_id": 18240, "label": "AKYURT"},
    {"station_id": 18243, "label": "PURSAKLAR"},
    {"station_id": 18242, "label": "CUBUK"},
    {"station_id": 17130, "label": "ANKARA"},
)

# MGM API endpoint for Ankara Esenboğa Airport (LTAC) observations
MGM_URL = "https://servis.mgm.gov.tr/web/sondurumlar?istno=17128"

# All three headers — Origin is required, Referer and Accept are defensive
# against future upstream tightening on this undocumented API.
MGM_HEADERS = {
    "Origin": "https://www.mgm.gov.tr",
    "Referer": "https://www.mgm.gov.tr/",
    "Accept": "application/json",
}

# --- Timeouts (seconds) ---
# Hard rule: max(connect, read) < AGGRESSIVE_INTERVAL
# These are independent (not additive) — httpx enforces each separately.
CONNECT_TIMEOUT = 0.6
READ_TIMEOUT = 0.6

# --- Polling intervals (seconds) ---
BASE_INTERVAL = 5.0
AGGRESSIVE_INTERVAL = 1.5

# Known LTAC publish schedule (minute past the UTC hour)
METAR_PUBLISH_MINUTES = [20, 50]
AWS_PUBLISH_MINUTES = [6, 15, 27, 39, 48, 54]
ALL_PUBLISH_MINUTES = sorted(set(METAR_PUBLISH_MINUTES + AWS_PUBLISH_MINUTES))

# METAR-only aggressive windows in UTC, end-exclusive.
# Example: (20, 27) means 20:00 through 26:59.
METAR_HOT_WINDOWS_UTC = [
    (20, 27),
    (50, 57),
]

# Outside the hot window:
#   next publish within 5 min  → BASE_INTERVAL
#   next publish farther away  → IDLE_INTERVAL
# After a real METAR/correction, hold BASE_INTERVAL briefly for follow-up changes.
APPROACH_WINDOW_S = 300
POST_PUBLISH_HOLD_S = 120
IDLE_INTERVAL = 15.0

# Forecast refresh cadence. Forecast products are much slower-moving than the
# live LTAC poll, but refreshing every 5 minutes keeps the dashboard current.
FORECAST_REFRESH_S = 300

# --- State persistence ---
DEFAULT_STATE_DIR = "~/.metar_monitor"
DEFAULT_DB_PATH = "~/.metar_monitor/metar_monitor.db"
STATE_FILENAME = "state.json"
MAX_HISTORY = 50
MAX_AWS_HISTORY = 300
MAX_CAPTURE_LOG = 500
MAX_FORECAST_HISTORY = 1000

# --- METAR sentinel ---
METAR_UNAVAILABLE = "-9999"
