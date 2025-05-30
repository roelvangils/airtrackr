#!/usr/bin/env python3
"""
Script to update coordinates for Home locations with the custom coordinates from config.json
"""

import sqlite3
from pathlib import Path
from geocoding import Geocoder

def update_home_coordinates():
    """Update all 'Home' locations with custom coordinates"""
    
    db_path = Path("database") / "airtracker.db"
    
    if not db_path.exists():
        print("Database not found!")
        return
    
    geocoder = Geocoder()
    
    # Test the geocoding first
    print("Testing custom location geocoding...")
    lat, lon = geocoder.geocode("Home")
    if lat and lon:
        print(f"✓ Custom 'Home' coordinates: ({lat:.6f}, {lon:.6f})")
    else:
        print("✗ Failed to get custom 'Home' coordinates")
        return
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Update all Home locations
    cursor.execute("""
        UPDATE swift_locations
        SET latitude = ?, longitude = ?
        WHERE LOWER(location) = 'home'
    """, (lat, lon))
    
    updated = cursor.rowcount
    conn.commit()
    
    print(f"\nUpdated {updated} 'Home' locations with coordinates ({lat:.6f}, {lon:.6f})")
    
    # Show some examples
    cursor.execute("""
        SELECT device_name, location, latitude, longitude, timestamp
        FROM swift_locations
        WHERE LOWER(location) = 'home'
        ORDER BY timestamp DESC
        LIMIT 5
    """)
    
    print("\nRecent Home locations:")
    for row in cursor.fetchall():
        print(f"  {row[0]}: {row[1]} → ({row[2]:.6f}, {row[3]:.6f}) at {row[4]}")
    
    conn.close()


if __name__ == "__main__":
    update_home_coordinates()