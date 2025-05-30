#!/usr/bin/env python3
"""
Script to geocode existing locations in the database
"""

import sqlite3
import time
from pathlib import Path
from geocoding import Geocoder

def geocode_existing_locations():
    """Geocode all locations that don't have coordinates yet"""
    
    db_path = Path("database") / "airtracker.db"
    
    if not db_path.exists():
        print("Database not found!")
        return
    
    geocoder = Geocoder()
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Get locations without coordinates
    cursor.execute("""
        SELECT id, location
        FROM swift_locations
        WHERE location IS NOT NULL 
        AND location != 'No location found'
        AND latitude IS NULL
        ORDER BY timestamp DESC
    """)
    
    locations = cursor.fetchall()
    
    if not locations:
        print("No locations need geocoding.")
        return
    
    print(f"Found {len(locations)} locations to geocode.")
    print("-" * 60)
    
    success_count = 0
    
    for location_id, location_text in locations:
        print(f"Geocoding: '{location_text}'... ", end="", flush=True)
        
        try:
            lat, lon = geocoder.geocode(location_text)
            
            if lat and lon:
                # Update the database
                cursor.execute("""
                    UPDATE swift_locations
                    SET latitude = ?, longitude = ?
                    WHERE id = ?
                """, (lat, lon, location_id))
                
                conn.commit()
                print(f"✓ ({lat:.6f}, {lon:.6f})")
                success_count += 1
            else:
                print("✗ Failed")
                
        except Exception as e:
            print(f"✗ Error: {e}")
        
        # Rate limiting
        time.sleep(1.1)
    
    print("-" * 60)
    print(f"Geocoding complete! Successfully geocoded {success_count}/{len(locations)} locations.")
    
    # Show statistics
    cursor.execute("""
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN latitude IS NOT NULL THEN 1 ELSE 0 END) as with_coords,
            COUNT(DISTINCT device_name) as devices
        FROM swift_locations
        WHERE location IS NOT NULL AND location != 'No location found'
    """)
    
    stats = cursor.fetchone()
    print(f"\nDatabase statistics:")
    print(f"  Total locations: {stats[0]}")
    print(f"  With coordinates: {stats[1]} ({stats[1]/stats[0]*100:.1f}%)")
    print(f"  Unique devices: {stats[2]}")
    
    conn.close()


if __name__ == "__main__":
    geocode_existing_locations()