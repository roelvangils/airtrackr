#!/usr/bin/env python3

"""
Improved AirTracker with proper relational database structure
"""

import sqlite3
from datetime import datetime

class ImprovedAirTracker:
    def __init__(self, db_path="database/airtracker_improved.db"):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Initialize improved database schema"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Create devices table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_name TEXT NOT NULL UNIQUE,
                device_type TEXT,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_seen DATETIME,
                is_active BOOLEAN DEFAULT TRUE
            )
        ''')
        
        # Create device_locations table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS device_locations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id INTEGER NOT NULL,
                screenshot_id INTEGER NOT NULL,
                distance_meters INTEGER,
                location_text TEXT,
                latitude REAL,
                longitude REAL,
                timestamp_unix INTEGER NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (device_id) REFERENCES devices(id),
                FOREIGN KEY (screenshot_id) REFERENCES screenshots(id)
            )
        ''')
        
        # Create indexes
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_device_locations_device_id ON device_locations(device_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_device_locations_timestamp ON device_locations(timestamp_unix)')
        
        conn.commit()
        conn.close()
    
    def get_or_create_device(self, device_name):
        """Get device ID, creating if necessary"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Try to get existing device
        cursor.execute('SELECT id FROM devices WHERE device_name = ?', (device_name,))
        result = cursor.fetchone()
        
        if result:
            device_id = result[0]
        else:
            # Create new device
            device_type = self.guess_device_type(device_name)
            cursor.execute('''
                INSERT INTO devices (device_name, device_type)
                VALUES (?, ?)
            ''', (device_name, device_type))
            device_id = cursor.lastrowid
            print(f"✓ New device registered: {device_name} (ID: {device_id})")
        
        conn.commit()
        conn.close()
        
        return device_id
    
    def guess_device_type(self, device_name):
        """Guess device type from name"""
        name_lower = device_name.lower()
        if 'key' in name_lower:
            return 'keys'
        elif 'bag' in name_lower or 'pack' in name_lower:
            return 'bag'
        elif 'valize' in name_lower:
            return 'luggage'
        elif 'auto' in name_lower or 'car' in name_lower:
            return 'vehicle'
        elif 'wallet' in name_lower or 'portefeu' in name_lower:
            return 'wallet'
        else:
            return 'airtag'
    
    def save_device_location(self, device_name, location_data, screenshot_id):
        """Save location with proper device relationship"""
        # Get or create device
        device_id = self.get_or_create_device(device_name)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Insert location
        cursor.execute('''
            INSERT INTO device_locations (
                device_id, screenshot_id, distance_meters,
                location_text, latitude, longitude, timestamp_unix
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            device_id,
            screenshot_id,
            location_data.get('distance_meters'),
            location_data.get('location_text'),
            location_data.get('latitude'),
            location_data.get('longitude'),
            location_data.get('timestamp_unix')
        ))
        
        # Update device last_seen
        cursor.execute('''
            UPDATE devices 
            SET last_seen = datetime(?, 'unixepoch')
            WHERE id = ?
        ''', (location_data.get('timestamp_unix'), device_id))
        
        conn.commit()
        conn.close()
    
    def get_device_history(self, device_name, limit=10):
        """Get location history for a specific device"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                dl.location_text,
                dl.distance_meters,
                dl.latitude,
                dl.longitude,
                datetime(dl.timestamp_unix, 'unixepoch', 'localtime') as seen_at
            FROM device_locations dl
            JOIN devices d ON dl.device_id = d.id
            WHERE d.device_name = ?
            ORDER BY dl.timestamp_unix DESC
            LIMIT ?
        ''', (device_name, limit))
        
        history = cursor.fetchall()
        conn.close()
        
        return history
    
    def get_all_devices_status(self):
        """Get current status of all devices"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                d.device_name,
                d.device_type,
                dl.location_text,
                dl.distance_meters,
                dl.latitude,
                dl.longitude,
                datetime(dl.timestamp_unix, 'unixepoch', 'localtime') as last_seen
            FROM devices d
            LEFT JOIN device_locations dl ON d.id = dl.device_id
            WHERE dl.id = (
                SELECT id FROM device_locations 
                WHERE device_id = d.id 
                ORDER BY timestamp_unix DESC 
                LIMIT 1
            )
            ORDER BY d.device_name
        ''')
        
        devices = cursor.fetchall()
        conn.close()
        
        return devices
    
    def find_devices_not_seen_recently(self, minutes=60):
        """Find devices not seen in the last N minutes"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                d.device_name,
                d.device_type,
                MAX(dl.timestamp_unix) as last_timestamp,
                (strftime('%s', 'now') - MAX(dl.timestamp_unix)) / 60 as minutes_ago
            FROM devices d
            LEFT JOIN device_locations dl ON d.id = dl.device_id
            GROUP BY d.id
            HAVING minutes_ago > ? OR minutes_ago IS NULL
            ORDER BY minutes_ago DESC
        ''', (minutes,))
        
        missing_devices = cursor.fetchall()
        conn.close()
        
        return missing_devices


# Example usage showing benefits
if __name__ == "__main__":
    tracker = ImprovedAirTracker()
    
    # Simulate adding some data
    print("IMPROVED STRUCTURE BENEFITS:")
    print("=" * 60)
    
    # Example location data
    location_data = {
        'distance_meters': 700,
        'location_text': 'Kouter, Ghent',
        'latitude': 51.050241,
        'longitude': 3.723831,
        'timestamp_unix': int(datetime.now().timestamp())
    }
    
    # This would be called during parsing
    # tracker.save_device_location("Black Valize", location_data, 1)
    
    print("\n1. AUTOMATIC DEVICE MANAGEMENT:")
    print("   - First time seeing 'Black Valize' → creates device record")
    print("   - Next time → reuses existing device ID")
    print("   - Maintains relationship even if position changes")
    
    print("\n2. EASY HISTORY QUERIES:")
    print("   history = tracker.get_device_history('Black Valize', limit=20)")
    print("   → Returns last 20 locations with timestamps")
    
    print("\n3. DEVICE STATUS DASHBOARD:")
    print("   devices = tracker.get_all_devices_status()")
    print("   → Shows current location of all devices")
    
    print("\n4. ALERTS AND MONITORING:")
    print("   missing = tracker.find_devices_not_seen_recently(60)")
    print("   → Find devices not seen in last hour")
    
    print("\n5. RELATIONSHIP BENEFITS:")
    print("   - Can add device metadata (owner, purchase date, etc.)")
    print("   - Track movement patterns per device")
    print("   - Generate device-specific reports")
    print("   - Handle device renaming gracefully")