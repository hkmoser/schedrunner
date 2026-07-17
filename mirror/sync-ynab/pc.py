import requests
import pandas as pd
import datetime
import json
from google.cloud import bigquery

path="/Users/joemoser/Dropbox/Source/afm/ynab/"
counter_file=path+'ctr_last.txt'

def send_alert(title, text):

    # Pushcut API URL and your API Key
    api_key = "QFNjvttld5Fem3eor-5pd"
    notification_name = "Zap%20Alert"
    url = f"https://api.pushcut.io/{api_key}/notifications/{notification_name}"

    # Optional payload for the notification
    payload = {
        "text": text,
        "title": title,
    }

    # Headers
    # headers = {
    #     "Authorization": f"Bearer {api_key}",
    #     "Content-Type": "application/json",
    # }

    # Send the POST request
    response = requests.post(url, json=payload)

    # Check the response
    if response.status_code == 200:
        print("Notification sent successfully!")
    else:
        print(f"Failed to send notification: {response.status_code} - {response.text}")

# send_alert()

print(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

