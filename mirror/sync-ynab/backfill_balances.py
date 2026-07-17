#!/usr/bin/env python3
"""
Approximate a 30-day backfill of daily account-balance history.

We don't have real historical snapshots before the daily capture started, but
YNAB's current account balance already reflects every transaction. So we can
roll the balance backwards day by day:

    balance_on_day_D  ~=  current_balance  -  sum(transaction amounts dated after D)

This is an APPROXIMATION:
  * It reconstructs the working ``balance`` accurately (current balance minus the
    net of transactions posted after each day).
  * ``cleared_balance`` is reconstructed using each transaction's *current*
    cleared status as a proxy for its historical status, so cleared vs. uncleared
    splits on past days are estimates. The working balance (the number that
    matters for trends/budgeting) is solid.

The backfill is non-destructive: it only fills dates that don't already have a
real snapshot in the history CSV / BigQuery table, so it never overwrites
genuine captured data. It is also idempotent — re-running fills the same gaps to
the same values.

Run manually (one-off):  python3 backfill_balances.py
"""

import datetime

import requests
import pandas as pd

import lib_bq
from get_balances import (
    fetch_account_balances,
    API_URL,
    HEADERS,
    HISTORY_FILENAME,
    HISTORY_BQ_TABLE_NAME,
)

BACKFILL_DAYS = 30
CLEARED_STATES = ("cleared", "reconciled")


def fetch_transactions(since_date):
    """Fetch non-deleted transactions on/after ``since_date`` (YYYY-MM-DD)."""
    response = requests.get(
        f"{API_URL}/transactions",
        headers=HEADERS,
        params={"since_date": since_date},
    )
    response.raise_for_status()
    transactions = response.json()["data"]["transactions"]
    return [t for t in transactions if not t.get("deleted")]


def reconstruct_history(accounts, transactions, target_dates, today):
    """
    Reconstruct balance rows for ``target_dates`` by rolling current balances
    backwards over ``transactions``.

    accounts: list of dicts with id, name, balance, cleared_balance, uncleared_balance
              (current balances, already in dollars).
    transactions: list of YNAB transaction dicts (amount in milliunits).
    target_dates: iterable of "YYYY-MM-DD" strings to reconstruct.
    today: datetime.date of the current day.
    """
    today_iso = today.isoformat()
    # Only transactions up to today can have affected the current balance.
    txns = [t for t in transactions if t["date"] <= today_iso]

    rows = []
    for date_iso in sorted(target_dates):
        for acct in accounts:
            after = [
                t for t in txns
                if t["account_id"] == acct["id"] and t["date"] > date_iso
            ]
            net_all = sum(t["amount"] for t in after) / 1000.0
            net_cleared = sum(
                t["amount"] for t in after if t.get("cleared") in CLEARED_STATES
            ) / 1000.0

            balance = round(acct["balance"] - net_all, 2)
            cleared = round(acct["cleared_balance"] - net_cleared, 2)
            rows.append({
                "snapshot_date": date_iso,
                "id": acct["id"],
                "name": acct["name"],
                "balance": balance,
                "cleared_balance": cleared,
                "uncleared_balance": round(balance - cleared, 2),
                "source": "backfill",
            })
    return rows


def existing_history_dates_csv():
    """Snapshot dates already present in the local history CSV."""
    try:
        df = pd.read_csv(HISTORY_FILENAME)
    except FileNotFoundError:
        return set()
    if "snapshot_date" not in df.columns:
        return set()
    return set(df["snapshot_date"].astype(str))


def write_history_csv_bulk(rows, dates):
    """Merge backfilled rows into the history CSV, replacing the given dates."""
    df_new = pd.DataFrame(rows)
    try:
        existing = pd.read_csv(HISTORY_FILENAME)
        existing = existing[~existing["snapshot_date"].astype(str).isin(dates)]
        combined = pd.concat([existing, df_new], ignore_index=True)
    except FileNotFoundError:
        combined = df_new
    combined = combined.sort_values(["snapshot_date", "name"]).reset_index(drop=True)
    combined.to_csv(HISTORY_FILENAME, index=False)


def main():
    today = datetime.date.today()
    candidate_dates = {
        (today - datetime.timedelta(days=offset)).isoformat()
        for offset in range(BACKFILL_DAYS)
    }

    # Don't overwrite real snapshots: only fill dates we don't already have.
    existing = existing_history_dates_csv() | lib_bq.existing_snapshot_dates(HISTORY_BQ_TABLE_NAME)
    target_dates = sorted(candidate_dates - existing)

    if not target_dates:
        print("Nothing to backfill — all of the last "
              f"{BACKFILL_DAYS} days already have snapshots.")
        return

    print(f"Backfilling {len(target_dates)} day(s): "
          f"{target_dates[0]} .. {target_dates[-1]}")

    accounts = fetch_account_balances()
    # Reach back one extra day so a transaction dated exactly on the earliest
    # target day is included when rolling that day's balance.
    since_date = (today - datetime.timedelta(days=BACKFILL_DAYS + 1)).isoformat()
    transactions = fetch_transactions(since_date)

    rows = reconstruct_history(accounts, transactions, target_dates, today)

    write_history_csv_bulk(rows, target_dates)
    lib_bq.append_snapshots(rows, HISTORY_BQ_TABLE_NAME, snapshot_dates=target_dates,
                            schema=lib_bq.BALANCES_HISTORY_SCHEMA)
    print(f"Backfilled {len(rows)} rows "
          f"({len(accounts)} accounts x {len(target_dates)} days).")


if __name__ == "__main__":
    main()
