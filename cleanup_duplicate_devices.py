#!/usr/bin/env python3

import sqlite3
from pathlib import Path
from collections import defaultdict
import re

def clean_device_name(name):
    """Clean and normalize device names"""
    if not name:
        return name
    
    # Remove trailing dots and special characters
    name = re.sub(r'\.+$', '', name)
    name = re.sub(r'\.+%\d+k$', '', name)
    name = re.sub(r'\.+\s*$', '', name)
    
    # Handle truncated names (keep the longest version)
    return name.strip()

def find_duplicate_groups():
    """Find groups of duplicate devices"""
    db_path = Path("database") / "airtracker.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Get all devices
    cursor.execute("""
        SELECT id, device_name, canonical_name, first_seen, last_seen 
        FROM devices 
        ORDER BY device_name, first_seen
    """)
    
    devices = cursor.fetchall()
    
    # Group devices by cleaned name
    device_groups = defaultdict(list)
    for device in devices:
        device_id, name, canonical, first_seen, last_seen = device
        cleaned_name = clean_device_name(name)
        
        # For "Jelliede Bellie" type names, use the base name
        if cleaned_name and "Jelliede Bellie" in cleaned_name:
            cleaned_name = "Jelliede Bellie Portefeuille"
        
        device_groups[cleaned_name].append({
            'id': device_id,
            'name': name,
            'canonical': canonical,
            'first_seen': first_seen,
            'last_seen': last_seen
        })
    
    # Find duplicates
    duplicates = {k: v for k, v in device_groups.items() if len(v) > 1}
    
    print("DUPLICATE DEVICE GROUPS FOUND:")
    print("=" * 60)
    
    for cleaned_name, devices in duplicates.items():
        print(f"\nGroup: '{cleaned_name}'")
        print(f"  Count: {len(devices)} devices")
        for device in devices:
            # Count locations for this device
            cursor.execute("SELECT COUNT(*) FROM device_locations WHERE device_id = ?", (device['id'],))
            location_count = cursor.fetchone()[0]
            print(f"  - ID {device['id']}: '{device['name']}' ({location_count} locations)")
    
    conn.close()
    return duplicates

def consolidate_duplicates(dry_run=True):
    """Consolidate duplicate devices into single entries"""
    db_path = Path("database") / "airtracker.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    duplicates = find_duplicate_groups()
    
    print("\n\nCONSOLIDATION PLAN:")
    print("=" * 60)
    
    for cleaned_name, devices in duplicates.items():
        if not devices:
            continue
            
        # Choose the primary device (the one with most locations or earliest first_seen)
        primary = None
        max_locations = -1
        
        for device in devices:
            cursor.execute("SELECT COUNT(*) FROM device_locations WHERE device_id = ?", (device['id'],))
            location_count = cursor.fetchone()[0]
            
            if location_count > max_locations or (location_count == max_locations and primary is None):
                max_locations = location_count
                primary = device
        
        if not primary:
            continue
            
        print(f"\nFor '{cleaned_name}':")
        print(f"  Primary device: ID {primary['id']} (keeping this one)")
        
        # Update the primary device with the cleaned name
        if not dry_run:
            cursor.execute("""
                UPDATE devices 
                SET device_name = ?, canonical_name = ?
                WHERE id = ?
            """, (cleaned_name, cleaned_name.lower().replace(' ', '_'), primary['id']))
        
        # Merge other devices into primary
        for device in devices:
            if device['id'] != primary['id']:
                print(f"  - Merging ID {device['id']} -> ID {primary['id']}")
                
                if not dry_run:
                    # Update all locations to point to primary device
                    cursor.execute("""
                        UPDATE device_locations 
                        SET device_id = ? 
                        WHERE device_id = ?
                    """, (primary['id'], device['id']))
                    
                    # Update last_seen on primary if needed
                    cursor.execute("""
                        UPDATE devices 
                        SET last_seen = MAX(last_seen, ?)
                        WHERE id = ?
                    """, (device['last_seen'], primary['id']))
                    
                    # Delete the duplicate device
                    cursor.execute("DELETE FROM devices WHERE id = ?", (device['id'],))
    
    if dry_run:
        print("\n⚠️  DRY RUN - No changes made")
        print("Run with --apply to apply these changes")
    else:
        conn.commit()
        print("\n✅ Changes applied successfully!")
        
        # Show final device count
        cursor.execute("SELECT COUNT(*) FROM devices")
        device_count = cursor.fetchone()[0]
        print(f"\nFinal device count: {device_count}")
    
    conn.close()

def main():
    import sys
    
    print("DEVICE DUPLICATE CLEANUP TOOL")
    print("=" * 60)
    
    if len(sys.argv) > 1 and sys.argv[1] == "--apply":
        print("Running in APPLY mode - changes will be made!\n")
        consolidate_duplicates(dry_run=False)
    else:
        print("Running in DRY RUN mode - no changes will be made\n")
        consolidate_duplicates(dry_run=True)

if __name__ == "__main__":
    main()