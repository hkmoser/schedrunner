"""
setup_eero.py — One-time eero login to obtain a session token.

eero authenticates with a verification code sent over SMS or email. Run:

    python setup_eero.py

Enter your eero account email or phone number, then the code eero sends
you. The resulting session token is written to .env as EERO_SESSION_TOKEN
(used by eero_collector.py for all subsequent API requests).
"""
import json
import sys
import urllib.request
from pathlib import Path

import config

API = config.EERO_API_URL


def _post(path: str, body: dict, cookie: str | None = None) -> dict:
    headers = {"Content-Type": "application/json", "User-Agent": "ha-events"}
    if cookie:
        headers["Cookie"] = f"s={cookie}"
    req = urllib.request.Request(
        API + path, data=json.dumps(body).encode(), headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def main() -> None:
    login = input("eero account email or phone number: ").strip()
    if not login:
        print("No login provided.")
        sys.exit(1)

    try:
        resp = _post("/2.2/login", {"login": login})
    except Exception as e:
        print(f"Login request failed: {e}")
        sys.exit(1)

    token = resp.get("data", {}).get("user_token")
    if not token:
        print(f"Login failed: {resp}")
        sys.exit(1)

    print(f"eero sent a verification code to {login}.")
    code = input("Enter the verification code: ").strip()

    try:
        _post("/2.2/login/verify", {"code": code}, cookie=token)
    except Exception as e:
        print(f"Verification failed: {e}")
        sys.exit(1)

    print("\neero session token obtained.")

    # Write to .env (replace any existing EERO_SESSION_TOKEN line)
    env_path = Path(__file__).parent / ".env"
    line = f"EERO_SESSION_TOKEN={token}"
    if env_path.exists():
        lines = [
            l for l in env_path.read_text().splitlines()
            if not l.startswith("EERO_SESSION_TOKEN=")
        ]
        lines.append(line)
        env_path.write_text("\n".join(lines) + "\n")
        print(f"Written to {env_path}")
    else:
        template = Path(__file__).parent / ".env.template"
        if template.exists():
            content = template.read_text().replace(
                "EERO_SESSION_TOKEN=your_eero_session_token_here", line
            )
            env_path.write_text(content)
            print(f"Created {env_path} from template (still need to add HA_TOKEN)")
        else:
            print(f"Add this to your .env:  {line}")


if __name__ == "__main__":
    main()
