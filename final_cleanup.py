#!/usr/bin/env python3

import sqlite3
from pathlib import Path

def final_cleanup():
    """Final cleanup to consolidate all duplicates"""
    db_path = Path("database") / "airtracker.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("FINAL DATABASE CLEANUP")
    print("=" * 60)
    
    # 1. Remove garbage entries
    print("\n1. Removing garbage entries...")
    garbage_ids = [29]  # "o F eee"
    for device_id in garbage_ids:
        cursor.execute("DELETE FROM device_locations WHERE device_id = ?", (device_id,))
        cursor.execute("DELETE FROM devices WHERE id = ?", (device_id,))
        print(f"   Removed device ID {device_id}")
    
    # 2. Consolidate all Jelliede Bellie variants
    print("\n2. Consolidating 'Jelliede Bellie' entries...")
    
    # Find all Jelliede Bellie devices
    cursor.execute("""
        SELECT id, device_name 
        FROM devices 
        WHERE device_name LIKE 'Jelliede Bellie%'
        ORDER BY 
            CASE 
                WHEN device_name = 'Jelliede Bellie Portefeuille' THEN 0
                ELSE 1
            END,
            id ASC
    """)
    jelliede_devices = cursor.fetchall()
    
    if len(jelliede_devices) > 1:
        # Keep the one with the full proper name
        primary = None
        for device_id, name in jelliede_devices:
            if name == 'Jelliede Bellie Portefeuille':
                primary = (device_id, name)
                break
        
        if not primary:
            primary = jelliede_devices[0]  # Fallback to first one
            
        print(f"   Primary: '{primary[1]}' (ID: {primary[0]})")
        
        # Merge all others into primary
        for device_id, name in jelliede_devices:
            if device_id != primary[0]:
                print(f"   Merging: '{name}' (ID: {device_id}) -> Primary")
                
                # Move all locations
                cursor.execute("""
                    UPDATE device_locations 
                    SET device_id = ? 
                    WHERE device_id = ?
                """, (primary[0], device_id))
                
                # Delete duplicate
                cursor.execute("DELETE FROM devices WHERE id = ?", (device_id,))
    
    # 3. Update last_seen for all devices
    print("\n3. Updating last_seen timestamps...")
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
    
    conn.commit()
    
    # 4. Show final state
    print("\n4. Final device list:")
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
    print("\n✅ Final cleanup complete!")

if __name__ == "__main__":
    final_cleanup()