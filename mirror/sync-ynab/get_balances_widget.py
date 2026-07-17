import requests
import csv
import json

import lib_bq

PUSHCUT_API_KEY = "QFNjvttld5Fem3eor-5pd"
PUSHCUT_WIDGET_NAME = "YNAB Balance"
 
# Replace with your YNAB access token and budget ID
YNAB_TOKEN = "YpxPH_8HxynRQXxgJkRrguo2Sd2t7t1NT-XxtWqT_ZM"
BUDGET_ID = "aca294de-e11a-4857-908f-bf19edaee302"
FILENAME = "data/account_balances.csv"

API_URL = f"https://api.ynab.com/v1/budgets/{BUDGET_ID}"
HEADERS = {"Authorization": f"Bearer {YNAB_TOKEN}"}

def get_account_balances():
    response = requests.get(f"{API_URL}/accounts", headers=HEADERS)
    response.raise_for_status()
    accounts = response.json()['data']['accounts']
    
    with open(FILENAME, 'w', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=["id", "name", "balance", "cleared_balance", "uncleared_balance"])
        writer.writeheader()
        
        for account in accounts:
            writer.writerow({
                "id": account["id"],
                "name": account["name"],
                "balance": account["balance"] / 1000,
                "cleared_balance": account["cleared_balance"] / 1000,
                "uncleared_balance": account["uncleared_balance"] / 1000
            })
    
    total_balance = sum(account["cleared_balance"] for account in accounts) / 1000
    formatted_balance = f"${total_balance:,.2f}"

    pushcut_url = (
        f"https://api.pushcut.io/{PUSHCUT_API_KEY}/widgets/{PUSHCUT_WIDGET_NAME}"
    )

    payload = {
        "inputs": {
            "input0": "Balance",
            "input1": formatted_balance
        }
    }

    response = requests.post(pushcut_url, json=payload)
    response.raise_for_status()
    print(f"Pushcut widget '{PUSHCUT_WIDGET_NAME}' inputs updated with total balance: {formatted_balance}")

if __name__ == "__main__":
    get_account_balances()
