-- Improved relational database schema for AirTracker

-- Table for AirTag devices (persistent entities)
CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_name TEXT NOT NULL UNIQUE,
    device_type TEXT, -- 'airtag', 'keys', 'bag', 'auto', etc.
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen DATETIME,
    is_active BOOLEAN DEFAULT TRUE
);

-- Improved locations table with device relationship
CREATE TABLE IF NOT EXISTS device_locations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL,
    screenshot_id INTEGER NOT NULL,
    distance_meters INTEGER,
    location_text TEXT,
    latitude REAL,
    longitude REAL,
    timestamp_unix INTEGER NOT NULL,
    signal_strength TEXT, -- could extract if available
    battery_level INTEGER, -- for future use
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (device_id) REFERENCES devices(id),
    FOREIGN KEY (screenshot_id) REFERENCES screenshots(id)
);

-- Index for faster queries
CREATE INDEX IF NOT EXISTS idx_device_locations_device_id ON device_locations(device_id);
CREATE INDEX IF NOT EXISTS idx_device_locations_timestamp ON device_locations(timestamp_unix);
CREATE INDEX IF NOT EXISTS idx_device_locations_screenshot ON device_locations(screenshot_id);

-- View to easily query latest location for each device
CREATE VIEW IF NOT EXISTS latest_device_locations AS
SELECT 
    d.id as device_id,
    d.device_name,
    d.device_type,
    dl.location_text,
    dl.distance_meters,
    dl.latitude,
    dl.longitude,
    dl.timestamp_unix,
    datetime(dl.timestamp_unix, 'unixepoch', 'localtime') as last_seen_datetime
FROM devices d
LEFT JOIN device_locations dl ON d.id = dl.device_id
WHERE dl.id = (
    SELECT id FROM device_locations 
    WHERE device_id = d.id 
    ORDER BY timestamp_unix DESC 
    LIMIT 1
);