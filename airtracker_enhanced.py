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
from logging.handlers import RotatingFileHandler
import pytesseract
import re
import requests
import json
import warnings
import functools
from typing import Optional, Tuple, Dict, List, Any, Callable
from dataclasses import dataclass
from collections import deque
from threading import Lock
import signal
import sys

with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message="Using slow pure-python SequenceMatcher")
    from fuzzywuzzy import fuzz
    from fuzzywuzzy import process

# Enhanced components

def retry_with_backoff(max_retries: int = 3, backoff_factor: float = 2.0):
    """Decorator for retrying functions with exponential backoff"""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries - 1:
                        logging.error(f"Failed after {max_retries} attempts: {e}")
                        raise
                    wait_time = backoff_factor ** attempt
                    logging.warning(f"Attempt {attempt + 1} failed, retrying in {wait_time}s: {e}")
                    time.sleep(wait_time)
            return None
        return wrapper
    return decorator

class DatabaseManager:
    """Context manager for database transactions"""
    def __init__(self, db_path):
        self.db_path = db_path
        self.conn = None
        self.cursor = None
    
    def __enter__(self):
        self.conn = sqlite3.connect(self.db_path, detect_types=sqlite3.PARSE_DECLTYPES)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.cursor = self.conn.cursor()
        return self.cursor
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self.conn.commit()
        else:
            self.conn.rollback()
            logging.error(f"Database transaction rolled back: {exc_val}")
        self.conn.close()

@dataclass
class ParsedLocation:
    """Validated location data with confidence scoring"""
    device_name: str
    distance_meters: Optional[int] = None
    location_text: Optional[str] = None
    timestamp_unix: Optional[int] = None
    confidence_score: float = 1.0
    
    def is_valid(self) -> bool:
        """Check if minimum required data is present"""
        return bool(self.device_name and self.timestamp_unix is not None)

class RateLimiter:
    """Thread-safe rate limiter for API calls"""
    def __init__(self, calls_per_second: float = 1.0):
        self.calls_per_second = calls_per_second
        self.min_interval = 1.0 / calls_per_second
        self.last_call_time = 0
        self.lock = Lock()
        self.call_times = deque(maxlen=100)
    
    def wait_if_needed(self):
        """Wait if necessary to respect rate limit"""
        with self.lock:
            current_time = time.time()
            time_since_last_call = current_time - self.last_call_time
            
            if time_since_last_call < self.min_interval:
                sleep_time = self.min_interval - time_since_last_call
                time.sleep(sleep_time)
                current_time = time.time()
            
            self.last_call_time = current_time
            self.call_times.append(current_time)

class AirTrackerEnhanced:
    def __init__(self):
        self.app_name = "FindMy"
        self.screenshots_dir = Path("screenshots")
        self.database_dir = Path("database")
        self.db_path = self.database_dir / "airtracker.db"
        
        # Ensure directories exist
        self.screenshots_dir.mkdir(exist_ok=True)
        self.database_dir.mkdir(exist_ok=True)
        self.temp_regions_dir = Path("temp_regions")
        self.temp_regions_dir.mkdir(exist_ok=True)
        
        # Enhanced components
        self.geocoding_rate_limiter = RateLimiter(calls_per_second=0.9)
        self.geocoding_cache = {}
        self._running = True
        
        # Set up enhanced logging
        self._setup_logging()
        
        # Initialize database
        self.init_database()
        
        # Clean up any duplicate devices on startup
        self.cleanup_duplicate_devices()
        
        # Set up signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
    def _setup_logging(self):
        """Enhanced logging setup with rotation"""
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        
        # Create rotating file handler
        file_handler = RotatingFileHandler(
            log_dir / "airtracker.log",
            maxBytes=10*1024*1024,  # 10MB
            backupCount=5
        )
        
        # Set up formatters
        file_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        console_formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s'
        )
        
        file_handler.setFormatter(file_formatter)
        
        # Configure root logger
        logger = logging.getLogger()
        logger.setLevel(logging.INFO)
        logger.addHandler(file_handler)
        
        # Keep console handler
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        logging.info(f"Received signal {signum}, shutting down...")
        self._running = False
        sys.exit(0)
        
    def init_database(self):
        """Initialize SQLite database with required tables"""
        with DatabaseManager(self.db_path) as cursor:
            # Create screenshots table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS screenshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT NOT NULL,
                    timestamp DATETIME NOT NULL,
                    processed BOOLEAN DEFAULT FALSE,
                    UNIQUE(filename)
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
                    FOREIGN KEY (screenshot_id) REFERENCES screenshots(id) ON DELETE CASCADE
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
                    is_active BOOLEAN DEFAULT TRUE,
                    CHECK (canonical_name != '')
                )
            ''')
            
            # Create improved locations table with device relationship
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS device_locations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id INTEGER NOT NULL,
                    screenshot_id INTEGER NOT NULL,
                    distance_meters INTEGER CHECK (distance_meters >= 0),
                    location_text TEXT,
                    timestamp_unix INTEGER,
                    latitude REAL CHECK (latitude IS NULL OR (latitude >= -90 AND latitude <= 90)),
                    longitude REAL CHECK (longitude IS NULL OR (longitude >= -180 AND longitude <= 180)),
                    confidence_score REAL DEFAULT 1.0 CHECK (confidence_score >= 0 AND confidence_score <= 1),
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE,
                    FOREIGN KEY (screenshot_id) REFERENCES screenshots(id) ON DELETE CASCADE
                )
            ''')
            
            # Create indexes for performance
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_device_locations_device_id ON device_locations(device_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_device_locations_timestamp ON device_locations(timestamp_unix)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_devices_canonical_name ON devices(canonical_name)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_screenshots_timestamp ON screenshots(timestamp)')
            
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
                    FOREIGN KEY (screenshot_id) REFERENCES screenshots(id) ON DELETE CASCADE
                )
            ''')
            
        logging.info("Database initialized with enhanced schema")
        
    def ensure_app_ready(self, max_attempts: int = 3) -> bool:
        """Ensure app is running and window is ready for capture"""
        for attempt in range(max_attempts):
            try:
                # Check if app is running
                is_running_script = '''
                tell application "System Events"
                    set appList to name of every process
                    return "FindMy" is in appList
                end tell
                '''
                result = subprocess.run(["osascript", "-e", is_running_script], 
                                      capture_output=True, text=True, timeout=5)
                
                if result.stdout.strip() == "false":
                    logging.info("Starting Find My app...")
                    subprocess.run(["open", "-a", "FindMy"])
                    time.sleep(3)
                    continue
                
                # Check if window is visible
                window_check_script = '''
                tell application "System Events"
                    tell process "FindMy"
                        if (count of windows) > 0 then
                            return true
                        else
                            return false
                        end if
                    end tell
                end tell
                '''
                
                result = subprocess.run(["osascript", "-e", window_check_script], 
                                      capture_output=True, text=True, timeout=5)
                
                if result.stdout.strip() == "true":
                    return True
                    
            except subprocess.TimeoutExpired:
                logging.error(f"Timeout checking app state (attempt {attempt + 1})")
            except Exception as e:
                logging.error(f"Error checking app state (attempt {attempt + 1}): {e}")
        
        return False
        
    def extract_text_from_region(self, region_path, region_index):
        """Extract text from a region using OCR with validation"""
        try:
            # Configure Tesseract to preserve line structure
            custom_config = r'--oem 3 --psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.,-:°% \n'
            
            # Extract text
            text = pytesseract.image_to_string(Image.open(region_path), config=custom_config)
            
            # Clean up the text but preserve line breaks
            lines = text.strip().split('\n')
            cleaned_lines = []
            for line in lines:
                cleaned_line = line.strip()
                if cleaned_line:
                    cleaned_line = ' '.join(cleaned_line.split())
                    cleaned_lines.append(cleaned_line)
            
            # Validate we got something useful
            if not cleaned_lines or all(len(line) < 2 for line in cleaned_lines):
                logging.warning(f"Poor OCR result for region {region_index}")
                return ""
            
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
        with DatabaseManager(self.db_path) as cursor:
            cursor.execute('''
                INSERT INTO extracted_text (screenshot_id, region_index, raw_text, extracted_at)
                VALUES (?, ?, ?, ?)
            ''', (screenshot_id, region_index, text, datetime.now()))
        
    def get_screenshot_id(self, filename):
        """Get screenshot ID from database"""
        with DatabaseManager(self.db_path) as cursor:
            cursor.execute('SELECT id FROM screenshots WHERE filename = ?', (filename,))
            result = cursor.fetchone()
            return result[0] if result else None
        
    def parse_device_name(self, name_text):
        """Parse device name with validation against known patterns"""
        if not name_text:
            return None
            
        # Validate against suspicious patterns
        suspicious_patterns = [
            r'(?i)google\s*lens',  # Google Lens
            r'(?i)^[a-z]$',  # Single letter
            r'(?i)^window$',  # Generic window
            r'(?i)screenshot',  # Screenshot text
            r'(?i)^find\s*my',  # Find My app itself
            r'^[0-9]+$',  # Just numbers
            r'^[\W_]+$',  # Only special characters
        ]
        
        for pattern in suspicious_patterns:
            if re.match(pattern, name_text.strip()):
                logging.warning(f"Rejecting suspicious device name: '{name_text}'")
                return None
        
        # Validate minimum length
        if len(name_text.strip()) < 3:
            logging.warning(f"Device name too short: '{name_text}'")
            return None
            
        # Add spaces before capital letters (camelCase -> proper spacing)
        name_with_spaces = re.sub(r'([a-z])([A-Z])', r'\1 \2', name_text)
        
        # Handle special cases
        name_with_spaces = re.sub(r'([a-z])(de)([A-Z])', r'\1 \2 \3', name_with_spaces)
        name_with_spaces = re.sub(r'([a-z])(Port)', r'\1 \2', name_with_spaces)
        
        # Final validation - should contain at least one letter
        if not re.search(r'[a-zA-Z]', name_with_spaces):
            logging.warning(f"Device name contains no letters: '{name_text}'")
            return None
        
        return name_with_spaces.strip()
    
    def parse_distance(self, distance_text):
        """Convert distance text to meters with enhanced parsing"""
        if not distance_text or distance_text.lower() == 'okm':
            return 0
            
        # Remove special characters
        distance_clean = re.sub(r'[%°]', '', distance_text)
        
        # Handle range like "9-10km" - take the average
        range_match = re.search(r'(\d+)-(\d+)\s*km', distance_clean, re.IGNORECASE)
        if range_match:
            start_km = float(range_match.group(1))
            end_km = float(range_match.group(2))
            avg_km = (start_km + end_km) / 2
            return int(avg_km * 1000)
        
        # Extract single distance and unit
        match = re.search(r'(\d+[,.]?\d*)\s*km', distance_clean, re.IGNORECASE)
        if match:
            distance_str = match.group(1).replace(',', '.')
            try:
                distance_km = float(distance_str)
                return int(distance_km * 1000)
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
    
    def parse_location_text_robust(self, lines: List[str], screenshot_timestamp: datetime) -> Optional[ParsedLocation]:
        """Parse OCR text with validation and confidence scoring"""
        if not lines:
            return None
        
        result = ParsedLocation(
            device_name="",
            timestamp_unix=int(screenshot_timestamp.timestamp())
        )
        
        confidence = 1.0
        
        # Parse first line - device name and distance
        if lines:
            first_line = lines[0].strip()
            if not first_line:
                confidence *= 0.5
            else:
                # Multiple distance patterns with confidence
                distance_patterns = [
                    (r'(\d+)\s*km', 1.0),
                    (r'(\d+[,.]?\d*)\s*km', 0.9),
                    (r'(\d+)-(\d+)\s*km', 0.8),
                    (r'[%°]?\d+[,.]?\d*\s*km', 0.7),
                ]
                
                distance_found = False
                for pattern, pattern_confidence in distance_patterns:
                    match = re.search(pattern, first_line, re.IGNORECASE)
                    if match:
                        result.distance_meters = self.parse_distance(match.group(0))
                        confidence *= pattern_confidence
                        device_name = first_line[:match.start()] + first_line[match.end():]
                        distance_found = True
                        break
                
                if not distance_found:
                    device_name = first_line
                    confidence *= 0.8
                
                parsed_name = self.parse_device_name(device_name.strip())
                if parsed_name is None:
                    logging.warning(f"Failed to parse valid device name from: '{device_name}'")
                    return None
                result.device_name = parsed_name
        
        # Parse location and timestamp
        if len(lines) >= 2:
            second_line = lines[1].strip()
            
            # Check for "no location found" variants
            no_location_patterns = ['nolocationfound', 'no location', 'location unavailable']
            if any(pattern in second_line.lower() for pattern in no_location_patterns):
                result.location_text = None
                confidence *= 0.8
            else:
                # Extract timestamp with multiple patterns
                time_patterns = [
                    (r'(\d+)\s*min\s*ago', lambda m: int(m.group(1))),
                    (r'now', lambda m: 0),
                    (r'just now', lambda m: 0),
                ]
                
                time_found = False
                for pattern, extractor in time_patterns:
                    match = re.search(pattern, second_line, re.IGNORECASE)
                    if match:
                        minutes_ago = extractor(match)
                        result.timestamp_unix = int((screenshot_timestamp - timedelta(minutes=minutes_ago)).timestamp())
                        location = second_line[:match.start()] + second_line[match.end():]
                        result.location_text = location.strip(' -')
                        time_found = True
                        break
                
                if not time_found:
                    result.location_text = second_line
                    confidence *= 0.7
        
        # Handle third line if present
        if len(lines) >= 3:
            third_line = lines[2]
            time_match = re.search(r'(\d+)\s*min\s*ago|now', third_line, re.IGNORECASE)
            if time_match:
                time_text = time_match.group(0)
                result.timestamp_unix = self.parse_timestamp(time_text, screenshot_timestamp)
        
        result.confidence_score = confidence
        
        # Validate and return
        if result.is_valid():
            return result
        else:
            logging.warning(f"Invalid parsed data: {result}")
            return None
        
    def save_parsed_location(self, screenshot_id, region_index, parsed_data):
        """Save parsed location data to database"""
        with DatabaseManager(self.db_path) as cursor:
            cursor.execute('''
                INSERT INTO locations (
                    screenshot_id, region_index, device_name, distance_meters, 
                    location_text, timestamp_unix, parsed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                screenshot_id, 
                region_index, 
                parsed_data.device_name if isinstance(parsed_data, ParsedLocation) else parsed_data['device_name'],
                parsed_data.distance_meters if isinstance(parsed_data, ParsedLocation) else parsed_data['distance_meters'],
                parsed_data.location_text if isinstance(parsed_data, ParsedLocation) else parsed_data.get('location'),
                parsed_data.timestamp_unix if isinstance(parsed_data, ParsedLocation) else parsed_data['timestamp_unix'],
                datetime.now()
            ))
        
    def get_or_create_device(self, device_name):
        """Get or create device with fuzzy matching and validation"""
        if not device_name:
            return None
            
        with DatabaseManager(self.db_path) as cursor:
            # First, try exact match
            cursor.execute('SELECT id, canonical_name FROM devices WHERE device_name = ?', (device_name,))
            result = cursor.fetchone()
            
            if result:
                return result[0]
            
            # Try fuzzy matching against device names (not just canonical)
            cursor.execute('SELECT id, device_name FROM devices WHERE is_active = TRUE')
            all_devices = cursor.fetchall()
            
            if all_devices:
                # Clean up the input name for better matching
                cleaned_input = re.sub(r'\.+$', '', device_name)
                cleaned_input = re.sub(r'\.+%.*$', '', cleaned_input)
                
                # For truncated names ending with "...", try partial matching
                if cleaned_input.endswith('...') or '...' in cleaned_input:
                    base_name = cleaned_input.replace('...', '').strip()
                    for device_id, existing_name in all_devices:
                        if existing_name.startswith(base_name):
                            logging.info(f"Matched truncated '{device_name}' to existing device '{existing_name}'")
                            return device_id
                
                # Regular fuzzy matching
                device_names = [row[1] for row in all_devices]
                best_match = process.extractOne(cleaned_input, device_names, scorer=fuzz.ratio)
                
                if best_match and best_match[1] >= 85:  # Higher threshold for better accuracy
                    matched_name = best_match[0]
                    for device_id, existing_name in all_devices:
                        if existing_name == matched_name:
                            logging.info(f"Fuzzy matched '{device_name}' to existing device '{existing_name}' (score: {best_match[1]})")
                            return device_id
            
            # No match found - create new device
            # Clean up truncated names before creating canonical
            cleaned_name = re.sub(r'\.+$', '', device_name)  # Remove trailing dots
            cleaned_name = re.sub(r'\.+%.*$', '', cleaned_name)  # Remove dots and % suffix
            
            canonical_name = cleaned_name.lower().replace(' ', '_')
            device_type = self.guess_device_type(cleaned_name)
            
            cursor.execute('''
                INSERT INTO devices (device_name, canonical_name, device_type)
                VALUES (?, ?, ?)
            ''', (device_name, canonical_name, device_type))
            
            device_id = cursor.lastrowid
            logging.info(f"Created new device: '{canonical_name}' (ID: {device_id})")
            
            return device_id
    
    def cleanup_duplicate_devices(self):
        """Clean up duplicate devices on startup"""
        with DatabaseManager(self.db_path) as cursor:
            # Find and consolidate Jelliede Bellie variants
            cursor.execute("""
                SELECT id, device_name 
                FROM devices 
                WHERE device_name LIKE 'Jelliede Bellie%'
                ORDER BY 
                    CASE 
                        WHEN device_name = 'Jelliede Bellie Portefeuille' THEN 0
                        ELSE 1
                    END,
                    LENGTH(device_name) DESC,
                    id ASC
            """)
            jelliede_devices = cursor.fetchall()
            
            if len(jelliede_devices) > 1:
                primary = jelliede_devices[0]
                logging.info(f"Consolidating Jelliede Bellie variants into ID {primary[0]}")
                
                for device_id, name in jelliede_devices[1:]:
                    cursor.execute("""
                        UPDATE device_locations 
                        SET device_id = ? 
                        WHERE device_id = ?
                    """, (primary[0], device_id))
                    
                    cursor.execute("DELETE FROM devices WHERE id = ?", (device_id,))
                    logging.info(f"Merged duplicate '{name}' (ID {device_id}) into primary")
            
            # Remove any single-letter or garbage devices
            cursor.execute("""
                SELECT id, device_name FROM devices 
                WHERE LENGTH(device_name) < 3 
                   OR (LENGTH(device_name) = 1 AND device_name >= 'a' AND device_name <= 'z')
                   OR device_name LIKE '%Google Lens%'
                   OR device_name LIKE 'o F %'
            """)
            garbage_devices = cursor.fetchall()
            
            for device_id, name in garbage_devices:
                cursor.execute("DELETE FROM device_locations WHERE device_id = ?", (device_id,))
                cursor.execute("DELETE FROM devices WHERE id = ?", (device_id,))
                logging.info(f"Removed garbage device '{name}' (ID {device_id})")
    
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
        """Save location data with device relationship and validation"""
        if not device_id:
            return
            
        with DatabaseManager(self.db_path) as cursor:
            # Validate device exists
            cursor.execute("SELECT id FROM devices WHERE id = ?", (device_id,))
            if not cursor.fetchone():
                logging.error(f"Device {device_id} not found")
                return
            
            # Validate screenshot exists
            cursor.execute("SELECT id FROM screenshots WHERE id = ?", (screenshot_id,))
            if not cursor.fetchone():
                logging.error(f"Screenshot {screenshot_id} not found")
                return
            
            # Insert into device_locations
            confidence = location_data.confidence_score if isinstance(location_data, ParsedLocation) else location_data.get('confidence_score', 1.0)
            
            cursor.execute('''
                INSERT INTO device_locations (
                    device_id, screenshot_id, distance_meters,
                    location_text, timestamp_unix, latitude, longitude, confidence_score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                device_id,
                screenshot_id,
                location_data.distance_meters if isinstance(location_data, ParsedLocation) else location_data.get('distance_meters'),
                location_data.location_text if isinstance(location_data, ParsedLocation) else location_data.get('location'),
                location_data.timestamp_unix if isinstance(location_data, ParsedLocation) else location_data.get('timestamp_unix'),
                location_data.get('latitude') if hasattr(location_data, 'get') else None,
                location_data.get('longitude') if hasattr(location_data, 'get') else None,
                confidence
            ))
            
            # Update device last_seen
            timestamp = location_data.timestamp_unix if isinstance(location_data, ParsedLocation) else location_data.get('timestamp_unix')
            if timestamp:
                cursor.execute('''
                    UPDATE devices 
                    SET last_seen = datetime(?, 'unixepoch')
                    WHERE id = ?
                ''', (timestamp, device_id))
        
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
        belgian_cities = ['Ghent', 'Merelbeke', 'Gent', 'Brussels', 'Antwerp']
        if any(city in cleaned for city in belgian_cities) and 'Belgium' not in cleaned:
            cleaned += ', Belgium'
            
        return cleaned
    
    @retry_with_backoff(max_retries=3)
    def geocode_location(self, location_text):
        """Convert location text to coordinates with retry logic"""
        if not location_text or location_text.lower() == 'home':
            return None, None
            
        # Check cache first
        cache_key = location_text.lower().strip()
        if cache_key in self.geocoding_cache:
            logging.info(f"Using cached geocoding result for '{location_text}'")
            return self.geocoding_cache[cache_key]
        
        # Clean the location text
        cleaned_location = self.clean_location_text(location_text)
        
        # Rate limit API call
        self.geocoding_rate_limiter.wait_if_needed()
        
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
                'User-Agent': 'AirTracker/1.0 (https://github.com/user/airtracker)'
            }
            
            # Make the request
            response = requests.get(url, params=params, headers=headers, timeout=10)
            response.raise_for_status()
            
            results = response.json()
            
            if results and len(results) > 0:
                result = results[0]
                lat = float(result['lat'])
                lon = float(result['lon'])
                
                # Cache the result
                self.geocoding_cache[cache_key] = (lat, lon)
                
                logging.info(f"Geocoded '{location_text}' (cleaned: '{cleaned_location}') to ({lat:.6f}, {lon:.6f})")
                return lat, lon
            else:
                logging.info(f"No geocoding results found for '{location_text}' (cleaned: '{cleaned_location}')")
                # Cache the negative result too
                self.geocoding_cache[cache_key] = (None, None)
                return None, None
                
        except requests.exceptions.Timeout:
            logging.error(f"Timeout geocoding '{location_text}'")
            raise
        except requests.exceptions.RequestException as e:
            logging.error(f"Request error geocoding '{location_text}': {e}")
            raise
        except (KeyError, ValueError, IndexError) as e:
            logging.error(f"Parse error geocoding '{location_text}': {e}")
            return None, None
    
    def update_location_coordinates(self, location_id, latitude, longitude):
        """Update location record with coordinates"""
        with DatabaseManager(self.db_path) as cursor:
            cursor.execute('''
                UPDATE locations 
                SET latitude = ?, longitude = ? 
                WHERE id = ?
            ''', (latitude, longitude, location_id))
        
    def get_window_bounds(self):
        """Get the window bounds for Find My app with title verification"""
        try:
            # First activate the app to ensure it's visible
            activate_script = '''
            tell application "FindMy"
                activate
            end tell
            delay 0.5
            '''
            subprocess.run(["osascript", "-e", activate_script], capture_output=True)
            
            # Get window title and bounds
            window_info_script = '''
            tell application "System Events"
                tell process "FindMy"
                    if (count of windows) > 0 then
                        set w to window 1
                        set windowTitle to name of w
                        set windowBounds to (position of w) & (size of w)
                        return windowTitle & "|" & windowBounds
                    else
                        return "no windows"
                    end if
                end tell
            end tell
            '''
            
            result = subprocess.run(["osascript", "-e", window_info_script], capture_output=True, text=True)
            
            if result.returncode == 0 and "no windows" not in result.stdout:
                output = result.stdout.strip()
                parts = output.split("|")
                
                if len(parts) == 2:
                    window_title = parts[0]
                    coords_str = parts[1]
                    
                    # Verify window title contains "Find My" or similar
                    if "Find My" not in window_title and "FindMy" not in window_title:
                        logging.warning(f"Window title '{window_title}' does not match Find My app")
                        return None
                    
                    coords = coords_str.split(", ")
                    if len(coords) == 4:
                        return tuple(map(int, coords))  # (x, y, width, height)
            
            return None
            
        except Exception as e:
            logging.error(f"Error getting window bounds: {e}")
            return None
            
    def take_screenshot(self):
        """Take a screenshot of the Find My window with validation"""
        if not self.ensure_app_ready():
            logging.error("Find My app not ready for screenshot")
            return None
        
        # Generate filename with timestamp
        timestamp = datetime.now()
        filename = timestamp.strftime("findmy_%Y%m%d_%H%M%S.png")
        filepath = self.screenshots_dir / filename
        
        try:
            # Get window bounds
            bounds = self.get_window_bounds()
            
            if bounds:
                x, y, width, height = bounds
                
                # Validate bounds are reasonable
                if width < 100 or height < 100:
                    logging.error(f"Window bounds too small: {width}x{height}")
                    return None
                
                # Take screenshot of specific region
                subprocess.run([
                    "screencapture",
                    "-R", f"{x},{y},{width},{height}",
                    "-o",  # No shadow
                    "-x",  # No sound
                    str(filepath)
                ], check=True)
                
                # Verify file was created and has content
                if filepath.exists() and filepath.stat().st_size > 0:
                    logging.info(f"Screenshot saved: {filename}")
                    
                    # Save to database
                    self.save_screenshot_record(filename, timestamp)
                    
                    return filepath
                else:
                    logging.error(f"Screenshot file empty or not created: {filename}")
                    return None
            else:
                logging.error("Could not find Find My window")
                return None
                
        except subprocess.CalledProcessError as e:
            logging.error(f"Error taking screenshot: {e}")
            return None
            
    def save_screenshot_record(self, filename, timestamp):
        """Save screenshot record to database with duplicate handling"""
        try:
            with DatabaseManager(self.db_path) as cursor:
                cursor.execute('''
                    INSERT INTO screenshots (filename, timestamp, processed)
                    VALUES (?, ?, ?)
                ''', (filename, timestamp, False))
        except sqlite3.IntegrityError:
            logging.warning(f"Screenshot {filename} already exists in database")
        
    def extract_regions(self, screenshot_path):
        """Extract AirTag regions from screenshot"""
        try:
            img = Image.open(screenshot_path)
            
            # Validate image dimensions
            if img.width < 500 or img.height < 500:
                logging.error(f"Screenshot too small: {img.width}x{img.height}")
                return None
            
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
                
                # Check if region is within image bounds
                if y + region_height > img.height:
                    logging.warning(f"Region {i+1} extends beyond image bounds, skipping")
                    break
                
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
        """Run a single capture cycle with enhanced error handling"""
        logging.info("Starting capture cycle...")
        
        try:
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
                        successful_extractions = 0
                        
                        for region_info in regions['regions']:
                            text = self.extract_text_from_region(region_info['path'], region_info['index'])
                            if text:
                                # Save raw OCR text
                                self.save_extracted_text(screenshot_id, region_info['index'], text)
                                
                                # Parse the text into structured data
                                lines = text.split('\n')
                                screenshot_datetime = datetime.fromtimestamp(screenshot_path.stat().st_mtime)
                                parsed_data = self.parse_location_text_robust(lines, screenshot_datetime)
                                
                                if parsed_data and parsed_data.device_name:
                                    successful_extractions += 1
                                    
                                    # Get or create device using fuzzy matching
                                    device_id = self.get_or_create_device(parsed_data.device_name)
                                    
                                    if device_id:
                                        # Save to old locations table for backward compatibility
                                        self.save_parsed_location(screenshot_id, region_info['index'], parsed_data)
                                        
                                        # Geocode location if we have location text
                                        latitude, longitude = None, None
                                        if parsed_data.location_text:
                                            try:
                                                latitude, longitude = self.geocode_location(parsed_data.location_text)
                                            except Exception as e:
                                                logging.error(f"Geocoding failed: {e}")
                                        
                                        # Update old table with coordinates if found
                                        if latitude and longitude:
                                            with DatabaseManager(self.db_path) as cursor:
                                                cursor.execute('SELECT last_insert_rowid()')
                                                location_id = cursor.fetchone()[0]
                                            self.update_location_coordinates(location_id, latitude, longitude)
                                        
                                        # Save to new device_locations table
                                        location_data = {
                                            'distance_meters': parsed_data.distance_meters,
                                            'location': parsed_data.location_text,
                                            'timestamp_unix': parsed_data.timestamp_unix,
                                            'latitude': latitude,
                                            'longitude': longitude,
                                            'confidence_score': parsed_data.confidence_score
                                        }
                                        self.save_device_location(device_id, location_data, screenshot_id)
                                        
                                        # Format log message
                                        distance_str = f"{parsed_data.distance_meters}m" if parsed_data.distance_meters is not None else "Unknown"
                                        location_str = parsed_data.location_text if parsed_data.location_text else "No location"
                                        confidence_str = f"(confidence: {parsed_data.confidence_score:.2f})"
                                        logging.info(f"Parsed device {device_id}: {parsed_data.device_name} - {distance_str} - {location_str} {confidence_str}")
                        
                        logging.info(f"OCR extraction complete: {successful_extractions}/{regions['total_extracted']} successful")
                        
                        # Mark screenshot as processed
                        with DatabaseManager(self.db_path) as cursor:
                            cursor.execute('UPDATE screenshots SET processed = TRUE WHERE id = ?', (screenshot_id,))
                    else:
                        logging.error("Could not find screenshot ID in database")
                else:
                    logging.error("Failed to extract regions")
            
        except Exception as e:
            logging.error(f"Capture cycle failed: {e}", exc_info=True)
        
        logging.info("Capture cycle complete")
        
    def cleanup_old_data(self, days_to_keep: int = 30):
        """Remove old screenshots and temp files"""
        cutoff_date = datetime.now() - timedelta(days=days_to_keep)
        
        try:
            # Clean old screenshots
            with DatabaseManager(self.db_path) as cursor:
                cursor.execute('''
                    SELECT filename FROM screenshots 
                    WHERE timestamp < ? AND processed = TRUE
                ''', (cutoff_date,))
                
                old_files = cursor.fetchall()
                
                for (filename,) in old_files:
                    file_path = self.screenshots_dir / filename
                    if file_path.exists():
                        file_path.unlink()
                        logging.info(f"Deleted old screenshot: {filename}")
            
            # Clean temp regions older than 1 day
            temp_cutoff = datetime.now() - timedelta(days=1)
            for region_dir in self.temp_regions_dir.iterdir():
                if region_dir.is_dir():
                    try:
                        # Parse timestamp from directory name
                        dir_timestamp = datetime.strptime(region_dir.name, "%Y%m%d_%H%M%S")
                        if dir_timestamp < temp_cutoff:
                            # Remove directory and contents
                            for file in region_dir.iterdir():
                                file.unlink()
                            region_dir.rmdir()
                            logging.info(f"Cleaned temp region directory: {region_dir.name}")
                    except ValueError:
                        pass  # Skip directories with invalid names
                        
        except Exception as e:
            logging.error(f"Error during cleanup: {e}")
            
    def start_scheduler(self):
        """Start the scheduler to run every minute with maintenance"""
        schedule.every(1).minutes.do(self.run_capture)
        schedule.every(24).hours.do(self.cleanup_old_data)
        
        logging.info("Enhanced AirTracker started - capturing every minute")
        logging.info("Press Ctrl+C to stop")
        
        # Run first capture immediately
        self.run_capture()
        
        # Keep running
        while self._running:
            schedule.run_pending()
            time.sleep(1)


if __name__ == "__main__":
    tracker = AirTrackerEnhanced()
    
    try:
        tracker.start_scheduler()
    except KeyboardInterrupt:
        logging.info("AirTracker stopped by user")
    except Exception as e:
        logging.error(f"Unexpected error: {e}", exc_info=True)