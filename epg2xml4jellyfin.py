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
PASSWORD = 'YOURPASSWORD' 
BASE_URL = 'https://json.schedulesdirect.org/20141201'
OUTPUT_DIR = "/mnt/user/appdata/schedulesdirect"
OUTPUT_FILE = f"{OUTPUT_DIR}/guide.xml"

# --- Configuration: Jellyfin API ---
JELLYFIN_URL = 'http://192.168.1.XXX:8096'  # Change to your Unraid IP
JELLYFIN_API_KEY = 'YOUR_JELLYFIN_API_KEY'
TRIGGER_JELLYFIN = False 
# Use False or True
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
    refresh_url = f"{JELLYFIN_URL}/ScheduledTasks/Running/RefreshGuide"
    headers = {"X-Emby-Token": JELLYFIN_API_KEY}
    try:
        lprint("Triggering Jellyfin Guide Refresh...")
        requests.post(refresh_url, headers=headers, timeout=10)
        lprint("Jellyfin refresh task signaled.")
    except Exception as e:
        lprint(f"Error connecting to Jellyfin: {e}")

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
        
        # Pulling the map specifically to get channel numbers (4.1, 4.3, etc)
        m_res = requests.get(f"{BASE_URL}/lineups/{l_id}", headers=headers).json()
        
        # Create a lookup for the virtual channel numbers
        # Some lineups use 'channel', some use 'atscMajor'/'atscMinor'
        map_lookup = {}
        for m in m_res.get('map', []):
            sid = m['stationID']
            if 'atscMajor' in m and 'atscMinor' in m:
                map_lookup[sid] = f"{m['atscMajor']}.{m['atscMinor']}"
            else:
                map_lookup[sid] = m.get('channel')
        
        for s in m_res.get('stations', []):
            sid = s['stationID']
            s['display_number'] = map_lookup.get(sid, s.get('channel', ''))
            master_stations_map[sid] = s

    # 3. Get Schedules
    station_ids = list(master_stations_map.keys())
    schedules_raw = []
    lprint(f"Requesting schedules for {len(station_ids)} stations...")
    for i in range(0, len(station_ids), 500):
        batch = [{"stationID": sid} for sid in station_ids[i:i+500]]
        sched_res = requests.post(f"{BASE_URL}/schedules", headers=headers, json=batch)
        if sched_res.status_code == 200:
            schedules_raw.extend(sched_res.json())

    # 4. Fetch Metadata
    all_prog_ids = list(set(p['programID'] for s in schedules_raw for p in s.get('programs', [])))
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
    
    for s_id, s_info in master_stations_map.items():
        ch = ET.SubElement(root, "channel", id=s_id)
        
        num = s_info.get('display_number', '')
        call = s_info.get('callsign', '')

        # Add "4.3" as the first display name so Jellyfin can auto-match
        if num:
            ET.SubElement(ch, "display-name").text = str(num)
            ET.SubElement(ch, "display-name").text = f"{num} {call}"
        
        ET.SubElement(ch, "display-name").text = call

        if 'logo' in s_info: 
            ET.SubElement(ch, "icon", src=s_info['logo']['URL'])

    # Build Programmes
    for s_map in schedules_raw:
        xml_id = s_map['stationID']
        for p in s_map.get('programs', []):
            details = programs_data.get(p['programID'], {})
            
            # Start/Stop times
            start_dt = datetime.strptime(p['airDateTime'].replace("Z","").split(".")[0], "%Y-%m-%dT%H:%M:%S")
            stop_dt = start_dt + timedelta(seconds=p.get('duration', 0))
            
            prog = ET.SubElement(root, "programme", 
                                 start=format_xmltv_date(p['airDateTime']), 
                                 stop=stop_dt.strftime("%Y%m%d%H%M%S +0000"),
                                 channel=xml_id)
            
            ET.SubElement(prog, "title").text = details.get('titles', [{}])[0].get('title120', 'No Title')
            
            # Description
            desc_text = ""
            if 'descriptions' in details:
                d = details['descriptions']
                desc_text = (d.get('description1000') or d.get('description100') or [{}])[0].get('description', '')
            if desc_text: ET.SubElement(prog, "desc").text = desc_text

            # Season/Episode
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
