import requests
import csv
import datetime
import os

import lib_bq
import pandas as pd

# Replace with your YNAB access token and budget ID
YNAB_TOKEN = "YpxPH_8HxynRQXxgJkRrguo2Sd2t7t1NT-XxtWqT_ZM"
BUDGET_ID = "aca294de-e11a-4857-908f-bf19edaee302"
FILENAME = "data/account_balances.csv"
BQ_TABLE_NAME = "ynab_balances"

# Daily history (one dated snapshot per day, appended over time)
HISTORY_FILENAME = "data/account_balances_history.csv"
HISTORY_BQ_TABLE_NAME = "ynab_balances_history"

API_URL = f"https://api.ynab.com/v1/budgets/{BUDGET_ID}"
HEADERS = {"Authorization": f"Bearer {YNAB_TOKEN}"}

def fetch_account_balances():
    response = requests.get(f"{API_URL}/accounts", headers=HEADERS)
    response.raise_for_status()
    accounts = response.json()['data']['accounts']
    rows = []
    for account in accounts:
        rows.append({
            "id": account["id"],
            "name": account["name"],
            "balance": account["balance"] / 1000,
            "cleared_balance": account["cleared_balance"] / 1000,
            "uncleared_balance": account["uncleared_balance"] / 1000
        })
    return rows

def write_csv(rows):
    with open(FILENAME, 'w', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=rows[0].keys())
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

def write_bigquery(rows):
    df = pd.DataFrame(rows)
    lib_bq.write_to_bigquery(df, BQ_TABLE_NAME)

def build_history_rows(rows, snapshot_date):
    """Tag each balance row with the snapshot date and mark it as a real capture."""
    return [{"snapshot_date": snapshot_date, **row, "source": "actual"} for row in rows]

def write_history_csv(history_rows, snapshot_date):
    """Append today's snapshot to the local history CSV, replacing any rows
    already recorded for the same date so re-runs stay idempotent."""
    df_today = pd.DataFrame(history_rows)
    if os.path.exists(HISTORY_FILENAME):
        existing = pd.read_csv(HISTORY_FILENAME)
        existing = existing[existing["snapshot_date"].astype(str) != str(snapshot_date)]
        combined = pd.concat([existing, df_today], ignore_index=True)
    else:
        combined = df_today
    combined.to_csv(HISTORY_FILENAME, index=False)

def write_history_bigquery(history_rows, snapshot_date):
    df = pd.DataFrame(history_rows)
    lib_bq.append_daily_snapshot(df, HISTORY_BQ_TABLE_NAME, snapshot_date,
                                 schema=lib_bq.BALANCES_HISTORY_SCHEMA)

def main():
    rows = fetch_account_balances()
    write_csv(rows)
    write_bigquery(rows)

    snapshot_date = datetime.date.today().isoformat()
    history_rows = build_history_rows(rows, snapshot_date)
    write_history_csv(history_rows, snapshot_date)
    write_history_bigquery(history_rows, snapshot_date)

if __name__ == "__main__":
    main()
