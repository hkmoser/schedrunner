import requests
import pandas as pd
import datetime
import json
from google.cloud import bigquery

path="/Users/joemoser/Dropbox/Source/afm/findmypy/"
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

print(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

client = bigquery.Client(project='ecstatic-pod-443723-f6')

# Perform a query.
# QUERY = ("select * from ecstatic-pod-443723-f6.home_afm.afm_curr")
QUERY = ("""SELECT *,
         sync_dt >= datetime_add(current_datetime('America/New_York'), INTERVAL -90 SECOND) as in_sync,
         in_last_5min = TRUE
            # and sync_dt >= datetime_add(current_datetime('America/New_York'), INTERVAL -90 SECOND)
            and has_changed = TRUE
            as needs_alert,
         FROM `ecstatic-pod-443723-f6.home_afm.afm_change_vw`""")
query_job = client.query(QUERY)  # API request
rows = query_job.result()  # Waits for query to finish

# for row in rows:
#     print(row)

df = query_job.to_dataframe()
# df.to_csv('afm_output/your_results.csv', index=False)

with open(counter_file, 'r') as f:
    last_dt = f.read()

for index, row in df.iterrows():
    print(row)
    if last_dt != row['max_dt'] and row['needs_alert']:
        print("Alert")
        title = f"hm {row['at_hm']}{row['dev']}"
        text = f"dt { row['max_dt'] }\ndev { row['dev'] }\nhm { row['at_hm'] }\nch { row['has_changed'] }\nnh { row['in_nh'] },\nmi { row['in_mi'] },\nreg { row['in_reg'] },\ntd { row['td_distance'] },\njp { row['jp_distance'] },\ncf { row['cf_distance'] },\nlocact { row['loc_active'] }";
        # text = f"cls_loc { row['cls_loc'] }\nmvmt_type { row['mvmt_type'] }\nall_mph { row['all_mph'] }\ntd_distance_ch { row['td_distance_ch'] }\ntd_mph { row['td_mph'] }\njp_distance_ch { row['jp_distance_ch'] }\njp_mph { row['jp_mph'] }\ncf_distance_ch { row['cf_distance_ch'] }\ncf_mph { row['cf_mph'] }\n\ndt { row['max_dt'] }\ndev { row['dev'] }\nhm { row['at_hm'] }\nch { row['has_changed'] }\nnh { row['in_nh'] },\nmi { row['in_mi'] },\nreg { row['in_reg'] },\ntd { row['td_distance'] },\njp { row['jp_distance'] },\ncf { row['cf_distance'] },\nlocact { row['loc_active'] }";
        print(f"Row {index} has needs_alert=True, Value: {row['dev']}")
        print(title)
        print(text)
        send_alert(title, text)
        with open(counter_file, 'w') as f:
            f.write(row['max_dt'])
            f.close()
    else:
        print("No alert")


# client = bigquery.Client()
# # bucket_name = 'my-bucket'
# project = "bigquery-public-data"
# dataset_id = "samples"
# table_id = "shakespeare"

# destination_uri = "gs://{}/{}".format(bucket_name, "shakespeare.csv")
# dataset_ref = bigquery.DatasetReference(project, dataset_id)
# table_ref = dataset_ref.table(table_id)

# extract_job = client.extract_table(
#     table_ref,
#     destination_uri,
#     # Location must match that of the source table.
#     location="US",
# )  # API request
# extract_job.result()  # Waits for job to complete.

# print(
#     "Exported {}:{}.{} to {}".format(project, dataset_id, table_id, destination_uri)
# )

