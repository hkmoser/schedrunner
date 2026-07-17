# Task: Schedrunner Log Summary Page

Add a `/screen/schedlogs` page to the dashboard that shows a concise per-script
status summary of the log files in `~/Dropbox/Source/schedrunner/log/`.

Each log file represents one scheduled script. Show: script name, last-run age,
run duration, OK/FAILED status, and a short snippet of actual output from the
most recent run.

This follows the exact same 6-file pattern as every other screen page. Do all
six changes in one PR.

---

## 1. `bq_sidecar/app.py` — new `/schedlogs` endpoint

Add this function after `get_schedrunner()`. Register it by adding
`"/schedlogs": get_schedlogs` to the `handlers` dict where the others are.

```python
def get_schedlogs():
    """Parse schedrunner's per-script log files into a concise status summary."""
    import re

    log_dir = os.path.expanduser(
        os.environ.get("SCHEDRUNNER_LOG_DIR") or "~/Dropbox/Source/schedrunner/log"
    )
    SEPARATOR = "----------------------------------------"
    # Wrapper lines: [Fri Jun 19 01:39:30 EDT 2026] Running|Finished /path
    WRAPPER_RE = re.compile(
        r"^\[(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) \w+ +\d+ \d{2}:\d{2}:\d{2} \w+ \d{4}\]"
        r" (Running|Finished) "
    )
    WRAPPER_TS_RE = re.compile(
        r"^\[(\w{3} \w{3} +\d+ \d{2}:\d{2}:\d{2} \w+ \d{4})\]"
    )

    def parse_wrapper_dt(line):
        m = WRAPPER_TS_RE.match(line)
        if not m:
            return None
        for fmt in ("%a %b %d %H:%M:%S %Z %Y", "%a %b  %d %H:%M:%S %Z %Y"):
            try:
                return datetime.datetime.strptime(m.group(1), fmt)
            except ValueError:
                pass
        return None

    now = datetime.datetime.now()
    entries = []

    try:
        names = sorted(n for n in os.listdir(log_dir) if n.endswith(".log"))
    except OSError:
        return {"title": "Sched Logs", "subtitleFormatted": f"log dir not found: {log_dir}",
                "entries": [], "rowCount": 0}

    for fname in names:
        path = os.path.join(log_dir, fname)
        label = fname[:-4]  # strip .log

        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError as exc:
            entries.append({"name": label, "metaFormatted": str(exc),
                            "statusFormatted": "ERR", "statusColor": "down", "snippetFormatted": ""})
            continue

        # Split into run blocks; take the last non-empty one
        blocks = [b.strip() for b in content.split(SEPARATOR) if b.strip()]
        if not blocks:
            entries.append({"name": label, "metaFormatted": "no runs",
                            "statusFormatted": "—", "statusColor": "down", "snippetFormatted": ""})
            continue

        block = blocks[-1]
        lines = block.splitlines()

        # Extract Running / Finished wrapper lines
        running_line = next((l for l in lines if WRAPPER_RE.match(l) and "Running" in l), None)
        finished_line = next((l for l in reversed(lines) if WRAPPER_RE.match(l) and "Finished" in l), None)

        # Duration
        duration_str = ""
        if running_line and finished_line:
            t1 = parse_wrapper_dt(running_line)
            t2 = parse_wrapper_dt(finished_line)
            if t1 and t2:
                secs = int((t2 - t1).total_seconds())
                duration_str = f"{secs}s"

        # Last-run age from the last wrapper timestamp
        last_dt = None
        for line in reversed(lines):
            if WRAPPER_RE.match(line):
                last_dt = parse_wrapper_dt(line)
                if last_dt:
                    break

        ago_str = _ago(last_dt, now) if last_dt else "unknown"

        # Status: look for FAILED keyword anywhere in the block
        failed = "FAILED" in block
        status_str = "FAILED" if failed else "OK"
        status_color = "down" if failed else "up"

        # Snippet: last 3 meaningful lines — skip wrapper boilerplate and blank lines
        snippet_lines = [
            l for l in lines
            if l.strip() and not WRAPPER_RE.match(l)
        ][-3:]
        snippet = "\n".join(snippet_lines)

        meta = ago_str
        if duration_str:
            meta += f" · {duration_str}"

        entries.append({
            "name": label,
            "metaFormatted": meta,
            "statusFormatted": status_str,
            "statusColor": status_color,
            "snippetFormatted": snippet,
        })

    subtitle = f"{len(entries)} scripts · {log_dir}"
    return {
        "title": "Sched Logs",
        "subtitleFormatted": subtitle,
        "entries": entries,
        "rowCount": len(entries),
    }
```

`_ago()` already exists in the file (used by `get_logs`). Use it as-is.

---

## 2. `Server/Sources/App/Providers/SidecarProviders.swift` — new provider

Add after `SchedrunnerProvider`:

```swift
struct SchedLogsProvider: DataProvider {
    var key: String { "schedlogs" }
    var ttl: TimeInterval { 30 }
    func fetch(client: Client, config: AppConfig, logger: Logger) async throws -> JSONValue {
        try await client.getJSON("\(config.bqSidecarURL)/schedlogs")
    }
    func stub(config: AppConfig) -> JSONValue { .object([:]) }
}
```

---

## 3. `Server/Sources/App/Composer.swift` — register template

In the `Templates` struct, add alongside the other `let` declarations:

```swift
let schedlogs: JSONValue
```

In `Templates.load()`, add alongside the other `loadResource` calls:

```swift
schedlogs: try loadResource("schedlogs"),
```

---

## 4. `Server/Sources/App/Templates/schedlogs.json` — screen template

Create this file. It mirrors `logs.json` but adds a status badge per entry and
uses the `snippetFormatted` field instead of a raw tail.

```json
{
  "type": "screen",
  "style": { "background": "$bg" },
  "children": [
    {
      "type": "scroll",
      "children": [
        {
          "type": "vstack",
          "style": { "spacing": 16, "padding": 16 },
          "children": [
            {
              "type": "hstack",
              "style": { "align": "center" },
              "children": [
                { "type": "text", "binding": "schedlogs.title",
                  "style": { "font": "largeTitle", "weight": "bold", "color": "$textPrimary" } },
                { "type": "spacer" },
                { "type": "text", "binding": "meta.updatedAtFormatted",
                  "style": { "font": "caption", "color": "$textSecondary" },
                  "action": { "type": "refresh" } }
              ]
            },
            { "type": "text", "binding": "schedlogs.subtitleFormatted",
              "style": { "font": "caption", "color": "$textSecondary" } },
            {
              "type": "vstack",
              "props": { "repeat": "schedlogs.entries" },
              "style": { "spacing": 12 },
              "children": [
                {
                  "type": "card",
                  "style": { "background": "$cardBg", "cornerRadius": 16, "padding": 14 },
                  "children": [
                    {
                      "type": "hstack",
                      "style": { "align": "center" },
                      "children": [
                        {
                          "type": "vstack",
                          "style": { "spacing": 2 },
                          "children": [
                            { "type": "text", "binding": "item.name",
                              "style": { "font": "headline", "weight": "semibold", "color": "$textPrimary" } },
                            { "type": "text", "binding": "item.metaFormatted",
                              "style": { "font": "caption", "color": "$textSecondary" } }
                          ]
                        },
                        { "type": "spacer" },
                        { "type": "badge", "binding": "item.statusFormatted",
                          "style": { "color": "item.statusColor" } }
                      ]
                    },
                    { "type": "code", "binding": "item.snippetFormatted" }
                  ]
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}
```

---

## 5. `Server/Sources/App/routes.swift` — new route

Add a composer alongside the others at the top of `routes(_:)`:

```swift
let schedlogsComposer = Composer(providers: [SchedLogsProvider()])
```

Add the route alongside the other `screen/*` routes:

```swift
app.get("screen", "schedlogs") { req async -> Manifest in
    await schedlogsComposer.build(
        client: req.client,
        config: req.application.dashboardConfig,
        cache: req.application.providerCache,
        templates: req.application.templates,
        logger: req.logger,
        screen: req.application.templates.schedlogs
    )
}
```

---

## 6. `Server/Sources/App/Templates/nav.json` — add nav entry

Find the System section (the one whose `children` already contains Logs, Repos,
Schedrunner, BQ Tables). Add after the Schedrunner entry:

```json
{ "title": "Sched Logs", "icon": "doc.text.magnifyingglass", "path": "/screen/schedlogs" }
```

---

## Deploy

```
make update
```

Verify: open `/screen/schedlogs` in the app. Each scheduled script should appear
as a card with name, last-run age, duration, OK/FAILED badge, and a short output
snippet. The `code` block is hidden automatically when `snippetFormatted` is empty.

## Edge cases to handle

- Log dir missing → return empty `entries` with a subtitle explaining the path.
- File unreadable → add an ERR entry rather than crashing.
- Block with no Finished line (script still running or was killed) → show
  "running?" in duration, treat status as unknown rather than FAILED.
- `_ago()` is already defined in `bq_sidecar/app.py` — do not redefine it.
