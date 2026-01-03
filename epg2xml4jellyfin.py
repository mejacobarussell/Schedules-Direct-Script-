#!/usr/bin/python3

import requests
import hashlib
import os
import time
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

# --- Configuration ---
USER_NAME = 'user'
PASSWORD = 'pass' 
BASE_URL = 'https://json.schedulesdirect.org/20141201'
OUTPUT_DIR = "/mnt/user/appdata/schedulesdirect"
OUTPUT_FILE = f"{OUTPUT_DIR}/guide.xml"

def lprint(text):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {text}", flush=True)

def get_token():
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
    lprint("--- Starting Enhanced Series Guide Update ---")
    
    if os.path.exists(OUTPUT_FILE):
        os.remove(OUTPUT_FILE)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    token = get_token()
    if not token: return
    headers = {'token': token}

    # 1. Fetch Lineups
    lineups_res = requests.get(f"{BASE_URL}/lineups", headers=headers).json()
    master_stations_map = {}
    for entry in lineups_res.get('lineups', []):
        l_id = entry['lineup']
        m_res = requests.get(f"{BASE_URL}/lineups/{l_id}", headers=headers).json()
        map_lookup = {m['stationID']: (f"{m['atscMajor']}.{m['atscMinor']}" if 'atscMajor' in m else m.get('channel')) for m in m_res.get('map', [])}
        for s in m_res.get('stations', []):
            sid = s['stationID']
            s['display_number'] = map_lookup.get(sid, '')
            master_stations_map[sid] = s

    # 2. Get Schedules
    station_ids = list(master_stations_map.keys())
    schedules_raw = []
    for i in range(0, len(station_ids), 500):
        batch = [{"stationID": sid} for sid in station_ids[i:i+500]]
        sched_res = requests.post(f"{BASE_URL}/schedules", headers=headers, json=batch)
        if sched_res.status_code == 200:
            schedules_raw.extend(sched_res.json())

    # 3. Fetch Metadata
    all_prog_ids = list(set(p['programID'] for s in schedules_raw for p in s.get('programs', [])))
    programs_data = {}
    for i in range(0, len(all_prog_ids), 5000):
        batch = all_prog_ids[i:i+5000]
        prog_res = requests.post(f"{BASE_URL}/programs", headers=headers, json=batch)
        if prog_res.status_code == 200:
            for p in prog_res.json():
                programs_data[p['programID']] = p

    # 4. Build XMLTV
    root = ET.Element("tv", {"generator-info-name": "Jellyfin-Series-Logic"})
    
    # Channels
    for s_id, s_info in master_stations_map.items():
        ch = ET.SubElement(root, "channel", id=s_id)
        if s_info.get('display_number'):
            ET.SubElement(ch, "display-name").text = str(s_info['display_number'])
        ET.SubElement(ch, "display-name").text = s_info.get('callsign', '')
        if 'logo' in s_info: 
            ET.SubElement(ch, "icon", src=s_info['logo']['URL'])

    today_str = datetime.now().strftime("%Y-%m-%d")

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
            
            # --- NEW/REPEAT LOGIC ---
            orig_air_date = details.get('originalAirDate', '')
            is_new = p.get('new') or p.get('liveTapeDelay') == "Live" or orig_air_date == today_str
            
            if is_new:
                ET.SubElement(prog, "new")
            else:
                ET.SubElement(prog, "previously-shown")

            ET.SubElement(prog, "title").text = details.get('titles', [{}])[0].get('title120', 'No Title')
            if details.get('episodeTitle150'):
                ET.SubElement(prog, "sub-title").text = details['episodeTitle150']

            # --- FORCING SERIES RECORDING SUPPORT ---
            # 1. Provide a consistent Series-ID (first 10 chars of SHID)
            series_id = prog_id[:10]
            ET.SubElement(prog, "series-id", system="gracenote").text = series_id
            
            # 2. Provide a Unique-ID for the specific episode
            ET.SubElement(prog, "unique-id", type="gracenote").text = prog_id
            
            # 3. Add Category 'Series' explicitly
            genres = details.get('genres', [])
            if "Series" not in genres:
                genres.append("Series")
            for g in genres:
                ET.SubElement(prog, "category").text = g

            # 4. The "Episode-Num" Logic (Crucial for the Record Series Button)
            has_se_metadata = False
            if 'metadata' in details:
                for meta in details['metadata']:
                    if 'Gracenote' in meta:
                        s_n, e_n = meta['Gracenote'].get('season'), meta['Gracenote'].get('episode')
                        if s_n and e_n:
                            # Standard SxxExx format
                            ET.SubElement(prog, "episode-num", system="xmltv_ns").text = f"{int(s_n)-1}.{int(e_n)-1}.0/1"
                            has_se_metadata = True

            # If it's a series (starts with SH) but has no season info (like News), 
            # we add a placeholder to trigger Jellyfin's Series Mode.
            if not has_se_metadata and prog_id.startswith("SH"):
                ET.SubElement(prog, "episode-num", system="xmltv_ns").text = ". ."

            # Add the raw ID as a secondary episode-num system
            ET.SubElement(prog, "episode-num", system="dd_progid").text = prog_id

    # 5. Save
    tree = ET.ElementTree(root)
    with open(OUTPUT_FILE, "wb") as f:
        tree.write(f, encoding='utf-8', xml_declaration=True)
    os.chmod(OUTPUT_FILE, 0o666)
    lprint(f"--- SUCCESS: Created {OUTPUT_FILE} ---")

if __name__ == "__main__":
    main()
