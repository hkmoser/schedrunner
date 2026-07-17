import requests
import pandas as pd
import csv

import lib_bq
import os
os.environ["TMPDIR"] = "/tmp"

# Replace with your actual YNAB access token and budget ID
YNAB_TOKEN = "YpxPH_8HxynRQXxgJkRrguo2Sd2t7t1NT-XxtWqT_ZM"
BUDGET_ID = "aca294de-e11a-4857-908f-bf19edaee302"
TRANSACTIONS_FILE = "data/ynab_transactions.csv"
ACCOUNTS_FILE = "data/ynab_accounts.csv"
CATEGORIES_FILE = "data/ynab_categories.csv"
TRANSACTIONS_TABLE_NAME = "ynab_transactions"
ACCOUNTS_TABLE_NAME = "ynab_accounts"
CATEGORIES_TABLE_NAME = "ynab_categories"

API_URL = f"https://api.ynab.com/v1/budgets/{BUDGET_ID}"
HEADERS = {"Authorization": f"Bearer {YNAB_TOKEN}"}


def get_data():
    accounts_response = requests.get(f"{API_URL}/accounts", headers=HEADERS)
    accounts_response.raise_for_status()
    accounts = accounts_response.json()['data']['accounts']

    categories_response = requests.get(f"{API_URL}/categories", headers=HEADERS)
    categories_response.raise_for_status()
    categories = []
    for group in categories_response.json()['data']['category_groups']:
        for cat in group['categories']:
            categories.append(cat)

    transactions = []
    next_url = f"{API_URL}/transactions"
    while next_url:
        response = requests.get(next_url, headers=HEADERS)
        response.raise_for_status()
        data = response.json()
        transactions.extend(data['data']['transactions'])
        next_url = None
        if 'next' in data['data'].get('pagination', {}):
            next_url = data['data']['pagination']['next']

    return {
        'accounts': accounts,
        'categories': categories,
        'transactions': transactions
    }


def transform_transactions(data):
    account_lookup = {acc['id']: acc['name'] for acc in data['accounts']}
    category_lookup = {cat['id']: cat['name'] for cat in data['categories']}
    for txn in data['transactions']:
        txn['account_name'] = account_lookup.get(txn['account_id'], "Unknown")
        txn['category_name'] = category_lookup.get(txn['category_id'], "Uncategorized")
    return data['transactions']


def write_to_csv(rows, filename):
    if not rows:
        print(f"No data to save to {filename}")
        return
    fieldnames = rows[0].keys()
    with open(filename, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved {len(rows)} records to {filename}")


def write_bigquery(transactions, accounts, categories):
    print("Writing transactions to BigQuery...")
    df_transactions = pd.DataFrame(transactions)
    lib_bq.write_to_bigquery(df_transactions, TRANSACTIONS_TABLE_NAME)

    print("Writing categories to BigQuery...")
    df_categories = pd.DataFrame(categories)
    lib_bq.write_to_bigquery(df_categories, CATEGORIES_TABLE_NAME)

    print("Writing accounts to BigQuery...")
    df_accounts = pd.DataFrame(accounts)
    # Drop debt columns that are empty structs and break Parquet/BigQuery load
    debt_cols = ["debt_interest_rates", "debt_minimum_payments", "debt_escrow_amounts"]
    for col in debt_cols:
        if col in df_accounts.columns:
            df_accounts = df_accounts.drop(columns=[col])
    lib_bq.write_to_bigquery(df_accounts, ACCOUNTS_TABLE_NAME)


def main():
    print("Fetching data...")
    data = get_data()
    write_to_csv(data['accounts'], ACCOUNTS_FILE)
    write_to_csv(data['categories'], CATEGORIES_FILE)
    transactions = transform_transactions(data)
    write_to_csv(transactions, TRANSACTIONS_FILE)
    write_bigquery(transactions, data['accounts'], data['categories'])


if __name__ == "__main__":
    main()