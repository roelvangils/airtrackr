#!/usr/bin/env python3

import sqlite3
from pathlib import Path
from airtracker import AirTracker
import time

def test_geocoding():
    """Test geocoding on existing location data"""
    
    db_path = Path("database") / "airtracker.db"
    
    if not db_path.exists():
        print("No database found. Run a capture first.")
        return
    
    # Create tracker instance for geocoding
    tracker = AirTracker()
    
    # Get locations that need geocoding
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT id, device_name, location_text, latitude, longitude
        FROM locations 
        WHERE location_text IS NOT NULL 
        AND location_text != ''
        AND latitude IS NULL
        ORDER BY id
    ''')
    
    locations = cursor.fetchall()
    conn.close()
    
    if not locations:
        print("No locations found that need geocoding.")
        return
    
    print(f"Found {len(locations)} locations to geocode:")
    print("-" * 60)
    
    for location_id, device_name, location_text, lat, lon in locations:
        print(f"Geocoding '{location_text}' for {device_name}...")
        
        # Geocode the location
        latitude, longitude = tracker.geocode_location(location_text)
        
        if latitude and longitude:
            # Update the database
            tracker.update_location_coordinates(location_id, latitude, longitude)
            print(f"  ✓ Updated: ({latitude:.6f}, {longitude:.6f})")
        else:
            print(f"  ✗ Failed to geocode")
        
        # Rate limiting - wait 1 second between requests (Nominatim requirement)
        time.sleep(1.1)
    
    print("-" * 60)
    print("Geocoding complete!")
    
    # Show results
    print("\nUpdated locations:")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT device_name, location_text, latitude, longitude
        FROM locations 
        WHERE location_text IS NOT NULL 
        AND latitude IS NOT NULL
        ORDER BY device_name
    ''')
    
    results = cursor.fetchall()
    
    for device_name, location_text, lat, lon in results:
        print(f"  {device_name}: {location_text} → ({lat:.6f}, {lon:.6f})")
    
    conn.close()

if __name__ == "__main__":
    test_geocoding()