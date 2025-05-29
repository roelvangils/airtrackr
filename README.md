# AirTracker 🏷️

AirTracker is an automated system that captures and tracks Apple AirTag locations over time by taking screenshots of the Find My app and extracting location data using OCR.

## Why AirTracker?

Apple's Find My app doesn't provide:
- Historical location data for AirTags
- API access to AirTag locations
- Export functionality for tracking data

AirTracker solves this by:
- Taking automated screenshots every minute
- Extracting location data using OCR
- Storing complete location history in a database
- Providing GPS coordinates via geocoding
- Handling device tracking even when positions change

## Features

- 📸 **Automated Screenshot Capture** - Takes windowed screenshots of Find My app every minute
- 🔍 **OCR Text Extraction** - Extracts device names, locations, distances, and timestamps
- 🗺️ **Geocoding** - Converts addresses to GPS coordinates using OpenStreetMap (free!)
- 🎯 **Smart Device Tracking** - Uses fuzzy matching to handle OCR errors and position changes
- 📊 **Relational Database** - Proper structure with device persistence and location history
- 🔗 **Google Maps Integration** - Direct links to view locations on Google Maps

## Prerequisites

- macOS (for screenshot functionality)
- Python 3.x
- Tesseract OCR (`brew install tesseract`)
- A dedicated Mac with Find My app open

## Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/airtrackr.git
cd airtrackr
```

2. Install Python dependencies:
```bash
pip install -r requirements.txt
```

3. Ensure Tesseract is installed:
```bash
brew install tesseract
```

## Initial Setup

1. Open Find My app and ensure your AirTags are visible:
```bash
open -a FindMy
```

2. Test screenshot capture:
```bash
python test_single_capture.py
```

3. View extracted regions to verify coordinates:
```bash
python view_regions.py
```

## Usage

### Start Continuous Monitoring

Run the tracker (captures every minute):
```bash
python airtracker.py
```

### View Your Devices

See all registered devices:
```bash
python view_device_history.py
```

Check specific device history:
```bash
python view_device_history.py "Black Valize"
python view_device_history.py "Jelles Keys"
```

View latest parsed locations:
```bash
python view_parsed_locations.py
```

### Keep It Running 24/7

**Option 1 - Background Process:**
```bash
nohup python airtracker.py > airtracker.log 2>&1 &
```

**Option 2 - Screen Session:**
```bash
screen -S airtracker
python airtracker.py
# Detach with Ctrl+A, D
# Reattach with: screen -r airtracker
```

**Option 3 - macOS LaunchAgent (Recommended):**

Create `~/Library/LaunchAgents/com.airtracker.plist`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.airtracker</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/PATH/TO/airtrackr/airtracker.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/PATH/TO/airtrackr</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/PATH/TO/airtrackr/airtracker.log</string>
    <key>StandardErrorPath</key>
    <string>/PATH/TO/airtrackr/airtracker.error.log</string>
</dict>
</plist>
```

Then load it:
```bash
launchctl load ~/Library/LaunchAgents/com.airtracker.plist
```

## Database Structure

AirTracker uses a proper relational database structure:

- **devices** - Stores each AirTag once with canonical name
- **device_locations** - Location history linked by device_id
- **screenshots** - Metadata for each capture
- **extracted_text** - Raw OCR results

### Key Features:
- **Fuzzy Matching** - Handles OCR errors (e.g., "BlackValize" → "Black Valize")
- **Position Independence** - Tracks devices even when they change order in Find My
- **Device Persistence** - Maintains history across captures

## Practical Examples

### Track Luggage During Travel
```bash
# Start monitoring before your trip
python airtracker.py &

# Check luggage location history
python view_device_history.py "Yellow Valize"
```

### Find Lost Keys
```bash
# See last known location with GPS coordinates
python view_device_history.py "Roels Keys"
```

### Export Data for Analysis
```bash
# Export to CSV
sqlite3 -header -csv database/airtracker.db \
  "SELECT d.canonical_name, dl.location_text, dl.distance_meters, \
   datetime(dl.timestamp_unix, 'unixepoch') as time \
   FROM device_locations dl JOIN devices d ON dl.device_id = d.id \
   ORDER BY dl.timestamp_unix DESC;" > airtag_history.csv
```

### Check Devices Not Seen Recently
```bash
sqlite3 database/airtracker.db \
  "SELECT canonical_name, datetime(last_seen) FROM devices \
   WHERE datetime(last_seen) < datetime('now', '-2 hours');"
```

## Configuration

### Region Coordinates

The current configuration captures 9 AirTag regions:
- Starting position: (120, 220)
- Region size: 460×120 pixels
- Vertical spacing: 150 pixels (120px height + 30px gap)

To adjust for your screen, modify coordinates in `airtracker.py`:
```python
# Region extraction parameters
start_x = 120
start_y = 220
region_width = 460
region_height = 120
```

### OCR Configuration

The system handles common OCR errors:
- Missing spaces in device names
- Character substitutions (l→ll, o→0)
- Special character issues

## Troubleshooting

### Screenshots show wrong content
- Ensure Find My is the active window
- Adjust activation delay if needed

### OCR missing devices
- Check if all devices are visible (not scrolled)
- Verify region coordinates match your screen
- Check `temp_regions/` folder for extracted images

### Database queries
```bash
# View recent screenshots
sqlite3 database/airtracker.db \
  "SELECT filename, timestamp FROM screenshots \
   ORDER BY timestamp DESC LIMIT 10;"

# Check device variations
sqlite3 database/airtracker.db \
  "SELECT device_name, canonical_name FROM devices \
   ORDER BY canonical_name;"
```

## Tips for Best Results

1. **Keep Find My app visible** on your dedicated Mac
2. **Don't scroll** the device list - keep all AirTags visible
3. **Regular backups**: `cp database/airtracker.db database/backup_$(date +%Y%m%d).db`
4. **Monitor logs** for errors: `tail -f airtracker.log`
5. **Ensure consistent lighting** for better OCR accuracy

## How It Works

1. **Screenshot Capture**: Uses macOS `screencapture` to grab Find My window
2. **Region Extraction**: Splits screenshot into individual AirTag regions
3. **OCR Processing**: Tesseract extracts text with line preservation
4. **Text Parsing**: Extracts device names, distances, locations, timestamps
5. **Geocoding**: OpenStreetMap Nominatim converts addresses to coordinates
6. **Database Storage**: Relational structure with device tracking
7. **Fuzzy Matching**: Identifies devices despite OCR errors or position changes

## Future Enhancements

- Web interface for viewing location history
- Real-time alerts for device movement
- Location heatmaps and analytics
- Multi-user support
- iOS app integration

## License

This project is for personal use. Use responsibly and in accordance with local laws and Apple's terms of service.

## Acknowledgments

- OpenStreetMap Nominatim for free geocoding
- Tesseract OCR for text extraction
- FuzzyWuzzy for string matching