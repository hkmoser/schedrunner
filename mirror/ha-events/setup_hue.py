"""
setup_hue.py — One-time Hue bridge application key setup.

Press the physical button on your Hue bridge, then run:
    python setup_hue.py

The key will be printed and written to .env automatically.
"""
import json
import ssl
import sys
import time
import urllib.request
from pathlib import Path

HUE_IP = "192.168.4.41"
APP_NAME = "ha-events#mac"

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

print(f"Press the physical button on your Hue bridge at {HUE_IP} now.")
print("You have 30 seconds...")

deadline = time.time() + 30
key = None
while time.time() < deadline:
    try:
        req = urllib.request.Request(
            f"https://{HUE_IP}/api",
            data=json.dumps({"devicetype": APP_NAME, "generateclientkey": True}).encode(),
            method="POST",
        )
        with urllib.request.urlopen(req, context=ctx, timeout=5) as resp:
            result = json.loads(resp.read())
        for item in result:
            if "success" in item:
                key = item["success"].get("username")
                break
    except Exception as e:
        print(f"  Waiting… ({e})", end="\r")
    if key:
        break
    time.sleep(2)

if not key:
    print("\nFailed to get a key. Make sure you pressed the bridge button.")
    sys.exit(1)

print(f"\nHue key: {key}")

# Write to .env
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    lines = env_path.read_text().splitlines()
    lines = [l for l in lines if not l.startswith("HUE_KEY=")]
    lines.append(f"HUE_KEY={key}")
    env_path.write_text("\n".join(lines) + "\n")
    print(f"Written to {env_path}")
else:
    # Create from template
    template = Path(__file__).parent / ".env.template"
    if template.exists():
        content = template.read_text().replace("your_hue_application_key_here", key)
        env_path.write_text(content)
        print(f"Created {env_path} from template (still need to add HA_TOKEN)")
    else:
        print(f"Add this to your .env:  HUE_KEY={key}")
