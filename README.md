Buy a Paramedic a coffee (or a line of code!) 

"Hi there! By day (and often by night), I’m a paramedic working on the front lines. When I’m not on the road, I’m at my desk diving into the world of computer science. It’s my favorite way to decompress after a long shift.

Your support helps keep me caffeinated for those 24-hour shifts and contributes to my learning journey in tech. Whether it's a 'thank you' for my service or just a shared love for clean code, I truly appreciate the support!"
Getting the family to adopt Jellyfin and Plex instead of paying for TV has been hard for me. They want it to be easy to use and always work.

<script type="text/javascript" src="https://cdnjs.buymeacoffee.com/1.0.0/button.prod.min.js" data-name="bmc-button" data-slug="yourditchdoc" data-color="#FFDD00" data-emoji="☕"  data-font="Arial" data-text="Buy a Paramedic a coffee." data-outline-color="#000000" data-font-color="#000000" data-coffee-color="#ffffff" ></script>

With JellyFin being broken when working with schedules direct I tried dockers and all sorts of stuff, but running a nextPVR docker just for guide data didnt make sense to me. I created this script over a week with what free time I had.

🛰️This is my Schedules Direct to Jellyfin EPG Optimizer
An automated Electronic Program Guide (EPG) tool built for Unraid users. This script pulls data from the Schedules Direct JSON API, merges multiple lineups (OTA, Cable, Satellite), and formats the output into a rich XMLTV file optimized for Jellyfin Live TV.

🚀 Key Features
Multi-Lineup Support: Aggregates all channels from all lineups on your SD account.

Auto-Mapping Fix: Injects virtual channel numbers (e.g., 4.1, 4.3) into <display-name> tags to ensure Jellyfin automatically links channels to your tuner.

Metadata Rich: Includes high-resolution icons, long-form descriptions, and xmltv_ns season/episode formatting.

Jellyfin API Trigger: Automatically tells Jellyfin to refresh its guide as soon as the download finishes.

Progress Tracking: Provides real-time visual feedback in 5% increments during execution.

🛠️ Configuration
Before running the script, update the variables at the top of the file:

USER_NAME & PASSWORD: Your Schedules Direct credentials.

OUTPUT_DIR: The folder on Unraid where the XML will be saved (default: /mnt/user/appdata/schedulesdirect).

JELLYFIN_URL: Your Unraid server's local IP and port (e.g., http://192.168.1.50:8096).

JELLYFIN_API_KEY: Generate this in Jellyfin under Dashboard > API Keys.

📂 Unraid Setup Instructions (User Scripts)
To automate your guide updates, use the User Scripts plugin on Unraid.

1. Create the Script

Go to your Unraid WebGUI -> Settings -> User Scripts.

Click Add New Script and name it Update-Jellyfin-EPG.

Click the gear icon next to the new script and select Edit Script.

Paste the entire Python script into the window and click Save Changes.

2. Set the Schedule

Change the schedule dropdown from "Manual" to Daily.

It is recommended to run this early in the morning (e.g., 3:00 AM) so your guide is fresh every day.

📺 Jellyfin Integration
1. Docker Path Mapping

Ensure your Jellyfin Docker container can see the folder where the script saves the XML.

Host Path: /mnt/user/appdata/schedulesdirect

Container Path: /data/guide (or similar)

2. Add Guide Provider

Open Jellyfin Dashboard > Live TV.

Under TV Guide Data Providers, click "+".

Select XMLTV.

Enter the container path to your file: /data/guide/guide.xml.

Refresh the guide data. Because this script includes the subchannel numbers in the metadata, Jellyfin should auto-map your channels immediately.
