#!/usr/bin/env python3

from fastapi import FastAPI, HTTPException, Query, Path as PathParam
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
import sqlite3
from pathlib import Path
import json
from contextlib import contextmanager

# FastAPI app initialization
app = FastAPI(
    title="AirTag Tracker API",
    description="REST API for tracking AirTag locations over time",
    version="1.0.0"
)

# CORS middleware for web frontends
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic models for request/response
class LocationResponse(BaseModel):
    id: int
    location_text: Optional[str]
    distance_meters: Optional[int]
    latitude: Optional[float]
    longitude: Optional[float]
    timestamp: datetime
    screenshot_id: int

class DeviceResponse(BaseModel):
    id: int
    name: str
    type: Optional[str]
    first_seen: datetime
    last_seen: Optional[datetime]
    is_active: bool
    current_location: Optional[LocationResponse] = None

class DeviceLocationHistory(BaseModel):
    device_id: int
    device_name: str
    locations: List[LocationResponse]
    total_count: int
    page: int = 1

class DeviceStats(BaseModel):
    device_id: int
    device_name: str
    period: str
    total_updates: int
    unique_locations: int
    most_frequent_location: Optional[str]
    average_distance: Optional[float]
    last_movement: Optional[datetime]

class CaptureResponse(BaseModel):
    success: bool
    screenshot_id: Optional[int]
    devices_found: int
    timestamp: datetime
    message: Optional[str] = None

class HealthResponse(BaseModel):
    status: str
    database_connected: bool
    last_capture: Optional[datetime]
    total_devices: int
    total_locations: int

# Database connection manager
@contextmanager
def get_db():
    db_path = Path("database") / "airtracker.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

# Helper function to convert sqlite3.Row to dict
def row_to_dict(row):
    return dict(zip(row.keys(), row))

# API Endpoints

@app.get("/", tags=["General"])
async def root():
    """Welcome endpoint"""
    return {
        "message": "AirTag Tracker API",
        "version": "1.0.0",
        "docs": "/docs",
        "redoc": "/redoc"
    }

@app.get("/health", response_model=HealthResponse, tags=["General"])
async def health_check():
    """Check API and database health"""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            
            # Get last capture time
            cursor.execute("SELECT MAX(timestamp) as last_capture FROM screenshots")
            last_capture_row = cursor.fetchone()
            last_capture = datetime.fromtimestamp(last_capture_row['last_capture']) if last_capture_row['last_capture'] else None
            
            # Get device count
            cursor.execute("SELECT COUNT(DISTINCT id) as count FROM devices")
            device_count = cursor.fetchone()['count']
            
            # Get location count
            cursor.execute("SELECT COUNT(*) as count FROM device_locations")
            location_count = cursor.fetchone()['count']
            
            return HealthResponse(
                status="healthy",
                database_connected=True,
                last_capture=last_capture,
                total_devices=device_count,
                total_locations=location_count
            )
    except Exception as e:
        return HealthResponse(
            status="unhealthy",
            database_connected=False,
            last_capture=None,
            total_devices=0,
            total_locations=0
        )

@app.get("/devices", response_model=List[DeviceResponse], tags=["Devices"])
async def get_all_devices(
    active_only: bool = Query(False, description="Only return active devices")
):
    """Get all tracked devices with their current location"""
    with get_db() as conn:
        cursor = conn.cursor()
        
        query = """
        SELECT 
            d.id,
            d.device_name as name,
            d.device_type as type,
            d.first_seen,
            d.last_seen,
            d.is_active,
            dl.id as location_id,
            dl.location_text,
            dl.distance_meters,
            dl.latitude,
            dl.longitude,
            dl.timestamp_unix,
            dl.screenshot_id
        FROM devices d
        LEFT JOIN device_locations dl ON d.id = dl.device_id
        WHERE (dl.id = (
            SELECT id FROM device_locations 
            WHERE device_id = d.id 
            ORDER BY timestamp_unix DESC 
            LIMIT 1
        ) OR dl.id IS NULL)
        AND d.device_type IN ('keys', 'luggage', 'vehicle', 'bag', 'wallet')
        AND d.device_name IS NOT NULL
        AND LENGTH(d.device_name) > 2
        """
        
        if active_only:
            query += " AND d.is_active = 1"
        
        cursor.execute(query)
        rows = cursor.fetchall()
        
        devices = []
        for row in rows:
            device = DeviceResponse(
                id=row['id'],
                name=row['name'],
                type=row['type'],
                first_seen=datetime.fromisoformat(row['first_seen']),
                last_seen=datetime.fromisoformat(row['last_seen']) if row['last_seen'] else None,
                is_active=bool(row['is_active'])
            )
            
            if row['location_id']:
                device.current_location = LocationResponse(
                    id=row['location_id'],
                    location_text=row['location_text'],
                    distance_meters=row['distance_meters'],
                    latitude=row['latitude'],
                    longitude=row['longitude'],
                    timestamp=datetime.fromtimestamp(row['timestamp_unix']) if row['timestamp_unix'] else datetime.now(),
                    screenshot_id=row['screenshot_id']
                )
            
            devices.append(device)
        
        return devices

@app.get("/devices/inactive", response_model=List[DeviceResponse], tags=["Devices"]) 
async def get_inactive_devices(
    hours: int = Query(2, ge=1, le=168, description="Hours since last update")
):
    """Get devices not seen in the last N hours"""
    cutoff_time = datetime.now() - timedelta(hours=hours)
    
    with get_db() as conn:
        cursor = conn.cursor()
        
        query = """
        SELECT 
            d.*,
            MAX(dl.timestamp_unix) as last_update
        FROM devices d
        LEFT JOIN device_locations dl ON d.id = dl.device_id
        WHERE d.is_active = 1
        GROUP BY d.id
        HAVING last_update < ? OR last_update IS NULL
        """
        
        cursor.execute(query, (int(cutoff_time.timestamp()),))
        rows = cursor.fetchall()
        
        devices = []
        for row in rows:
            device = DeviceResponse(
                id=row['id'],
                name=row['device_name'],
                type=row['device_type'],
                first_seen=datetime.fromisoformat(row['first_seen']),
                last_seen=datetime.fromtimestamp(row['last_update']) if row['last_update'] else None,
                is_active=bool(row['is_active'])
            )
            devices.append(device)
        
        return devices

@app.get("/devices/{device_id}", response_model=DeviceResponse, tags=["Devices"])
async def get_device_by_id(
    device_id: int = PathParam(..., description="Device ID")
):
    """Get specific device details with recent locations"""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Get device info
        cursor.execute("SELECT * FROM devices WHERE id = ?", (device_id,))
        device_row = cursor.fetchone()
        
        if not device_row:
            raise HTTPException(status_code=404, detail="Device not found")
        
        # Get current location
        cursor.execute("""
            SELECT * FROM device_locations 
            WHERE device_id = ? 
            ORDER BY timestamp_unix DESC 
            LIMIT 1
        """, (device_id,))
        location_row = cursor.fetchone()
        
        device = DeviceResponse(
            id=device_row['id'],
            name=device_row['device_name'],
            type=device_row['device_type'],
            first_seen=datetime.fromisoformat(device_row['first_seen']),
            last_seen=datetime.fromisoformat(device_row['last_seen']) if device_row['last_seen'] else None,
            is_active=bool(device_row['is_active'])
        )
        
        if location_row:
            device.current_location = LocationResponse(
                id=location_row['id'],
                location_text=location_row['location_text'],
                distance_meters=location_row['distance_meters'],
                latitude=location_row['latitude'],
                longitude=location_row['longitude'],
                timestamp=datetime.fromtimestamp(location_row['timestamp_unix']),
                screenshot_id=location_row['screenshot_id']
            )
        
        return device

@app.get("/devices/{device_id}/locations", response_model=DeviceLocationHistory, tags=["Locations"])
async def get_device_locations(
    device_id: int = PathParam(..., description="Device ID"),
    from_date: Optional[datetime] = Query(None, description="Start date for location history"),
    to_date: Optional[datetime] = Query(None, description="End date for location history"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of locations to return"),
    offset: int = Query(0, ge=0, description="Number of locations to skip")
):
    """Get location history for a specific device"""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Check device exists
        cursor.execute("SELECT device_name FROM devices WHERE id = ?", (device_id,))
        device = cursor.fetchone()
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")
        
        # Build query
        query = "SELECT * FROM device_locations WHERE device_id = ?"
        params = [device_id]
        
        if from_date:
            query += " AND timestamp_unix >= ?"
            params.append(int(from_date.timestamp()))
        
        if to_date:
            query += " AND timestamp_unix <= ?"
            params.append(int(to_date.timestamp()))
        
        # Get total count
        count_query = query.replace("*", "COUNT(*) as count")
        cursor.execute(count_query, params)
        total_count = cursor.fetchone()['count']
        
        # Get paginated results
        query += " ORDER BY timestamp_unix DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        locations = []
        for row in rows:
            locations.append(LocationResponse(
                id=row['id'],
                location_text=row['location_text'],
                distance_meters=row['distance_meters'],
                latitude=row['latitude'],
                longitude=row['longitude'],
                timestamp=datetime.fromtimestamp(row['timestamp_unix']),
                screenshot_id=row['screenshot_id']
            ))
        
        return DeviceLocationHistory(
            device_id=device_id,
            device_name=device['device_name'],
            locations=locations,
            total_count=total_count,
            page=(offset // limit) + 1
        )

@app.get("/locations/current", response_model=List[DeviceResponse], tags=["Locations"])
async def get_current_locations():
    """Get current location of all active devices"""
    return await get_all_devices(active_only=True)

@app.get("/locations/search", response_model=List[LocationResponse], tags=["Locations"])
async def search_locations(
    q: str = Query(..., min_length=2, description="Search query"),
    device_id: Optional[int] = Query(None, description="Filter by device ID"),
    limit: int = Query(50, ge=1, le=200)
):
    """Search for locations by text"""
    with get_db() as conn:
        cursor = conn.cursor()
        
        query = """
            SELECT * FROM device_locations 
            WHERE location_text LIKE ?
        """
        params = [f"%{q}%"]
        
        if device_id:
            query += " AND device_id = ?"
            params.append(device_id)
        
        query += " ORDER BY timestamp_unix DESC LIMIT ?"
        params.append(limit)
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        locations = []
        for row in rows:
            locations.append(LocationResponse(
                id=row['id'],
                location_text=row['location_text'],
                distance_meters=row['distance_meters'],
                latitude=row['latitude'],
                longitude=row['longitude'],
                timestamp=datetime.fromtimestamp(row['timestamp_unix']),
                screenshot_id=row['screenshot_id']
            ))
        
        return locations

@app.delete("/locations/{location_id}", tags=["Locations"])
async def delete_location(
    location_id: int = PathParam(..., description="Location ID to delete")
):
    """Delete a specific location entry"""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Check if location exists
        cursor.execute("SELECT id FROM device_locations WHERE id = ?", (location_id,))
        location = cursor.fetchone()
        
        if not location:
            raise HTTPException(status_code=404, detail="Location not found")
        
        # Delete the location
        cursor.execute("DELETE FROM device_locations WHERE id = ?", (location_id,))
        conn.commit()
        
        return {"message": f"Location {location_id} deleted successfully"}

@app.delete("/devices/{device_id}", tags=["Devices"])
async def delete_device(
    device_id: int = PathParam(..., description="Device ID to delete")
):
    """Delete a device and all its associated data"""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Check if device exists
        cursor.execute("SELECT id, canonical_name FROM devices WHERE id = ?", (device_id,))
        device = cursor.fetchone()
        
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")
        
        device_name = device['canonical_name']
        
        try:
            # Delete all associated data in the correct order (foreign key constraints)
            # 1. Delete from device_locations
            cursor.execute("DELETE FROM device_locations WHERE device_id = ?", (device_id,))
            deleted_locations = cursor.rowcount
            
            # 2. Delete from old locations table (if any references exist)
            cursor.execute("""
                DELETE FROM locations 
                WHERE screenshot_id IN (
                    SELECT DISTINCT screenshot_id 
                    FROM extracted_text et 
                    WHERE et.raw_text LIKE ?
                )
            """, (f"%{device_name}%",))
            
            # 3. Mark device as inactive instead of deleting to preserve referential integrity
            cursor.execute("""
                UPDATE devices 
                SET is_active = FALSE, 
                    last_seen = CURRENT_TIMESTAMP 
                WHERE id = ?
            """, (device_id,))
            
            conn.commit()
            
            return {
                "message": f"Device '{device_name}' deleted successfully",
                "device_id": device_id,
                "locations_deleted": deleted_locations,
                "device_deactivated": True
            }
            
        except Exception as e:
            conn.rollback()
            raise HTTPException(status_code=500, detail=f"Error deleting device: {str(e)}")

@app.get("/stats/devices/{device_id}", response_model=DeviceStats, tags=["Statistics"])
async def get_device_stats(
    device_id: int = PathParam(..., description="Device ID"),
    period: str = Query("7d", regex="^\\d+[dhw]$", description="Time period (e.g., 7d, 24h, 1w)")
):
    """Get statistics for a device over a time period"""
    # Parse period
    import re
    match = re.match(r'^(\d+)([dhw])$', period)
    if not match:
        raise HTTPException(status_code=400, detail="Invalid period format")
    
    value, unit = match.groups()
    hours = int(value)
    if unit == 'd':
        hours *= 24
    elif unit == 'w':
        hours *= 168
    
    cutoff_time = datetime.now() - timedelta(hours=hours)
    
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Get device name
        cursor.execute("SELECT device_name FROM devices WHERE id = ?", (device_id,))
        device = cursor.fetchone()
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")
        
        # Get statistics
        cursor.execute("""
            SELECT 
                COUNT(*) as total_updates,
                COUNT(DISTINCT location_text) as unique_locations,
                AVG(distance_meters) as avg_distance,
                MAX(timestamp_unix) as last_movement
            FROM device_locations
            WHERE device_id = ? AND timestamp_unix >= ?
        """, (device_id, int(cutoff_time.timestamp())))
        
        stats = cursor.fetchone()
        
        # Get most frequent location
        cursor.execute("""
            SELECT location_text, COUNT(*) as count
            FROM device_locations
            WHERE device_id = ? AND timestamp_unix >= ? AND location_text IS NOT NULL
            GROUP BY location_text
            ORDER BY count DESC
            LIMIT 1
        """, (device_id, int(cutoff_time.timestamp())))
        
        frequent = cursor.fetchone()
        
        return DeviceStats(
            device_id=device_id,
            device_name=device['device_name'],
            period=period,
            total_updates=stats['total_updates'],
            unique_locations=stats['unique_locations'],
            most_frequent_location=frequent['location_text'] if frequent else None,
            average_distance=round(stats['avg_distance'], 2) if stats['avg_distance'] else None,
            last_movement=datetime.fromtimestamp(stats['last_movement']) if stats['last_movement'] else None
        )

@app.post("/capture", response_model=CaptureResponse, tags=["Actions"])
async def trigger_capture():
    """Manually trigger a screenshot capture"""
    import subprocess
    import os
    
    try:
        # Run the main tracker script
        result = subprocess.run(
            ["python3", "improved_tracker.py", "--single-capture"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent
        )
        
        if result.returncode != 0:
            return CaptureResponse(
                success=False,
                screenshot_id=None,
                devices_found=0,
                timestamp=datetime.now(),
                message=f"Capture failed: {result.stderr}"
            )
        
        # Get the latest screenshot ID
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, timestamp FROM screenshots ORDER BY timestamp DESC LIMIT 1")
            screenshot = cursor.fetchone()
            
            if screenshot:
                # Count devices found in this capture
                cursor.execute("""
                    SELECT COUNT(DISTINCT device_id) as count
                    FROM device_locations
                    WHERE screenshot_id = ?
                """, (screenshot['id'],))
                device_count = cursor.fetchone()['count']
                
                return CaptureResponse(
                    success=True,
                    screenshot_id=screenshot['id'],
                    devices_found=device_count,
                    timestamp=datetime.fromtimestamp(screenshot['timestamp'])
                )
            else:
                return CaptureResponse(
                    success=False,
                    screenshot_id=None,
                    devices_found=0,
                    timestamp=datetime.now(),
                    message="No screenshot found after capture"
                )
                
    except Exception as e:
        return CaptureResponse(
            success=False,
            screenshot_id=None,
            devices_found=0,
            timestamp=datetime.now(),
            message=str(e)
        )

# Run the app
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)