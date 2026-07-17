#!/usr/bin/env python3

import json
import csv
import os
import datetime
import logging
import pandas as pd
import lib_bq
import time

from pyicloud import PyiCloudService

# logging.basicConfig(level=logging.DEBUG)

api = PyiCloudService('joe@joemoser.com')

j='AaHxu7OfOiBiW9RPAzTY7vwSj1JBHWOFhwRH6gExNXh5rVayf3TjFGKoJy2m+45YW5UJNAK/PJFIJA=='
t='AUGMI3r423xBlRKk7ikSZZP58FP99hjIG792YKzAfrECqtaVzcq1mKynHyqI5jlszlCTSyD7bce1lIy8r7dwAyWwmUyvLCYYE8/1YSPo3382+68OIz/iits7VydiuwHl83OjxK7yoE8kE4HHzm5shZOm7/i+C69OHrRNryBPhOkff9AjNm4h9A=='
stop='AXiaZlpiPpcsQR3vWRRhoMGFrCGUO0Tx68x0IOe5mml3QNQzE9DxZSPi8DjqtV36aPAARY0Y/Ri0xw=='

dt = datetime.datetime.now().strftime("%Y%m%d_%H%M")
dt_full = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

path="/Users/joemoser/Dropbox/Source/afm/findmypy/"
filename=path+"afm/afm_"+dt+".csv"
filename_all=path+"afm/afm_all.csv"
file_exists=os.path.exists(filename)
file_all_exists=os.path.exists(filename_all)

# keys = ['deviceID', 'deviceName'] | api.devices[j].location().keys() | api.devices[j].status().keys() 

column_order = ['date_time', 'deviceID', 'deviceName', 'deviceDisplayName', 'name', 'deviceStatus', 'batteryLevel', 'positionType', 'timeStamp', 'latitude', 'longitude', 'horizontalAccuracy', 'verticalAccuracy', 'locationFinished', 'isOld', 'isInaccurate', 'altitude', 'floorLevel', 'secureLocationTs', 'locationType', 'secureLocation', 'locationMode', 'addresses', 'prsId', 'batteryStatus', 'lowPowerMode', 'deviceWithYou', 'locationEnabled', 'deviceClass', 'deviceModel', 'rawDeviceModel', 'locationCapable', 'trackingInfo', 'audioChannels', 'darkWake']

SKIP_MODELS = ['FifthGen-white','SecondGen-white','iphoneSE-1-1-0','AirPods_8194','FourthGen','MacBookPro15_1-spacegray','TenthGen-2-3-0','MacBookPro15_2-spacegray','FirstGen','Mac15_6-spaceblack','Mac14_3','MacPro3_1','MacBookPro5_5']  # Add models to skip here

print("Script started at: {}".format(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
start_time = time.time()

with open(filename, "a", newline="") as f:
    w = csv.DictWriter(f, column_order)
    if not file_exists:
        w.writeheader()

    for k, dev in api.devices.items():
        if dev.data.get('deviceModel') in SKIP_MODELS:
            # print(f"Skipping device {dev.data.get('deviceModel')}")
            continue

        # print(dev)

        prefix = { 'date_time': dt_full, 'deviceID': k, 'deviceName': dev }

        data1 = dev.status(['prsId', 'batteryStatus', 'lowPowerMode', 'deviceWithYou', 'locationEnabled', 'deviceClass', 'deviceModel', 'rawDeviceModel', 'locationCapable', 'trackingInfo', 'audioChannels', 'darkWake'])

        if data1.get('rawDeviceModel') == 'iPhone17,3':
            data1['deviceModel'] = 'iphone12mini-1-17-0'

        if data1.get('rawDeviceModel') == 'iPhone13,1':
            data1['deviceModel'] = 'i12o'

        if dev.location() == None:
            data2 = { 'positionType': 'None' }
        else:
            data2 = dev.location()

        w.writerow(prefix | data1 | data2)
        # print()

print("Data collection from iCloud completed at: {}. Time taken: {:.2f} seconds".format(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), time.time() - start_time))

df1 = pd.read_csv(filename)

if file_all_exists:
    df2 = pd.read_csv(filename_all)
else:
    df2 = pd.DataFrame()

df_combined = pd.concat([df1, df2], ignore_index=True)
df_combined.to_csv(filename_all, index=False)

print("Writing to files completed at: {}. Time taken: {:.2f} seconds".format(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), time.time() - start_time))

lib_bq.append_to_bigquery(df1, 'afm_latest_live')

lib_bq.materialize_view(view_name='afm_now_live', destination_table='afm_now_live_mat',)
lib_bq.materialize_view(view_name='afm_change_live_vw', destination_table='afm_change_live_vw_mat',)
lib_bq.materialize_view(view_name='afm_now_ch_live', destination_table='afm_now_ch_live_mat',)

print("Uploading to BigQuery completed at: {}. Time taken: {:.2f} seconds".format(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), time.time() - start_time))


import lib_bq_changealerts
