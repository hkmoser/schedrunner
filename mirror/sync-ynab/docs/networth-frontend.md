# Net Worth Graph — Front-End Integration Guide

Everything the front end needs to build a **net worth over time** graph from the
YNAB → BigQuery pipeline produced by the `sync-ynab` repo.

---

## 1. Where the data lives

| | |
|---|---|
| **Backend** | Google BigQuery |
| **GCP project** | `ecstatic-pod-443723-f6` |
| **Dataset** | `home_ynab` |
| **History table** | `ecstatic-pod-443723-f6.home_ynab.ynab_balances_history` |
| **Current-state table** | `ecstatic-pod-443723-f6.home_ynab.ynab_balances` |

- `ynab_balances_history` — one row **per account per day**. This is what the
  graph reads from. Populated daily, plus an approximate 30-day backfill.
- `ynab_balances` — latest snapshot only (one row per account, overwritten each
  run). Use this if you just want the current total, not the trend.

Data refreshes once per day (driven by the pipeline's scheduler).

---

## 2. Schema — `ynab_balances_history`

| column | type | notes |
|---|---|---|
| `snapshot_date` | `DATE` | the day this balance is for |
| `id` | `STRING` | YNAB account id |
| `name` | `STRING` | account name (e.g. "Checking") |
| `balance` | `FLOAT64` | working balance, in **dollars** |
| `cleared_balance` | `FLOAT64` | dollars |
| `uncleared_balance` | `FLOAT64` | dollars |
| `source` | `STRING` | `'actual'` = real capture · `'backfill'` = approximated |

**Net worth = `SUM(balance)` across all accounts for a given `snapshot_date`.**

---

## 3. Authentication (read this — security matters)

> **Do NOT query BigQuery directly from the browser.** That would require
> shipping a Google service-account key to the client, which exposes full read
> access to your financial data. Always query from a **server-side** route
> (API route / serverless function / backend) and return plain JSON to the chart.

1. Create a GCP **service account** in project `ecstatic-pod-443723-f6`.
2. Grant it two roles on the project:
   - **BigQuery Data Viewer** (read the tables)
   - **BigQuery Job User** (run queries)
3. Download its JSON key and provide it to your backend via
   `GOOGLE_APPLICATION_CREDENTIALS` (path to the key file) or your platform's
   secret store. Keep it server-side only — never in client bundles or the repo.

---

## 4. The query

### Net worth over time (primary graph series)
```sql
SELECT
  snapshot_date,
  ROUND(SUM(balance), 2)                                    AS net_worth,
  -- 'backfill' if ANY account that day was approximated, else 'actual'
  IF(LOGICAL_OR(source = 'backfill'), 'backfill', 'actual') AS source
FROM `ecstatic-pod-443723-f6.home_ynab.ynab_balances_history`
GROUP BY snapshot_date
ORDER BY snapshot_date;
```

### Per-account trend (optional — for a stacked/area chart)
```sql
SELECT snapshot_date, name, ROUND(balance, 2) AS balance
FROM `ecstatic-pod-443723-f6.home_ynab.ynab_balances_history`
ORDER BY snapshot_date, name;
```

### Just the current total (no trend)
```sql
SELECT ROUND(SUM(balance), 2) AS net_worth
FROM `ecstatic-pod-443723-f6.home_ynab.ynab_balances`;
```

---

## 5. Server-side fetch example (Node.js / TypeScript)

```ts
// server-side only — e.g. Next.js route handler, Express, or a serverless fn
import { BigQuery } from '@google-cloud/bigquery';

const bq = new BigQuery({ projectId: 'ecstatic-pod-443723-f6' });
// auth comes from GOOGLE_APPLICATION_CREDENTIALS (path to the SA key)

export async function getNetWorthSeries() {
  const query = `
    SELECT
      snapshot_date,
      ROUND(SUM(balance), 2) AS net_worth,
      IF(LOGICAL_OR(source = 'backfill'), 'backfill', 'actual') AS source
    FROM \`ecstatic-pod-443723-f6.home_ynab.ynab_balances_history\`
    GROUP BY snapshot_date
    ORDER BY snapshot_date`;

  const [rows] = await bq.query({ query });
  // snapshot_date comes back as a BigQuery date wrapper; normalize to ISO string
  return rows.map((r: any) => ({
    date: r.snapshot_date.value,   // "YYYY-MM-DD"
    netWorth: r.net_worth,
    source: r.source,              // 'actual' | 'backfill'
  }));
}
```

Expose that behind an endpoint (e.g. `GET /api/net-worth`) returning:

```json
[
  { "date": "2026-05-22", "netWorth": 48230.11, "source": "backfill" },
  { "date": "2026-05-23", "netWorth": 48190.04, "source": "backfill" },
  { "date": "2026-06-20", "netWorth": 51002.77, "source": "actual" }
]
```

---

## 6. Rendering the graph

- **X axis:** `date` (treat as a time axis; values are ISO `YYYY-MM-DD`).
- **Y axis:** `netWorth` (USD; format as currency).
- **One line/area** for the net-worth series, sorted by date (the query already
  orders it).
- **Distinguish estimated history:** rows with `source = 'backfill'` are
  approximations. Render them differently — e.g. a dashed segment, lighter
  shade, or a "≈ estimated" band — and switch to a solid line where
  `source = 'actual'`. Optionally annotate the transition point.

---

## 7. Caveats

- **Backfilled days are approximate.** The `balance` (and therefore the net
  worth total) is reconstructed by rolling current balances backward over
  transactions; it's accurate for the working balance but won't capture manual
  balance adjustments or reconciliation corrections made in the past. The
  `cleared_balance` / `uncleared_balance` split on backfilled days is estimated.
  Use the `source` column to flag this in the UI.
- **Daily granularity.** One point per day; there is no intraday history.
- **Refreshed once per day** — the latest point is yesterday/today depending on
  when the pipeline last ran.
- **`balance` already in dollars** (the pipeline divides YNAB's milliunits by
  1000). No further scaling needed.
