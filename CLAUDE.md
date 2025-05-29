# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AirTracker is a macOS application that captures and analyzes screenshots from the Find My app to track AirTag locations over time. It uses windowed screenshots to extract location data and stores it in an SQLite database for historical tracking.

## Technology Stack

- Python 3.x
- SQLite for data storage
- Pillow for image processing
- schedule for periodic execution
- macOS native screencapture utility

## Project Structure

```
airtrackr/
├── screenshots/          # Stored Find My screenshots
├── database/            # SQLite database files
├── airtracker.py        # Main application script
├── test_screenshot.py   # Test script for screenshot functionality
└── requirements.txt     # Python dependencies
```

## Common Commands

### Setup
```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Running the Application
```bash
# Run the main tracker (captures every minute)
python airtracker.py

# Test single screenshot capture
python test_screenshot.py
```

### Database Access
```bash
# Access SQLite database
sqlite3 database/airtracker.db
```

## Architecture

The application follows a simple architecture:

1. **Screenshot Capture**: Uses macOS `screencapture` command with window ID targeting to capture Find My app
2. **Image Storage**: Screenshots saved with timestamps in `screenshots/` directory
3. **Data Extraction**: Regions extracted from screenshots based on coordinates (to be provided)
4. **Data Storage**: SQLite database with two main tables:
   - `screenshots`: Tracks all captured screenshots
   - `locations`: Stores extracted location data from screenshots

## Key Implementation Notes

- The app uses AppleScript to find the Find My window ID for targeted screenshots
- Screenshots are taken every minute using the `schedule` library
- Region extraction is placeholder code awaiting coordinate specifications
- The app ensures Find My is running before taking screenshots