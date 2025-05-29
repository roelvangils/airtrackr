#!/usr/bin/env python3

import sqlite3
from pathlib import Path
from datetime import datetime

def view_parsed_locations():
    """View all parsed location data from the database"""
    
    db_path = Path("database") / "airtracker.db"
    
    if not db_path.exists():
        print("No database found. Run a capture first.")
        return
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Get latest screenshot data with parsed locations
    cursor.execute('''
        SELECT s.filename, s.timestamp, COUNT(l.id) as location_count
        FROM screenshots s
        LEFT JOIN locations l ON s.id = l.screenshot_id
        GROUP BY s.id
        ORDER BY s.timestamp DESC
        LIMIT 1
    ''')
    
    latest = cursor.fetchone()
    
    if latest:
        filename, timestamp, location_count = latest
        print(f"Latest Screenshot: {filename}")
        print(f"Timestamp: {timestamp}")
        print(f"Parsed locations: {location_count}")
        print("=" * 80)
        
        # Get all parsed location data for latest screenshot
        cursor.execute('''
            SELECT l.region_index, l.device_name, l.distance_meters, 
                   l.location_text, l.timestamp_unix, l.latitude, l.longitude, l.parsed_at
            FROM locations l
            JOIN screenshots s ON l.screenshot_id = s.id
            WHERE s.filename = ?
            ORDER BY l.region_index
        ''', (filename,))
        
        locations = cursor.fetchall()
        
        for region_index, device_name, distance_meters, location_text, timestamp_unix, latitude, longitude, parsed_at in locations:
            print(f"Region {region_index:2d}: {device_name}")
            
            if distance_meters is not None:
                if distance_meters == 0:
                    print(f"           Distance: At location (0m)")
                else:
                    distance_km = distance_meters / 1000
                    print(f"           Distance: {distance_meters}m ({distance_km:.1f}km)")
            else:
                print(f"           Distance: Unknown")
                
            if location_text:
                print(f"           Location: {location_text}")
            else:
                print(f"           Location: No location found")
                
            if timestamp_unix:
                actual_time = datetime.fromtimestamp(timestamp_unix)
                print(f"           Last seen: {actual_time.strftime('%Y-%m-%d %H:%M:%S')}")
                
            if latitude and longitude:
                print(f"           Coordinates: ({latitude:.6f}, {longitude:.6f})")
                print(f"           Maps URL: https://www.google.com/maps?q={latitude},{longitude}")
            
            print()
            
        print("=" * 80)
        print(f"Total devices tracked: {len(locations)}")
        
        # Show summary by location status
        with_location = [l for l in locations if l[3] is not None]  # location_text
        without_location = [l for l in locations if l[3] is None]
        
        print(f"Devices with location: {len(with_location)}")
        print(f"Devices without location: {len(without_location)}")
        
    else:
        print("No data found in database.")
    
    conn.close()

if __name__ == "__main__":
    view_parsed_locations()