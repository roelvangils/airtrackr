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
from datetime import datetime, timedelta

from db import get_connection

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

    def _check_cache(self, location_text: str) -> Optional[Tuple[float, float]]:
        """Check if location is in cache (main database) and not expired"""
        if not self.config.get('cache_results', True):
            return None

        cache_days = self.config.get('cache_duration_days', 7)
        cutoff_date = datetime.now() - timedelta(days=cache_days)

        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT latitude, longitude
                FROM geocoding_cache
                WHERE location_text = ? AND created_at > ?
            ''', (location_text.strip(), cutoff_date))

            result = cursor.fetchone()

        if result and result[0] is not None and result[1] is not None:
            logger.debug(f"Cache hit for '{location_text}'")
            return (result[0], result[1])

        return None

    def _check_cache_full(self, location_text: str) -> Optional[Dict]:
        """Check cache for full structured address data."""
        if not self.config.get('cache_results', True):
            return None

        cache_days = self.config.get('cache_duration_days', 7)
        cutoff_date = datetime.now() - timedelta(days=cache_days)

        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT latitude, longitude, street, house_number, postal_code, city, country, address_json
                FROM geocoding_cache
                WHERE location_text = ? AND created_at > ?
            ''', (location_text.strip(), cutoff_date))

            result = cursor.fetchone()

        if result and result[0] is not None and result[1] is not None:
            return {
                'latitude': result[0],
                'longitude': result[1],
                'street': result[2],
                'house_number': result[3],
                'postal_code': result[4],
                'city': result[5],
                'country': result[6],
                'address_json': result[7],
            }

        return None

    def _save_to_cache(self, location_text: str, latitude: Optional[float], longitude: Optional[float],
                       address: Optional[Dict] = None):
        """Save geocoding result to cache in main database, including structured address fields."""
        if not self.config.get('cache_results', True):
            return

        try:
            with get_connection() as conn:
                conn.execute('''
                    INSERT OR REPLACE INTO geocoding_cache
                    (location_text, latitude, longitude, street, house_number, postal_code, city, country, address_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    location_text.strip(), latitude, longitude,
                    address.get('street') if address else None,
                    address.get('house_number') if address else None,
                    address.get('postal_code') if address else None,
                    address.get('city') if address else None,
                    address.get('country') if address else None,
                    json.dumps(address) if address else None,
                ))
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to save to cache: {e}")
    
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
    
    def _rate_limit(self):
        """Sleep to respect the Nominatim rate limit."""
        time.sleep(self.config.get('rate_limit_seconds', 1.1))

    def geocode_nominatim(self, location_text: str) -> Tuple[Optional[float], Optional[float]]:
        """
        Geocode using OpenStreetMap's Nominatim API

        Args:
            location_text: Location to geocode

        Returns:
            Tuple of (latitude, longitude) or (None, None) if failed
        """
        self._rate_limit()
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
    
    def geocode_nominatim_full(self, location_text: str) -> Optional[Dict]:
        """
        Geocode using Nominatim and return full structured address data.

        Returns:
            Dict with lat, lon, street, house_number, postal_code, city, country,
            or None if geocoding failed.
        """
        self._rate_limit()
        url = "https://nominatim.openstreetmap.org/search"

        params = {
            'q': location_text,
            'format': 'json',
            'limit': 1,
            'addressdetails': 1,
        }

        headers = {
            'User-Agent': self.config.get('user_agent', 'AirTracker/1.0')
        }

        try:
            response = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=self.config.get('timeout_seconds', 10),
            )

            if response.status_code == 200:
                data = response.json()
                if data and len(data) > 0:
                    result = data[0]
                    addr = result.get('address', {})
                    return {
                        'latitude': float(result['lat']),
                        'longitude': float(result['lon']),
                        'street': addr.get('road'),
                        'house_number': addr.get('house_number'),
                        'postal_code': addr.get('postcode'),
                        'city': addr.get('city') or addr.get('town') or addr.get('village'),
                        'country': addr.get('country'),
                    }

        except requests.exceptions.Timeout:
            logger.error(f"Timeout geocoding '{location_text}'")
        except Exception as e:
            logger.error(f"Error geocoding '{location_text}': {e}")

        return None

    def reverse_geocode(self, lat: float, lon: float) -> Optional[Dict]:
        """
        Reverse geocode coordinates to a structured address.

        Args:
            lat: Latitude
            lon: Longitude

        Returns:
            Dict with street, house_number, postal_code, city, country, display_name,
            or None if reverse geocoding failed.
        """
        # Check cache by coordinate key
        coord_key = f"@reverse:{lat:.6f},{lon:.6f}"
        cached = self._check_cache_full(coord_key)
        if cached:
            return cached

        url = "https://nominatim.openstreetmap.org/reverse"

        params = {
            'lat': lat,
            'lon': lon,
            'format': 'json',
            'addressdetails': 1,
        }

        headers = {
            'User-Agent': self.config.get('user_agent', 'AirTracker/1.0')
        }

        try:
            self._rate_limit()

            response = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=self.config.get('timeout_seconds', 10),
            )

            if response.status_code == 200:
                data = response.json()
                addr = data.get('address', {})
                result = {
                    'latitude': lat,
                    'longitude': lon,
                    'street': addr.get('road'),
                    'house_number': addr.get('house_number'),
                    'postal_code': addr.get('postcode'),
                    'city': addr.get('city') or addr.get('town') or addr.get('village'),
                    'country': addr.get('country'),
                    'display_name': data.get('display_name'),
                }

                # Cache the reverse result
                self._save_to_cache(coord_key, lat, lon, address=result)
                return result

        except requests.exceptions.Timeout:
            logger.error(f"Timeout reverse geocoding ({lat}, {lon})")
        except Exception as e:
            logger.error(f"Error reverse geocoding ({lat}, {lon}): {e}")

        return None

    def geocode_full(self, location_text: str) -> Optional[Dict]:
        """
        Main geocoding method that returns full structured address data.

        Returns:
            Dict with latitude, longitude, street, house_number, postal_code, city, country,
            or None if geocoding failed.
        """
        if not location_text:
            return None

        cleaned_text = self.clean_location_text(location_text)
        if not cleaned_text:
            return None

        # Check cache for full data
        cached = self._check_cache_full(cleaned_text)
        if cached:
            return cached

        # Geocode with full address details
        result = self.geocode_nominatim_full(cleaned_text)

        if result:
            self._save_to_cache(cleaned_text, result['latitude'], result['longitude'], address=result)
            return result

        # Save failed result to avoid repeated lookups
        self._save_to_cache(cleaned_text, None, None)
        return None

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