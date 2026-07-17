# ha-events

Collects state-change events from all smart home sources and stores them in
SQLite, with periodic sync to Google Drive.

## Sources

| Source | Method | What it captures |
|--------|--------|-----------------|
| Home Assistant | WebSocket events (all types by default) + snapshot | All HA-integrated devices |
| Philips Hue | CLIP v2 SSE (`/eventstream/clip/v2`) | Lights, sensors, buttons |
| YoLink | Cloud MQTT (`api.yosmart.com:8003`) | Door sensors, leak detectors, etc. |
| eero | Cloud API polling (unofficial) | AP/node data, client devices, data-usage activity |

## Setup

### 1. Copy and fill .env

```bash
cp .env.template .env
```

Fill in:
- `HA_TOKEN` — Long-lived token from `http://homeassistant.local:8123/profile`
- `HA_EVENT_TYPES` *(optional)* — comma-separated HA event types to capture.
  Defaults to **every** event type (`call_service`, `automation_triggered`,
  `zha_event`, …). Set a list such as `state_changed` to narrow it down.
- `HUE_KEY` — Run `python setup_hue.py` (press bridge button when prompted)
- `EERO_SESSION_TOKEN` — Run `python setup_eero.py` (enter your eero email/phone,
  then the verification code eero texts/emails you)
- YoLink and Hue IP are pre-filled from discovery

### 2. Create venv and install deps

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 3. Run manually to test

```bash
.venv/bin/python main.py --debug
```

### 4. Register with schedrunner (already done)

`run.sh` is registered in `schedrunner/scripts.conf` as a startup service.
It will start automatically on next reboot (or trigger manually via schedrunner).

Auto-deploy is enabled via `.auto-deploy` — any push to main will restart
the service with updated code.

## Output

- **SQLite**: `events.db` (local, in repo dir — git-ignored)
- **Google Drive**: `~/CloudStorage/GoogleDrive-joe@joemoser.com/My Drive/Smart Home Events/`
  - `events_YYYY-MM-DD.jsonl` — newline-delimited JSON log
  - `summary_latest.json` — rolling counts by source

## Manually start/stop

```bash
# Start
bash run.sh

# Stop
kill $(cat .ha-events.pid)
```
