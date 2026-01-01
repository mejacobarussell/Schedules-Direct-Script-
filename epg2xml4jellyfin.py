#!/usr/bin/python3

import requests
import hashlib
import os
import time
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

# --- Configuration: Schedules Direct ---
USER_NAME = 'username'
PASSWORD = 'password'

BASE_URL = 'https://json.schedulesdirect.org/20141201'
OUTPUT_DIR = "/mnt/user/appdata/schedulesdirect"
OUTPUT_FILE = f"{OUTPUT_DIR}/schedulesdirect.xml"

# --- Configuration: Jellyfin API ---
# Ensure there is NO trailing slash at the end of the URL
JELLYFIN_URL = 'http://192.168.0.8:8096'
JELLYFIN_API_KEY = 'api key'
TRIGGER_JELLYFIN = True

def lprint(text):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {text}", flush=True)

def show_progress(label, duration=1):
    sys.stdout.write(f"[{datetime.now().strftime('%H:%M:%S')}] {label}: ")
    for i in range(0, 105, 5):
        sys.stdout.write(f"{i}% ")
        sys.stdout.flush()
        time.sleep(duration / 20)
    sys.stdout.write(" - Complete!\n")

os.makedirs(OUTPUT_DIR, exist_ok=True)

def get_token():
    password_hash = hashlib.sha1(PASSWORD.encode('utf-8')).hexdigest()
    try:
        res = requests.post(f"{BASE_URL}/token", json={"username": USER_NAME, "password": password_hash})
        res.raise_for_status()
        data = res.json()
        if data.get('code') == 0:
            return data.get('token')
        else:
            lprint(f"API Error: {data.get('message')}")
            return None
    except Exception as e:
        lprint(f"Auth failed: {e}")
        return None

def format_xmltv_date(date_str):
    clean = date_str.replace("-","").replace(":","").replace("T","").replace("Z","").split(".")[0]
    return f"{clean} +0000"

def trigger_jellyfin_refresh():
    if not TRIGGER_JELLYFIN: return
    
    headers = {
        "X-Emby-Token": JELLYFIN_API_KEY,
        "Content-Type": "application/json"
    }

    try:
        lprint("Searching for Jellyfin Refresh Task ID...")
        # Get the list of tasks to find the specific ID for this server
        tasks_url = f"{JELLYFIN_URL}/ScheduledTasks"
        tasks_res = requests.get(tasks_url, headers=headers, timeout=10)
        
        if tasks_res.status_code != 200:
            lprint(f"Failed to get tasks list. Status: {tasks_res.status_code}")
            return

        tasks = tasks_res.json()
        # Find task by Key or Name
        task_id = next((t['Id'] for t in tasks if t.get('Key') == 'RefreshGuide' or t.get('Name') == 'Refresh Guide'), None)

        if not task_id:
            lprint("Could not find the 'Refresh Guide' task in Jellyfin.")
            return

        lprint(f"Triggering Jellyfin Task ID: {task_id}")
        run_url = f"{JELLYFIN_URL}/ScheduledTasks/Running/{task_id}"
        response = requests.post(run_url, headers=headers, timeout=15)
        
        if response.status_code in [200, 204]:
            lprint("SUCCESS: Jellyfin guide refresh started.")
        else:
            lprint(f"Jellyfin returned status: {response.status_code}")

    except Exception as e:
        lprint(f"Could not connect to Jellyfin: {e}")

def main():
    lprint("--- Starting Multi-Lineup EPG Download ---")
    token = get_token()
    if not token: 
        lprint("Could not obtain token. Exiting.")
        return
    headers = {'token': token}

    # 1. Fetch Lineups
    lineups_res = requests.get(f"{BASE_URL}/lineups", headers=headers)
    lineups_data = lineups_res.json()
    
    if 'lineups' not in lineups_data:
        lprint(f"Error fetching lineups: {lineups_data}")
        return

    # 2. Collect Stations & Mapping Numbers
    master_stations_map = {}
    
    for entry in lineups_data.get('lineups', []):
        l_id = entry['lineup']
        lprint(f"Fetching mapping for: {l_id}")
        m_res = requests.get(f"{BASE_URL}/lineups/{l_id}", headers=headers).json()
        
        map_lookup = {}
        for m in m_res.get('map', []):
            sid = m['stationID']
            if 'atscMajor' in m and 'atscMinor' in m:
                chan_val = f"{m['atscMajor']}.{m['atscMinor']}"
            else:
                chan_val = m.get('channel')
            
            if sid not in map_lookup:
                map_lookup[sid] = []
            map_lookup[sid].append(chan_val)
        
        for s in m_res.get('stations', []):
            sid = s['stationID']
            if sid in map_lookup:
                for channel_num in map_lookup[sid]:
                    unique_key = f"{sid}_{channel_num}"
                    s_instance = s.copy()
                    s_instance['display_number'] = channel_num
                    master_stations_map[unique_key] = s_instance

    # 3. Get Schedules
    unique_sids = list(set(key.split('_')[0] for key in master_stations_map.keys()))
    schedules_raw = {}
    lprint(f"Requesting schedules for {len(unique_sids)} unique stations...")
    for i in range(0, len(unique_sids), 500):
        batch = [{"stationID": sid} for sid in unique_sids[i:i+500]]
        sched_res = requests.post(f"{BASE_URL}/schedules", headers=headers, json=batch)
        if sched_res.status_code == 200:
            for s_data in sched_res.json():
                schedules_raw[s_data['stationID']] = s_data

    # 4. Fetch Metadata
    all_prog_ids = []
    for s_id in schedules_raw:
        for p in schedules_raw[s_id].get('programs', []):
            all_prog_ids.append(p['programID'])
    
    all_prog_ids = list(set(all_prog_ids))
    programs_data = {}
    show_progress(f"Downloading Metadata for {len(all_prog_ids)} programs", duration=2)
    
    for i in range(0, len(all_prog_ids), 5000):
        batch = all_prog_ids[i:i+5000]
        prog_res = requests.post(f"{BASE_URL}/programs", headers=headers, json=batch)
        if prog_res.status_code == 200:
            for p in prog_res.json():
                programs_data[p['programID']] = p

    # 5. Build XMLTV
    root = ET.Element("tv", {"generator-info-name": "Jellyfin-Full-Auto"})
    
    for unique_key, s_info in master_stations_map.items():
        ch = ET.SubElement(root, "channel", id=unique_key)
        num = s_info.get('display_number', '')
        call = s_info.get('callsign', '')
        name = s_info.get('name', '')

        # Filter ShopLC branding
        display_label = call
        if name and ("SHOPLC" in name.upper() or "SHOP LC" in name.upper()):
            display_label = call

        if num:
            ET.SubElement(ch, "display-name").text = str(num)
            ET.SubElement(ch, "display-name").text = f"{num} {display_label}"
        
        ET.SubElement(ch, "display-name").text = display_label

        if 'logo' in s_info: 
            ET.SubElement(ch, "icon", src=s_info['logo']['URL'])

    # Build Programmes
    for unique_key, s_info in master_stations_map.items():
        sid = s_info['stationID']
        if sid not in schedules_raw: continue
            
        for p in schedules_raw[sid].get('programs', []):
            details = programs_data.get(p['programID'], {})
            
            start_dt = datetime.strptime(p['airDateTime'].replace("Z","").split(".")[0], "%Y-%m-%dT%H:%M:%S")
            stop_dt = start_dt + timedelta(seconds=p.get('duration', 0))
            
            prog = ET.SubElement(root, "programme", 
                                 start=format_xmltv_date(p['airDateTime']), 
                                 stop=stop_dt.strftime("%Y%m%d%H%M%S +0000"),
                                 channel=unique_key)
            
            ET.SubElement(prog, "title").text = details.get('titles', [{}])[0].get('title120', 'No Title')
            
            # REPEAT TAG FIX
            if not p.get('new', False):
                ET.SubElement(prog, "previously-shown")

            desc_text = ""
            if 'descriptions' in details:
                d = details['descriptions']
                desc_text = (d.get('description1000') or d.get('description100') or [{}])[0].get('description', '')
            if desc_text: ET.SubElement(prog, "desc").text = desc_text

            if 'metadata' in details:
                for meta in details['metadata']:
                    if 'Gracenote' in meta:
                        s_n, e_n = meta['Gracenote'].get('season'), meta['Gracenote'].get('episode')
                        if s_n and e_n:
                            ET.SubElement(prog, "episode-num", system="xmltv_ns").text = f"{int(s_n)-1}.{int(e_n)-1}.0/1"

    # 6. Save
    show_progress("Finalizing XML and setting permissions", duration=1)
    tree = ET.ElementTree(root)
    with open(OUTPUT_FILE, "wb") as f:
        tree.write(f, encoding='utf-8', xml_declaration=True)
    
    os.chmod(OUTPUT_FILE, 0o666) 
    lprint(f"--- SUCCESS: Guide saved to {OUTPUT_FILE} ---")
    trigger_jellyfin_refresh()

if __name__ == "__main__":
    main()
