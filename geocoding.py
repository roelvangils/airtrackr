#!/usr/bin/env python3
"""
Geocoding module for AirTracker
Converts location names to geographic coordinates using OpenStreetMap's Nominatim API
"""

import requests
import json
import time
import logging
from typing import Tuple, Optional, Dict
from pathlib import Path
import sqlite3
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class Geocoder:
    """Handles geocoding of location names to coordinates"""
    
    def __init__(self, config_path: str = "config.json"):
        """
        Initialize geocoder with configuration
        
        Args:
            config_path: Path to configuration file
        """
        self.config = self._load_config(config_path)
        self.cache_db = Path("database") / "geocoding_cache.db"
        self._init_cache_db()
        
    def _load_config(self, config_path: str) -> Dict:
        """Load geocoding configuration"""
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
                return config.get('geocoding', {
                    'provider': 'nominatim',
                    'rate_limit_seconds': 1.1,
                    'timeout_seconds': 10,
                    'user_agent': 'AirTracker/1.0',
                    'cache_results': True,
                    'cache_duration_days': 7
                })
        except Exception as e:
            logger.warning(f"Could not load config: {e}. Using defaults.")
            return {
                'provider': 'nominatim',
                'rate_limit_seconds': 1.1,
                'timeout_seconds': 10,
                'user_agent': 'AirTracker/1.0',
                'cache_results': True,
                'cache_duration_days': 7
            }
    
    def _init_cache_db(self):
        """Initialize the geocoding cache database"""
        if not self.config.get('cache_results', True):
            return
            
        self.cache_db.parent.mkdir(exist_ok=True)
        
        conn = sqlite3.connect(self.cache_db)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS geocoding_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                location_text TEXT UNIQUE NOT NULL,
                latitude REAL,
                longitude REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                provider TEXT DEFAULT 'nominatim'
            )
        ''')
        
        # Create index for faster lookups
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_location_text 
            ON geocoding_cache(location_text)
        ''')
        
        conn.commit()
        conn.close()
    
    def _check_cache(self, location_text: str) -> Optional[Tuple[float, float]]:
        """Check if location is in cache and not expired"""
        if not self.config.get('cache_results', True):
            return None
            
        conn = sqlite3.connect(self.cache_db)
        cursor = conn.cursor()
        
        # Check for cached result within cache duration
        cache_days = self.config.get('cache_duration_days', 7)
        cutoff_date = datetime.now() - timedelta(days=cache_days)
        
        cursor.execute('''
            SELECT latitude, longitude 
            FROM geocoding_cache 
            WHERE location_text = ? AND created_at > ?
        ''', (location_text.strip(), cutoff_date))
        
        result = cursor.fetchone()
        conn.close()
        
        if result and result[0] is not None and result[1] is not None:
            logger.debug(f"Cache hit for '{location_text}'")
            return (result[0], result[1])
        
        return None
    
    def _save_to_cache(self, location_text: str, latitude: Optional[float], longitude: Optional[float]):
        """Save geocoding result to cache"""
        if not self.config.get('cache_results', True):
            return
            
        conn = sqlite3.connect(self.cache_db)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                INSERT OR REPLACE INTO geocoding_cache 
                (location_text, latitude, longitude) 
                VALUES (?, ?, ?)
            ''', (location_text.strip(), latitude, longitude))
            
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to save to cache: {e}")
        finally:
            conn.close()
    
    def clean_location_text(self, location_text: str) -> str:
        """
        Clean and normalize location text for better geocoding results
        
        Args:
            location_text: Raw location text
            
        Returns:
            Cleaned location text
        """
        if not location_text:
            return ""
        
        # Remove time-related suffixes
        text = location_text.strip()
        text = text.replace(", Paused", "")
        text = text.replace(", paused", "")
        
        # Remove "No location found" type entries
        if "No location found" in text:
            return ""
        
        # Add country context for Belgian cities if not present
        belgian_cities = ["Ghent", "Gent", "Brussels", "Brussel", "Antwerp", "Antwerpen"]
        for city in belgian_cities:
            if city in text and "Belgium" not in text:
                text += ", Belgium"
        
        return text
    
    def geocode_nominatim(self, location_text: str) -> Tuple[Optional[float], Optional[float]]:
        """
        Geocode using OpenStreetMap's Nominatim API
        
        Args:
            location_text: Location to geocode
            
        Returns:
            Tuple of (latitude, longitude) or (None, None) if failed
        """
        url = "https://nominatim.openstreetmap.org/search"
        
        params = {
            'q': location_text,
            'format': 'json',
            'limit': 1,
            'addressdetails': 1
        }
        
        headers = {
            'User-Agent': self.config.get('user_agent', 'AirTracker/1.0')
        }
        
        try:
            response = requests.get(
                url, 
                params=params, 
                headers=headers,
                timeout=self.config.get('timeout_seconds', 10)
            )
            
            if response.status_code == 200:
                data = response.json()
                if data and len(data) > 0:
                    result = data[0]
                    lat = float(result['lat'])
                    lon = float(result['lon'])
                    logger.info(f"Geocoded '{location_text}' -> ({lat:.6f}, {lon:.6f})")
                    return (lat, lon)
            else:
                logger.warning(f"Nominatim returned status {response.status_code}")
                
        except requests.exceptions.Timeout:
            logger.error(f"Timeout geocoding '{location_text}'")
        except Exception as e:
            logger.error(f"Error geocoding '{location_text}': {e}")
        
        return (None, None)
    
    def _check_custom_locations(self, location_text: str) -> Optional[Tuple[float, float]]:
        """
        Check if location matches any custom coordinates from config
        
        Args:
            location_text: Location to check
            
        Returns:
            Tuple of (latitude, longitude) or None if not found
        """
        try:
            with open('config.json', 'r') as f:
                config = json.load(f)
                custom_locations = config.get('locations', {}).get('custom_coordinates', {})
                
                # Check exact match (case-insensitive)
                for custom_name, coords in custom_locations.items():
                    if location_text.lower() == custom_name.lower():
                        # Handle European decimal format (comma instead of dot)
                        lat_str = str(coords.get('latitude', '')).replace(',', '.')
                        lon_str = str(coords.get('longitude', '')).replace(',', '.')
                        
                        if lat_str and lon_str:
                            try:
                                lat = float(lat_str)
                                lon = float(lon_str)
                                logger.info(f"Using custom coordinates for '{location_text}': ({lat:.6f}, {lon:.6f})")
                                return (lat, lon)
                            except ValueError:
                                logger.warning(f"Invalid coordinates for custom location '{custom_name}'")
                                
        except Exception as e:
            logger.debug(f"Could not load custom locations: {e}")
            
        return None
    
    def geocode(self, location_text: str) -> Tuple[Optional[float], Optional[float]]:
        """
        Main geocoding method with caching
        
        Args:
            location_text: Location to geocode
            
        Returns:
            Tuple of (latitude, longitude) or (None, None) if failed
        """
        if not location_text:
            return (None, None)
        
        # Clean the location text
        cleaned_text = self.clean_location_text(location_text)
        if not cleaned_text:
            return (None, None)
        
        # Check custom locations first
        custom_result = self._check_custom_locations(cleaned_text)
        if custom_result:
            return custom_result
        
        # Check cache
        cached_result = self._check_cache(cleaned_text)
        if cached_result:
            return cached_result
        
        # Geocode based on provider
        provider = self.config.get('provider', 'nominatim')
        
        if provider == 'nominatim':
            lat, lon = self.geocode_nominatim(cleaned_text)
        else:
            logger.error(f"Unknown geocoding provider: {provider}")
            return (None, None)
        
        # Save to cache (even if None to avoid repeated failed lookups)
        self._save_to_cache(cleaned_text, lat, lon)
        
        return (lat, lon)
    
    def batch_geocode(self, locations: list, delay_seconds: Optional[float] = None) -> Dict[str, Tuple[float, float]]:
        """
        Geocode multiple locations with rate limiting
        
        Args:
            locations: List of location strings
            delay_seconds: Delay between requests (uses config if not specified)
            
        Returns:
            Dictionary mapping location text to (lat, lon) tuples
        """
        if delay_seconds is None:
            delay_seconds = self.config.get('rate_limit_seconds', 1.1)
        
        results = {}
        
        for i, location in enumerate(locations):
            if i > 0:  # Don't delay before first request
                time.sleep(delay_seconds)
            
            lat, lon = self.geocode(location)
            if lat and lon:
                results[location] = (lat, lon)
        
        return results


# Convenience function for quick geocoding
def geocode_location(location_text: str) -> Tuple[Optional[float], Optional[float]]:
    """Quick geocoding function"""
    geocoder = Geocoder()
    return geocoder.geocode(location_text)


if __name__ == "__main__":
    # Test the geocoder
    test_locations = [
        "François Laurentplein, Ghent",
        "Home",
        "Brussels, Belgium",
        "Times Square, New York",
        "No location found"
    ]
    
    geocoder = Geocoder()
    
    print("Testing geocoder:")
    print("-" * 50)
    
    for location in test_locations:
        print(f"Testing: '{location}'")
        lat, lon = geocoder.geocode(location)
        
        if lat and lon:
            print(f"  ✓ Result: ({lat:.6f}, {lon:.6f})")
        else:
            print(f"  ✗ Failed")
        
        time.sleep(1.1)  # Rate limiting