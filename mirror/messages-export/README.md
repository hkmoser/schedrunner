# Personal data collectors → Google Drive

A small suite of macOS scripts that archive your personal communications into
Google Drive (`My Drive/Private/...`). Each runs incrementally and is safe to
schedule. Output lands in the Google Drive Desktop sync folder for
`joe@joemoser.com`.

| Script | Source | Output | Deps |
|---|---|---|---|
| `export_messages.py` | iMessage/SMS (`chat.db`) + AddressBook | `Private/Messages/*.txt` | stdlib |
| `whatsapp_export.py` | WhatsApp for Mac (`ChatStorage.sqlite`) + AddressBook | `Private/WhatsApp/*.txt` | stdlib |
| `email_collector.py` | Gmail via IMAP | `Private/mail/<account>/…json` | stdlib |
| `gchat_collector.py` | Google Chat (web, scraped) | `Private/Chat/*.jsonl` | playwright |

## Messages — `export_messages.py`
Exports iMessage/SMS conversations, resolving contact names from the macOS
AddressBook database directly. Requires Full Disk Access.

```bash
python3 export_messages.py                 # incremental
python3 export_messages.py --full          # re-export everything
python3 export_messages.py --list          # list conversations
python3 export_messages.py --check-contacts  # diagnose name resolution
```

Replies show which message they answer:

```
[2026-10-04 18:40:10] Me [replying to Jane Doe: "Dinner at 7?"]: yes!
```

For iMessage this is the message that started the reply thread, which is what
Messages itself shows.

iMessage reactions (tapbacks, including custom emoji) get their own line, and
so does taking one back:

```
[2026-10-04 18:41:02] Jane Doe: [reacted ❤️ to Me: "yes!"]
[2026-10-04 18:41:30] Jane Doe: [removed ❤️ from Me: "yes!"]
```

## WhatsApp — `whatsapp_export.py`
Same idea and output format as the Messages export, for WhatsApp. Reads the
database kept by **WhatsApp for Mac** (the App Store app) at
`~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite`
and writes one file per chat to `Private/WhatsApp/`, next to `Private/Messages/`.
Names come from the AddressBook first, so people match the Messages export, then
WhatsApp's own names. Most people are stored under anonymous IDs rather than
phone numbers; WhatsApp's `LID.sqlite` links those to phone numbers and
`ContactsV2.sqlite` holds its copy of your address book, so both are read too.
Anyone with no name anywhere shows as "Unknown contact". Needs Full Disk Access, same as the Messages export.

```bash
python3 whatsapp_export.py           # incremental
python3 whatsapp_export.py --full    # re-export everything
python3 whatsapp_export.py --list    # list chats
```

It only sees what the Mac app has synced. Media shows as `[image]`, `[voice
message]` and so on, with the caption if there is one; group join/leave notices
are skipped. Replies and reactions are written the same way as in the Messages
export. WhatsApp only stores each message's current reactions, so a reaction
that's taken back is logged when the exporter notices, not when it happened.
Schedule it with `run_whatsapp_export.sh`.

## Email — `email_collector.py`
Collects the last week of mail from each Gmail account over IMAP and writes one
JSON file per message (decoded plain-text body + normalized headers — the most
useful shape for downstream automation/AI). Uses `BODY.PEEK`, so it **never
marks mail as read**.

Setup: enable 2-Step Verification, create an [app password](https://myaccount.google.com/apppasswords)
per account, then:
```bash
cp mail_collector_config.example.json ~/.mail_collector_config.json
chmod 600 ~/.mail_collector_config.json   # then fill in your accounts
python3 email_collector.py                # last 7 days, all accounts
python3 email_collector.py --days 14 --account you@gmail.com
```

## Google Chat — `gchat_collector.py`
Drives a real Chrome session (Playwright) to scrape Google Chat. Google Chat
**sends sender-visible read receipts** when you open a conversation, so:

- **Default** and **`--watch`** are *non-intrusive* — they archive only the
  conversation-list **previews** (latest snippet per chat) and never open a
  conversation, so **no read receipts are sent** and unread markers are
  untouched. (Previews may be truncated; bursts between polls can collapse to
  the latest message.)
- **`--full-read`** is the explicit deep mode — it **opens** every conversation
  (which **does send read receipts**) to capture full history, then **restores
  the unread marker** on conversations that were unread beforehand.

```bash
pip install -r requirements.txt && playwright install chromium
python3 gchat_collector.py --login        # sign in once; profile is persisted
python3 gchat_collector.py                # non-intrusive preview snapshot (no receipts)
python3 gchat_collector.py --watch        # realtime watch via previews (no receipts)
python3 gchat_collector.py --full-read    # deep scrape every chat (sends receipts), restore unread
```

> chat.google.com's DOM is obfuscated and changes over time; if scraping stops
> matching, adjust the `SELECTORS` block at the top of the script. Automating
> Google properties may also conflict with their Terms of Service.

## Notes
- Secrets/state (`~/.mail_collector_config.json`, `~/.gchat_collector_profile/`)
  live outside the repo and are git-ignored.
- **Full Disk Access, briefly:** FDA is *not* a Python requirement — it's only
  needed to read Apple's TCC-protected stores (`chat.db`, the AddressBook DB).
  If the process that launches the export (your scheduler, terminal, or IDE)
  already has FDA, child Python processes inherit it and everything just works.
- **Google Drive online-only files:** if Drive has turned an exported file into
  an online-only placeholder, appending to it fails with "Resource deadlock
  avoided". The exporter then rebuilds that file from `chat.db` and swaps it in,
  so nothing is skipped. Anything that's gone from `chat.db` (e.g. deleted
  messages) won't be in the rebuilt file. To avoid it, right-click
  `My Drive/Private/Messages` in Finder → **Available offline**.
- **Scheduling the Messages export (headless, no Terminal window):** point your
  scheduler at `run_export.sh` — it runs `python3` directly, with no window. If
  your scheduler already has FDA, that's all you need. Only if a run fails on
  permissions do you grant FDA to something in the launch chain (the failure
  message prints the exact interpreter path). As an optional, self-contained
  alternative that needs just one grant, `./install_launchagent.sh` installs a
  background LaunchAgent that execs the interpreter directly.
