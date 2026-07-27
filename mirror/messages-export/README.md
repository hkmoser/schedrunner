# Personal data collectors → Google Drive

A small suite of macOS scripts that archive your personal communications into
Google Drive (`My Drive/Private/...`). Each runs incrementally and is safe to
schedule. Output lands in the Google Drive Desktop sync folder for
`joe@joemoser.com`.

| Script | Source | Output | Deps |
|---|---|---|---|
| `export_messages.py` | iMessage/SMS (`chat.db`) + AddressBook | `Private/Messages/*.txt` | stdlib |
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
- **Scheduling the Messages export (headless, no Terminal window):** point your
  scheduler at `run_export.sh` — it runs `python3` directly, with no window. If
  your scheduler already has FDA, that's all you need. Only if a run fails on
  permissions do you grant FDA to something in the launch chain (the failure
  message prints the exact interpreter path). As an optional, self-contained
  alternative that needs just one grant, `./install_launchagent.sh` installs a
  background LaunchAgent that execs the interpreter directly.
