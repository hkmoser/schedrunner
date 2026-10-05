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
import errno
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ── Configuration ──────────────────────────────────────────────────────────────
MESSAGES_DB    = Path.home() / "Library/Messages/chat.db"
TMP_DB         = Path("/tmp/messages_export_chat.db")
STATE_FILE     = Path.home() / ".messages_export_state.json"
CONTACTS_CACHE = Path.home() / ".messages_export_contacts.json"
SOURCE_MARKER  = Path.home() / ".messages_export_source.json"
CONTACTS_REF_HASH = Path.home() / ".messages_export_contacts_ref.sha256"
FORMAT_FILE    = Path.home() / ".messages_export_format"

# Bump when the text written for a message changes. The next scheduled run
# then re-exports everything once (--full) so old files get the new format.
#   2: reply tags ("[replying to Jane: ...]")
#   3: reactions ("[reacted ❤️ to Jane: ...]")
FORMAT_VERSION = 3

# Tapbacks: message.associated_message_type 2000-2007 adds one, 3000-3007
# removes it. 2006 is a custom emoji (in associated_message_emoji).
TAPBACKS = {0: "❤️", 1: "👍", 2: "👎", 3: "😂", 4: "‼️", 5: "❓", 7: "a sticker"}
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


def ensure_messages_db_readable() -> None:
    """Exit with an ACCURATE message if chat.db is missing or unreadable.

    A bare Path.exists() check reports "not found" even when the file is present
    but blocked by Full Disk Access — which masked a week of silent FDA failures.
    Actually opening the file surfaces the real cause (FileNotFoundError vs
    PermissionError) so the log says what's wrong and nothing fails quietly.
    """
    try:
        with open(MESSAGES_DB, "rb") as f:
            f.read(16)
    except FileNotFoundError:
        log("ERROR: ~/Library/Messages/chat.db not found. "
            "Is this a Mac signed in to Messages?")
        sys.exit(1)
    except PermissionError:
        log("ERROR: cannot read ~/Library/Messages/chat.db — macOS Full Disk "
            "Access (FDA) is required, and nothing in the launch chain has it.")
        log("       Grant FDA to whatever starts this job (your scheduler), or "
            "run it via ./install_launchagent.sh and grant FDA to the interpreter.")
        log("       Nothing was exported. See README → Full Disk Access.")
        sys.exit(2)
    except OSError as e:
        log(f"ERROR: cannot read ~/Library/Messages/chat.db: {e}")
        sys.exit(2)


# Google Drive's File Provider returns EDEADLK ("Resource deadlock avoided")
# when a write lands on a file it's busy with (mid-sync), and, persistently, when
# the file is an online-only placeholder that this process can't download. The
# first kind clears on its own, so retry briefly; the second never does, so
# conversation files fall back to being rewritten whole (see export_messages).
DRIVE_RETRY_DELAYS = (1, 3)


def enable_dataless_materialization() -> None:
    """Let this process download online-only (dataless) files on access.

    macOS can disable this per process, in which case touching a placeholder
    fails with EDEADLK instead of fetching it. Best effort: if the call isn't
    available, the rewrite fallback still covers it.
    """
    if sys.platform != "darwin":
        return
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        # <sys/resource.h>: IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES = 3,
        # IOPOL_SCOPE_PROCESS = 0, IOPOL_MATERIALIZE_DATALESS_FILES_ON = 2
        if libc.setiopolicy_np(3, 0, 2) != 0:
            log(f"Note: couldn't enable online-only file downloads "
                f"(errno {ctypes.get_errno()}); continuing.")
    except Exception as e:
        log(f"Note: couldn't enable online-only file downloads ({e}); continuing.")


def drive_write(fn):
    """Run fn(), retrying while Google Drive reports the file as busy (EDEADLK)."""
    for delay in DRIVE_RETRY_DELAYS:
        try:
            return fn()
        except OSError as e:
            if e.errno != errno.EDEADLK:
                raise
            log(f"  Google Drive busy ({e.strerror}) — retrying in {delay}s ...")
            time.sleep(delay)
    return fn()


def drive_replace(path: Path, text: str) -> None:
    """Write text to a temp file beside path, then swap it in.

    Unlike opening the synced file, this never needs Drive to download the old
    contents, so it works on online-only placeholders.
    """
    tmp_path = path.with_name(f".{path.name}.tmp")

    def _write() -> None:
        tmp_path.write_text(text, encoding="utf-8")
        os.replace(tmp_path, path)

    drive_write(_write)


# ── Source snapshot / change detection ──────────────────────────────────────────
def snapshot_db(src: Path, dst: Path) -> None:
    """Copy a live SQLite DB to dst, including what's still in its -wal file.

    A plain file copy of the main DB misses the newest messages: SQLite keeps
    them in the -wal journal until the app folds them in. The backup API reads
    the DB the way SQLite sees it, journal included.
    """
    dst.unlink(missing_ok=True)
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    target = sqlite3.connect(dst)
    try:
        source.backup(target)
    finally:
        source.close()
        target.close()


def source_signature(db: Path) -> list:
    """Size and modified time of the DB and its -wal: changes on any new message."""
    sig = []
    for path in (db, db.with_name(db.name + "-wal")):
        try:
            st = path.stat()
        except FileNotFoundError:
            st = None
        # An empty -wal holds nothing; treat it like a missing one. (Opening the
        # DB can create an empty one, which would otherwise defeat the skip.)
        if st is None or st.st_size == 0:
            sig.append([path.name, None, None])
        else:
            sig.append([path.name, st.st_mtime_ns, st.st_size])
    return sig


def source_unchanged(signature: list, marker: Path) -> bool:
    """True if the DB looks exactly as it did after the last successful run."""
    try:
        return json.loads(marker.read_text()) == signature
    except (OSError, ValueError):
        return False


def record_source(signature: list, marker: Path) -> None:
    marker.write_text(json.dumps(signature))


def table_columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    return {row[1] for row in cur.execute(f"PRAGMA table_info({table})")}


def quote_ref(quoted: tuple[str, str] | None) -> str:
    """'Jane: "original text"' for another message, shortened to fit on a line.

    quoted is (sender, text) of the original, or None if it isn't in the
    database any more (deleted, or from before this device's history).
    """
    if quoted is None:
        return "an earlier message"
    sender, text = quoted
    text = " ".join((text or "").split())
    if len(text) > 60:
        text = text[:57].rstrip() + "..."
    return f'{sender}: "{text}"'


def reply_tag(quoted: tuple[str, str] | None) -> str:
    """' [replying to Jane: "original text"]' for a message that quotes another."""
    return f" [replying to {quote_ref(quoted)}]"


def reaction_text(kind: int, emoji: str | None, quoted: tuple[str, str] | None) -> str:
    """'[reacted ❤️ to Jane: "original text"]' for a tapback row.

    kind is associated_message_type: 2000-2007 adds, 3000-3007 removes.
    """
    added = kind < 3000
    symbol = emoji if kind % 1000 == 6 and emoji else TAPBACKS.get(kind % 1000, "a reaction")
    if added:
        return f"[reacted {symbol} to {quote_ref(quoted)}]"
    return f"[removed {symbol} from {quote_ref(quoted)}]"


def is_reaction(kind: int | None) -> bool:
    return kind is not None and (2000 <= kind <= 2999 or 3000 <= kind <= 3999)


def format_outdated(marker: Path, version: int = FORMAT_VERSION) -> bool:
    """True if the files on Drive were written by an older output format."""
    try:
        return int(marker.read_text().strip()) < version
    except (OSError, ValueError):
        return True


def record_format(marker: Path, version: int = FORMAT_VERSION) -> None:
    marker.write_text(f"{version}\n")


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
    def _persist(contact_map: dict[str, str]) -> None:
        CONTACTS_CACHE.write_text(json.dumps(contact_map, indent=2))
        if output_dir:
            # Reference copy only: a Drive hiccup here shouldn't stop the export.
            try:
                _write_contacts_reference(contact_map, output_dir)
            except OSError as e:
                log(f"WARNING: couldn't write _contacts.json ({e}); continuing.")

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
    text = json.dumps(ordered, indent=2, ensure_ascii=False)

    # Skip the write (and Drive's re-upload) when nothing changed. The hash of
    # the last write is kept locally so this never has to read the Drive copy.
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    try:
        unchanged = CONTACTS_REF_HASH.read_text().strip() == digest and out_path.exists()
    except OSError:
        unchanged = False
    if unchanged:
        return

    drive_replace(out_path, text)
    CONTACTS_REF_HASH.write_text(digest)
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
    ensure_messages_db_readable()

    if not full and format_outdated(FORMAT_FILE):
        log(f"Output format changed (now v{FORMAT_VERSION}) — re-exporting everything once.")
        full = True

    # Taken before the snapshot: anything that changes mid-run makes the next
    # run see a different signature and look again.
    signature = source_signature(MESSAGES_DB)
    if not (full or refresh_contacts) and source_unchanged(signature, SOURCE_MARKER):
        log("chat.db unchanged since last run — nothing to do.")
        return

    enable_dataless_materialization()

    output_dir = find_output_dir()
    log(f"Output directory: {output_dir}")

    log("Snapshotting chat.db to /tmp for safe read ...")
    snapshot_db(MESSAGES_DB, TMP_DB)

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

    msg_cols = table_columns(cur, "message")
    reply_col = (", m.thread_originator_guid AS reply_to"
                 if {"guid", "thread_originator_guid"} <= msg_cols
                 else ", NULL AS reply_to")
    if {"guid", "associated_message_type", "associated_message_guid"} <= msg_cols:
        reply_col += (", m.associated_message_type AS react_kind"
                      ", m.associated_message_guid AS react_target")
        reply_col += (", m.associated_message_emoji AS react_emoji"
                      if "associated_message_emoji" in msg_cols else ", NULL AS react_emoji")
    else:
        reply_col += ", NULL AS react_kind, NULL AS react_target, NULL AS react_emoji"

    MESSAGES_SQL = """
        SELECT
            cmj.chat_id,
            m.date,
            CASE WHEN m.is_from_me = 1
                 THEN 'Me'
                 ELSE COALESCE(h.id, 'Unknown')
            END AS sender,
            COALESCE(m.text, '[attachment/reaction]') AS body
            {reply_col}
        FROM message m
        JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
        LEFT JOIN handle h         ON m.handle_id = h.ROWID
        WHERE cmj.chat_id IN ({ids}) AND m.date > ?
        ORDER BY m.date ASC
    """

    def fetch_messages(chat_ids: list[int], since: int) -> list[sqlite3.Row]:
        sql = MESSAGES_SQL.format(ids=",".join("?" * len(chat_ids)), reply_col=reply_col)
        cur.execute(sql, (*chat_ids, since))
        return cur.fetchall()

    def sender_name(raw: str) -> str:
        return resolve_handle(raw, contacts) if raw != "Me" else "Me"

    # Inline replies (macOS 11+): thread_originator_guid points at the message
    # that started the reply thread. Look the original up by guid; it may be in
    # an earlier export, so this goes to the database, not this batch.
    quoted_cache: dict[str, tuple[str, str] | None] = {}

    def quoted(guid: str) -> tuple[str, str] | None:
        if guid not in quoted_cache:
            q = conn.cursor()
            q.execute("""
                SELECT CASE WHEN m.is_from_me = 1 THEN 'Me'
                            ELSE COALESCE(h.id, 'Unknown') END,
                       COALESCE(m.text, '[attachment]')
                FROM message m
                LEFT JOIN handle h ON m.handle_id = h.ROWID
                WHERE m.guid = ?
            """, (guid,))
            row = q.fetchone()
            quoted_cache[guid] = None if row is None else (sender_name(row[0]), row[1])
        return quoted_cache[guid]

    def render(messages: list[sqlite3.Row]) -> str:
        lines = []
        for msg in messages:
            ts_str = apple_ts_to_str(msg["date"])
            body   = (msg["body"] or "").replace("\n", " ").replace("\r", " ")
            sender = sender_name(msg["sender"])
            tag    = reply_tag(quoted(msg["reply_to"])) if msg["reply_to"] else ""
            if is_reaction(msg["react_kind"]) and msg["react_target"]:
                # "p:0/GUID" or "bp:GUID": the reacted-to message's guid is last.
                target = msg["react_target"].rsplit("/", 1)[-1].rsplit(":", 1)[-1]
                body = reaction_text(msg["react_kind"], msg["react_emoji"], quoted(target))
                tag = ""
            lines.append(f"[{ts_str}] {sender}{tag}: {body}\n")
        return "".join(lines)

    def header(display: str, participants_raw: str) -> str:
        return (f"# Conversation: {display}\n"
                f"# Participants: {participants_raw}\n"
                f"# Exported: {datetime.now():%Y-%m-%d %H:%M:%S}\n\n")

    # Resolve every chat's name up front. Different chats can share a filename
    # (e.g. an SMS and an iMessage thread with the same person); the rewrite
    # fallback needs all of them to rebuild that file without losing any.
    resolved = []
    by_filename: dict[str, list[int]] = {}
    for chat in chats:
        participants_raw = chat["participants"] or ""
        participant_list = [p.strip() for p in participants_raw.split(",") if p.strip()]

        # Build a human-readable display name
        if chat["display_name"]:
            display = chat["display_name"]
        elif len(participant_list) == 1:
            display = resolve_handle(participant_list[0], contacts)
        else:
            display = ", ".join(resolve_handle(p, contacts) for p in participant_list)

        filename = safe_filename(display) + ".txt"
        resolved.append((chat, display, participants_raw, filename))
        by_filename.setdefault(filename, []).append(chat["chat_id"])
    guid_by_id = {chat["chat_id"]: chat["chat_guid"] for chat in chats}

    total_written = 0
    rewritten = 0
    done_full: set[str] = set()
    failed: list[str] = []

    for chat, display, participants_raw, filename in resolved:
        chat_id   = chat["chat_id"]
        chat_guid = chat["chat_guid"]
        out_path  = output_dir / filename
        # new_state, not state: a rewrite earlier in this run may already
        # have covered this chat (it shares a file with another one).
        last_date = new_state.get(chat_guid, 0) if not full else 0

        messages = fetch_messages([chat_id], last_date)
        if not messages:
            continue

        try:
            if full:
                # One write per file, covering every chat that shares it.
                if filename in done_full:
                    continue
                group = by_filename[filename]
                if len(group) > 1:
                    messages = fetch_messages(group, 0)
                drive_replace(out_path, header(display, participants_raw) + render(messages))
                done_full.add(filename)
                for gid in group:
                    dates = [m["date"] for m in messages if m["chat_id"] == gid]
                    if dates:
                        new_state[guid_by_id[gid]] = max(dates)
                total_written += len(messages)
                log(f"  {display}: +{len(messages)} → {filename}")
                continue
            else:
                text = render(messages)
                try:
                    with open(out_path, "a", encoding="utf-8") as f:
                        f.write(text)
                except OSError as e:
                    if e.errno != errno.EDEADLK:
                        raise
                    # Online-only placeholder: appending needs the old contents,
                    # which Drive won't hand over. Rebuild the whole file from
                    # chat.db (every chat that shares this filename) and swap it in.
                    group = by_filename[filename]
                    everything = fetch_messages(group, 0)
                    drive_replace(out_path, header(display, participants_raw) + render(everything))
                    added = sum(1 for m in everything
                                if m["date"] > new_state.get(guid_by_id[m["chat_id"]], 0))
                    for gid in group:
                        dates = [m["date"] for m in everything if m["chat_id"] == gid]
                        if dates:
                            new_state[guid_by_id[gid]] = max(dates)
                    rewritten += 1
                    total_written += added
                    log(f"  {display}: +{added} → {filename} "
                        f"(online-only in Drive; rewrote from chat.db)")
                    continue
        except OSError as e:
            # Leave this chat's state alone so the next run picks these up.
            log(f"  ERROR: {display}: couldn't write {filename} ({e}); will retry next run.")
            failed.append(display)
            continue

        new_state[chat_guid] = max(m["date"] for m in messages)
        total_written += len(messages)
        log(f"  {display}: +{len(messages)} → {filename}")

    conn.close()
    TMP_DB.unlink(missing_ok=True)
    save_state(new_state)

    mode_label = "full re-export" if full else "incremental export"
    log(f"Done ({mode_label}). {total_written} messages written across {len(chats)} conversations.")
    if rewritten:
        log(f"{rewritten} file(s) were online-only in Google Drive and were rewritten "
            f"from chat.db. Marking the Messages folder 'Available offline' avoids this.")
    if failed:
        log(f"ERROR: {len(failed)} conversation(s) failed to write: {', '.join(failed)}")
        sys.exit(1)
    record_source(signature, SOURCE_MARKER)
    if full:
        record_format(FORMAT_FILE)


def list_conversations() -> None:
    """Print a summary of all conversations, sorted by most recent activity."""
    ensure_messages_db_readable()

    snapshot_db(MESSAGES_DB, TMP_DB)
    conn = sqlite3.connect(TMP_DB)
    cur  = conn.cursor()
    contacts = load_contact_map()

    cur.execute("""
        SELECT
            c.display_name,
            GROUP_CONCAT(DISTINCT h.id) AS participants,
            COUNT(DISTINCT m.ROWID)     AS msg_count,
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

    ensure_messages_db_readable()

    snapshot_db(MESSAGES_DB, TMP_DB)
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
