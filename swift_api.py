#!/usr/bin/env python3
"""
FastAPI server for Swift-based AirTag tracking data.

This API provides REST endpoints for accessing AirTag location data
collected by the Swift accessibility-based tracker.
"""

from fastapi import FastAPI, HTTPException, Query, Path as PathParam
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
import sqlite3
from pathlib import Path
import json
from contextlib import contextmanager

# FastAPI app initialization
app = FastAPI(
    title="AirTag Swift Tracker API",
    description="REST API for Swift-based AirTag location tracking",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# CORS middleware for web frontends
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic models
class DeviceLocation(BaseModel):
    """Single location record for a device"""
    id: int
    device_name: str
    location: Optional[str]
    time_status: Optional[str]
    distance: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]
    timestamp: datetime
    extracted_at: Optional[datetime]

class Device(BaseModel):
    """Device summary information"""
    device_name: str
    first_seen: datetime
    last_seen: datetime
    last_location: Optional[str]
    update_count: int
    minutes_since_update: Optional[float]
    location_count: Optional[int] = 0

class DeviceHistory(BaseModel):
    """Device with location history"""
    device: Device
    locations: List[DeviceLocation]

class HealthStatus(BaseModel):
    """API health status"""
    status: str
    database_connected: bool
    total_devices: int
    total_locations: int
    last_update: Optional[datetime]
    database_path: str

class Statistics(BaseModel):
    """Statistics for a device over a time period"""
    device_name: str
    period: str
    total_updates: int
    unique_locations: int
    location_frequencies: Dict[str, int]
    average_updates_per_day: float
    last_movement: Optional[datetime]

# Database connection
@contextmanager
def get_db():
    """Get database connection with proper cleanup"""
    db_path = Path("database/airtracker.db")
    if not db_path.exists():
        raise HTTPException(status_code=500, detail=f"Database not found at {db_path}")
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

# Helper functions
def row_to_dict(row: sqlite3.Row) -> dict:
    """Convert SQLite row to dictionary"""
    return dict(zip(row.keys(), row))

def parse_period(period: str) -> timedelta:
    """Parse period string like '7d', '24h', '1w' to timedelta"""
    import re
    match = re.match(r'^(\d+)([dhw])$', period)
    if not match:
        raise ValueError(f"Invalid period format: {period}")
    
    value, unit = match.groups()
    value = int(value)
    
    if unit == 'h':
        return timedelta(hours=value)
    elif unit == 'd':
        return timedelta(days=value)
    elif unit == 'w':
        return timedelta(weeks=value)

# API Endpoints

@app.get("/", response_class=HTMLResponse, tags=["General"])
async def root():
    """Root endpoint with API information"""
    return """
    <html>
        <head>
            <title>AirTag Swift Tracker API</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 40px; }
                h1 { color: #333; }
                .endpoint { background: #f4f4f4; padding: 10px; margin: 10px 0; border-radius: 5px; }
                code { background: #e0e0e0; padding: 2px 5px; border-radius: 3px; }
                a { color: #007bff; text-decoration: none; }
                a:hover { text-decoration: underline; }
            </style>
        </head>
        <body>
            <h1>🏷️ AirTag Swift Tracker API</h1>
            <p>REST API for accessing AirTag location data collected via Swift accessibility APIs.</p>
            
            <h2>📚 Documentation</h2>
            <ul>
                <li><a href="/docs">Swagger UI Documentation</a></li>
                <li><a href="/redoc">ReDoc Documentation</a></li>
            </ul>
            
            <h2>🔗 Quick Links</h2>
            <div class="endpoint">
                <strong>GET</strong> <code>/health</code> - Check API health status
            </div>
            <div class="endpoint">
                <strong>GET</strong> <code>/devices</code> - List all tracked devices
            </div>
            <div class="endpoint">
                <strong>GET</strong> <code>/devices/{device_name}/history</code> - Get device location history
            </div>
            <div class="endpoint">
                <strong>GET</strong> <code>/locations/latest</code> - Get latest locations for all devices
            </div>
            <div class="endpoint">
                <strong>GET</strong> <code>/stats/{device_name}</code> - Get device statistics
            </div>
        </body>
    </html>
    """

@app.get("/health", response_model=HealthStatus, tags=["General"])
async def health_check():
    """Check API and database health"""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            
            # Check swift_devices table
            cursor.execute("SELECT COUNT(*) FROM swift_devices")
            device_count = cursor.fetchone()[0]
            
            # Check swift_locations table
            cursor.execute("SELECT COUNT(*) FROM swift_locations")
            location_count = cursor.fetchone()[0]
            
            # Get last update
            cursor.execute("SELECT MAX(timestamp) FROM swift_locations")
            last_update = cursor.fetchone()[0]
            if last_update:
                last_update = datetime.fromisoformat(last_update)
            
            return HealthStatus(
                status="healthy",
                database_connected=True,
                total_devices=device_count,
                total_locations=location_count,
                last_update=last_update,
                database_path="database/airtracker.db"
            )
    except Exception as e:
        return HealthStatus(
            status="unhealthy",
            database_connected=False,
            total_devices=0,
            total_locations=0,
            last_update=None,
            database_path="database/airtracker.db"
        )

@app.get("/devices", response_model=List[Device], tags=["Devices"])
async def get_devices():
    """Get all tracked devices with their current status"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                d.device_name,
                d.first_seen,
                d.last_seen,
                d.last_location,
                d.update_count,
                ROUND((julianday('now') - julianday(d.last_seen)) * 24 * 60, 1) as minutes_since_update,
                COUNT(l.id) as location_count
            FROM swift_devices d
            LEFT JOIN swift_locations l ON d.device_name = l.device_name
            GROUP BY d.device_name
            ORDER BY d.last_seen DESC
        """)
        
        devices = []
        for row in cursor.fetchall():
            device_dict = row_to_dict(row)
            # Parse datetime strings
            device_dict['first_seen'] = datetime.fromisoformat(device_dict['first_seen'])
            device_dict['last_seen'] = datetime.fromisoformat(device_dict['last_seen'])
            devices.append(Device(**device_dict))
        
        return devices

@app.get("/devices/{device_name}", response_model=Device, tags=["Devices"])
async def get_device(device_name: str = PathParam(..., description="Device name")):
    """Get specific device information"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                device_name,
                first_seen,
                last_seen,
                last_location,
                update_count,
                ROUND((julianday('now') - julianday(last_seen)) * 24 * 60, 1) as minutes_since_update
            FROM swift_devices
            WHERE device_name = ?
        """, (device_name,))
        
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Device '{device_name}' not found")
        
        device_dict = row_to_dict(row)
        device_dict['first_seen'] = datetime.fromisoformat(device_dict['first_seen'])
        device_dict['last_seen'] = datetime.fromisoformat(device_dict['last_seen'])
        
        return Device(**device_dict)

@app.get("/devices/{device_name}/history", response_model=DeviceHistory, tags=["Devices"])
async def get_device_history(
    device_name: str = PathParam(..., description="Device name"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of locations to return"),
    offset: int = Query(0, ge=0, description="Number of records to skip")
):
    """Get device location history"""
    device = await get_device(device_name)
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                id,
                device_name,
                location,
                time_status,
                distance,
                latitude,
                longitude,
                timestamp,
                extracted_at
            FROM swift_locations
            WHERE device_name = ?
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
        """, (device_name, limit, offset))
        
        locations = []
        for row in cursor.fetchall():
            loc_dict = row_to_dict(row)
            loc_dict['timestamp'] = datetime.fromisoformat(loc_dict['timestamp'])
            if loc_dict['extracted_at']:
                loc_dict['extracted_at'] = datetime.fromisoformat(loc_dict['extracted_at'])
            locations.append(DeviceLocation(**loc_dict))
        
        return DeviceHistory(device=device, locations=locations)

@app.get("/locations/latest", response_model=List[DeviceLocation], tags=["Locations"])
async def get_latest_locations():
    """Get the most recent location for each device"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                l.id,
                l.device_name,
                l.location,
                l.time_status,
                l.distance,
                l.timestamp,
                l.extracted_at
            FROM swift_locations l
            INNER JOIN (
                SELECT device_name, MAX(timestamp) as max_timestamp
                FROM swift_locations
                GROUP BY device_name
            ) latest ON l.device_name = latest.device_name 
                AND l.timestamp = latest.max_timestamp
            ORDER BY l.device_name
        """)
        
        locations = []
        for row in cursor.fetchall():
            loc_dict = row_to_dict(row)
            loc_dict['timestamp'] = datetime.fromisoformat(loc_dict['timestamp'])
            if loc_dict['extracted_at']:
                loc_dict['extracted_at'] = datetime.fromisoformat(loc_dict['extracted_at'])
            locations.append(DeviceLocation(**loc_dict))
        
        return locations

@app.get("/locations/search", response_model=List[DeviceLocation], tags=["Locations"])
async def search_locations(
    location: Optional[str] = Query(None, description="Location text to search for"),
    device_name: Optional[str] = Query(None, description="Filter by device name"),
    start_date: Optional[datetime] = Query(None, description="Start date filter"),
    end_date: Optional[datetime] = Query(None, description="End date filter"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum results")
):
    """Search location records with filters"""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Build query dynamically
        query = "SELECT * FROM swift_locations WHERE 1=1"
        params = []
        
        if location:
            query += " AND location LIKE ?"
            params.append(f"%{location}%")
        
        if device_name:
            query += " AND device_name = ?"
            params.append(device_name)
        
        if start_date:
            query += " AND timestamp >= ?"
            params.append(start_date.isoformat())
        
        if end_date:
            query += " AND timestamp <= ?"
            params.append(end_date.isoformat())
        
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        
        cursor.execute(query, params)
        
        locations = []
        for row in cursor.fetchall():
            loc_dict = row_to_dict(row)
            loc_dict['timestamp'] = datetime.fromisoformat(loc_dict['timestamp'])
            if loc_dict['extracted_at']:
                loc_dict['extracted_at'] = datetime.fromisoformat(loc_dict['extracted_at'])
            locations.append(DeviceLocation(**loc_dict))
        
        return locations

@app.get("/stats/{device_name}", response_model=Statistics, tags=["Statistics"])
async def get_device_stats(
    device_name: str = PathParam(..., description="Device name"),
    period: str = Query("7d", pattern="^\\d+[dhw]$", description="Time period (e.g., 7d, 24h, 1w)")
):
    """Get statistics for a device over a time period"""
    try:
        period_delta = parse_period(period)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    start_date = datetime.now() - period_delta
    
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Get basic stats
        cursor.execute("""
            SELECT 
                COUNT(*) as total_updates,
                COUNT(DISTINCT location) as unique_locations,
                MAX(timestamp) as last_movement
            FROM swift_locations
            WHERE device_name = ? AND timestamp >= ?
        """, (device_name, start_date.isoformat()))
        
        row = cursor.fetchone()
        if not row or row['total_updates'] == 0:
            raise HTTPException(status_code=404, detail=f"No data found for device '{device_name}' in period '{period}'")
        
        total_updates = row['total_updates']
        unique_locations = row['unique_locations']
        last_movement = datetime.fromisoformat(row['last_movement']) if row['last_movement'] else None
        
        # Get location frequencies
        cursor.execute("""
            SELECT location, COUNT(*) as count
            FROM swift_locations
            WHERE device_name = ? AND timestamp >= ?
            GROUP BY location
            ORDER BY count DESC
        """, (device_name, start_date.isoformat()))
        
        location_frequencies = {row['location']: row['count'] for row in cursor.fetchall()}
        
        # Calculate average updates per day
        days_in_period = max(period_delta.days, 1)
        avg_updates_per_day = total_updates / days_in_period
        
        return Statistics(
            device_name=device_name,
            period=period,
            total_updates=total_updates,
            unique_locations=unique_locations,
            location_frequencies=location_frequencies,
            average_updates_per_day=round(avg_updates_per_day, 2),
            last_movement=last_movement
        )

@app.post("/track", tags=["Actions"])
async def trigger_tracking():
    """Trigger a new tracking cycle using the Swift extractor"""
    from swift_tracker import SwiftAirTagTracker
    
    try:
        tracker = SwiftAirTagTracker()
        success = tracker.track_once()
        
        if success:
            return {
                "success": True,
                "message": "Tracking completed successfully",
                "timestamp": datetime.now()
            }
        else:
            raise HTTPException(status_code=500, detail="Tracking failed - no devices found")
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Tracking error: {str(e)}")

# Run the server
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)