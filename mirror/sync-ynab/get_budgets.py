import requests
import csv
import pandas as pd

import lib_bq

# Replace with your YNAB personal access token
YNAB_TOKEN = "YpxPH_8HxynRQXxgJkRrguo2Sd2t7t1NT-XxtWqT_ZM"
FILENAME = "data/budgets.csv"
BQ_TABLE_NAME = "ynab_budgets"

API_URL = "https://api.ynab.com/v1/budgets"
HEADERS = {
    "Authorization": f"Bearer {YNAB_TOKEN}"
}

def write_to_csv(rows, filename):
    if not rows:
        raise ValueError("No rows to write")
    fieldnames = rows[0].keys()
    with open(filename, mode='w', newline='', encoding='utf-8') as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

def get_data():
    response = requests.get(API_URL, headers=HEADERS)
    response.raise_for_status()
    budgets = response.json()['data']['budgets']
    return budgets

def write_bigquery(rows):
    df = pd.DataFrame(rows)
    lib_bq.write_to_bigquery(df, BQ_TABLE_NAME)

def main():
    rows = get_data()
    write_to_csv(rows, FILENAME)
    write_bigquery(rows)

if __name__ == "__main__":
    main()
