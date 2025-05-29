#!/usr/bin/env python3

import os
import subprocess
import time
import sqlite3
from datetime import datetime, timedelta
sqlite3.register_adapter(datetime, lambda val: val.isoformat())
sqlite3.register_converter("DATETIME", lambda val: datetime.fromisoformat(val.decode()))
from pathlib import Path
from PIL import Image
import schedule
import logging
import pytesseract
import re
import requests
import json
import warnings
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message="Using slow pure-python SequenceMatcher")
    from fuzzywuzzy import fuzz
    from fuzzywuzzy import process

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

class AirTracker:
    def __init__(self):
        # Load configuration first
        self.config_path = Path("config.json")
        self.config = self.load_config()
        
        # Set up paths and app settings from config
        self.app_name = self.get_config('findmy.app_name', 'FindMy')
        self.screenshots_dir = Path("screenshots")
        self.database_dir = Path("database")
        self.db_path = self.database_dir / "airtracker.db"
        
        # Ensure directories exist
        self.screenshots_dir.mkdir(exist_ok=True)
        self.database_dir.mkdir(exist_ok=True)
        self.temp_regions_dir = Path("temp_regions")
        self.temp_regions_dir.mkdir(exist_ok=True)
        
        # Create logs directory if file logging is enabled
        if self.get_config('logging.file_output', True):
            log_file = Path(self.get_config('logging.log_file', 'logs/airtracker.log'))
            log_file.parent.mkdir(exist_ok=True)
        
        # Initialize database
        self.init_database()
        
    def load_config(self):
        """Load configuration from config.json file"""
        try:
            if self.config_path.exists():
                with open(self.config_path, 'r') as f:
                    return json.load(f)
            else:
                logging.info(f"No config file found at {self.config_path}, using defaults")
                return {}
        except Exception as e:
            logging.error(f"Error loading config: {e}")
            return {}
    
    def get_config(self, key_path, default=None):
        """Get a configuration value using dot notation (e.g., 'app.update_interval_seconds')"""
        keys = key_path.split('.')
        value = self.config
        
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        
        return value
    
    def init_database(self):
        """Initialize SQLite database with required tables"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Create screenshots table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS screenshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                timestamp DATETIME NOT NULL,
                processed BOOLEAN DEFAULT FALSE
            )
        ''')
        
        # Create extracted_text table for raw OCR results
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS extracted_text (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                screenshot_id INTEGER NOT NULL,
                region_index INTEGER NOT NULL,
                raw_text TEXT NOT NULL,
                processed BOOLEAN DEFAULT FALSE,
                extracted_at DATETIME NOT NULL,
                FOREIGN KEY (screenshot_id) REFERENCES screenshots(id)
            )
        ''')
        
        # Create devices table for persistent device tracking
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_name TEXT NOT NULL UNIQUE,
                canonical_name TEXT NOT NULL,
                device_type TEXT,
                first_seen DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_seen DATETIME,
                is_active BOOLEAN DEFAULT TRUE
            )
        ''')
        
        # Create improved locations table with device relationship
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS device_locations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id INTEGER NOT NULL,
                screenshot_id INTEGER NOT NULL,
                distance_meters INTEGER,
                location_text TEXT,
                timestamp_unix INTEGER,
                latitude REAL,
                longitude REAL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (device_id) REFERENCES devices(id),
                FOREIGN KEY (screenshot_id) REFERENCES screenshots(id)
            )
        ''')
        
        # Create indexes for performance
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_device_locations_device_id ON device_locations(device_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_device_locations_timestamp ON device_locations(timestamp_unix)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_devices_canonical_name ON devices(canonical_name)')
        
        # Keep old locations table for backward compatibility
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS locations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                screenshot_id INTEGER NOT NULL,
                region_index INTEGER NOT NULL,
                device_name TEXT,
                distance_meters INTEGER,
                location_text TEXT,
                timestamp_unix INTEGER,
                latitude REAL,
                longitude REAL,
                parsed_at DATETIME NOT NULL,
                FOREIGN KEY (screenshot_id) REFERENCES screenshots(id)
            )
        ''')
        
        conn.commit()
        conn.close()
        logging.info("Database initialized")
        
    def extract_text_from_region(self, region_path, region_index):
        """Extract text from a region using OCR"""
        try:
            # Configure Tesseract to preserve line structure
            # PSM 6: Uniform block of text (better for structured layouts)
            # Preserve line breaks and positioning
            custom_config = r'--oem 3 --psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.,-:°% \n'
            
            # Extract text
            text = pytesseract.image_to_string(Image.open(region_path), config=custom_config)
            
            # Clean up the text but preserve line breaks
            lines = text.strip().split('\n')
            # Clean each line and remove empty lines
            cleaned_lines = []
            for line in lines:
                cleaned_line = line.strip()
                if cleaned_line:  # Only keep non-empty lines
                    # Remove excessive spaces within the line
                    cleaned_line = ' '.join(cleaned_line.split())
                    cleaned_lines.append(cleaned_line)
            
            # Join lines back with newlines
            text = '\n'.join(cleaned_lines)
            
            logging.info(f"Extracted text from region {region_index}:")
            for i, line in enumerate(cleaned_lines, 1):
                logging.info(f"  Line {i}: {line}")
            
            return text
            
        except Exception as e:
            logging.error(f"Error extracting text from region {region_index}: {e}")
            return ""
            
    def save_extracted_text(self, screenshot_id, region_index, text):
        """Save extracted text to database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO extracted_text (screenshot_id, region_index, raw_text, extracted_at)
            VALUES (?, ?, ?, ?)
        ''', (screenshot_id, region_index, text, datetime.now()))
        
        conn.commit()
        conn.close()
        
    def get_screenshot_id(self, filename):
        """Get screenshot ID from database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT id FROM screenshots WHERE filename = ?', (filename,))
        result = cursor.fetchone()
        
        conn.close()
        
        return result[0] if result else None
        
    def parse_device_name(self, name_text):
        """Parse device name and add proper spacing with better normalization"""
        if not name_text:
            return ""
            
        # First, clean up common OCR artifacts and special characters
        cleaned_name = name_text.strip()
        
        # Remove trailing special characters and dots that are OCR artifacts
        cleaned_name = re.sub(r'[.%°]+$', '', cleaned_name)
        cleaned_name = re.sub(r'[-]+$', '', cleaned_name)
        
        # Remove repeated characters at the end (like "...")
        cleaned_name = re.sub(r'\.{2,}$', '', cleaned_name)
        
        # Fix common OCR character substitutions
        cleaned_name = cleaned_name.replace('°', '')
        cleaned_name = cleaned_name.replace('%', '')
        
        # Add spaces before capital letters (camelCase -> proper spacing)
        name_with_spaces = re.sub(r'([a-z])([A-Z])', r'\1 \2', cleaned_name)
        
        # Handle special cases like "JelliedeBelliePortefeu" 
        # Add space before "de" and "Port"
        name_with_spaces = re.sub(r'([a-z])(de)([A-Z])', r'\1 \2 \3', name_with_spaces)
        name_with_spaces = re.sub(r'([a-z])(Port)', r'\1 \2', name_with_spaces)
        
        # Clean up excessive whitespace
        name_with_spaces = ' '.join(name_with_spaces.split())
        
        return name_with_spaces.strip()
    
    def parse_distance(self, distance_text):
        """Convert distance text to meters (as integer)"""
        if not distance_text or distance_text.lower() == 'okm':
            return 0
            
        # Remove special characters like % and ° and extract numbers
        distance_clean = re.sub(r'[%°]', '', distance_text)
        
        # Handle range like "9-10km" - take the average
        range_match = re.search(r'(\d+)-(\d+)\s*km', distance_clean, re.IGNORECASE)
        if range_match:
            start_km = float(range_match.group(1))
            end_km = float(range_match.group(2))
            avg_km = (start_km + end_km) / 2
            return int(avg_km * 1000)  # Convert to meters
        
        # Extract single distance and unit
        match = re.search(r'(\d+[,.]?\d*)\s*km', distance_clean, re.IGNORECASE)
        if match:
            distance_str = match.group(1).replace(',', '.')
            try:
                distance_km = float(distance_str)
                return int(distance_km * 1000)  # Convert to meters
            except ValueError:
                return None
        
        return None
    
    def parse_timestamp(self, time_text, screenshot_timestamp):
        """Convert relative timestamp to absolute Unix timestamp"""
        if not time_text:
            return None
            
        # Handle "Now" case
        if 'now' in time_text.lower():
            return int(screenshot_timestamp.timestamp())
        
        # Extract minutes ago
        match = re.search(r'(\d+)\s*min\s*ago', time_text, re.IGNORECASE)
        if match:
            minutes_ago = int(match.group(1))
            actual_time = screenshot_timestamp - timedelta(minutes=minutes_ago)
            return int(actual_time.timestamp())
        
        # If we can't parse it, return screenshot time
        return int(screenshot_timestamp.timestamp())
    
    def parse_location_text(self, lines, screenshot_timestamp):
        """Parse the OCR text lines into structured data"""
        if not lines:
            return None
            
        result = {
            'device_name': None,
            'distance_meters': None,
            'location': None,
            'timestamp_unix': None,
            'raw_lines': lines
        }
        
        # First line usually contains device name and possibly distance
        if len(lines) >= 1:
            first_line = lines[0]
            
            # Extract distance if present (including ranges and special chars)
            distance_match = re.search(r'(\d+[,.]?\d*\s*km|%\d+[,.]?\d*\s*km|°\.\d+[,.]?\d*\s*km|\d+-\d+\s*km|okm)', first_line, re.IGNORECASE)
            if distance_match:
                distance_text = distance_match.group(1)
                result['distance_meters'] = self.parse_distance(distance_text)
                # Remove distance from device name
                device_name = first_line.replace(distance_text, '').strip()
            else:
                device_name = first_line
            
            result['device_name'] = self.parse_device_name(device_name)
        
        # Second line usually contains location and timestamp
        if len(lines) >= 2:
            second_line = lines[1]
            
            if 'nolocationfound' in second_line.lower():
                result['location'] = None
                result['timestamp_unix'] = int(screenshot_timestamp.timestamp())
            else:
                # Extract timestamp
                time_match = re.search(r'(\d+\s*min\s*ago|now)', second_line, re.IGNORECASE)
                if time_match:
                    time_text = time_match.group(1)
                    result['timestamp_unix'] = self.parse_timestamp(time_text, screenshot_timestamp)
                    # Remove timestamp to get location
                    location = second_line.replace(time_text, '').strip(' -')
                    result['location'] = location if location else None
                else:
                    result['location'] = second_line
                    result['timestamp_unix'] = int(screenshot_timestamp.timestamp())
        
        # Handle third line if present (additional timestamp info)
        if len(lines) >= 3:
            third_line = lines[2]
            time_match = re.search(r'(\d+\s*min\s*ago|now)', third_line, re.IGNORECASE)
            if time_match:
                time_text = time_match.group(1)
                result['timestamp_unix'] = self.parse_timestamp(time_text, screenshot_timestamp)
        
        return result
        
    def save_parsed_location(self, screenshot_id, region_index, parsed_data):
        """Save parsed location data to database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO locations (
                screenshot_id, region_index, device_name, distance_meters, 
                location_text, timestamp_unix, parsed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            screenshot_id, 
            region_index, 
            parsed_data['device_name'],
            parsed_data['distance_meters'],
            parsed_data['location'],
            parsed_data['timestamp_unix'],
            datetime.now()
        ))
        
        conn.commit()
        conn.close()
        
    def get_or_create_device(self, device_name):
        """Get or create device with aggressive fuzzy matching to prevent duplicates"""
        if not device_name:
            return None
            
        # Clean and normalize the device name first
        normalized_name = self.parse_device_name(device_name)
        if not normalized_name:
            return None
            
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # First, try exact match against normalized canonical names
        cursor.execute('SELECT id, canonical_name FROM devices WHERE canonical_name = ? AND is_active = TRUE', (normalized_name,))
        result = cursor.fetchone()
        
        if result:
            device_id = result[0]
            conn.close()
            logging.info(f"Exact canonical match for '{device_name}' -> '{normalized_name}' (ID: {device_id})")
            return device_id
        
        # Try exact match against any stored device name variations
        cursor.execute('SELECT id, canonical_name FROM devices WHERE device_name = ? AND is_active = TRUE', (device_name,))
        result = cursor.fetchone()
        
        if result:
            device_id = result[0]
            conn.close()
            logging.info(f"Exact device name match for '{device_name}' (ID: {device_id})")
            return device_id
        
        # Try fuzzy matching against canonical names with multiple algorithms
        cursor.execute('SELECT id, canonical_name, device_name FROM devices WHERE is_active = TRUE')
        all_devices = cursor.fetchall()
        
        if all_devices:
            # Extract canonical names for fuzzy matching
            device_list = [(row[0], row[1], row[2]) for row in all_devices]
            canonical_names = [row[1] for row in device_list]
            
            # Use multiple fuzzy matching algorithms for better detection
            ratio_match = process.extractOne(normalized_name, canonical_names, scorer=fuzz.ratio)
            token_sort_match = process.extractOne(normalized_name, canonical_names, scorer=fuzz.token_sort_ratio)
            partial_match = process.extractOne(normalized_name, canonical_names, scorer=fuzz.partial_ratio)
            
            # Check all matching algorithms - use lower threshold for better duplicate detection
            best_match = None
            best_score = 0
            
            for match in [ratio_match, token_sort_match, partial_match]:
                if match and match[1] > best_score:
                    best_match = match
                    best_score = match[1]
            
            # Lower threshold to 75% for more aggressive duplicate prevention
            if best_match and best_score >= 75:
                # Found a good match
                matched_canonical = best_match[0]
                for device_id, canonical, existing_name in device_list:
                    if canonical == matched_canonical:
                        logging.info(f"Fuzzy matched '{device_name}' -> '{normalized_name}' to existing device '{canonical}' (score: {best_score})")
                        
                        # Update the device to mark the latest activity
                        cursor.execute('''
                            UPDATE devices 
                            SET last_seen = CURRENT_TIMESTAMP
                            WHERE id = ?
                        ''', (device_id,))
                        
                        conn.commit()
                        conn.close()
                        return device_id
        
        # No match found - create new device with normalized name
        device_type = self.guess_device_type(normalized_name)
        
        cursor.execute('''
            INSERT INTO devices (device_name, canonical_name, device_type)
            VALUES (?, ?, ?)
        ''', (device_name, normalized_name, device_type))
        
        device_id = cursor.lastrowid
        logging.info(f"Created new device: '{normalized_name}' from '{device_name}' (ID: {device_id})")
        
        conn.commit()
        conn.close()
        
        return device_id
    
    def guess_device_type(self, device_name):
        """Guess device type from name"""
        name_lower = device_name.lower()
        if 'key' in name_lower:
            return 'keys'
        elif 'bag' in name_lower or 'pack' in name_lower:
            return 'bag'
        elif 'valize' in name_lower or 'luggage' in name_lower:
            return 'luggage'
        elif 'auto' in name_lower or 'car' in name_lower:
            return 'vehicle'
        elif 'wallet' in name_lower or 'portefeu' in name_lower:
            return 'wallet'
        else:
            return 'airtag'
    
    def save_device_location(self, device_id, location_data, screenshot_id):
        """Save location data with device relationship"""
        if not device_id:
            return
            
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Insert into device_locations
        cursor.execute('''
            INSERT INTO device_locations (
                device_id, screenshot_id, distance_meters,
                location_text, timestamp_unix, latitude, longitude
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            device_id,
            screenshot_id,
            location_data.get('distance_meters'),
            location_data.get('location'),
            location_data.get('timestamp_unix'),
            location_data.get('latitude'),
            location_data.get('longitude')
        ))
        
        # Update device last_seen
        cursor.execute('''
            UPDATE devices 
            SET last_seen = datetime(?, 'unixepoch')
            WHERE id = ?
        ''', (location_data.get('timestamp_unix'), device_id))
        
        conn.commit()
        conn.close()
        
    def clean_location_text(self, location_text):
        """Clean and improve location text for better geocoding"""
        if not location_text:
            return location_text
            
        # Remove trailing dashes and clean up
        cleaned = location_text.strip(' -')
        
        # Fix common OCR issues
        replacements = {
            'Frangois': 'François',
            'Francois': 'François',
            'FrangoisLaurentplein': 'François Laurentplein',
            'FrancoisLaurentplein': 'François Laurentplein',
            'FrançoisLaurentplein': 'François Laurentplein',
            'Gent': 'Ghent',
            ',Ghent': ', Ghent',
            ',Belgium': ', Belgium',
            'E40,': 'E40 highway near ',
            'Merelbeke-Melle': 'Merelbeke'
        }
        
        for old, new in replacements.items():
            cleaned = cleaned.replace(old, new)
        
        # Add country if it's a Belgian city
        if 'Ghent' in cleaned and 'Belgium' not in cleaned:
            cleaned += ', Belgium'
            
        return cleaned
    
    def geocode_location(self, location_text):
        """Convert location text to coordinates using custom config or OpenStreetMap Nominatim"""
        if not location_text:
            return None, None
            
        # Check custom location configuration first
        location_lower = location_text.lower().strip()
        custom_locations = self.get_config('locations.custom_coordinates', {})
        
        if location_lower in custom_locations:
            config_entry = custom_locations[location_lower]
            lat = config_entry.get('latitude')
            lon = config_entry.get('longitude')
            if lat is not None and lon is not None:
                try:
                    lat_float = float(lat)
                    lon_float = float(lon)
                    logging.info(f"Using configured coordinates for '{location_text}': ({lat_float:.6f}, {lon_float:.6f})")
                    return lat_float, lon_float
                except (ValueError, TypeError):
                    logging.warning(f"Invalid coordinates in config for '{location_text}': lat={lat}, lon={lon}")
                    return None, None
            else:
                logging.warning(f"Location '{location_text}' found in config but coordinates are null. Please set them in {self.config_path}")
                return None, None
            
        # Clean the location text first
        cleaned_location = self.clean_location_text(location_text)
        
        try:
            # Nominatim API endpoint
            url = "https://nominatim.openstreetmap.org/search"
            
            # Parameters for the request
            params = {
                'q': cleaned_location,
                'format': 'json',
                'limit': 1,
                'addressdetails': 1
            }
            
            # Set a proper User-Agent (required by Nominatim)
            headers = {
                'User-Agent': self.get_config('geocoding.user_agent', 'AirTracker/1.0 (https://github.com/user/airtracker)')
            }
            
            # Make the request with configurable timeout
            timeout = self.get_config('geocoding.timeout_seconds', 10)
            response = requests.get(url, params=params, headers=headers, timeout=timeout)
            
            if response.status_code == 200:
                results = response.json()
                
                if results and len(results) > 0:
                    result = results[0]
                    try:
                        lat = float(result['lat'])
                        lon = float(result['lon'])
                        
                        logging.info(f"Geocoded '{location_text}' (cleaned: '{cleaned_location}') to ({lat:.6f}, {lon:.6f})")
                    except (ValueError, TypeError):
                        logging.error(f"Invalid coordinates from geocoding API for '{location_text}': lat={result.get('lat')}, lon={result.get('lon')}")
                        return None, None
                    return lat, lon
                else:
                    logging.info(f"No geocoding results found for '{location_text}' (cleaned: '{cleaned_location}')")
                    return None, None
            else:
                logging.error(f"Geocoding request failed with status {response.status_code}")
                return None, None
                
        except Exception as e:
            logging.error(f"Error geocoding location '{location_text}': {e}")
            return None, None
    
    def update_location_coordinates(self, location_id, latitude, longitude):
        """Update location record with coordinates"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE locations 
            SET latitude = ?, longitude = ? 
            WHERE id = ?
        ''', (latitude, longitude, location_id))
        
        conn.commit()
        conn.close()
        
    def get_window_bounds(self):
        """Get the window bounds for Find My app"""
        try:
            # First activate the app to ensure it's visible
            activate_script = '''
            tell application "FindMy"
                activate
            end tell
            delay 0.5
            '''
            subprocess.run(["osascript", "-e", activate_script], capture_output=True)
            
            # Then get the window bounds
            bounds_script = '''
            tell application "System Events"
                tell process "FindMy"
                    if (count of windows) > 0 then
                        set w to window 1
                        return (position of w) & (size of w)
                    else
                        return "no windows"
                    end if
                end tell
            end tell
            '''
            
            result = subprocess.run(["osascript", "-e", bounds_script], capture_output=True, text=True)
            
            if result.returncode == 0 and "no windows" not in result.stdout:
                coords = result.stdout.strip().split(", ")
                if len(coords) == 4:
                    return tuple(map(int, coords))  # (x, y, width, height)
            
            return None
            
        except Exception as e:
            logging.error(f"Error getting window bounds: {e}")
            return None
            
    def ensure_app_running(self):
        """Ensure Find My app is running"""
        try:
            # Check if app is running
            result = subprocess.run(
                ["osascript", "-e", 'tell application "System Events" to (name of processes) contains "FindMy"'],
                capture_output=True,
                text=True
            )
            
            if result.stdout.strip() == "false":
                logging.info("Starting Find My app...")
                subprocess.run(["open", "-a", "FindMy"])
                time.sleep(3)  # Give the app time to start
                
        except Exception as e:
            logging.error(f"Error checking/starting Find My app: {e}")
            
    def take_screenshot(self):
        """Take a screenshot of the Find My window"""
        self.ensure_app_running()
        
        # Generate filename with timestamp
        timestamp = datetime.now()
        filename = timestamp.strftime("findmy_%Y%m%d_%H%M%S.png")
        filepath = self.screenshots_dir / filename
        
        try:
            # Get window bounds
            bounds = self.get_window_bounds()
            
            if bounds:
                x, y, width, height = bounds
                
                # Take screenshot of specific region
                subprocess.run([
                    "screencapture",
                    "-R", f"{x},{y},{width},{height}",
                    "-o",  # No shadow
                    "-x",  # No sound
                    str(filepath)
                ], check=True)
                
                logging.info(f"Screenshot saved: {filename}")
                
                # Save to database
                self.save_screenshot_record(filename, timestamp)
                
                return filepath
            else:
                logging.error("Could not find Find My window")
                return None
                
        except subprocess.CalledProcessError as e:
            logging.error(f"Error taking screenshot: {e}")
            return None
            
    def save_screenshot_record(self, filename, timestamp):
        """Save screenshot record to database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO screenshots (filename, timestamp, processed)
            VALUES (?, ?, ?)
        ''', (filename, timestamp, False))
        
        conn.commit()
        conn.close()
        
    def extract_regions(self, screenshot_path):
        """Extract AirTag regions from screenshot"""
        try:
            img = Image.open(screenshot_path)
            
            # Region extraction parameters (measured from Find My interface)
            start_x = 120
            start_y = 220
            region_width = 460
            region_height = 120
            num_regions = 9
            
            extracted_regions = []
            
            # Create timestamp-based subfolder for this screenshot's regions
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            screenshot_regions_dir = self.temp_regions_dir / timestamp
            screenshot_regions_dir.mkdir(exist_ok=True)
            
            for i in range(num_regions):
                # Calculate region coordinates (120px height + 30px spacing = 150px total)
                x = start_x
                y = start_y + (i * (region_height + 30))
                
                # Extract region
                region = img.crop((x, y, x + region_width, y + region_height))
                
                # Save region
                region_filename = f"airtag_{i+1:02d}.png"
                region_path = screenshot_regions_dir / region_filename
                region.save(region_path)
                
                extracted_regions.append({
                    'index': i + 1,
                    'filename': region_filename,
                    'path': region_path,
                    'coordinates': (x, y, x + region_width, y + region_height)
                })
                
                logging.info(f"Extracted AirTag {i+1}: {region_filename} at ({x},{y},{x + region_width},{y + region_height})")
            
            logging.info(f"Extracted {len(extracted_regions)} regions to {screenshot_regions_dir}")
            
            return {
                'regions_dir': screenshot_regions_dir,
                'regions': extracted_regions,
                'total_extracted': len(extracted_regions)
            }
            
        except Exception as e:
            logging.error(f"Error extracting regions: {e}")
            return None
        
    def run_capture(self):
        """Run a single capture cycle"""
        logging.info("Starting capture cycle...")
        
        screenshot_path = self.take_screenshot()
        
        if screenshot_path:
            # Extract regions
            regions = self.extract_regions(screenshot_path)
            
            if regions:
                logging.info(f"Successfully extracted {regions['total_extracted']} AirTag regions")
                
                # Get screenshot ID for database
                screenshot_id = self.get_screenshot_id(screenshot_path.name)
                
                if screenshot_id:
                    # Extract text from each region using OCR
                    for region_info in regions['regions']:
                        text = self.extract_text_from_region(region_info['path'], region_info['index'])
                        if text:
                            # Save raw OCR text
                            self.save_extracted_text(screenshot_id, region_info['index'], text)
                            
                            # Parse the text into structured data
                            lines = text.split('\n')
                            screenshot_datetime = datetime.fromtimestamp(screenshot_path.stat().st_mtime)
                            parsed_data = self.parse_location_text(lines, screenshot_datetime)
                            
                            if parsed_data and parsed_data['device_name']:
                                # Get or create device using fuzzy matching
                                device_id = self.get_or_create_device(parsed_data['device_name'])
                                
                                if device_id:
                                    # Save to old locations table for backward compatibility
                                    self.save_parsed_location(screenshot_id, region_info['index'], parsed_data)
                                    
                                    # Geocode location if we have location text
                                    latitude, longitude = None, None
                                    if parsed_data['location']:
                                        rate_limit = self.get_config('geocoding.rate_limit_seconds', 1.1)
                                        time.sleep(rate_limit)  # Rate limiting for geocoding API
                                        latitude, longitude = self.geocode_location(parsed_data['location'])
                                        
                                        if latitude and longitude:
                                            # Update old table with coordinates
                                            conn = sqlite3.connect(self.db_path)
                                            cursor = conn.cursor()
                                            cursor.execute('SELECT last_insert_rowid()')
                                            location_id = cursor.fetchone()[0]
                                            conn.close()
                                            self.update_location_coordinates(location_id, latitude, longitude)
                                    
                                    # Save to new device_locations table
                                    location_data = {
                                        'distance_meters': parsed_data['distance_meters'],
                                        'location': parsed_data['location'],
                                        'timestamp_unix': parsed_data['timestamp_unix'],
                                        'latitude': latitude,
                                        'longitude': longitude
                                    }
                                    self.save_device_location(device_id, location_data, screenshot_id)
                                    
                                    # Format log message better
                                    if parsed_data['distance_meters'] is not None:
                                        distance_str = f"{parsed_data['distance_meters']}m"
                                    else:
                                        distance_str = "Unknown"
                                    location_str = parsed_data['location'] if parsed_data['location'] else "No location"
                                    logging.info(f"Parsed device {device_id}: {parsed_data['device_name']} - {distance_str} - {location_str}")
                        
                    logging.info("OCR text extraction, parsing, and geocoding complete")
                else:
                    logging.error("Could not find screenshot ID in database")
            else:
                logging.error("Failed to extract regions")
            
        logging.info("Capture cycle complete")
        
    def start_scheduler(self):
        """Start the scheduler with configurable interval"""
        update_interval = self.get_config('app.update_interval_seconds', 60)
        schedule.every(update_interval).seconds.do(self.run_capture)
        
        logging.info(f"AirTracker started - capturing every {update_interval} seconds")
        logging.info("Press Ctrl+C to stop")
        
        # Run first capture immediately
        self.run_capture()
        
        # Keep running
        while True:
            schedule.run_pending()
            time.sleep(1)


if __name__ == "__main__":
    tracker = AirTracker()
    
    try:
        tracker.start_scheduler()
    except KeyboardInterrupt:
        logging.info("AirTracker stopped by user")
    except Exception as e:
        logging.error(f"Unexpected error: {e}")