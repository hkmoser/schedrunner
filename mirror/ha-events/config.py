"""
config.py — Load settings from .env (or environment).
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env relative to this file
_here = Path(__file__).parent
load_dotenv(_here / ".env", override=False)

# Home Assistant
HA_URL = os.getenv("HA_URL", "http://homeassistant.local:8123")
HA_TOKEN = os.getenv("HA_TOKEN", "")
# Comma-separated list of HA event types to subscribe to.
# Defaults to ALL event types; set a list to narrow it (e.g. "state_changed").
# "*" or "all" (or leave empty) also means ALL event types.
HA_EVENT_TYPES = [
    t.strip() for t in os.getenv("HA_EVENT_TYPES", "*").split(",")
    if t.strip()
]

# Hue
HUE_IP = os.getenv("HUE_IP", "192.168.4.41")
HUE_KEY = os.getenv("HUE_KEY", "")

# YoLink
YOLINK_USER_ACCESS_ID = os.getenv("YOLINK_USER_ACCESS_ID", "")
YOLINK_SECRET_KEY = os.getenv("YOLINK_SECRET_KEY", "")
YOLINK_TOKEN_URL = os.getenv("YOLINK_TOKEN_URL", "https://api.yosmart.com/open/yolink/token")
YOLINK_API_URL = os.getenv("YOLINK_API_URL", "https://api.yosmart.com/open/yolink/v2/api")
YOLINK_MQTT_HOST = os.getenv("YOLINK_MQTT_HOST", "api.yosmart.com")
YOLINK_MQTT_PORT = int(os.getenv("YOLINK_MQTT_PORT", "8003"))

# eero (Amazon) — unofficial cloud API used by the eero mobile app.
# Obtain EERO_SESSION_TOKEN once via `python setup_eero.py`.
EERO_API_URL = os.getenv("EERO_API_URL", "https://api-user.e2ro.com")
EERO_SESSION_TOKEN = os.getenv("EERO_SESSION_TOKEN", "")
# eero has no push stream, so we poll on this interval (seconds).
EERO_POLL_INTERVAL = int(os.getenv("EERO_POLL_INTERVAL", "300"))
# Activity series to pull each poll (comma-separated). Each maps to an eero
# endpoint + insight_type (see eero_collector._ACTIVITY_SPECS):
#   data_usage  -> {network}/data_usage   (data throughput; no insight_type)
#   blocked     -> {network}/insights     (insight_type=blocked)
#   inspected   -> {network}/insights     (insight_type=inspected)
#   adblock     -> {network}/insights     (insight_type=adblock)
# data_usage works on any account; the insights series require eero Secure.
EERO_ACTIVITY_TYPES = [
    t.strip() for t in os.getenv("EERO_ACTIVITY_TYPES", "data_usage").split(",")
    if t.strip()
]
# Time window for each activity series: "day" (hourly cadence), "week", or
# "month" (both daily cadence).
EERO_ACTIVITY_PERIOD = os.getenv("EERO_ACTIVITY_PERIOD", "day")

# Storage
DB_PATH = os.getenv("DB_PATH", str(_here / "events.db"))
GDRIVE_PATH = os.getenv(
    "GDRIVE_PATH",
    os.path.expanduser(
        "~/CloudStorage/GoogleDrive-joe@joemoser.com/My Drive/Smart Home Events"
    ),
)

# Sync
DRIVE_SYNC_INTERVAL = int(os.getenv("DRIVE_SYNC_INTERVAL", "300"))
