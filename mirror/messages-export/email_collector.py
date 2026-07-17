#!/usr/bin/env python3
"""
email_collector.py — Collect the last week of email from each Gmail account via
IMAP and archive it to Google Drive under Private/mail.

Output: <GoogleDrive>/My Drive/Private/mail/<account>/<YYYY-MM-DD>/<stem>.json
        One JSON file per message, with decoded plain-text body and normalized
        headers (the most useful shape for an AI agent / automation to consume).
        Attachments, if any, are saved beside the JSON in <stem>.attachments/.

Design notes:
  - Uses Gmail app passwords (requires 2-Step Verification on each account).
  - Reads "[Gmail]/All Mail" so it captures sent + received + archived mail
    (excludes Spam/Trash), filtered to the last N days.
  - Uses BODY.PEEK so collecting NEVER marks your mail as read.
  - Idempotent: each message maps to a stable filename, so re-runs skip
    anything already saved. Safe to run on a schedule.

Config (~/.mail_collector_config.json, chmod 600 — keep it out of git):
    {
      "accounts": [
        {"email": "you@gmail.com",    "app_password": "abcd efgh ijkl mnop"},
        {"email": "other@gmail.com",  "app_password": "..."}
      ]
    }

Usage:
    python3 email_collector.py                 # last 7 days, all accounts
    python3 email_collector.py --days 14       # change the window
    python3 email_collector.py --account you@gmail.com
    python3 email_collector.py --full          # re-save even if already archived
"""

import argparse
import email
import hashlib
import imaplib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

# ── Configuration ──────────────────────────────────────────────────────────────
CONFIG_FILE    = Path.home() / ".mail_collector_config.json"
IMAP_HOST      = "imap.gmail.com"
IMAP_PORT      = 993
MAILBOX        = "[Gmail]/All Mail"   # everything except Spam/Trash
GDRIVE_ACCOUNT = "joe@joemoser.com"
OUTPUT_SUBPATH = "My Drive/Private/mail"
DEFAULT_DAYS   = 7


# ── Helpers ────────────────────────────────────────────────────────────────────
def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def find_output_dir(subpath: str = OUTPUT_SUBPATH) -> Path:
    """Locate the Google Drive Desktop sync folder and ensure subpath exists."""
    cloudstore = Path.home() / "Library/CloudStorage"
    for entry in cloudstore.iterdir():
        if entry.name.startswith(f"GoogleDrive-{GDRIVE_ACCOUNT}"):
            target = entry / subpath
            target.mkdir(parents=True, exist_ok=True)
            return target
    raise RuntimeError(
        f"Google Drive not found for {GDRIVE_ACCOUNT}. "
        "Is Google Drive Desktop installed and signed in?"
    )


def load_config() -> list[dict]:
    if not CONFIG_FILE.exists():
        log(f"ERROR: config not found at {CONFIG_FILE}. See the module docstring "
            "for the expected format (accounts + app passwords).")
        sys.exit(1)
    accounts = json.loads(CONFIG_FILE.read_text()).get("accounts", [])
    if not accounts:
        log(f"ERROR: no accounts listed in {CONFIG_FILE}")
        sys.exit(1)
    return accounts


def decode_hdr(value: str | None) -> str:
    """Decode an RFC 2047 encoded header into a plain unicode string."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def safe_stem(name: str) -> str:
    return re.sub(r"[^\w\-.]", "_", name)[:60]


class _HTMLToText(HTMLParser):
    """Minimal HTML → text extractor (stdlib only, no dependencies)."""
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("br", "p", "div", "tr", "li"):
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self._parts.append(data)

    def text(self) -> str:
        raw = "".join(self._parts)
        return re.sub(r"\n{3,}", "\n\n", raw).strip()


def html_to_text(html: str) -> str:
    parser = _HTMLToText()
    try:
        parser.feed(html)
    except Exception:
        return html
    return parser.text()


# ── Message parsing ──────────────────────────────────────────────────────────────
def extract_body_and_attachments(msg) -> tuple[str, list[tuple[str, bytes]]]:
    """Return (plain_text_body, [(filename, bytes), ...])."""
    attachments: list[tuple[str, bytes]] = []
    text_plain: str | None = None
    text_html: str | None = None

    if msg.is_multipart():
        for part in msg.walk():
            if part.is_multipart():
                continue
            disp = (part.get_content_disposition() or "").lower()
            ctype = part.get_content_type()
            if disp == "attachment" or (part.get_filename() and disp != "inline"):
                payload = part.get_payload(decode=True)
                if payload is not None:
                    attachments.append((decode_hdr(part.get_filename()) or "attachment", payload))
                continue
            if ctype == "text/plain" and text_plain is None:
                text_plain = _decoded_text(part)
            elif ctype == "text/html" and text_html is None:
                text_html = _decoded_text(part)
    else:
        if msg.get_content_type() == "text/html":
            text_html = _decoded_text(msg)
        else:
            text_plain = _decoded_text(msg)

    body = text_plain if text_plain else (html_to_text(text_html) if text_html else "")
    return body.strip(), attachments


def _decoded_text(part) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, TypeError):
        return payload.decode("utf-8", errors="replace")


def parse_message(raw: bytes) -> tuple[dict, list[tuple[str, bytes]]]:
    msg = email.message_from_bytes(raw)
    body, attachments = extract_body_and_attachments(msg)

    date_hdr = msg.get("Date")
    try:
        dt = parsedate_to_datetime(date_hdr) if date_hdr else None
    except (TypeError, ValueError):
        dt = None
    iso_date = dt.astimezone(timezone.utc).isoformat() if dt else ""

    record = {
        "message_id": decode_hdr(msg.get("Message-ID")),
        "date": iso_date,
        "from": decode_hdr(msg.get("From")),
        "to": decode_hdr(msg.get("To")),
        "cc": decode_hdr(msg.get("Cc")),
        "subject": decode_hdr(msg.get("Subject")),
        "in_reply_to": decode_hdr(msg.get("In-Reply-To")),
        "references": decode_hdr(msg.get("References")),
        "body_text": body,
        "attachments": [name for name, _ in attachments],
    }
    return record, attachments


def message_stem(record: dict) -> str:
    """Stable, sortable filename stem: <YYYYMMDDTHHMMSS>_<msgid hash>."""
    mid = record.get("message_id") or record.get("subject") or ""
    digest = hashlib.md5(mid.encode("utf-8", "ignore")).hexdigest()[:10]
    if record.get("date"):
        try:
            ts = datetime.fromisoformat(record["date"]).strftime("%Y%m%dT%H%M%S")
        except ValueError:
            ts = "00000000T000000"
    else:
        ts = "00000000T000000"
    return f"{ts}_{digest}"


def date_folder(record: dict) -> str:
    if record.get("date"):
        try:
            return datetime.fromisoformat(record["date"]).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return "undated"


# ── Collection ───────────────────────────────────────────────────────────────────
def collect_account(account: dict, out_root: Path, days: int, full: bool) -> int:
    addr = account["email"]
    pw = account["app_password"]
    log(f"Connecting to {addr} ...")
    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        imap.login(addr, pw)
    except imaplib.IMAP4.error as e:
        log(f"  Login failed for {addr}: {e}. "
            "Check the app password and that IMAP is enabled.")
        return 0

    written = 0
    try:
        imap.select(f'"{MAILBOX}"', readonly=True)  # readonly: never change flags
        since = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
        status, data = imap.uid("search", None, f'(SINCE {since})')
        if status != "OK":
            log(f"  SEARCH failed for {addr}: {status}")
            return 0
        uids = data[0].split()
        log(f"  {len(uids)} messages in the last {days} days")

        acct_dir = out_root / safe_stem(addr)
        for uid in uids:
            # BODY.PEEK[] fetches the full message WITHOUT setting the \Seen flag.
            status, fetched = imap.uid("fetch", uid, "(BODY.PEEK[])")
            if status != "OK" or not fetched or fetched[0] is None:
                continue
            raw = fetched[0][1]
            record, attachments = parse_message(raw)

            stem = message_stem(record)
            day_dir = acct_dir / date_folder(record)
            json_path = day_dir / f"{stem}.json"
            if json_path.exists() and not full:
                continue

            day_dir.mkdir(parents=True, exist_ok=True)
            json_path.write_text(json.dumps(record, indent=2, ensure_ascii=False))
            if attachments:
                att_dir = day_dir / f"{stem}.attachments"
                att_dir.mkdir(exist_ok=True)
                for fname, payload in attachments:
                    (att_dir / safe_stem(fname)).write_bytes(payload)
            written += 1

        log(f"  {addr}: {written} new message(s) saved")
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect recent Gmail via IMAP to Google Drive")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS,
                        help=f"How many days back to collect (default {DEFAULT_DAYS})")
    parser.add_argument("--account", help="Only collect this single account address")
    parser.add_argument("--full", action="store_true",
                        help="Re-save messages even if already archived")
    args = parser.parse_args()

    accounts = load_config()
    if args.account:
        accounts = [a for a in accounts if a["email"] == args.account]
        if not accounts:
            log(f"No matching account {args.account} in {CONFIG_FILE}")
            sys.exit(1)

    out_root = find_output_dir()
    log(f"Output directory: {out_root}")

    total = 0
    for account in accounts:
        total += collect_account(account, out_root, args.days, args.full)
    log(f"Done. {total} new message(s) across {len(accounts)} account(s).")


if __name__ == "__main__":
    main()
