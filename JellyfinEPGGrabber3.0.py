#!/usr/bin/python3
import os
import sys
import subprocess
import json
import datetime
import xml.etree.ElementTree as ET
from xml.dom import minidom
import pwd
import grp

# --- CONFIGURATION ---
USER_NAME = "username"
PASSWORD_HASH = "hashed password" 
BASE_URL = "https://json.schedulesdirect.org/20141201"
OUTPUT_DIR = "/mnt/user/appdata/schedulesdirect"
XML_OUTPUT = os.path.join(OUTPUT_DIR, "guide.xml")
USER_AGENT = "JellyfinEPGGrabberV3.0/Unraid"
#by mejacobarussell 
#downloaded from https://github.com/mejacobarussell/Schedules-Direct-Script/edit/main/JellyfinEPGGrabber3.0.py
DAYS_TO_FETCH = 7 # Increased to 7 days
VERBOSE = True 

try:
    import requests
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])
    import requests

class SchedulesDirectAPI:
    def __init__(self):
        self.token = self.get_token()
    def get_token(self):
        if VERBOSE: print(f"[DEBUG] Authenticating as {USER_AGENT}...")
        payload = {"username": USER_NAME, "password": PASSWORD_HASH}
        r = requests.post(f"{BASE_URL}/token", json=payload, headers={'User-Agent': USER_AGENT})
        return r.json().get('token')
    def post_request(self, endpoint, data):
        headers = {'token': self.token, 'User-Agent': USER_AGENT}
        r = requests.post(f"{BASE_URL}/{endpoint}", json=data, headers=headers)
        return r.json()

def format_date(sd_date):
    clean = sd_date.replace("-", "").replace(":", "").replace("T", "").replace("Z", "")
    return f"{clean[:14]} +0000"

def set_permissions(path):
    """Applies chown nobody:users and chmod 777."""
    try:
        uid = pwd.getpwnam("nobody").pw_uid
        gid = grp.getgrnam("users").gr_gid
        os.chown(path, uid, gid)
        os.chmod(path, 0o777)
    except Exception as e:
        print(f"[ERROR] Permissions failed: {e}")

def generate_xml():
    api = SchedulesDirectAPI()
    if not api.token: return

    # 1. Get Stations & Lineup Map
    lineup_resp = requests.get(f"{BASE_URL}/lineups", headers={'token': api.token, 'User-Agent': USER_AGENT}).json()
    lineup_id = lineup_resp['lineups'][0]['lineup']
    stations_data = requests.get(f"{BASE_URL}/lineups/{lineup_id}", headers={'token': api.token, 'User-Agent': USER_AGENT}).json()
    stations = stations_data.get('stations', [])
    map_data = stations_data.get('map', [])
    
    root = ET.Element("tv", {"generator-info-name": USER_AGENT})

    id_map = {}
    station_ids = []

    # Build lookup for ATSC Major.Minor
    channel_lookup = {}
    for entry in map_data:
        sid = entry.get('stationID')
        if 'atscMajor' in entry and 'atscMinor' in entry:
            channel_lookup[sid] = f"{entry['atscMajor']}.{entry['atscMinor']}"
        elif 'channel' in entry:
            channel_lookup[sid] = entry['channel'].replace('_', '.')

    for s in stations:
        sd_id = s['stationID']
        station_ids.append(sd_id)
        
        display_number = channel_lookup.get(sd_id, s.get('channel', sd_id)).replace('_', '.')
        id_map[sd_id] = display_number
        
        channel_node = ET.SubElement(root, "channel", id=display_number)
        ET.SubElement(channel_node, "display-name").text = display_number
        ET.SubElement(channel_node, "display-name").text = s.get('callsign', sd_id)
        
        # Pull Icon from SD
        if 'stationLogos' in s and len(s['stationLogos']) > 0:
            logo_url = s['stationLogos'][0].get('URL')
            if logo_url:
                ET.SubElement(channel_node, "icon", src=logo_url)

    # 2. Fetch Schedule (Iterative for 7 days)
    if VERBOSE: print(f"[DEBUG] Fetching {DAYS_TO_FETCH} days of data...")
    today = datetime.date.today()
    dates = [(today + datetime.timedelta(days=x)).isoformat() for x in range(DAYS_TO_FETCH)]
    batch_query = [{"stationID": sid, "date": dates} for sid in station_ids]
    schedules = api.post_request("schedules", batch_query)

    # 3. Fetch Metadata
    unique_prog_ids = {p['programID'] for sched in schedules for p in sched.get('programs', [])}
    meta_cache = {}
    prog_list = list(unique_prog_ids)
    for i in range(0, len(prog_list), 5000):
        chunk = prog_list[i:i + 5000]
        meta_response = api.post_request("programs", chunk)
        if isinstance(meta_response, list):
            for m in meta_response:
                meta_cache[m['programID']] = m

    # 4. Build Programmes
    for sched in schedules:
        current_sid = sched['stationID']
        xml_chan_id = id_map.get(current_sid)
        
        for prog in sched.get('programs', []):
            p_id = prog['programID']
            meta = meta_cache.get(p_id, {})
            start_dt = datetime.datetime.strptime(prog['airDateTime'].replace("Z", ""), "%Y-%m-%dT%H:%M:%S")
            end_dt = start_dt + datetime.timedelta(seconds=prog['duration'])
            
            p_node = ET.SubElement(root, "programme", 
                                    start=format_date(prog['airDateTime']), 
                                    stop=format_date(end_dt.isoformat()), 
                                    channel=xml_chan_id)

            title = meta.get('titles', [{}])[0].get('title120', "To Be Announced")
            ET.SubElement(p_node, "title").text = title
            
            if not p_id.startswith("MV"):
                ET.SubElement(p_node, "category").text = "Series"
                ET.SubElement(p_node, "category").text = "tvshow"
            if "News" in title:
                ET.SubElement(p_node, "category").text = "News"

            # Episode Logic
            air_dt = datetime.datetime.strptime(prog['airDateTime'][:10], "%Y-%m-%d")
            if p_id.startswith("EP"):
                s_num = int(p_id[2:6])
                e_num = int(p_id[6:10])
                ET.SubElement(p_node, "episode-num", system="xmltv_ns").text = f"{s_num-1}.{e_num-1}.0"
                ET.SubElement(p_node, "episode-num", system="onscreen").text = f"S{s_num:02d}E{e_num:02d}"
            else:
                year = air_dt.year
                month_day = int(air_dt.strftime('%m%d'))
                ET.SubElement(p_node, "episode-num", system="xmltv_ns").text = f"{year-1}.{month_day-1}.0"
                ET.SubElement(p_node, "episode-num", system="onscreen").text = f"S{year}E{air_dt.strftime('%m%d')}"

            if "News" in title or prog.get('new'):
                ET.SubElement(p_node, "new")
            else:
                ET.SubElement(p_node, "previously-shown")

    # 5. Save
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)
        set_permissions(OUTPUT_DIR)

    xml_data = ET.tostring(root, encoding='utf-8')
    reparsed = minidom.parseString(xml_data)
    with open(XML_OUTPUT, "w", encoding="utf-8") as f:
        f.write(reparsed.toprettyxml(indent="  "))
    
    set_permissions(XML_OUTPUT)
    if VERBOSE: print(f"[INFO] Success. {DAYS_TO_FETCH} days generated at {XML_OUTPUT}")

if __name__ == "__main__":
    generate_xml()
