#!/usr/bin/env python3

import sqlite3
from pathlib import Path

def fix_duplicate_auto_device():
    """Merge 'Auto km' device with 'Auto' device"""
    
    db_path = Path("database") / "airtracker.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("CHECKING DUPLICATE DEVICES")
    print("=" * 60)
    
    # First, let's see what we have
    cursor.execute('''
        SELECT id, device_name, canonical_name, device_type
        FROM devices
        WHERE canonical_name LIKE '%Auto%'
        ORDER BY id
    ''')
    
    auto_devices = cursor.fetchall()
    
    print("\nCurrent Auto-related devices:")
    for device in auto_devices:
        print(f"ID: {device[0]}, Name: '{device[1]}', Canonical: '{device[2]}', Type: {device[3]}")
    
    # Find the main Auto device and the duplicate
    cursor.execute("SELECT id FROM devices WHERE canonical_name = 'Auto'")
    auto_result = cursor.fetchone()
    
    cursor.execute("SELECT id FROM devices WHERE canonical_name = 'Auto km'")
    auto_km_result = cursor.fetchone()
    
    if not auto_result or not auto_km_result:
        print("\nCouldn't find both devices. Please check manually.")
        conn.close()
        return
    
    auto_id = auto_result[0]
    auto_km_id = auto_km_result[0]
    
    print(f"\nWill merge 'Auto km' (ID: {auto_km_id}) into 'Auto' (ID: {auto_id})")
    
    # Count locations for each
    cursor.execute("SELECT COUNT(*) FROM device_locations WHERE device_id = ?", (auto_id,))
    auto_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM device_locations WHERE device_id = ?", (auto_km_id,))
    auto_km_count = cursor.fetchone()[0]
    
    print(f"\nLocation counts:")
    print(f"  Auto: {auto_count} locations")
    print(f"  Auto km: {auto_km_count} locations")
    
    if auto_km_count > 0:
        print(f"\nMigrating {auto_km_count} locations from 'Auto km' to 'Auto'...")
        
        # Update all locations from Auto km to point to Auto
        cursor.execute('''
            UPDATE device_locations 
            SET device_id = ? 
            WHERE device_id = ?
        ''', (auto_id, auto_km_id))
        
        print(f"Migrated {cursor.rowcount} location records")
    
    # Delete the duplicate device entry
    cursor.execute("DELETE FROM devices WHERE id = ?", (auto_km_id,))
    print(f"\nDeleted 'Auto km' device entry")
    
    # Also update the Auto device to recognize "Auto km" as a variation
    cursor.execute('''
        INSERT OR IGNORE INTO devices (device_name, canonical_name, device_type)
        VALUES (?, ?, ?)
    ''', ('Auto km', 'Auto', 'vehicle'))
    
    print("Added 'Auto km' as a recognized variation of 'Auto'")
    
    # Commit changes
    conn.commit()
    
    # Verify the fix
    print("\n\nVERIFYING FIX:")
    print("-" * 60)
    
    cursor.execute('''
        SELECT id, device_name, canonical_name
        FROM devices
        WHERE canonical_name LIKE '%Auto%'
        ORDER BY canonical_name, device_name
    ''')
    
    print("Auto-related devices after fix:")
    for device in cursor.fetchall():
        print(f"  '{device[1]}' → '{device[2]}' (ID: {device[0]})")
    
    cursor.execute('''
        SELECT COUNT(*) 
        FROM device_locations 
        WHERE device_id = ?
    ''', (auto_id,))
    
    final_count = cursor.fetchone()[0]
    print(f"\nTotal locations for 'Auto': {final_count}")
    
    conn.close()
    print("\nDone! The duplicate has been merged.")

if __name__ == "__main__":
    fix_duplicate_auto_device()