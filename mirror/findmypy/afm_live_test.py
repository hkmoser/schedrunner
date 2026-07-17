#!/usr/bin/env python3

import json
import csv
import os
import datetime
import logging
import pandas as pd
import lib_bq

from pyicloud import PyiCloudService

# logging.basicConfig(level=logging.DEBUG)

api = PyiCloudService('joe@joemoser.com')

j='AaHxu7OfOiBiW9RPAzTY7vwSj1JBHWOFhwRH6gExNXh5rVayf3TjFGKoJy2m+45YW5UJNAK/PJFIJA=='
t='AUGMI3r423xBlRKk7ikSZZP58FP99hjIG792YKzAfrECqtaVzcq1mKynHyqI5jlszlCTSyD7bce1lIy8r7dwAyWwmUyvLCYYE8/1YSPo3382+68OIz/iits7VydiuwHl83OjxK7yoE8kE4HHzm5shZOm7/i+C69OHrRNryBPhOkff9AjNm4h9A=='
stop='AXiaZlpiPpcsQR3vWRRhoMGFrCGUO0Tx68x0IOe5mml3QNQzE9DxZSPi8DjqtV36aPAARY0Y/Ri0xw=='

dt = datetime.datetime.now().strftime("%Y%m%d_%H%M")
dt_full = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

path="/Users/joemoser/Dropbox/Source/afm/findmypy/"
filename=path+"afm_test/afm_"+dt+".csv"
filename_all=path+"afm_test/afm_all.csv"
file_exists=os.path.exists(filename)
file_all_exists=os.path.exists(filename_all)

# keys = ['deviceID', 'deviceName'] | api.devices[j].location().keys() | api.devices[j].status().keys() 

column_order = ['date_time', 'deviceID', 'deviceName', 'deviceDisplayName', 'name', 'deviceStatus', 'batteryLevel', 'positionType', 'timeStamp', 'latitude', 'longitude', 'horizontalAccuracy', 'verticalAccuracy', 'locationFinished', 'isOld', 'isInaccurate', 'altitude', 'floorLevel', 'secureLocationTs', 'locationType', 'secureLocation', 'locationMode', 'addresses', 'prsId', 'batteryStatus', 'lowPowerMode', 'deviceWithYou', 'locationEnabled', 'deviceClass', 'deviceModel', 'rawDeviceModel', 'locationCapable', 'trackingInfo', 'audioChannels', 'darkWake']

with open(filename, "a", newline="") as f:
    w = csv.DictWriter(f, column_order)
    if not file_exists:
        w.writeheader()

    for k, dev in api.devices.items():
        print(dev)

        prefix = { 'date_time': dt_full, 'deviceID': k, 'deviceName': dev }

        data1 = dev.status(['prsId', 'batteryStatus', 'lowPowerMode', 'deviceWithYou', 'locationEnabled', 'deviceClass', 'deviceModel', 'rawDeviceModel', 'locationCapable', 'trackingInfo', 'audioChannels', 'darkWake'])

        if dev.location() == None:
            data2 = { 'positionType': 'None' }
        else:
            data2 = dev.location()

        w.writerow(prefix | data1 | data2)
        print()

df1 = pd.read_csv(filename)

if file_all_exists:
    df2 = pd.read_csv(filename_all)
else:
    df2 = pd.DataFrame()

df_combined = pd.concat([df1, df2], ignore_index=True)
df_combined.to_csv(filename_all, index=False)

lib_bq.append_to_bigquery(df1, 'afm_latest_live')

import lib_bq_changealerts
