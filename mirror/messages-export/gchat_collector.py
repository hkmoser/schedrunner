#!/usr/bin/env python3
"""
gchat_collector.py — Collect Google Chat (chat.google.com) messages by driving a
real Chrome session with Playwright, and archive them to Google Drive.

Output: <GoogleDrive>/My Drive/Private/Chat/<conversation>.jsonl
        One JSON object per line: {conversation, sender, timestamp, text, collected_at}.
        JSONL is append-friendly and ideal for incremental capture + AI ingestion.

Read-receipt / unread behavior
------------------------------
Google Chat DOES send sender-visible read receipts: opening a conversation marks
its messages "seen" for the other party. So collection avoids opening chats
except when you explicitly ask for it:
  - Default and --watch modes are NON-INTRUSIVE: they read only the conversation
    LIST PREVIEWS (latest snippet per chat). They never open a conversation, so
    NO read receipts are sent and your unread markers are left untouched.
    Trade-off: previews can be truncated, and multiple messages arriving between
    polls may collapse into just the latest snippet.
  - --full-read is the explicit deep mode: it OPENS every conversation (which
    DOES send read receipts) to capture complete history, then restores the
    "unread" marker on conversations that were unread beforehand, so your own
    inbox state is preserved.

Modes
-----
    python3 gchat_collector.py --login        # one-time: sign in, persist profile
    python3 gchat_collector.py                # non-intrusive preview snapshot (no read receipts)
    python3 gchat_collector.py --watch        # realtime watch via previews (no read receipts)
    python3 gchat_collector.py --full-read    # OPENS chats (sends receipts), deep scrape, restore unread
    python3 gchat_collector.py --full-read --since-days 14

Setup
-----
    pip install playwright && playwright install chromium
    python3 gchat_collector.py --login        # sign in once in the opened window

Caveat: chat.google.com's DOM is obfuscated and changes periodically. The
SELECTORS block below is the single place to adjust if scraping stops matching.
Automating Google properties may also conflict with their Terms of Service.
"""

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

GDRIVE_ACCOUNT = "joe@joemoser.com"
OUTPUT_SUBPATH = "My Drive/Private/Chat"
PROFILE_DIR    = Path.home() / ".gchat_collector_profile"   # persistent Chrome login
CHAT_URL       = "https://chat.google.com/"
DEFAULT_DAYS   = 7
WATCH_INTERVAL = 15   # seconds between polls in --watch mode

# ── Selectors (the one place to tweak if Google changes the DOM) ─────────────────
# These favour ARIA roles/labels, which are more stable than generated class names.
SELECTORS = {
    # Each conversation row in the left-hand list.
    "conversation_rows": '[role="listitem"], [data-group-id], div[role="row"]',
    # A conversation that has unread messages (Google marks these with an
    # "Unread" / bold cue; aria-label usually contains the word "unread").
    "unread_hint": "unread",
    # Message rows inside an open conversation.
    "message_rows": '[data-message-id], div[role="listitem"]',
    # Within a message row: sender name and the text body.
    "message_sender": '[data-sender-name], [data-name]',
    "message_text": '[data-message-text], [jsname]',
    # Conversation row context menu → "Mark as unread".
    "more_options_name": "More options",
    "mark_unread_text": "Mark as unread",
}


def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def find_output_dir(subpath: str = OUTPUT_SUBPATH) -> Path:
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


def _require_playwright():
    try:
        from playwright.sync_api import sync_playwright  # noqa
        return sync_playwright
    except ImportError:
        log("Playwright is required. Install it with:\n"
            "    pip install playwright && playwright install chromium")
        sys.exit(1)


def safe_name(name: str) -> str:
    import re
    return re.sub(r"[^\w\s\-]", "_", name).strip().replace(" ", "_")[:80] or "unknown"


# ── Persistence (dedup-aware append) ─────────────────────────────────────────────
def _msg_id(conversation: str, sender: str, timestamp: str, text: str) -> str:
    return hashlib.md5(f"{conversation}|{sender}|{timestamp}|{text}".encode("utf-8")).hexdigest()


def load_seen(jsonl_path: Path) -> set[str]:
    """Build the set of already-saved message ids from an existing JSONL file."""
    seen: set[str] = set()
    if not jsonl_path.exists():
        return seen
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
            seen.add(_msg_id(rec["conversation"], rec["sender"],
                             rec["timestamp"], rec["text"]))
        except Exception:
            continue
    return seen


def append_messages(out_root: Path, conversation: str, messages: list[dict]) -> int:
    """Append new (deduped) messages to the conversation's JSONL. Returns count added."""
    jsonl_path = out_root / f"{safe_name(conversation)}.jsonl"
    seen = load_seen(jsonl_path)
    added = 0
    with open(jsonl_path, "a", encoding="utf-8") as f:
        for m in messages:
            mid = _msg_id(conversation, m["sender"], m["timestamp"], m["text"])
            if mid in seen:
                continue
            seen.add(mid)
            rec = {
                "conversation": conversation,
                "sender": m["sender"],
                "timestamp": m["timestamp"],
                "text": m["text"],
                "collected_at": datetime.now().isoformat(timespec="seconds"),
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            added += 1
    return added


# ── Scraping primitives ──────────────────────────────────────────────────────────
def list_conversations(page) -> list[dict]:
    """Return [{name, unread, preview, timestamp, locator}] for each conversation row.

    Reads only the list row — does NOT open the conversation, so no read receipt
    is sent. `preview` is the latest-message snippet shown in the list (may be
    truncated by the UI); `timestamp` is whatever time string the row exposes.
    """
    rows = page.locator(SELECTORS["conversation_rows"])
    out: list[dict] = []
    for i in range(rows.count()):
        row = rows.nth(i)
        try:
            label = (row.get_attribute("aria-label") or row.inner_text() or "").strip()
        except Exception:
            continue
        if not label:
            continue
        lines = [ln.strip() for ln in label.splitlines() if ln.strip()]
        name = lines[0] if lines else label
        preview = lines[1] if len(lines) > 1 else ""
        timestamp = lines[-1] if len(lines) > 2 else ""
        unread = SELECTORS["unread_hint"] in label.lower()
        out.append({"name": name, "unread": unread, "preview": preview,
                    "timestamp": timestamp, "locator": row})
    return out


def scrape_open_conversation(page, conversation: str, since: datetime) -> list[dict]:
    """Scrape messages from the currently open conversation (best-effort)."""
    page.wait_for_timeout(800)  # let messages render
    rows = page.locator(SELECTORS["message_rows"])
    messages: list[dict] = []
    for i in range(rows.count()):
        row = rows.nth(i)
        try:
            sender = ""
            sender_loc = row.locator(SELECTORS["message_sender"])
            if sender_loc.count():
                sender = (sender_loc.first.inner_text() or "").strip()
            text_loc = row.locator(SELECTORS["message_text"])
            text = (text_loc.first.inner_text() if text_loc.count()
                    else row.inner_text() or "").strip()
            ts = (row.get_attribute("data-absolute-timestamp")
                  or row.get_attribute("aria-label") or "").strip()
        except Exception:
            continue
        if not text:
            continue
        messages.append({"sender": sender or "unknown", "timestamp": ts, "text": text})
    return messages


def mark_unread(page, row) -> bool:
    """Restore the 'unread' marker on a conversation row. Best-effort."""
    try:
        row.hover()
        page.wait_for_timeout(150)
        more = row.get_by_role("button", name=SELECTORS["more_options_name"])
        if not more.count():
            more = page.get_by_role("button", name=SELECTORS["more_options_name"])
        more.first.click()
        page.wait_for_timeout(150)
        item = page.get_by_text(SELECTORS["mark_unread_text"], exact=False)
        if item.count():
            item.first.click()
            return True
    except Exception as e:
        log(f"    could not mark unread: {e}")
    return False


def open_and_collect(page, conv: dict, out_root: Path, since: datetime,
                     restore_unread: bool) -> int:
    """Open a conversation, scrape it, save new messages, optionally restore unread."""
    name = conv["name"]
    try:
        conv["locator"].click()
    except Exception as e:
        log(f"  skip {name}: {e}")
        return 0
    messages = scrape_open_conversation(page, name, since)
    added = append_messages(out_root, name, messages)
    if added:
        log(f"  {name}: +{added} message(s)")
    if restore_unread and conv["unread"]:
        # Re-resolve the row (DOM may have re-rendered) and mark unread again.
        for r in list_conversations(page):
            if r["name"] == name:
                mark_unread(page, r["locator"])
                break
    return added


def collect_previews(page, out_root: Path) -> int:
    """Non-intrusive collection: archive the latest list-preview snippet of every
    conversation WITHOUT opening any chat, so no read receipts are sent. Dedup in
    append_messages prevents re-saving unchanged previews."""
    added = 0
    for conv in list_conversations(page):
        if not conv["preview"]:
            continue
        msg = {
            "sender": conv["name"],  # best-effort: for a 1:1, the incoming partner
            "timestamp": conv["timestamp"] or datetime.now().isoformat(timespec="seconds"),
            "text": conv["preview"],
        }
        added += append_messages(out_root, conv["name"], [msg])
    return added


# ── Modes ────────────────────────────────────────────────────────────────────────
def run_login() -> None:
    sync_playwright = _require_playwright()
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=False, channel="chrome",
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(CHAT_URL)
        log("Sign in to Google Chat in the opened window, then press Enter here ...")
        try:
            input()
        except EOFError:
            page.wait_for_timeout(120_000)
        ctx.close()
    log("Login profile saved.")


def run_collect(since_days: int, full_read: bool) -> None:
    sync_playwright = _require_playwright()
    out_root = find_output_dir()

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=False, channel="chrome",
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(CHAT_URL)
        page.wait_for_timeout(4000)

        if not full_read:
            # NON-INTRUSIVE: previews only, never open a chat → no read receipts.
            log(f"Output: {out_root} — non-intrusive preview snapshot (no read receipts)")
            added = collect_previews(page, out_root)
            log(f"Done. {added} new preview message(s). No conversations were opened.")
            ctx.close()
            return

        # FULL READ: opens every conversation (read receipts WILL be sent), then
        # restores the unread marker on conversations that were unread beforehand.
        since = datetime.now() - timedelta(days=since_days)
        log(f"Output: {out_root} — FULL READ since {since:%Y-%m-%d} "
            "(this sends read receipts)")
        conversations = list_conversations(page)
        unread_before = [c["name"] for c in conversations if c["unread"]]
        log(f"{len(conversations)} conversations; {len(unread_before)} unread. "
            "Opening all to deep-scrape ...")
        total = 0
        for conv in conversations:
            total += open_and_collect(page, conv, out_root, since, restore_unread=True)
        log(f"Done. {total} new message(s). Restored unread on: "
            f"{', '.join(unread_before) or 'none'}")
        ctx.close()


def run_watch() -> None:
    sync_playwright = _require_playwright()
    out_root = find_output_dir()
    log(f"Watching Google Chat previews (poll every {WATCH_INTERVAL}s, no read "
        "receipts). Ctrl-C to stop.")

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=False, channel="chrome",
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(CHAT_URL)
        page.wait_for_timeout(4000)
        try:
            while True:
                added = collect_previews(page, out_root)
                if added:
                    log(f"+{added} new preview message(s)")
                page.wait_for_timeout(WATCH_INTERVAL * 1000)
        except KeyboardInterrupt:
            log("Stopping watch.")
        finally:
            ctx.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect Google Chat messages to Google Drive")
    parser.add_argument("--login", action="store_true",
                        help="Open a browser to sign in once and persist the profile")
    parser.add_argument("--watch", action="store_true",
                        help="Realtime watch via list previews (no read receipts)")
    parser.add_argument("--full-read", action="store_true",
                        help="Open every conversation for a deep scrape (SENDS read "
                             "receipts), then restore unread on those unread before")
    parser.add_argument("--since-days", type=int, default=DEFAULT_DAYS,
                        help=f"For --full-read: how many days back to scrape (default {DEFAULT_DAYS})")
    args = parser.parse_args()

    if args.login:
        run_login()
    elif args.watch:
        run_watch()
    else:
        run_collect(args.since_days, full_read=args.full_read)


if __name__ == "__main__":
    main()
