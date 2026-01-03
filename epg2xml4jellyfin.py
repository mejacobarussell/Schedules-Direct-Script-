#!/usr/bin/python3
#
# Developed by Jacob Russell Free ot use and modify to fit your needs.
# Pleaselook at toggles section and enable disable as needed.
# this is version 3.0
#
#
import requests
import hashlib
import os
import time
import sys
import shutil
import random
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
#
# --- Configuration ---
USER_NAME = 'user name'
PASSWORD = 'password' 
BASE_URL = 'https://json.schedulesdirect.org/20141201'
OUTPUT_DIR = "/mnt/user/appdata/schedulesdirect"
OUTPUT_FILE = f"{OUTPUT_DIR}/guide.xml"
LOGO_DIR = f"{OUTPUT_DIR}/logos"
#
# --- Toggles ---
DEBUG = True       # Set to False to hide granular details
TEST_MODE = False  # Set to True to only process 1 random channel for speed
SAVE_JSON = False  # Set to False to stop saving raw .json files to disk where xml is stored.
TRIGGER_JELLYFIN = False 
JELLYFIN_URL = 'http://192.168.1.XXX:8096' 
JELLYFIN_API_KEY = 'YOUR_API_KEY'

def lprint(text, is_debug=False):
    if is_debug and not DEBUG:
        return
    prefix = "[DEBUG]" if is_debug else "[INFO]"
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {prefix} {text}", flush=True)

def apply_permissions(path):
    lprint(f"Setting permissions (777, nobody:users) on {path}...", is_debug=True)
    try:
        os.chmod(path, 0o777)
        shutil.chown(path, user="nobody", group="users")
        for root, dirs, files in os.walk(path):
            for d in dirs:
                p = os.path.join(root, d)
                os.chmod(p, 0o777)
                shutil.chown(p, user="nobody", group="users")
            for f in files:
                p = os.path.join(root, f)
                os.chmod(p, 0o777)
                shutil.chown(p, user="nobody", group="users")
    except Exception as e:
        lprint(f"Permission error: {e}")

def get_token():
    lprint("Logging in to Schedules Direct...", is_debug=True)
    password_hash = hashlib.sha1(PASSWORD.encode('utf-8')).hexdigest()
    try:
        res = requests.post(f"{BASE_URL}/token", json={"username": USER_NAME, "password": password_hash})
        data = res.json()
        return data.get('token') if data.get('code') == 0 else None
    except:
        return None

def format_xmltv_date(date_str):
    clean = date_str.replace("-","").replace(":","").replace("T","").replace("Z","").split(".")[0]
    return f"{clean} +0000"

def main():
    if TEST_MODE:
        lprint("!!! TEST MODE ENABLED: Only processing 1 random channel !!!")
    
    os.makedirs(LOGO_DIR, exist_ok=True)
    token = get_token()
    if not token: 
        lprint("Login failed.")
        return
    headers = {'token': token}

    # 1. Fetch Lineups
    lineups_res = requests.get(f"{BASE_URL}/lineups", headers=headers).json()
    master_stations_map = {}
    
    for entry in lineups_res.get('lineups', []):
        l_id = entry['lineup']
        lprint(f"Scraping lineup: {l_id}", is_debug=True)
        m_res = requests.get(f"{BASE_URL}/lineups/{l_id}", headers=headers).json()
        map_lookup = {m['stationID']: (f"{m['atscMajor']}.{m['atscMinor']}" if 'atscMajor' in m else m.get('channel')) for m in m_res.get('map', [])}
        for s in m_res.get('stations', []):
            sid = s['stationID']
            s['display_number'] = map_lookup.get(sid, '')
            master_stations_map[sid] = s

    # Pick a random channel for TEST_MODE
    station_ids = list(master_stations_map.keys())
    if TEST_MODE and station_ids:
        random_sid = random.choice(station_ids)
        lprint(f"Test Mode: Picked {master_stations_map[random_sid].get('callsign')} (ID: {random_sid})")
        station_ids = [random_sid]
        master_stations_map = {random_sid: master_stations_map[random_sid]}

    # 2. Get Schedules
    schedules_raw = []
    lprint(f"Downloading schedules for {len(station_ids)} station(s)...")
    for i in range(0, len(station_ids), 500):
        batch = [{"stationID": sid} for sid in station_ids[i:i+500]]
        sched_res = requests.post(f"{BASE_URL}/schedules", headers=headers, json=batch)
        if sched_res.status_code == 200:
            schedules_raw.extend(sched_res.json())

    if SAVE_JSON:
        with open(f"{OUTPUT_DIR}/raw_schedules.json", "w") as f:
            json.dump(schedules_raw, f, indent=4)
        lprint("Saved raw_schedules.json to disk.", is_debug=True)

    # 3. Fetch Metadata
    all_prog_ids = list(set(p['programID'] for s in schedules_raw for p in s.get('programs', [])))
    programs_data = {}
    lprint(f"Downloading details for {len(all_prog_ids)} programs...")
    for i in range(0, len(all_prog_ids), 5000):
        batch = all_prog_ids[i:i+5000]
        prog_res = requests.post(f"{BASE_URL}/programs", headers=headers, json=batch)
        if prog_res.status_code == 200:
            for p in prog_res.json():
                programs_data[p['programID']] = p

    if SAVE_JSON:
        with open(f"{OUTPUT_DIR}/raw_programs.json", "w") as f:
            json.dump(list(programs_data.values()), f, indent=4)
        lprint("Saved raw_programs.json to disk.", is_debug=True)

    # 4. Build XMLTV
    lprint("Building XMLTV structure...")
    root = ET.Element("tv", {"generator-info-name": "Jellyfin-Full-Clean"})
    
    # Channels
    for s_id, s_info in master_stations_map.items():
        ch = ET.SubElement(root, "channel", id=s_id)
        if s_info.get('display_number'):
            ET.SubElement(ch, "display-name").text = str(s_info['display_number'])
        ET.SubElement(ch, "display-name").text = s_info.get('callsign', '')
        
        if 'logo' in s_info:
            remote_url = s_info['logo']['URL']
            ext = remote_url.split('.')[-1].split('_')[0]
            local_name = f"{s_id}.{ext}"
            local_path = os.path.join(LOGO_DIR, local_name)
            if not os.path.exists(local_path):
                try:
                    r = requests.get(remote_url)
                    with open(local_path, 'wb') as f: f.write(r.content)
                except: pass
            ET.SubElement(ch, "icon", src=local_path)

    today_dt = datetime.now().date()
    today_str = today_dt.strftime("%Y-%m-%d")

    # Programs
    for s_map in schedules_raw:
        xml_id = s_map['stationID']
        for p in s_map.get('programs', []):
            prog_id = p['programID']
            details = programs_data.get(prog_id, {})
            start_dt = datetime.strptime(p['airDateTime'].replace("Z","").split(".")[0], "%Y-%m-%dT%H:%M:%S")
            stop_dt = start_dt + timedelta(seconds=p.get('duration', 0))
            
            prog = ET.SubElement(root, "programme", 
                                 start=format_xmltv_date(p['airDateTime']), 
                                 stop=stop_dt.strftime("%Y%m%d%H%M%S +0000"),
                                 channel=xml_id)
            
            # Series & Repeat Logic
            orig_air_date = details.get('originalAirDate', '')
            is_new = p.get('new') or orig_air_date == today_str
            if is_new:
                ET.SubElement(prog, "new")
            else:
                ET.SubElement(prog, "previously-shown", start=orig_air_date.replace("-",""))

            ET.SubElement(prog, "title").text = details.get('titles', [{}])[0].get('title120', 'No Title')
            ET.SubElement(prog, "series-id", system="gracenote").text = prog_id[:10]
            if prog_id.startswith("SH"):
                ET.SubElement(prog, "episode-num", system="xmltv_ns").text = ". ."
            ET.SubElement(prog, "episode-num", system="dd_progid").text = prog_id
            
            for g in details.get('genres', []):
                ET.SubElement(prog, "category").text = g

    # 5. Save & Permissions
    tree = ET.ElementTree(root)
    with open(OUTPUT_FILE, "wb") as f:
        tree.write(f, encoding='utf-8', xml_declaration=True)
    
    apply_permissions(OUTPUT_DIR)
    lprint(f"--- FINISHED: Guide saved to {OUTPUT_FILE} ---")

if __name__ == "__main__":
    main()
