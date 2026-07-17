import requests
import pandas as pd
import datetime
import json
import time
from google.cloud import bigquery

api_key = "QFNjvttld5Fem3eor-5pd"
path="/Users/joemoser/Dropbox/Source/afm/findmypy/test_"
counter_file=path+'ctr_last.txt'
counter_file_ch=path+'ctr_ch_last.txt'
state_file_ch=path+'ctr_ch_last_state.json'  # mvmt_type, battery_stat, batteryStatus per dev for "changes" channel
force_trigger_alerts=False


def _safe_bool(val):
    """Convert value to bool without raising on pandas NA/NaN."""
    return False if pd.isna(val) else bool(val)


def _norm(val):
    """Normalize for change detection: treat NaN/None as '', else str().strip()."""
    if val is None or pd.isna(val):
        return ''
    return str(val).strip()


def send_alert(title, text, channel="Zap%20Alert"):
    notification_name = channel
    url = f"https://api.pushcut.io/{api_key}/notifications/{notification_name}"

    payload = {
        "text": text,
        "title": title,
    }

    print(f"[pushcut] Sending alert to {channel} ...")
    response = requests.post(url, json=payload, timeout=30)

    if response.status_code == 200:
        print(f"[pushcut] Alert sent ({response.elapsed.total_seconds():.2f}s)")
    else:
        print(f"[pushcut] Failed: {response.status_code} - {response.text}")

def update_widget(title, text, widget_name="AFM Widget"):
    pushcut_widget_url = f"https://api.pushcut.io/{api_key}/widgets/{widget_name}"
    widget_payload = {
        "inputs": {
            "input0": title,
            "input1": text
        }
    }
    print(f"[pushcut] Updating widget {widget_name} ...")
    widget_response = requests.post(pushcut_widget_url, json=widget_payload, timeout=30)
    if widget_response.status_code == 200:
        print(f"[pushcut] Widget updated ({widget_response.elapsed.total_seconds():.2f}s)")
    else:
        print(f"[pushcut] Widget update failed: {widget_response.status_code} - {widget_response.text}")

def query_bigquery(query):
    client = bigquery.Client(project='ecstatic-pod-443723-f6')
    print("[bq] Running changealerts query ...")
    _t = time.time()
    query_job = client.query(query)
    query_job.result(timeout=300)
    print(f"[bq] Changealerts query done in {time.time() - _t:.2f}s")
    return query_job.to_dataframe()

def _load_ch_state(state_file=state_file_ch):
    """Load last mvmt_type, battery_stat, batteryStatus per device for change detection."""
    try:
        with open(state_file, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_ch_state(state, state_file=state_file_ch):
    with open(state_file, 'w') as f:
        json.dump(state, f, indent=0)


def _mvmt_bat_changed(dev, row, state):
    """True if mvmt_type, battery_stat, or batteryStatus changed since last alert for this dev."""
    prev = state.get(dev)
    if prev is None:
        return True
    return (
        _norm(row.get('mvmt_type')) != _norm(prev.get('mvmt_type'))
        or _norm(row.get('battery_stat')) != _norm(prev.get('battery_stat'))
        or _norm(row.get('batteryStatus')) != _norm(prev.get('batteryStatus'))
    )


def process_rows(df, counter_file=counter_file):
    last_dts = {}
    try:
        with open(counter_file, 'r') as f:
            lines = f.readlines()
            if lines:
                last_dts = {line.split()[0]: line.split()[1] for line in lines}
    except FileNotFoundError:
        pass
    
    rows_to_alert = []
    for index, row in df.iterrows():
        device_model = row.get('dev')
        last_dt = last_dts.get(device_model) or '' if device_model is not None else ''
        max_dt = row.get('max_dt')
        max_dt_str = str(max_dt).strip() if max_dt is not None else ''
        needs_alert = _safe_bool(row.get('needs_alert'))
        if (last_dt.strip() != max_dt_str and needs_alert) or force_trigger_alerts:
            rows_to_alert.append((index, row))
    return rows_to_alert

def send_alerts(rows,counter_file=counter_file,group="hm"):
    ch_state = _load_ch_state() if group == 'full' else {}
    for index, row in rows:
        if 'horizontalAccuracy' not in row:
            row['horizontalAccuracy'] = 'unk'
        else:
            row['horizontalAccuracy'] = round(float(row['horizontalAccuracy']),2)

        title = f"hm {row['at_hm']}{row['dev']}"
        template = """{mvmt_type} @ {cls_loc_ref} {cls_loc_ref_dist} > twd {twd_loc}
dist_ch {dist_ch} mph {dist_mph}
ha {horizontalAccuracy} locact {loc_active}
bat {battery_stat} ch {battery_ch} hr {battery_ch_hr}

poi {poi_name}
{poi_address}
{poi_distance}

bl {batteryLevel}
bstat {batteryStatus}

dt {max_dt}

so_same_loc {so_same_loc}
cls_loc {cls_loc}
time_diff_seconds {time_diff_seconds}
in_nh {in_nh}, mi {in_mi}, reg {in_reg}
dist_td {td_distance} jp {jp_distance} cf {cf_distance}
dist_ch {dist_td_ch} jp {dist_jp_ch} cf {dist_cf_ch}
dist_mph {dist_td_mph} jp {dist_jp_mph} cf {dist_cf_mph}

nw live {group}
dev {dev}
"""
        text = template.format(group=group, **row)
        print(f"Row {index} has needs_alert=True, Value: {row['dev']}")
        print(title)
        print(text)
        send_alert(title, text, "AFM%20"+group+"%20"+row['dev']+(' same_loc' if _safe_bool(row.get('so_same_loc')) else ''))

        # "AFM full {dev} changes" channel: same alert but only when mvmt_type, battery_stat, or batteryStatus changed
        if group == 'full' and row.get('dev') and (_mvmt_bat_changed(row['dev'], row, ch_state) or force_trigger_alerts):
            send_alert(title+" changes", text, "AFM%20full%20"+row['dev']+"%20changes")
            ch_state[row['dev']] = {
                'mvmt_type': _norm(row.get('mvmt_type')),
                'battery_stat': _norm(row.get('battery_stat')),
                'batteryStatus': _norm(row.get('batteryStatus')),
            }

        if row['dev'] == "12m":
            w_lines = [
                f"{row['at_hm']}{row['dev']} {row['mvmt_type']} {row['cls_loc_ref']} {row['cls_loc_ref_dist']} {row['twd_loc']}",
                f"{round(row['batteryLevel'],4)} ch {row['battery_ch_hr']} {row['batteryStatus']}",
                f"{row['loc_active']} ha {row['horizontalAccuracy']}"
            ]
            w_text = "\n".join(w_lines)
            print(w_text)
            update_widget('Last', w_text)

        device_model = row.get('dev')
        if device_model is not None:
            try:
                with open(counter_file, 'r') as f:
                    lines = f.readlines()
            except FileNotFoundError:
                lines = []  # counter file not created yet (untracked runtime state)
            with open(counter_file, 'w') as f:
                updated = False
                for line in lines:
                    if line.startswith(device_model):
                        f.write(f"{device_model} {row['max_dt']}\n")
                        updated = True
                    else:
                        f.write(line)
                if not updated:
                    f.write(f"{device_model} {row['max_dt']}\n")
    if group == 'full':
        _save_ch_state(ch_state)

print(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

# First batch of alerts
df1 = query_bigquery("""SELECT *,
         sync_dt >= datetime_add(current_datetime('America/New_York'), INTERVAL -90 SECOND) as in_sync,
         has_changed =
                     TRUE
            as needs_alert,
         FROM `ecstatic-pod-443723-f6.home_afm.afm_change_live_vw_mat`""")
rows_to_alert = process_rows(df1)

if rows_to_alert:
    send_alerts(rows_to_alert)
else:
    print("No alert")

# Second batch of alerts
df2 = query_bigquery("""with lastdt as (
  select deviceModel, max(date_time) as max_dt
  from home_afm.afm_now_ch_live_mat
  where deviceModel <> 'iPhone16-1-4-0-iphone12mini'
  group by deviceModel
)

select
    max_dt,
    batteryStatus,
    batteryLevel,
    in_nh,
    in_mi,
    in_reg,
    td_distance,
    jp_distance,
    cf_distance,
    so_distance,
    so_same_loc,
    horizontalAccuracy,
    loc_active,
    dist_td_ch,
    dist_jp_ch,
    dist_cf_ch,
    dist_ch,
    time_diff_seconds,
    dist_td_mph,
    dist_jp_mph,
    dist_cf_mph,
    dist_mph,
    mvmt_type,
    cls_loc,
    cls_loc_ref,
    cls_loc_ref_dist,
    twd_loc,
    battery_ch,
    battery_ch_hr,
    battery_stat,
    poi_name,
    poi_address,
    poi_distance,
    case when ancl.deviceModel like '%one16%' then "e16"
    when ancl.deviceModel like '%12min%' then "12m"
    end as dev,
    if(at_hm,"y","n") as at_hm,
    TRUE as has_changed,
    lastdt.max_dt = ancl.date_time as needs_alert
from home_afm.afm_now_ch_live_mat ancl
left join lastdt
on ancl.deviceModel = lastdt.deviceModel
where lastdt.max_dt = ancl.date_time
""")

rows_to_alert_ch = process_rows(df2, counter_file_ch)

if rows_to_alert_ch:
    send_alerts(rows_to_alert_ch, counter_file_ch, 'full')
else:
    print("No alert")
