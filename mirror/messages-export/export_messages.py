#!/usr/bin/env python3
"""
export_messages.py — Export all iMessage/SMS conversations to Google Drive.

Runs incrementally: tracks the last-exported message date per conversation
and only appends new messages on subsequent runs. On first run, exports all history.

Output: ~/Library/CloudStorage/GoogleDrive-joe@joemoser.com/My Drive/Private/Messages/
        One .txt file per conversation, named by resolved contact name or phone number.

Contact names are resolved by reading the local macOS AddressBook SQLite database
directly (same Full Disk Access already required for chat.db). This is fast enough
to refresh on every run. The legacy AppleScript path is kept as a fallback under
--refresh-contacts for environments where the AddressBook DB can't be read.

Usage:
    python3 export_messages.py               # incremental (default)
    python3 export_messages.py --full        # full re-export, overwrite all files
    python3 export_messages.py --list        # list all conversations with message counts
    python3 export_messages.py --check-contacts  # diagnose contact resolution coverage
"""

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ── Configuration ──────────────────────────────────────────────────────────────
MESSAGES_DB    = Path.home() / "Library/Messages/chat.db"
TMP_DB         = Path("/tmp/messages_export_chat.db")
STATE_FILE     = Path.home() / ".messages_export_state.json"
CONTACTS_CACHE = Path.home() / ".messages_export_contacts.json"
CONTACTS_MAX_AGE_DAYS = 7   # re-query Contacts.app after this many days

# macOS AddressBook databases. Contacts are usually split across per-account
# "source" DBs, so all matching files are read and merged. The top-level DB
# exists on some setups; the Sources/*/ DBs cover iCloud/Exchange/etc accounts.
ADDRESSBOOK_DIR = Path.home() / "Library/Application Support/AddressBook"

GDRIVE_ACCOUNT = "joe@joemoser.com"
OUTPUT_SUBPATH = "My Drive/Private/Messages"

APPLE_EPOCH = 978307200  # seconds between Unix epoch (1970) and Apple epoch (2001)


# ── Output directory detection ─────────────────────────────────────────────────
def find_output_dir() -> Path:
    cloudstore = Path.home() / "Library/CloudStorage"
    for entry in cloudstore.iterdir():
        if entry.name.startswith(f"GoogleDrive-{GDRIVE_ACCOUNT}"):
            target = entry / OUTPUT_SUBPATH
            target.mkdir(parents=True, exist_ok=True)
            return target
    raise RuntimeError(
        f"Google Drive not found for {GDRIVE_ACCOUNT}. "
        "Is Google Drive Desktop installed and signed in?"
    )


# ── Helpers ────────────────────────────────────────────────────────────────────
def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def apple_ts_to_str(ns: int) -> str:
    """Convert Apple nanosecond epoch to a human-readable local datetime string."""
    unix_ts = ns / 1_000_000_000 + APPLE_EPOCH
    return datetime.fromtimestamp(unix_ts).strftime("%Y-%m-%d %H:%M:%S")


def safe_filename(name: str) -> str:
    """Convert an arbitrary string to a safe, reasonably short filename stem."""
    name = re.sub(r"[^\w\s\-+@.]", "_", name)
    return name.strip().replace(" ", "_")[:80]


def normalize_phone(phone: str) -> str:
    return re.sub(r"\D", "", phone)


# ── Contact resolution ─────────────────────────────────────────────────────────
def find_addressbook_dbs() -> list[Path]:
    """Return every AddressBook-v22.abcddb file (top-level + per-account sources)."""
    dbs: list[Path] = []
    top = ADDRESSBOOK_DIR / "AddressBook-v22.abcddb"
    if top.exists():
        dbs.append(top)
    dbs.extend(sorted((ADDRESSBOOK_DIR / "Sources").glob("*/AddressBook-v22.abcddb")))
    return dbs


def _add_identifier(contact_map: dict[str, str], name: str, identifier: str) -> None:
    """Insert one phone/email identifier into the map using the standard
    normalization (shared with the AppleScript path so matching is identical)."""
    name = name.strip()
    identifier = (identifier or "").strip()
    if not name or not identifier:
        return
    if "@" in identifier:
        contact_map[identifier.lower()] = name
    else:
        digits = normalize_phone(identifier)
        if digits:
            contact_map[digits] = name
            if len(digits) > 10:
                contact_map[digits[-10:]] = name


def fetch_contacts_from_addressbook() -> dict[str, str]:
    """
    Read the local macOS AddressBook SQLite database(s) directly and return
    {normalized_id -> "Full Name"}. Fast (milliseconds) and needs no Apple Events,
    only the Full Disk Access this script already requires for chat.db.

    Returns {} if no DB is found or readable (e.g. missing Full Disk Access),
    so callers can fall back to the cache / AppleScript path.
    """
    dbs = find_addressbook_dbs()
    if not dbs:
        log("No AddressBook database found "
            f"under {ADDRESSBOOK_DIR} — falling back to contacts cache.")
        return {}

    def _name(first, last, org) -> str:
        full = f"{(first or '').strip()} {(last or '').strip()}".strip()
        return full or (org or "").strip()

    contact_map: dict[str, str] = {}
    sources_read = 0
    for db in dbs:
        try:
            # Read-only + immutable: never locks the live DB, ignores WAL.
            conn = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
            cur = conn.cursor()
            cur.execute("""
                SELECT r.ZFIRSTNAME, r.ZLASTNAME, r.ZORGANIZATION, p.ZFULLNUMBER
                FROM ZABCDRECORD r
                JOIN ZABCDPHONENUMBER p ON p.ZOWNER = r.Z_PK
                WHERE p.ZFULLNUMBER IS NOT NULL
            """)
            for first, last, org, phone in cur.fetchall():
                _add_identifier(contact_map, _name(first, last, org), phone)

            cur.execute("""
                SELECT r.ZFIRSTNAME, r.ZLASTNAME, r.ZORGANIZATION, e.ZADDRESS
                FROM ZABCDRECORD r
                JOIN ZABCDEMAILADDRESS e ON e.ZOWNER = r.Z_PK
                WHERE e.ZADDRESS IS NOT NULL
            """)
            for first, last, org, email in cur.fetchall():
                _add_identifier(contact_map, _name(first, last, org), email)

            conn.close()
            sources_read += 1
        except Exception as e:
            log(f"Skipping AddressBook DB {db}: {e}")

    if contact_map:
        log(f"Loaded {len(contact_map)} contact entries from AddressBook "
            f"({sources_read} source DB{'s' if sources_read != 1 else ''})")
    else:
        log("AddressBook DBs found but yielded no contacts.")
    return contact_map


APPLESCRIPT_CONTACTS = r"""
set output to ""
with timeout of 3600 seconds
tell application "Contacts"
    repeat with p in every person
        set firstName to first name of p
        set lastName to last name of p
        if firstName is missing value then set firstName to ""
        if lastName is missing value then set lastName to ""
        set fullName to (firstName & " " & lastName)
        set fullName to my trim(fullName)
        if fullName is "" then
            try
                set fullName to organization of p
                if fullName is missing value then set fullName to ""
            end try
        end if
        if fullName is not "" then
            repeat with ph in phones of p
                set phoneVal to value of ph
                set output to output & fullName & "|" & phoneVal & linefeed
            end repeat
            repeat with em in emails of p
                set emailVal to value of em
                set output to output & fullName & "|" & emailVal & linefeed
            end repeat
        end if
    end repeat
end tell
end timeout
return output

on trim(str)
    set str to str as string
    if str starts with " " then set str to text 2 thru -1 of str
    if str ends with " " then set str to text 1 thru -2 of str
    return str
end trim
"""


def fetch_contacts_from_app() -> dict[str, str]:
    """
    Query Contacts.app via AppleScript and return {normalized_id -> "Full Name"}.
    Works without Full Disk Access — uses the official Contacts framework.
    May prompt the user once for Contacts permission if not yet granted.

    NOTE: For large address books this can take 20-60 minutes. Only called when
    force_refresh=True (i.e. --refresh-contacts mode). Normal exports use the cache.
    """
    try:
        # Ensure Contacts.app is running before sending Apple Events (error -600
        # occurs when the target app isn't launched in the current session context).
        subprocess.run(["open", "-a", "Contacts"], check=False, timeout=10)
        import time; time.sleep(2)

        log("Contacts.app query started — may take 20-60 min for large address books ...")
        result = subprocess.run(
            ["osascript", "-e", APPLESCRIPT_CONTACTS],
            capture_output=True, text=True, timeout=3700  # 3600s AppleEvent timeout + 100s buffer
        )
        if result.returncode != 0:
            log(f"Contacts.app query failed: {result.stderr.strip()}")
            return {}

        contact_map: dict[str, str] = {}
        for line in result.stdout.splitlines():
            line = line.strip()
            if "|" not in line:
                continue
            name, identifier = line.split("|", 1)
            name = name.strip()
            identifier = identifier.strip()
            if not name or not identifier:
                continue
            if "@" in identifier:
                # Store email as-is (lowercased for matching)
                contact_map[identifier.lower()] = name
            else:
                digits = normalize_phone(identifier)
                if digits:
                    contact_map[digits] = name
                    if len(digits) > 10:
                        contact_map[digits[-10:]] = name

        log(f"Loaded {len(contact_map)} contact entries from Contacts.app")
        return contact_map

    except subprocess.TimeoutExpired:
        log("Contacts.app query timed out (3700s) — phone numbers won't be resolved this run")
        return {}
    except Exception as e:
        log(f"Contacts.app query error: {e}")
        return {}


def load_contact_map(force_refresh: bool = False, output_dir: Path | None = None) -> dict[str, str]:
    """
    Return the phone/email → name map.

    Default path: read the AddressBook SQLite DB directly (fast, every run). On
    success, refresh the JSON cache and the _contacts.json reference, then return.

    Fallbacks, in order, when the AddressBook DB yields nothing:
      1. force_refresh=True (--refresh-contacts): query Contacts.app via AppleScript
         (legacy; can take 20-60 min for large address books).
      2. An existing JSON cache (used as-is, even if stale, with a hint logged).
      3. {} — export proceeds with raw phone numbers/emails.

    If output_dir is provided and contacts are (re)loaded, a human-readable
    _contacts.json is written there for reference alongside message threads.
    """
    import time

    def _persist(contact_map: dict[str, str]) -> None:
        CONTACTS_CACHE.write_text(json.dumps(contact_map, indent=2))
        if output_dir:
            _write_contacts_reference(contact_map, output_dir)

    # Default: read AddressBook DB directly (skip when an explicit AppleScript
    # refresh was requested).
    if not force_refresh:
        contact_map = fetch_contacts_from_addressbook()
        if contact_map:
            _persist(contact_map)
            return contact_map
        log("AddressBook DB unavailable — falling back to cache "
            "(run --refresh-contacts to rebuild from Contacts.app).")

    # Legacy AppleScript path (explicit --refresh-contacts).
    if force_refresh:
        log("--refresh-contacts: querying Contacts.app via AppleScript "
            "(may take 20-60 min for large address books) ...")
        contact_map = fetch_contacts_from_app()
        if contact_map:
            _persist(contact_map)
            log(f"Contacts cache updated: {len(contact_map)} entries → {CONTACTS_CACHE}")
            return contact_map

    # Cache fallback.
    if CONTACTS_CACHE.exists():
        age_days = (time.time() - CONTACTS_CACHE.stat().st_mtime) / 86400
        if age_days > CONTACTS_MAX_AGE_DAYS:
            log(f"Contacts cache is {age_days:.0f} days old (> {CONTACTS_MAX_AGE_DAYS}). "
                f"Using stale cache.")
        contact_map = json.loads(CONTACTS_CACHE.read_text())
        log(f"Loaded {len(contact_map)} contacts from cache")
        return contact_map

    log("No AddressBook DB and no contacts cache — exporting with identifiers unresolved.")
    return {}


def _write_contacts_reference(contact_map: dict[str, str], output_dir: Path) -> None:
    """
    Write a tidy _contacts.json to the output directory for use as a human reference.
    Format: { "Chris Frasco": ["+14132191442", "4132191442"], ... }
    Sorted alphabetically by name.
    """
    # Invert: name → [identifiers], deduplicating
    by_name: dict[str, list[str]] = {}
    for identifier, name in contact_map.items():
        by_name.setdefault(name, [])
        if identifier not in by_name[name]:
            by_name[name].append(identifier)

    # Sort identifiers: phone numbers first, then emails
    for name in by_name:
        by_name[name].sort(key=lambda x: (0 if "@" not in x else 1, x))

    ordered = dict(sorted(by_name.items()))
    out_path = output_dir / "_contacts.json"
    out_path.write_text(json.dumps(ordered, indent=2, ensure_ascii=False))
    log(f"Contacts reference written → {out_path} ({len(ordered)} people)")


def resolve_handle(handle_id: str, contacts: dict[str, str]) -> str:
    """Resolve a phone number or email handle to a contact display name."""
    if "@" in handle_id:
        return contacts.get(handle_id.lower()) or handle_id
    digits = normalize_phone(handle_id)
    return (
        contacts.get(digits)
        or (len(digits) > 10 and contacts.get(digits[-10:]))
        or handle_id
    )


# ── State (incremental tracking) ───────────────────────────────────────────────
def load_state() -> dict[str, int]:
    """Return {chat_guid: last_message_date_ns} from the state file."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_state(state: dict[str, int]) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ── Core export ────────────────────────────────────────────────────────────────
def export_messages(full: bool = False, refresh_contacts: bool = False) -> None:
    if not MESSAGES_DB.exists():
        log("ERROR: ~/Library/Messages/chat.db not found")
        sys.exit(1)

    output_dir = find_output_dir()
    log(f"Output directory: {output_dir}")

    log("Copying chat.db to /tmp for safe read ...")
    shutil.copy2(MESSAGES_DB, TMP_DB)

    contacts = load_contact_map(force_refresh=refresh_contacts, output_dir=output_dir)

    state     = {} if full else load_state()
    new_state = dict(state)

    conn = sqlite3.connect(TMP_DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # All chats with their participants
    cur.execute("""
        SELECT
            c.ROWID   AS chat_id,
            c.guid    AS chat_guid,
            c.display_name,
            GROUP_CONCAT(DISTINCT h.id) AS participants
        FROM chat c
        JOIN chat_handle_join chj ON c.ROWID = chj.chat_id
        JOIN handle h             ON h.ROWID  = chj.handle_id
        GROUP BY c.ROWID
        ORDER BY c.ROWID
    """)
    chats = cur.fetchall()
    log(f"Found {len(chats)} conversations")

    total_written = 0

    for chat in chats:
        chat_id   = chat["chat_id"]
        chat_guid = chat["chat_guid"]
        participants_raw = chat["participants"] or ""
        participant_list = [p.strip() for p in participants_raw.split(",") if p.strip()]

        # Build a human-readable display name
        if chat["display_name"]:
            display = chat["display_name"]
        elif len(participant_list) == 1:
            display = resolve_handle(participant_list[0], contacts)
        else:
            display = ", ".join(resolve_handle(p, contacts) for p in participant_list)

        filename   = safe_filename(display) + ".txt"
        out_path   = output_dir / filename
        last_date  = state.get(chat_guid, 0) if not full else 0

        cur.execute("""
            SELECT
                m.date,
                CASE WHEN m.is_from_me = 1
                     THEN 'Me'
                     ELSE COALESCE(h.id, 'Unknown')
                END AS sender,
                COALESCE(m.text, '[attachment/reaction]') AS body
            FROM message m
            JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
            LEFT JOIN handle h         ON m.handle_id = h.ROWID
            WHERE cmj.chat_id = ? AND m.date > ?
            ORDER BY m.date ASC
        """, (chat_id, last_date))

        messages = cur.fetchall()
        if not messages:
            continue

        write_mode = "w" if full else "a"
        with open(out_path, write_mode, encoding="utf-8") as f:
            if full:
                f.write(f"# Conversation: {display}\n")
                f.write(f"# Participants: {participants_raw}\n")
                f.write(f"# Exported: {datetime.now():%Y-%m-%d %H:%M:%S}\n\n")
            for msg in messages:
                ts_str = apple_ts_to_str(msg["date"])
                body   = (msg["body"] or "").replace("\n", " ").replace("\r", " ")
                sender = resolve_handle(msg["sender"], contacts) if msg["sender"] != "Me" else "Me"
                f.write(f"[{ts_str}] {sender}: {body}\n")

        new_state[chat_guid] = max(m["date"] for m in messages)
        total_written += len(messages)
        log(f"  {display}: +{len(messages)} → {filename}")

    conn.close()
    TMP_DB.unlink(missing_ok=True)
    save_state(new_state)

    mode_label = "full re-export" if full else "incremental export"
    log(f"Done ({mode_label}). {total_written} messages written across {len(chats)} conversations.")


def list_conversations() -> None:
    """Print a summary of all conversations, sorted by most recent activity."""
    if not MESSAGES_DB.exists():
        log("ERROR: ~/Library/Messages/chat.db not found")
        sys.exit(1)

    shutil.copy2(MESSAGES_DB, TMP_DB)
    conn = sqlite3.connect(TMP_DB)
    cur  = conn.cursor()
    contacts = load_contact_map()

    cur.execute("""
        SELECT
            c.display_name,
            GROUP_CONCAT(DISTINCT h.id) AS participants,
            COUNT(m.ROWID)              AS msg_count,
            MIN(m.date)                 AS first_date,
            MAX(m.date)                 AS last_date
        FROM chat c
        JOIN chat_handle_join chj ON c.ROWID = chj.chat_id
        JOIN handle h             ON h.ROWID  = chj.handle_id
        JOIN chat_message_join cmj ON c.ROWID  = cmj.chat_id
        JOIN message m            ON m.ROWID   = cmj.message_id
        GROUP BY c.ROWID
        ORDER BY last_date DESC
    """)
    rows = cur.fetchall()
    conn.close()
    TMP_DB.unlink(missing_ok=True)

    print(f"\n{'Messages':>8}  {'First':^19}  {'Last':^19}  Conversation")
    print("-" * 90)
    for display_name, participants, count, first, last in rows:
        parts = [p.strip() for p in (participants or "").split(",") if p.strip()]
        if display_name:
            name = display_name
        elif len(parts) == 1:
            name = resolve_handle(parts[0], contacts)
        else:
            name = ", ".join(resolve_handle(p, contacts) for p in parts)
        first_str = apple_ts_to_str(first) if first else "?"
        last_str  = apple_ts_to_str(last)  if last  else "?"
        print(f"{count:>8}  {first_str}  {last_str}  {name}")
    print()


# ── Diagnostics ──────────────────────────────────────────────────────────────
def check_contacts() -> None:
    """Report contact-resolution coverage: how many message handles map to names."""
    contacts = load_contact_map()
    print(f"\nLoaded {len(contacts)} contact identifiers.")
    if contacts:
        print("Sample entries (identifier → name):")
        for ident, name in list(contacts.items())[:5]:
            print(f"  {ident} → {name}")

    if not MESSAGES_DB.exists():
        log("ERROR: ~/Library/Messages/chat.db not found")
        sys.exit(1)

    shutil.copy2(MESSAGES_DB, TMP_DB)
    conn = sqlite3.connect(TMP_DB)
    cur  = conn.cursor()
    cur.execute("SELECT DISTINCT id FROM handle WHERE id IS NOT NULL")
    handles = [row[0] for row in cur.fetchall()]
    conn.close()
    TMP_DB.unlink(missing_ok=True)

    unresolved = [h for h in handles if resolve_handle(h, contacts) == h]
    resolved   = len(handles) - len(unresolved)
    total      = len(handles) or 1
    print(f"\nMessage handles: {len(handles)} unique")
    print(f"  Resolved to a name: {resolved} ({resolved * 100 // total}%)")
    print(f"  Unresolved:         {len(unresolved)}")
    if unresolved:
        print("\nFirst unresolved handles (not found in contacts):")
        for h in unresolved[:25]:
            print(f"  {h}")
        if len(unresolved) > 25:
            print(f"  ... and {len(unresolved) - 25} more")
    print()


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Export iMessage/SMS history to Google Drive"
    )
    parser.add_argument(
        "--full", action="store_true",
        help="Full re-export: overwrite all files instead of appending new messages"
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List all conversations with message counts, don't export anything"
    )
    parser.add_argument(
        "--refresh-contacts", action="store_true",
        help="Legacy fallback: force re-query of Contacts.app via AppleScript "
             "(slow). Normally contacts are read from the AddressBook DB directly."
    )
    parser.add_argument(
        "--check-contacts", action="store_true",
        help="Diagnose contact resolution: report how many message handles "
             "resolve to names, and list unresolved ones. Exports nothing."
    )
    args = parser.parse_args()

    if args.check_contacts:
        check_contacts()
    elif args.list:
        list_conversations()
    else:
        export_messages(full=args.full, refresh_contacts=args.refresh_contacts)
