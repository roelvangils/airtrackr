#!/usr/bin/env python3

import sqlite3
from pathlib import Path
import re

def is_valid_device_name(name):
    """Check if a device name is valid"""
    if not name or len(name) < 3:
        return False
    
    # List of known garbage patterns
    garbage_patterns = [
        r'^[a-z]$',  # Single letter
        r'^e$',
        r'^a$',
        r'^base\.$',
        r'^atesinthelast\d+$',
        r'^herclient$',
        r'^\d+$',  # Just numbers
    ]
    
    for pattern in garbage_patterns:
        if re.match(pattern, name.lower()):
            return False
    
    return True

def cleanup_database():
    """Comprehensive database cleanup"""
    db_path = Path("database") / "airtracker.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("COMPREHENSIVE DATABASE CLEANUP")
    print("=" * 60)
    
    # 1. Remove invalid devices
    print("\n1. Removing invalid device entries...")
    cursor.execute("SELECT id, device_name FROM devices")
    devices = cursor.fetchall()
    
    invalid_ids = []
    for device_id, name in devices:
        if not is_valid_device_name(name):
            print(f"   Removing invalid device: '{name}' (ID: {device_id})")
            invalid_ids.append(device_id)
    
    if invalid_ids:
        placeholders = ','.join('?' * len(invalid_ids))
        cursor.execute(f"DELETE FROM device_locations WHERE device_id IN ({placeholders})", invalid_ids)
        cursor.execute(f"DELETE FROM devices WHERE id IN ({placeholders})", invalid_ids)
        print(f"   Removed {len(invalid_ids)} invalid devices")
    else:
        print("   No invalid devices found")
    
    # 2. Consolidate Jelliede Bellie entries
    print("\n2. Consolidating 'Jelliede Bellie' entries...")
    
    # Find all Jelliede Bellie variants
    cursor.execute("""
        SELECT id, device_name 
        FROM devices 
        WHERE device_name LIKE 'Jelliede Bellie%'
        ORDER BY LENGTH(device_name) DESC, id ASC
    """)
    jelliede_devices = cursor.fetchall()
    
    if len(jelliede_devices) > 1:
        # Keep the one with the full name
        primary = jelliede_devices[0]  # Longest name first
        print(f"   Primary: '{primary[1]}' (ID: {primary[0]})")
        
        # Update to clean name
        cursor.execute("""
            UPDATE devices 
            SET device_name = 'Jelliede Bellie Portefeuille',
                canonical_name = 'jelliede_bellie_portefeuille'
            WHERE id = ?
        """, (primary[0],))
        
        # Merge all others into primary
        for device_id, name in jelliede_devices[1:]:
            print(f"   Merging: '{name}' (ID: {device_id}) -> Primary")
            
            # Move all locations
            cursor.execute("""
                UPDATE device_locations 
                SET device_id = ? 
                WHERE device_id = ?
            """, (primary[0], device_id))
            
            # Delete duplicate
            cursor.execute("DELETE FROM devices WHERE id = ?", (device_id,))
    
    # 3. Fix "Auto km" -> "Auto"
    print("\n3. Consolidating 'Auto' entries...")
    cursor.execute("""
        SELECT id, device_name 
        FROM devices 
        WHERE device_name IN ('Auto', 'Auto km')
        ORDER BY device_name ASC
    """)
    auto_devices = cursor.fetchall()
    
    if len(auto_devices) > 1:
        # Keep "Auto", merge "Auto km" into it
        auto_id = None
        auto_km_id = None
        
        for device_id, name in auto_devices:
            if name == 'Auto':
                auto_id = device_id
            elif name == 'Auto km':
                auto_km_id = device_id
        
        if auto_id and auto_km_id:
            print(f"   Merging 'Auto km' (ID: {auto_km_id}) -> 'Auto' (ID: {auto_id})")
            
            # Move all locations
            cursor.execute("""
                UPDATE device_locations 
                SET device_id = ? 
                WHERE device_id = ?
            """, (auto_id, auto_km_id))
            
            # Delete duplicate
            cursor.execute("DELETE FROM devices WHERE id = ?", (auto_km_id,))
    
    # 4. Update last_seen timestamps
    print("\n4. Updating last_seen timestamps...")
    cursor.execute("""
        UPDATE devices 
        SET last_seen = (
            SELECT datetime(MAX(timestamp_unix), 'unixepoch')
            FROM device_locations 
            WHERE device_locations.device_id = devices.id
        )
        WHERE EXISTS (
            SELECT 1 FROM device_locations 
            WHERE device_locations.device_id = devices.id
        )
    """)
    
    # 5. Show final state
    conn.commit()
    
    print("\n5. Final device list:")
    cursor.execute("""
        SELECT d.id, d.device_name, COUNT(dl.id) as location_count
        FROM devices d
        LEFT JOIN device_locations dl ON d.id = dl.device_id
        GROUP BY d.id, d.device_name
        ORDER BY d.device_name
    """)
    
    devices = cursor.fetchall()
    print(f"\n   Total devices: {len(devices)}")
    for device_id, name, count in devices:
        print(f"   - {name} (ID: {device_id}): {count} locations")
    
    conn.close()
    print("\n✅ Cleanup complete!")

if __name__ == "__main__":
    cleanup_database()