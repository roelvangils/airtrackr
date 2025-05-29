#!/usr/bin/env python3

import sqlite3
from pathlib import Path
from datetime import datetime

def view_all_devices():
    """View all registered devices and their status"""
    
    db_path = Path("database") / "airtracker.db"
    
    if not db_path.exists():
        print("No database found. Run a capture first.")
        return
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("REGISTERED DEVICES")
    print("=" * 80)
    
    # Get all devices with their latest location
    cursor.execute('''
        SELECT 
            d.id,
            d.canonical_name,
            d.device_type,
            COUNT(DISTINCT dl.id) as location_count,
            MAX(datetime(dl.timestamp_unix, 'unixepoch', 'localtime')) as last_seen
        FROM devices d
        LEFT JOIN device_locations dl ON d.id = dl.device_id
        GROUP BY d.id
        ORDER BY d.canonical_name
    ''')
    
    devices = cursor.fetchall()
    
    print(f"{'ID':<4} {'Device Name':<25} {'Type':<10} {'Locations':<10} {'Last Seen':<20}")
    print("-" * 80)
    
    for device_id, name, device_type, count, last_seen in devices:
        last_seen_str = last_seen if last_seen else "Never"
        print(f"{device_id:<4} {name:<25} {device_type:<10} {count:<10} {last_seen_str:<20}")
    
    print("\n" + "=" * 80)
    print(f"Total devices: {len(devices)}")
    
    # Show device name variations
    print("\n\nDEVICE NAME VARIATIONS (OCR Handling)")
    print("=" * 80)
    
    cursor.execute('''
        SELECT canonical_name, GROUP_CONCAT(device_name, ', ') as variations
        FROM devices
        GROUP BY canonical_name
        HAVING COUNT(*) > 1
        ORDER BY canonical_name
    ''')
    
    variations = cursor.fetchall()
    
    if variations:
        for canonical, names in variations:
            print(f"{canonical}:")
            print(f"  Variations: {names}")
            print()
    else:
        print("No variations found yet.")
    
    conn.close()

def view_device_history(device_name):
    """View location history for a specific device"""
    
    db_path = Path("database") / "airtracker.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Find the device
    cursor.execute('''
        SELECT id, canonical_name 
        FROM devices 
        WHERE canonical_name LIKE ? OR device_name LIKE ?
        LIMIT 1
    ''', (f'%{device_name}%', f'%{device_name}%'))
    
    device = cursor.fetchone()
    
    if not device:
        print(f"Device '{device_name}' not found.")
        conn.close()
        return
    
    device_id, canonical_name = device
    
    print(f"\nLOCATION HISTORY: {canonical_name}")
    print("=" * 80)
    
    # Get location history
    cursor.execute('''
        SELECT 
            datetime(dl.timestamp_unix, 'unixepoch', 'localtime') as seen_at,
            dl.distance_meters,
            dl.location_text,
            dl.latitude,
            dl.longitude
        FROM device_locations dl
        WHERE dl.device_id = ?
        ORDER BY dl.timestamp_unix DESC
        LIMIT 20
    ''', (device_id,))
    
    locations = cursor.fetchall()
    
    if locations:
        for seen_at, distance, location, lat, lon in locations:
            print(f"{seen_at}")
            
            if distance is not None:
                if distance == 0:
                    print(f"  Distance: At location")
                else:
                    print(f"  Distance: {distance}m ({distance/1000:.1f}km)")
            
            if location:
                print(f"  Location: {location}")
            
            if lat and lon:
                print(f"  Coordinates: ({lat:.6f}, {lon:.6f})")
                print(f"  Maps: https://www.google.com/maps?q={lat},{lon}")
            
            print()
    else:
        print("No location history found for this device.")
    
    conn.close()

if __name__ == "__main__":
    import sys
    
    view_all_devices()
    
    if len(sys.argv) > 1:
        device_name = ' '.join(sys.argv[1:])
        view_device_history(device_name)