#!/usr/bin/env python3
"""Dump the data schemas the __APP_NAME_LOWER__ reads — every BigQuery table in BQ_DATASET (plus
the YNAB balances table) and the field shape of the Drive-synced JSON exports (Messages,
Smart Home) — into a committed Markdown file so they're visible without DB access.

Field names + types ONLY by default (safe to commit). Set SCHEMAS_SAMPLES=1 to also
include a few real sample rows/records (do this only for a PRIVATE repo).

    make schemas            # writes docs/SCHEMAS.md
    SCHEMAS_SAMPLES=1 make schemas

Reuses the sidecar's own table-name + folder helpers (app.py) and the machine's gcloud
Application Default Credentials — no keys.
"""
import datetime
import glob
import json
import os
import sys

import app  # bq_sidecar/app.py

SAMPLES = os.environ.get("SCHEMAS_SAMPLES", "0") == "1"
SAMPLE_N = int(os.environ.get("SCHEMAS_SAMPLE_ROWS", "3"))


def _fields_md(schema, prefix=""):
    """BigQuery schema → Markdown table rows, flattening nested RECORD fields."""
    out = []
    for f in schema:
        mode = "" if f.mode in ("NULLABLE", "", None) else f.mode
        desc = (getattr(f, "description", "") or "").replace("|", "\\|")[:80]
        out.append(f"| `{prefix}{f.name}` | {f.field_type} | {mode} | {desc} |")
        if f.field_type in ("RECORD", "STRUCT") and f.fields:
            out += _fields_md(f.fields, prefix + f.name + ".")
    return out


def _bq_section(lines):
    try:
        from google.cloud import bigquery  # noqa: F401
        client = app._client()
    except Exception as exc:  # noqa: BLE001
        lines.append(f"_BigQuery unavailable: {exc}_\n")
        return

    ds = app._dataset()
    # Enumerate every table in each dataset the app reads: the AFM dataset (BQ_DATASET)
    # and the YNAB dataset (home_ynab — its own dataset, derived from the YNAB table id),
    # so balances *and* the net-worth history / any transactions tables all show up.
    datasets = [ds]
    try:
        ynab_ds = app._ynab_table().rsplit(".", 1)[0]  # "project.home_ynab"
        if ynab_ds and ynab_ds not in datasets:
            datasets.append(ynab_ds)
    except Exception:  # noqa: BLE001
        pass
    table_ids = []
    for dsi in datasets:
        try:
            for t in client.list_tables(dsi):
                table_ids.append(f"{dsi}.{t.table_id}")
        except Exception as exc:  # noqa: BLE001
            lines.append(f"_Could not list tables in {dsi}: {exc}_\n")
    # Plus the specific tables the app names directly, in case a dataset listing missed one.
    for getter in (app._ynab_table, app._ynab_history_table, app._balances_table,
                   app._config_table, app._known_locs_table, app._afm_history_table,
                   app._afm_table):
        try:
            tid = getter()
            if tid not in table_ids:
                table_ids.append(tid)
        except Exception:  # noqa: BLE001
            pass

    for tid in sorted(set(table_ids)):
        try:
            t = client.get_table(tid)
        except Exception as exc:  # noqa: BLE001
            lines.append(f"### `{tid}`\n\n_not found / unreadable: {str(exc)[:120]}_\n")
            continue
        rows = f"{t.num_rows:,} rows" if t.num_rows is not None else "?"
        kind = (t.table_type or "TABLE").title()
        lines.append(f"### `{tid}`  ·  {kind} · {rows}")
        lines.append("")
        lines.append("| field | type | mode | description |")
        lines.append("|---|---|---|---|")
        lines += _fields_md(t.schema)
        lines.append("")
        if SAMPLES:
            try:
                res = client.query(f"SELECT * FROM `{tid}` LIMIT {SAMPLE_N}").result(timeout=30)
                cols = [f.name for f in res.schema]
                lines.append("<details><summary>sample rows</summary>\n")
                lines.append("| " + " | ".join(cols) + " |")
                lines.append("|" + "---|" * len(cols))
                for r in res:
                    lines.append("| " + " | ".join(str(app._coerce(v)).replace("|", "\\|")[:40] for v in r.values()) + " |")
                lines.append("\n</details>\n")
            except Exception as exc:  # noqa: BLE001
                lines.append(f"_sample query failed: {str(exc)[:120]}_\n")


def _record_keys(path, limit=80):
    """Union of field names across the first records of a JSONL or JSON-array export,
    including one level of nesting (`parent.child`)."""
    recs = []
    try:
        if path.endswith(".jsonl"):
            with open(path, encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f):
                    if i >= limit:
                        break
                    line = line.strip()
                    if line:
                        try:
                            recs.append(json.loads(line))
                        except Exception:  # noqa: BLE001
                            pass
        else:
            data = json.load(open(path, encoding="utf-8", errors="replace"))
            rows = data.get("messages", data) if isinstance(data, dict) else data
            if isinstance(rows, list):
                recs = rows[:limit]
    except Exception:  # noqa: BLE001
        return [], recs
    keys = set()
    for o in recs:
        if isinstance(o, dict):
            for k, v in o.items():
                keys.add(k)
                if isinstance(v, dict):
                    for kk in list(v)[:10]:
                        keys.add(f"{k}.{kk}")
    return sorted(keys), recs


def _export_section(lines, label, resolver):
    try:
        d = resolver()
    except Exception as exc:  # noqa: BLE001
        d = None
        lines.append(f"### {label}\n\n_resolver error: {exc}_\n")
        return
    if not d or not os.path.isdir(d):
        lines.append(f"### {label}\n\n_folder not found (set the env override)._\n")
        return
    # Google Drive for Desktop can EDEADLK on a folder glob while it hydrates; retry briefly.
    try:
        files = app._retry_os(
            lambda: sorted(glob.glob(os.path.join(d, "*.jsonl")) + glob.glob(os.path.join(d, "*.json")),
                           key=os.path.getmtime, reverse=True))
    except Exception as exc:  # noqa: BLE001
        lines.append(f"### {label}\n\n- folder: `{d}`\n- _could not list (Drive busy): {exc}_\n")
        return
    lines.append(f"### {label}")
    lines.append("")
    lines.append(f"- folder: `{d}`")
    if not files:
        lines.append("- _no .jsonl/.json files found_\n")
        return
    newest = files[0]
    keys, recs = _record_keys(newest)
    lines.append(f"- newest file: `{os.path.basename(newest)}`  ({len(files)} files)")
    lines.append(f"- fields: {', '.join('`' + k + '`' for k in keys) or '(none parsed)'}")
    lines.append("")
    if SAMPLES and recs:
        lines.append("<details><summary>sample record</summary>\n")
        lines.append("```json")
        lines.append(json.dumps(recs[0], indent=2)[:1500])
        lines.append("```\n</details>\n")


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else "docs/SCHEMAS.md"
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# Data schemas",
        "",
        f"_Generated by `make schemas` on {now}. Field names + types"
        + (" + sample data" if SAMPLES else " only (no row values)") + "._",
        "",
        "Regenerate after the underlying tables/exports change so the __APP_NAME_LOWER__ code can be",
        "matched to the real shapes. `SCHEMAS_SAMPLES=1` also dumps a few rows (private repos only).",
        "",
        "## BigQuery",
        "",
    ]
    _bq_section(lines)
    lines += ["## Drive-synced exports", ""]
    _export_section(lines, "Messages (iMessage/SMS)", app._messages_dir)
    _export_section(lines, "Smart Home events", app._smarthome_dir)

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")
    print(f"wrote {out_path}  (SCHEMAS_SAMPLES={'1' if SAMPLES else '0'})")


if __name__ == "__main__":
    main()
