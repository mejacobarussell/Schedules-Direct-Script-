#!/usr/bin/python3

import requests
import hashlib
import os
import time
import sys
import shutil
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

# --- Configuration ---
USER_NAME = 'user'
PASSWORD = 'password' 
BASE_URL = 'https://json.schedulesdirect.org/20141201'
OUTPUT_DIR = "/mnt/user/appdata/schedulesdirect"
OUTPUT_FILE = f"{OUTPUT_DIR}/guide.xml"
LOGO_DIR = f"{OUTPUT_DIR}/logos"

# --- Features ---
DEBUG = False  # Set to False to reduce log output
TRIGGER_JELLYFIN = False 
JELLYFIN_URL = 'http://192.168.1.XXX:8096' 
JELLYFIN_API_KEY = 'YOUR_API_KEY'

def lprint(text, is_debug=False):
    """Standard logging with a debug filter"""
    if is_debug and not DEBUG:
        return
    prefix = "[DEBUG]" if is_debug else "[INFO]"
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {prefix} {text}", flush=True)

def apply_permissions(path):
    """Recursively apply chmod 777 and chown nobody:users"""
    lprint(f"Applying permissions (777, nobody:users) to {path}...", is_debug=True)
    try:
        # Set root first
        os.chmod(path, 0o777)
        shutil.chown(path, user="nobody", group="users")
        
        for root, dirs, files in os.walk(path):
            for d in dirs:
                full_path = os.path.join(root, d)
                os.chmod(full_path, 0o777)
                shutil.chown(full_path, user="nobody", group="users")
            for f in files:
                full_path = os.path.join(root, f)
                os.chmod(full_path, 0o777)
                shutil.chown(full_path, user="nobody", group="users")
    except Exception as e:
        lprint(f"Warning: Could not set permissions: {e}")

def get_token():
    lprint("Requesting new token from Schedules Direct...", is_debug=True)
    password_hash = hashlib.sha1(PASSWORD.encode('utf-8')).hexdigest()
    try:
        res = requests.post(f"{BASE_URL}/token", json={"username": USER_NAME, "password": password_hash})
        data = res.json()
        if data.get('code') == 0:
            lprint("Token acquired successfully.", is_debug=True)
            return data.get('token')
        lprint(f"Auth error: {data.get('message')}")
        return None
    except Exception as e:
        lprint(f"Token request failed: {e}")
        return None

def format_xmltv_date(date_str):
    # Standard XMLTV format: YYYYMMDDHHMMSS +0000
    clean = date_str.replace("-","").replace(":","").replace("T","").replace("Z","").split(".")[0]
    return f"{clean} +0000"

def main():
    lprint("--- Starting EPG Update Process ---")
    
    os.makedirs(LOGO_DIR, exist_ok=True)
    token = get_token()
    if not token: return
    headers = {'token': token}

    # 1. Fetch Lineups
    lprint("Fetching user lineups...")
    lineups_res = requests.get(f"{BASE_URL}/lineups", headers=headers).json()
    master_stations_map = {}
    
    for entry in lineups_res.get('lineups', []):
        l_id = entry['lineup']
        lprint(f"Processing Lineup: {l_id}", is_debug=True)
        m_res = requests.get(f"{BASE_URL}/lineups/{l_id}", headers=headers).json()
        
        # Build channel map
        map_lookup = {m['stationID']: (f"{m['atscMajor']}.{m['atscMinor']}" if 'atscMajor' in m else m.get('channel')) for m in m_res.get('map', [])}
        
        for s in m_res.get('stations', []):
            sid = s['stationID']
            s['display_number'] = map_lookup.get(sid, '')
            master_stations_map[sid] = s

    # 2. Get Schedules
    station_ids = list(master_stations_map.keys())
    schedules_raw = []
    lprint(f"Downloading schedules for {len(station_ids)} stations...")
    for i in range(0, len(station_ids), 500):
        batch = [{"stationID": sid} for sid in station_ids[i:i+500]]
        sched_res = requests.post(f"{BASE_URL}/schedules", headers=headers, json=batch)
        if sched_res.status_code == 200:
            schedules_raw.extend(sched_res.json())

    # 3. Fetch Metadata
    all_prog_ids = list(set(p['programID'] for s in schedules_raw for p in s.get('programs', [])))
    programs_data = {}
    lprint(f"Downloading program details for {len(all_prog_ids)} items...")
    for i in range(0, len(all_prog_ids), 5000):
        batch = all_prog_ids[i:i+5000]
        prog_res = requests.post(f"{BASE_URL}/programs", headers=headers, json=batch)
        if prog_res.status_code == 200:
            for p in prog_res.json():
                programs_data[p['programID']] = p

    # 4. Build XMLTV
    lprint("Generating XMLTV file...")
    root = ET.Element("tv", {"generator-info-name": "Jellyfin-Debug-Logic"})
    
    # Process Channels & Local Icons
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
                lprint(f"Downloading logo for station {s_id}", is_debug=True)
                try:
                    img_data = requests.get(remote_url).content
                    with open(local_path, 'wb') as f:
                        f.write(img_data)
                except: 
                    lprint(f"Failed logo download for {s_id}", is_debug=True)
            
            ET.SubElement(ch, "icon", src=local_path)

    today_dt = datetime.now().date()
    today_str = today_dt.strftime("%Y-%m-%d")

    # Process Programs
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
            
            # Date-based Repeat/New logic
            orig_air_date_str = details.get('originalAirDate', '')
            is_new = p.get('new') or p.get('liveTapeDelay') == "Live" or orig_air_date_str == today_str
            
            if is_new:
                ET.SubElement(prog, "new")
            else:
                ET.SubElement(prog, "previously-shown", start=orig_air_date_str.replace("-",""))

            ET.SubElement(prog, "title").text = details.get('titles', [{}])[0].get('title120', 'No Title')
            if details.get('episodeTitle150'):
                ET.SubElement(prog, "sub-title").text = details['episodeTitle150']

            # Series/Episode Logic
            ET.SubElement(prog, "series-id", system="gracenote").text = prog_id[:10]
            if prog_id.startswith("SH"):
                ET.SubElement(prog, "episode-num", system="xmltv_ns").text = ". ."
            ET.SubElement(prog, "episode-num", system="dd_progid").text = prog_id

            # Metadata
            genres = details.get('genres', [])
            if "Series" not in genres: genres.append("Series")
            for g in genres:
                ET.SubElement(prog, "category").text = g

    # 5. Finalize
    tree = ET.ElementTree(root)
    with open(OUTPUT_FILE, "wb") as f:
        tree.write(f, encoding='utf-8', xml_declaration=True)
    
    # Sync permissions
    apply_permissions(OUTPUT_DIR)
    lprint(f"--- SUCCESS: Guide written to {OUTPUT_FILE} ---")

if __name__ == "__main__":
    main()
