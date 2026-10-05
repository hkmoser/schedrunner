#!/usr/bin/env python3
"""
whatsapp_export.py — Export WhatsApp conversations to Google Drive.

The WhatsApp counterpart to export_messages.py. Reads the local database kept by
WhatsApp for Mac (the App Store app) and writes one .txt file per chat, in the
same format as the Messages export, to a folder next to it:

    ~/Library/CloudStorage/GoogleDrive-joe@joemoser.com/My Drive/Private/WhatsApp/

Runs incrementally: tracks the last exported message per chat and only appends
new ones. Names are resolved through the macOS AddressBook first (so a person
gets the same name as in the Messages export), then WhatsApp's own contact and
profile names, then the phone number.

Only what WhatsApp for Mac has synced is exported. If the Mac app was linked
recently, older history may exist only on the phone.

Usage:
    python3 whatsapp_export.py           # incremental (default)
    python3 whatsapp_export.py --full    # full re-export, overwrite all files
    python3 whatsapp_export.py --list    # list chats with message counts
"""

import argparse
import errno
import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import export_messages as em
from export_messages import log, safe_filename, drive_write, drive_replace

# ── Configuration ──────────────────────────────────────────────────────────────
WHATSAPP_DB = (Path.home() / "Library/Group Containers"
               / "group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite")
TMP_DB      = Path("/tmp/whatsapp_export_chat.db")
STATE_FILE  = Path.home() / ".whatsapp_export_state.json"
REACTIONS_FILE = Path.home() / ".whatsapp_export_reactions.json"
SOURCE_MARKER = Path.home() / ".whatsapp_export_source.json"
FORMAT_FILE   = Path.home() / ".whatsapp_export_format"

# Bump when the text written for a message changes; the next run then
# re-exports everything once. (Separate from export_messages.FORMAT_VERSION so
# a Messages-only change doesn't rewrite every WhatsApp file.)
#   2: reply tags
#   3: reactions
FORMAT_VERSION = 3
OUTPUT_SUBPATH = "My Drive/Private/WhatsApp"

# Chats that aren't conversations: status updates, and the account's own JID.
SKIP_JIDS = {"status@broadcast"}

# ZWAMESSAGE.ZMESSAGETYPE values for messages with no text of their own.
MEDIA_LABELS = {
    1: "[image]", 2: "[video]", 3: "[voice message]", 4: "[contact card]",
    5: "[location]", 7: "[link]", 8: "[document]", 11: "[GIF]",
    14: "[deleted]", 15: "[sticker]",
}
SYSTEM_MESSAGE_TYPE = 6   # group events, encryption notices: skipped


# ── Setup ──────────────────────────────────────────────────────────────────────
def find_output_dir() -> Path:
    cloudstore = Path.home() / "Library/CloudStorage"
    for entry in cloudstore.iterdir():
        if entry.name.startswith(f"GoogleDrive-{em.GDRIVE_ACCOUNT}"):
            target = entry / OUTPUT_SUBPATH
            target.mkdir(parents=True, exist_ok=True)
            return target
    raise RuntimeError(
        f"Google Drive not found for {em.GDRIVE_ACCOUNT}. "
        "Is Google Drive Desktop installed and signed in?"
    )


def ensure_whatsapp_db_readable() -> None:
    """Exit with an accurate message if WhatsApp's database is missing or blocked.

    Exit codes match export_messages.py: 1 = not found, 2 = can't read (macOS
    privacy protection), so run_export.sh shows the Full Disk Access hint on 2.
    """
    try:
        with open(WHATSAPP_DB, "rb") as f:
            f.read(16)
    except FileNotFoundError:
        log(f"ERROR: WhatsApp database not found at {WHATSAPP_DB}. "
            "Is WhatsApp for Mac (the App Store app) installed and signed in?")
        sys.exit(1)
    except PermissionError:
        log("ERROR: cannot read the WhatsApp database — macOS blocks other apps' "
            "data without Full Disk Access. Grant FDA to whatever starts this job.")
        log("       Nothing was exported. See README → Full Disk Access.")
        sys.exit(2)
    except OSError as e:
        log(f"ERROR: cannot read the WhatsApp database: {e}")
        sys.exit(2)


def snapshot_db() -> sqlite3.Connection:
    """Copy the live DB (journal included) to /tmp and open the copy."""
    em.snapshot_db(WHATSAPP_DB, TMP_DB)
    conn = sqlite3.connect(TMP_DB)
    conn.row_factory = sqlite3.Row
    return conn


def check_schema(cur: sqlite3.Cursor) -> None:
    """Fail clearly if WhatsApp has changed the tables this script reads."""
    needed = {
        "ZWACHATSESSION": {"Z_PK", "ZCONTACTJID", "ZPARTNERNAME", "ZSESSIONTYPE"},
        "ZWAMESSAGE": {"Z_PK", "ZCHATSESSION", "ZISFROMME", "ZFROMJID", "ZTEXT",
                       "ZMESSAGEDATE", "ZMESSAGETYPE", "ZGROUPMEMBER"},
        "ZWAGROUPMEMBER": {"Z_PK", "ZMEMBERJID", "ZCONTACTNAME", "ZFIRSTNAME"},
    }
    for table, cols in needed.items():
        have = {row[1] for row in cur.execute(f"PRAGMA table_info({table})")}
        missing = cols - have
        if missing:
            log(f"ERROR: WhatsApp database layout has changed: {table} is missing "
                f"{', '.join(sorted(missing))}. This script needs updating.")
            sys.exit(1)


def has_column(cur: sqlite3.Cursor, table: str, column: str) -> bool:
    return any(row[1] == column for row in cur.execute(f"PRAGMA table_info({table})"))


def has_table(cur: sqlite3.Cursor, table: str) -> bool:
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


# ── Names ──────────────────────────────────────────────────────────────────────
def wa_ts_to_str(seconds: float) -> str:
    """WhatsApp stores Core Data timestamps: seconds since 2001-01-01."""
    return datetime.fromtimestamp(seconds + em.APPLE_EPOCH).strftime("%Y-%m-%d %H:%M:%S")


def jid_phone(jid: str | None) -> str | None:
    """'15551234567@s.whatsapp.net' → '+15551234567'. Other JID kinds → None."""
    if jid and jid.endswith("@s.whatsapp.net"):
        return "+" + jid.split("@", 1)[0]
    return None


class NameResolver:
    """AddressBook name → WhatsApp contact name → WhatsApp profile name → number."""

    def __init__(self, cur: sqlite3.Cursor, contacts: dict[str, str]):
        self.contacts = contacts
        self.push_names: dict[str, str] = {}
        if has_table(cur, "ZWAPROFILEPUSHNAME"):
            for row in cur.execute("SELECT ZJID, ZPUSHNAME FROM ZWAPROFILEPUSHNAME"):
                if row[0] and isinstance(row[1], str) and row[1].strip():
                    self.push_names[row[0]] = row[1].strip()

    def name(self, jid: str | None, *wa_names: str | None) -> str:
        phone = jid_phone(jid)
        if phone:
            resolved = em.resolve_handle(phone, self.contacts)
            if resolved != phone:
                return resolved
        for n in wa_names:
            if n and n.strip():
                return n.strip()
        if jid in self.push_names:
            return self.push_names[jid]
        return phone or (jid or "Unknown").split("@", 1)[0]


# ── Replies ────────────────────────────────────────────────────────────────────
def protobuf_fields(data: bytes) -> list[tuple[int, int, int | bytes]]:
    """Top-level (field number, wire type, value) entries of a protobuf blob.

    Varint and fixed-width values come back as ints, length-delimited ones as
    bytes. Stops at the first malformed byte and returns what it read.
    """
    out: list[tuple[int, int, int | bytes]] = []
    i, n = 0, len(data)

    def varint() -> int:
        nonlocal i
        shift = result = 0
        while i < n:
            b = data[i]
            i += 1
            result |= (b & 0x7F) << shift
            if not b & 0x80:
                return result
            shift += 7
        raise ValueError("truncated varint")

    try:
        while i < n:
            key = varint()
            field, wire = key >> 3, key & 7
            if wire == 0:
                out.append((field, 0, varint()))
            elif wire in (1, 5):
                size = 8 if wire == 1 else 4
                if i + size > n:
                    break
                out.append((field, wire, int.from_bytes(data[i:i + size], "little")))
                i += size
            elif wire == 2:
                length = varint()
                chunk = data[i:i + length]
                if len(chunk) != length:
                    break
                i += length
                out.append((field, 2, chunk))
            else:
                break
    except ValueError:
        pass
    return out


def protobuf_strings(data: bytes, depth: int = 0) -> list[str]:
    """Every UTF-8 string inside a protobuf blob, at any nesting level.

    WhatsApp keeps a reply's link to the quoted message (that message's
    ZSTANZAID) in ZWAMEDIAITEM.ZMETADATA, a protobuf. Field numbers aren't
    documented and could change, so rather than read one field, collect every
    string and let the caller match them against real message IDs.
    """
    out: list[str] = []
    for _, wire, value in protobuf_fields(data):
        if wire != 2:
            continue
        if depth < 6:
            out.extend(protobuf_strings(value, depth + 1))
        try:
            out.append(value.decode("utf-8"))
        except UnicodeDecodeError:
            pass
    return out


def parse_reactions(blob: bytes) -> dict[str, tuple[str, int]]:
    """{reactor: (emoji, unix ms)} for one message, from ZWAMESSAGEINFO.ZRECEIPTINFO.

    Layout, worked out from a real WhatsApp for Mac database: field 7 holds the
    reactions, one field-1 entry each, with 2 = reactor JID (absent when the
    reaction is your own), 3 = emoji, 4 = time in unix milliseconds. The
    reactor is "me" for your own reactions.
    """
    found: dict[str, tuple[str, int]] = {}
    for field, wire, container in protobuf_fields(blob):
        if field != 7 or wire != 2:
            continue
        for sub, sub_wire, entry in protobuf_fields(container):
            if sub != 1 or sub_wire != 2:
                continue
            values = {f: v for f, _, v in protobuf_fields(entry)}
            try:
                emoji = values[3].decode("utf-8") if isinstance(values.get(3), bytes) else ""
            except UnicodeDecodeError:
                continue
            if not emoji:
                continue
            jid = values.get(2)
            reactor = jid.decode("utf-8", "replace") if isinstance(jid, bytes) and jid else "me"
            when = values.get(4) if isinstance(values.get(4), int) else 0
            found[reactor] = (emoji, when)
    return found


def load_reactions() -> dict[str, dict[str, dict[str, str]]]:
    """{chat JID: {message Z_PK: {reactor: emoji}}} as of the last export."""
    try:
        return json.loads(REACTIONS_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_reactions(seen: dict) -> None:
    REACTIONS_FILE.write_text(json.dumps(seen))


# ── State (incremental tracking) ───────────────────────────────────────────────
# {chat JID: highest exported ZWAMESSAGE.Z_PK}. Z_PK only grows, so it also picks
# up messages that arrive late with older timestamps (e.g. history sync).
def load_state() -> dict[str, int]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_state(state: dict[str, int]) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ── Core export ────────────────────────────────────────────────────────────────
def load_chats(cur: sqlite3.Cursor, names: NameResolver) -> list[dict]:
    """Every real chat, with a display name and a unique output filename."""
    cur.execute("""
        SELECT Z_PK, ZCONTACTJID, ZPARTNERNAME, ZSESSIONTYPE
        FROM ZWACHATSESSION
        WHERE ZCONTACTJID IS NOT NULL
        ORDER BY Z_PK
    """)
    chats = []
    for row in cur.fetchall():
        jid = row["ZCONTACTJID"]
        if jid in SKIP_JIDS or jid.endswith("@broadcast"):
            continue
        is_group = jid.endswith("@g.us")
        display = (row["ZPARTNERNAME"] or jid.split("@", 1)[0]) if is_group \
            else names.name(jid, row["ZPARTNERNAME"])
        chats.append({"pk": row["Z_PK"], "jid": jid, "group": is_group,
                      "display": display, "filename": safe_filename(display)})

    # Two chats can resolve to the same name (two contacts called "Mike").
    # Keep them in separate files by adding the chat's number or ID.
    counts: dict[str, int] = {}
    for c in chats:
        counts[c["filename"]] = counts.get(c["filename"], 0) + 1
    for c in chats:
        if counts[c["filename"]] > 1:
            c["filename"] += "_" + c["jid"].split("@", 1)[0]
        c["filename"] += ".txt"
    return chats


def export_whatsapp(full: bool = False) -> None:
    ensure_whatsapp_db_readable()

    if not full and em.format_outdated(FORMAT_FILE, FORMAT_VERSION):
        log(f"Output format changed (now v{FORMAT_VERSION}) — re-exporting everything once.")
        full = True

    signature = em.source_signature(WHATSAPP_DB)
    if not full and em.source_unchanged(signature, SOURCE_MARKER):
        log("WhatsApp database unchanged since last run — nothing to do.")
        return

    em.enable_dataless_materialization()

    output_dir = find_output_dir()
    log(f"Output directory: {output_dir}")

    log("Snapshotting WhatsApp database to /tmp for safe read ...")
    conn = snapshot_db()
    cur = conn.cursor()
    check_schema(cur)

    contacts = em.load_contact_map()
    names = NameResolver(cur, contacts)

    media_join = caption_col = metadata_col = ""
    if has_table(cur, "ZWAMEDIAITEM") and has_column(cur, "ZWAMESSAGE", "ZMEDIAITEM"):
        media_join = "LEFT JOIN ZWAMEDIAITEM mi ON mi.Z_PK = m.ZMEDIAITEM"
        if has_column(cur, "ZWAMEDIAITEM", "ZTITLE"):
            caption_col = ", mi.ZTITLE AS caption"
        if has_column(cur, "ZWAMEDIAITEM", "ZMETADATA") \
                and has_column(cur, "ZWAMESSAGE", "ZSTANZAID"):
            metadata_col = ", mi.ZMETADATA AS metadata"

    def fetch_messages(chat_pk: int, after_pk: int) -> list[sqlite3.Row]:
        cur.execute(f"""
            SELECT m.Z_PK, m.ZISFROMME, m.ZFROMJID, m.ZTEXT, m.ZMESSAGEDATE,
                   m.ZMESSAGETYPE, gm.ZMEMBERJID, gm.ZCONTACTNAME, gm.ZFIRSTNAME
                   {caption_col} {metadata_col}
            FROM ZWAMESSAGE m
            LEFT JOIN ZWAGROUPMEMBER gm ON gm.Z_PK = m.ZGROUPMEMBER
            {media_join}
            WHERE m.ZCHATSESSION = ? AND m.Z_PK > ?
            ORDER BY m.ZMESSAGEDATE ASC, m.Z_PK ASC
        """, (chat_pk, after_pk))
        return cur.fetchall()

    def fetch_message(pk: int) -> sqlite3.Row | None:
        q = conn.cursor()
        q.execute(f"""
            SELECT m.Z_PK, m.ZISFROMME, m.ZFROMJID, m.ZTEXT, m.ZMESSAGEDATE,
                   m.ZMESSAGETYPE, gm.ZMEMBERJID, gm.ZCONTACTNAME, gm.ZFIRSTNAME
                   {caption_col}
            FROM ZWAMESSAGE m
            LEFT JOIN ZWAGROUPMEMBER gm ON gm.Z_PK = m.ZGROUPMEMBER
            {media_join}
            WHERE m.Z_PK = ?
        """, (pk,))
        return q.fetchone()

    def message_text(m: sqlite3.Row) -> str:
        mtype = m["ZMESSAGETYPE"]
        text = m["ZTEXT"]
        if not text or mtype in MEDIA_LABELS:
            label = MEDIA_LABELS.get(mtype, "[attachment]")
            caption = m["caption"] if caption_col else None
            text = " ".join(p for p in (label, caption or text) if p)
        return text

    def sender_of(chat: dict, m: sqlite3.Row) -> str:
        if m["ZISFROMME"]:
            return "Me"
        if chat["group"]:
            jid = m["ZMEMBERJID"] or m["ZFROMJID"]
            return names.name(jid, m["ZCONTACTNAME"], m["ZFIRSTNAME"])
        return chat["display"]

    def quoted(chat: dict, m: sqlite3.Row) -> tuple[str, str] | None | bool:
        """(sender, text) of the message m replies to; False if not a reply."""
        if not metadata_col or not m["metadata"]:
            return False
        q = conn.cursor()
        for candidate in set(protobuf_strings(bytes(m["metadata"]))):
            if not 6 <= len(candidate) <= 64 or not candidate.isalnum():
                continue
            q.execute(f"""
                SELECT m.Z_PK, m.ZISFROMME, m.ZFROMJID, m.ZTEXT, m.ZMESSAGETYPE,
                       gm.ZMEMBERJID, gm.ZCONTACTNAME, gm.ZFIRSTNAME {caption_col}
                FROM ZWAMESSAGE m
                LEFT JOIN ZWAGROUPMEMBER gm ON gm.Z_PK = m.ZGROUPMEMBER
                {media_join}
                WHERE m.ZSTANZAID = ? AND m.ZCHATSESSION = ? AND m.Z_PK != ?
            """, (candidate, chat["pk"], m["Z_PK"]))
            row = q.fetchone()
            if row is not None:
                return sender_of(chat, row), message_text(row)
        return False

    reactions_supported = (has_table(cur, "ZWAMESSAGEINFO")
                           and has_column(cur, "ZWAMESSAGEINFO", "ZMESSAGE")
                           and has_column(cur, "ZWAMESSAGEINFO", "ZRECEIPTINFO"))
    member_names: dict[int, dict[str, tuple]] = {}

    def current_reactions(chat: dict) -> dict[int, dict[str, tuple[str, int]]]:
        """{message Z_PK: {reactor: (emoji, unix ms)}} for every message in chat."""
        if not reactions_supported:
            return {}
        q = conn.cursor()
        q.execute("""
            SELECT i.ZMESSAGE, i.ZRECEIPTINFO
            FROM ZWAMESSAGEINFO i JOIN ZWAMESSAGE m ON m.Z_PK = i.ZMESSAGE
            WHERE m.ZCHATSESSION = ? AND i.ZRECEIPTINFO IS NOT NULL
        """, (chat["pk"],))
        found = {}
        for pk, blob in q.fetchall():
            reactions = parse_reactions(bytes(blob))
            if reactions:
                found[pk] = reactions
        return found

    def reactor_name(chat: dict, reactor: str) -> str:
        if reactor == "me":
            return "Me"
        if not chat["group"]:
            return chat["display"]
        # Reactors are usually anonymous IDs (...@lid), which carry no phone
        # number; match them against the group's member list instead.
        if chat["pk"] not in member_names:
            q = conn.cursor()
            q.execute("SELECT ZMEMBERJID, ZCONTACTNAME, ZFIRSTNAME FROM ZWAGROUPMEMBER "
                      "WHERE ZCHATSESSION = ?", (chat["pk"],))
            member_names[chat["pk"]] = {r[0]: (r[1], r[2]) for r in q.fetchall() if r[0]}
        name = names.name(reactor, *member_names[chat["pk"]].get(reactor, ()))
        if reactor.endswith("@lid") and name == reactor.split("@", 1)[0]:
            return "A group member"
        return name

    def reaction_events(current: dict, seen: dict) -> list[tuple]:
        """(message pk, reactor, emoji, unix ms, added) for what changed since seen."""
        events = []
        for pk, reactions in current.items():
            before = seen.get(str(pk), {})
            for reactor, (emoji, when) in reactions.items():
                if before.get(reactor) != emoji:
                    events.append((pk, reactor, emoji, when, True))
        for pk_str, before in seen.items():
            now = current.get(int(pk_str), {})
            for reactor, emoji in before.items():
                if reactor not in now:
                    # WhatsApp doesn't keep a removal time; use when we noticed.
                    events.append((int(pk_str), reactor, emoji, 0, False))
        return events

    def snapshot(current: dict) -> dict[str, dict[str, str]]:
        return {str(pk): {r: e for r, (e, _) in reactions.items()}
                for pk, reactions in current.items()}

    def fmt_unix(seconds: float) -> str:
        return datetime.fromtimestamp(seconds).strftime("%Y-%m-%d %H:%M:%S")

    def render(chat: dict, messages: list[sqlite3.Row], events: list[tuple]) -> str:
        """Message and reaction lines for chat, merged in time order."""
        timed: list[tuple[float, str]] = []
        last = 0.0
        for m in messages:
            if m["ZMESSAGETYPE"] == SYSTEM_MESSAGE_TYPE:
                continue
            text = message_text(m).replace("\n", " ").replace("\r", " ")
            sender = sender_of(chat, m)
            original = quoted(chat, m)
            tag = em.reply_tag(original) if original is not False else ""
            if m["ZMESSAGEDATE"] is not None:
                last = m["ZMESSAGEDATE"] + em.APPLE_EPOCH
            timed.append((last, f"[{fmt_unix(last) if last else '?'}] {sender}{tag}: {text}\n"))
        for pk, reactor, emoji, when_ms, added in events:
            target = fetch_message(pk)
            original = (sender_of(chat, target), message_text(target)) if target else None
            when = when_ms / 1000 if when_ms else time.time()
            verb = f"reacted {emoji} to" if added else f"removed {emoji} from"
            timed.append((when, f"[{fmt_unix(when)}] {reactor_name(chat, reactor)}: "
                                f"[{verb} {em.quote_ref(original)}]\n"))
        timed.sort(key=lambda t: t[0])   # stable: messages keep their order
        return "".join(line for _, line in timed)

    def header(chat: dict) -> str:
        return (f"# Conversation: {chat['display']} (WhatsApp)\n"
                f"# Chat: {chat['jid']}\n"
                f"# Exported: {datetime.now():%Y-%m-%d %H:%M:%S}\n\n")

    chats = load_chats(cur, names)
    log(f"Found {len(chats)} chats")

    state     = {} if full else load_state()
    new_state = dict(state)
    seen_all  = {} if full else load_reactions()
    new_seen  = dict(seen_all)
    total_written = total_reactions = 0
    rewritten = 0
    failed: list[str] = []

    for chat in chats:
        out_path = output_dir / chat["filename"]
        after_pk = state.get(chat["jid"], 0)

        messages = fetch_messages(chat["pk"], after_pk)
        current = current_reactions(chat)
        events = reaction_events(current, seen_all.get(chat["jid"], {}))
        if not messages and not events:
            continue
        last_pk = max((m["Z_PK"] for m in messages), default=after_pk)
        text = render(chat, messages, events)
        if not text:
            # Only system messages: nothing to write, but don't re-read them.
            new_state[chat["jid"]] = last_pk
            continue
        written = sum(1 for m in messages if m["ZMESSAGETYPE"] != SYSTEM_MESSAGE_TYPE)
        reacted = len(events)

        try:
            if full:
                drive_replace(out_path, header(chat) + text)
            else:
                try:
                    if not out_path.exists():
                        text = header(chat) + text

                    def _append() -> None:
                        with open(out_path, "a", encoding="utf-8") as f:
                            f.write(text)
                    drive_write(_append)
                except OSError as e:
                    if e.errno != errno.EDEADLK:
                        raise
                    # Online-only placeholder in Drive: rebuild the file from the
                    # database and swap it in (see export_messages.py).
                    everything = fetch_messages(chat["pk"], 0)
                    drive_replace(out_path, header(chat) + render(
                        chat, everything, reaction_events(current, {})))
                    last_pk = max((m["Z_PK"] for m in everything), default=last_pk)
                    rewritten += 1
                    log(f"  {chat['display']}: +{written} msgs, +{reacted} reactions → "
                        f"{chat['filename']} (online-only in Drive; rewrote from database)")
                    new_state[chat["jid"]] = last_pk
                    new_seen[chat["jid"]] = snapshot(current)
                    total_written += written
                    total_reactions += reacted
                    continue
        except OSError as e:
            log(f"  ERROR: {chat['display']}: couldn't write {chat['filename']} "
                f"({e}); will retry next run.")
            failed.append(chat["display"])
            continue

        new_state[chat["jid"]] = last_pk
        new_seen[chat["jid"]] = snapshot(current)
        total_written += written
        total_reactions += reacted
        detail = f"+{written}" + (f", {reacted} reaction change(s)" if reacted else "")
        log(f"  {chat['display']}: {detail} → {chat['filename']}")

    conn.close()
    TMP_DB.unlink(missing_ok=True)
    save_state(new_state)
    save_reactions(new_seen)

    mode_label = "full re-export" if full else "incremental export"
    log(f"Done ({mode_label}). {total_written} messages and {total_reactions} reaction "
        f"change(s) written across {len(chats)} chats.")
    if rewritten:
        log(f"{rewritten} file(s) were online-only in Google Drive and were rewritten "
            f"from the database. Marking the WhatsApp folder 'Available offline' avoids this.")
    if failed:
        log(f"ERROR: {len(failed)} chat(s) failed to write: {', '.join(failed)}")
        sys.exit(1)
    em.record_source(signature, SOURCE_MARKER)
    if full:
        em.record_format(FORMAT_FILE, FORMAT_VERSION)


def list_chats() -> None:
    """Print each chat with its message count and date range, most recent first."""
    ensure_whatsapp_db_readable()
    conn = snapshot_db()
    cur = conn.cursor()
    check_schema(cur)
    names = NameResolver(cur, em.load_contact_map())
    chats = {c["pk"]: c for c in load_chats(cur, names)}

    cur.execute(f"""
        SELECT ZCHATSESSION, COUNT(*), MIN(ZMESSAGEDATE), MAX(ZMESSAGEDATE)
        FROM ZWAMESSAGE
        WHERE ZMESSAGETYPE != {SYSTEM_MESSAGE_TYPE}
        GROUP BY ZCHATSESSION
        ORDER BY MAX(ZMESSAGEDATE) DESC
    """)
    rows = cur.fetchall()
    conn.close()
    TMP_DB.unlink(missing_ok=True)

    print(f"\n{'Messages':>8}  {'First':^19}  {'Last':^19}  Chat")
    print("-" * 90)
    for pk, count, first, last in rows:
        if pk not in chats:
            continue
        first_str = wa_ts_to_str(first) if first is not None else "?"
        last_str  = wa_ts_to_str(last) if last is not None else "?"
        print(f"{count:>8}  {first_str}  {last_str}  {chats[pk]['display']}")
    print()


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export WhatsApp history to Google Drive")
    parser.add_argument("--full", action="store_true",
                        help="Full re-export: overwrite all files instead of appending")
    parser.add_argument("--list", action="store_true",
                        help="List chats with message counts, don't export anything")
    args = parser.parse_args()

    if args.list:
        list_chats()
    else:
        export_whatsapp(full=args.full)
