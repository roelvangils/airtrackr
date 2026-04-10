#!/usr/bin/env python3
"""
FastAPI server for Swift-based AirTag tracking data.

This API provides REST endpoints for accessing AirTag location data
collected by the Swift accessibility-based tracker.
"""

from fastapi import FastAPI, HTTPException, Query, Path as PathParam, BackgroundTasks, Security, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
import sqlite3
from pathlib import Path
import json
import csv
import io
import re
import os

from db import get_connection, init_schema, DB_PATH
from enrichment import haversine_km

# ─── API Key Authentication ───

import logging

logger = logging.getLogger("uvicorn.error")

def _load_api_key() -> Optional[str]:
    """Load API key from AIRTRACKR_API_KEY env var or .api_key file."""
    key = os.environ.get("AIRTRACKR_API_KEY")
    if key:
        return key.strip()
    key_file = Path(__file__).parent / ".api_key"
    if key_file.exists():
        return key_file.read_text().strip()
    return None

# Check authentication status at module load and warn if disabled
_startup_api_key = _load_api_key()
if _startup_api_key is None:
    logger.warning(
        "⚠️  API AUTHENTICATION DISABLED - No .api_key file or AIRTRACKR_API_KEY env var found. "
        "API is running in development mode without authentication. "
        "Create a .api_key file with a secret key to enable authentication."
    )

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

async def require_api_key(api_key: Optional[str] = Security(_api_key_header)):
    """Dependency that enforces API key authentication."""
    expected = _load_api_key()
    if expected is None:
        return  # No .api_key file = auth disabled (development mode)
    if not api_key or api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

# Ensure schema is up to date on startup
init_schema()

# FastAPI app initialization
app = FastAPI(
    title="AirTag Swift Tracker API",
    description="REST API for Swift-based AirTag location tracking",
    version="3.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    dependencies=[Depends(require_api_key)],
)

# CORS middleware — restrict to known dashboard origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://192.168.50.6:3000",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "X-API-Key"],
)

# ─── Pydantic models ───

class DeviceLocation(BaseModel):
    """Single location record for a device"""
    id: int
    device_name: str
    location: Optional[str]
    time_status: Optional[str]
    distance: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]
    device_type: Optional[str] = None
    timestamp: datetime
    extracted_at: Optional[datetime]
    distance_from_home_km: Optional[float] = None
    battery_status: Optional[str] = None
    street: Optional[str] = None
    house_number: Optional[str] = None
    postal_code: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None

class Device(BaseModel):
    """Device summary information"""
    device_name: str
    device_type: Optional[str] = None
    first_seen: datetime
    last_seen: datetime
    last_location: Optional[str]
    update_count: int
    minutes_since_update: Optional[float]
    location_count: Optional[int] = 0

class DeviceTypeCounts(BaseModel):
    """Count of devices by type"""
    people: int
    devices: int
    items: int
    total: int

class PaginatedResponse(BaseModel):
    """Paginated list response"""
    items: List[Any]
    total: int
    limit: int
    offset: int
    has_more: bool

class DeviceHistory(BaseModel):
    """Device with location history"""
    device: Device
    locations: List[DeviceLocation]
    total: int

class HealthStatus(BaseModel):
    """API health status"""
    status: str
    database_connected: bool
    total_devices: int
    total_locations: int
    last_update: Optional[datetime]
    database_path: str
    database_size_mb: Optional[float] = None
    oldest_raw_record: Optional[datetime] = None
    summary_count: Optional[int] = None
    # Tracker health metrics
    tracker_status: Optional[str] = None  # "active", "stale", "unknown"
    minutes_since_extraction: Optional[int] = None
    tracker_warning: Optional[str] = None  # Warning message if tracker is stale
    # Network health
    internet_connected: Optional[bool] = None
    icloud_reachable: Optional[bool] = None
    # Night mode status
    night_mode_active: Optional[bool] = None
    temp_wake_active: Optional[bool] = None
    temp_wake_expires: Optional[datetime] = None
    # Permission status
    permissions_ok: Optional[bool] = None
    missing_permissions: Optional[str] = None

class Statistics(BaseModel):
    """Statistics for a device over a time period"""
    device_name: str
    period: str
    total_updates: int
    unique_locations: int
    location_frequencies: Dict[str, int]
    average_updates_per_day: float
    last_movement: Optional[datetime]

class Zone(BaseModel):
    """Geofencing zone"""
    id: Optional[int] = None
    name: str
    latitude: float
    longitude: float
    radius_meters: float = 100

class ZoneCreate(BaseModel):
    """Zone creation request"""
    name: str
    latitude: float
    longitude: float
    radius_meters: float = 100

class ZoneCheck(BaseModel):
    """Result of checking if a device is in a zone"""
    device_name: str
    zone: Optional[Zone] = None
    distance_meters: Optional[float] = None
    in_zone: bool

class Trip(BaseModel):
    """A detected trip between two locations"""
    id: int
    device_name: str
    start_time: datetime
    end_time: datetime
    start_location: Optional[str]
    end_location: Optional[str]
    start_lat: Optional[float]
    start_lon: Optional[float]
    end_lat: Optional[float]
    end_lon: Optional[float]
    distance_km: Optional[float]
    duration_minutes: Optional[float]

class Visit(BaseModel):
    """A visit / dwell at a location"""
    id: int
    device_name: str
    location: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]
    arrival_time: datetime
    departure_time: Optional[datetime]
    duration_minutes: Optional[float]

class StructuredAddress(BaseModel):
    """Structured address components from geocoding"""
    street: Optional[str] = None
    house_number: Optional[str] = None
    postal_code: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None

class DeviceStatsSummary(BaseModel):
    """Summary statistics for a device"""
    device_name: str
    total_records: int
    first_seen: Optional[datetime]
    last_seen: Optional[datetime]
    days_tracked: int
    avg_updates_per_day: float
    unique_locations: int
    top_locations: List[Dict[str, Any]]
    home_record_count: int
    home_percentage: Optional[float]
    records_with_coords: int
    total_distance_km: Optional[float]
    furthest_from_home_km: Optional[float]
    furthest_location: Optional[str]


# ─── Helpers ───

def row_to_dict(row: sqlite3.Row) -> dict:
    """Convert SQLite row to dictionary"""
    return dict(zip(row.keys(), row))

def parse_period(period: str) -> timedelta:
    """Parse period string like '7d', '24h', '1w' to timedelta"""
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

def parse_datetime(value: str) -> datetime:
    """Parse datetime string from database"""
    return datetime.fromisoformat(value)

def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance in meters between two lat/lon points."""
    return haversine_km(lat1, lon1, lat2, lon2) * 1000

def _parse_location_row(row) -> DeviceLocation:
    """Parse a swift_locations row into a DeviceLocation."""
    loc_dict = row_to_dict(row)
    loc_dict['timestamp'] = parse_datetime(loc_dict['timestamp'])
    if loc_dict.get('extracted_at'):
        loc_dict['extracted_at'] = parse_datetime(loc_dict['extracted_at'])
    # Only pass known fields to avoid Pydantic errors from extra DB columns
    known_fields = {f for f in DeviceLocation.model_fields}
    filtered = {k: v for k, v in loc_dict.items() if k in known_fields}
    return DeviceLocation(**filtered)

def _parse_device_row(row) -> Device:
    """Parse a swift_devices row into a Device."""
    d = row_to_dict(row)
    d['first_seen'] = parse_datetime(d['first_seen'])
    d['last_seen'] = parse_datetime(d['last_seen'])
    return Device(**d)

def _generate_gpx(device_name: str, locations: list) -> str:
    """Generate GPX XML from location records."""
    # Escape XML special characters
    def esc(s):
        return (s or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="AirTrackr"',
        '  xmlns="http://www.topografix.com/GPX/1/1">',
        f'  <trk><name>{esc(device_name)}</name><trkseg>',
    ]
    for loc in locations:
        if loc.get('latitude') and loc.get('longitude'):
            ts = loc.get('timestamp', '')
            lines.append(
                f'    <trkpt lat="{loc["latitude"]}" lon="{loc["longitude"]}">'
                f'<time>{ts}</time>'
                f'<name>{esc(loc.get("location", ""))}</name>'
                f'</trkpt>'
            )
    lines.append('  </trkseg></trk>')
    lines.append('</gpx>')
    return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════
# API v1 Router
# ═══════════════════════════════════════════════════════════════

from fastapi import APIRouter

v1 = APIRouter(prefix="/api/v1")


# ─── General ───

def _check_internet_connectivity() -> tuple[bool, bool]:
    """
    Check internet and iCloud connectivity.

    Returns:
        Tuple of (internet_connected, icloud_reachable)
    """
    import socket

    internet_connected = False
    icloud_reachable = False

    # Check general internet (Google DNS)
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=3)
        internet_connected = True
    except OSError:
        pass

    # Check iCloud/Apple connectivity (used by Find My)
    if internet_connected:
        try:
            socket.create_connection(("icloud.com", 443), timeout=3)
            icloud_reachable = True
        except OSError:
            pass

    return internet_connected, icloud_reachable


# ─── Night Mode / Wake on Demand ───

NIGHT_FLAG = Path("/tmp/airtrackr_night_mode")
TEMP_WAKE_FLAG = Path("/tmp/airtrackr_temp_wake")
TEMP_WAKE_DURATION_MINUTES = 30


def _is_night_mode_active() -> bool:
    """Check if night mode flag exists."""
    return NIGHT_FLAG.exists()


def _get_temp_wake_expiry() -> Optional[datetime]:
    """Get temp wake expiry time, or None if not active/expired."""
    if not TEMP_WAKE_FLAG.exists():
        return None
    try:
        expiry_str = TEMP_WAKE_FLAG.read_text().strip()
        expiry = datetime.fromisoformat(expiry_str)
        if datetime.now() < expiry:
            return expiry
        # Expired - clean up
        TEMP_WAKE_FLAG.unlink(missing_ok=True)
        return None
    except (ValueError, OSError):
        return None


def _trigger_temp_wake() -> bool:
    """
    Trigger a temporary wake during night mode.

    - Creates temp wake flag with expiry timestamp
    - Removes night mode flag so watchdog starts tracker
    - Returns True if wake was triggered
    """
    try:
        # Calculate expiry time
        expiry = datetime.now() + timedelta(minutes=TEMP_WAKE_DURATION_MINUTES)

        # Create temp wake flag with expiry
        TEMP_WAKE_FLAG.write_text(expiry.isoformat())

        # Remove night mode flag (watchdog will start tracker within 5 min)
        NIGHT_FLAG.unlink(missing_ok=True)

        return True
    except OSError:
        return False


def _maybe_trigger_wake(minutes_since_extraction: Optional[int]) -> bool:
    """
    Check if we should trigger a wake and do it if needed.

    Conditions for triggering:
    - Night mode is active (00:00 - 07:00)
    - No temp wake already active
    - Data is stale (>30 minutes old)
    - We have internet connectivity

    Returns True if wake was triggered.
    """
    # Don't wake if temp wake already active
    if _get_temp_wake_expiry():
        return False

    # Only wake during night mode
    if not _is_night_mode_active():
        return False

    # Only wake if data is stale
    if minutes_since_extraction is None or minutes_since_extraction < 30:
        return False

    # Check internet first - no point waking without connectivity
    internet_ok, _ = _check_internet_connectivity()
    if not internet_ok:
        return False

    return _trigger_temp_wake()


@v1.get("/health", response_model=HealthStatus, tags=["General"])
async def health_check():
    """Check API and database health"""
    try:
        with get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM swift_devices")
            device_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM swift_locations")
            location_count = cursor.fetchone()[0]

            # Use extracted_at for tracking health (when we last collected data)
            cursor.execute("SELECT MAX(extracted_at) FROM swift_locations")
            last_update = cursor.fetchone()[0]
            if last_update:
                last_update = parse_datetime(last_update)

            # Database monitoring extras
            db_size_mb = None
            if DB_PATH.exists():
                db_size_mb = round(DB_PATH.stat().st_size / (1024 * 1024), 2)

            cursor.execute("SELECT MIN(timestamp) FROM swift_locations")
            oldest_raw = cursor.fetchone()[0]
            oldest_raw_record = parse_datetime(oldest_raw) if oldest_raw else None

            cursor.execute("SELECT COUNT(*) FROM location_summaries")
            summary_count = cursor.fetchone()[0]

            # Calculate tracker health
            tracker_status = "unknown"
            minutes_since_extraction = None
            tracker_warning = None

            if last_update:
                now = datetime.now()
                # Handle timezone-naive comparison
                if last_update.tzinfo is not None:
                    from datetime import timezone as tz
                    now = now.replace(tzinfo=tz.utc)
                delta = now - last_update
                minutes_since_extraction = int(delta.total_seconds() / 60)

                if minutes_since_extraction <= 10:
                    tracker_status = "active"
                elif minutes_since_extraction <= 30:
                    tracker_status = "active"  # Still ok, just slower cycle
                elif minutes_since_extraction <= 60:
                    tracker_status = "stale"
                    tracker_warning = f"No updates for {minutes_since_extraction} minutes - tracker may be having issues"
                else:
                    tracker_status = "stale"
                    hours = minutes_since_extraction // 60
                    tracker_warning = f"No updates for {hours}h {minutes_since_extraction % 60}m - tracker likely stopped or Find My not responding"

            # Check internet connectivity
            internet_connected, icloud_reachable = _check_internet_connectivity()

            # Check night mode status
            night_mode_active = _is_night_mode_active()
            temp_wake_expiry = _get_temp_wake_expiry()
            temp_wake_active = temp_wake_expiry is not None

            # Wake on demand: if data is stale during night mode, trigger temporary wake
            wake_triggered = False
            if night_mode_active and not temp_wake_active:
                wake_triggered = _maybe_trigger_wake(minutes_since_extraction)
                if wake_triggered:
                    # Update status - wake was just triggered
                    night_mode_active = False  # Flag was just removed
                    temp_wake_expiry = _get_temp_wake_expiry()
                    temp_wake_active = True

            # Determine overall status and warnings
            warnings = []
            if tracker_warning and not night_mode_active and not temp_wake_active:
                # Only show tracker warning if not in night mode
                warnings.append(tracker_warning)
            if wake_triggered:
                warnings.append(f"Wake on demand triggered - tracker will resume within 5 minutes")
            if night_mode_active:
                warnings.append("Night mode active (00:00-07:00) - tracker paused")
            if temp_wake_active:
                mins_left = int((temp_wake_expiry - datetime.now()).total_seconds() / 60)
                warnings.append(f"Temporary wake active - {mins_left} minutes remaining")
            if not internet_connected:
                warnings.append("No internet connection - Find My cannot sync")
            elif not icloud_reachable:
                warnings.append("Cannot reach iCloud - Find My sync may fail")

            # Determine overall status
            if not internet_connected:
                overall_status = "unhealthy"
            elif night_mode_active:
                overall_status = "night_mode"  # Special status for night mode
            elif tracker_status == "stale" or not icloud_reachable:
                overall_status = "degraded"
            else:
                overall_status = "healthy"

            # Check for missing permissions (flag file from permission-check script)
            permissions_ok = True
            missing_permissions = None
            perm_flag = Path("/tmp/airtrackr_missing_permissions")
            if perm_flag.exists():
                try:
                    missing_permissions = perm_flag.read_text().strip()
                    permissions_ok = False
                    warnings.append(f"Missing permissions: {missing_permissions}")
                    if overall_status == "healthy":
                        overall_status = "degraded"
                except OSError:
                    pass

            # Combine warnings
            combined_warning = " | ".join(warnings) if warnings else None

            return HealthStatus(
                status=overall_status,
                database_connected=True,
                total_devices=device_count,
                total_locations=location_count,
                last_update=last_update,
                database_path=str(DB_PATH),
                database_size_mb=db_size_mb,
                oldest_raw_record=oldest_raw_record,
                summary_count=summary_count,
                tracker_status=tracker_status,
                minutes_since_extraction=minutes_since_extraction,
                tracker_warning=combined_warning,
                internet_connected=internet_connected,
                icloud_reachable=icloud_reachable,
                night_mode_active=night_mode_active,
                temp_wake_active=temp_wake_active,
                temp_wake_expires=temp_wake_expiry,
                permissions_ok=permissions_ok,
                missing_permissions=missing_permissions,
            )
    except Exception:
        return HealthStatus(
            status="unhealthy",
            database_connected=False,
            total_devices=0,
            total_locations=0,
            last_update=None,
            database_path=str(DB_PATH),
        )


# ─── Devices ───

@v1.get("/devices", response_model=PaginatedResponse, tags=["Devices"])
async def get_devices(
    device_type: Optional[str] = Query(None, description="Filter by device type: person, device, or item"),
    limit: int = Query(50, ge=1, le=500, description="Maximum number of devices"),
    offset: int = Query(0, ge=0, description="Number of records to skip"),
):
    """Get all tracked devices with their current status"""
    with get_connection() as conn:
        cursor = conn.cursor()

        where = ""
        params: list = []
        if device_type:
            where = "WHERE d.device_type = ?"
            params.append(device_type)

        # Total count
        cursor.execute(f"SELECT COUNT(*) FROM swift_devices d {where}", params)
        total = cursor.fetchone()[0]

        query = f"""
            SELECT
                d.device_name,
                d.device_type,
                d.first_seen,
                d.last_seen,
                d.last_location,
                d.update_count,
                ROUND((julianday('now') - julianday(d.last_seen)) * 24 * 60, 1) as minutes_since_update,
                COUNT(l.id) as location_count
            FROM swift_devices d
            LEFT JOIN swift_locations l ON d.device_name = l.device_name
            {where}
            GROUP BY d.device_name
            ORDER BY d.last_seen DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        cursor.execute(query, params)

        devices = [_parse_device_row(row) for row in cursor.fetchall()]

        return PaginatedResponse(
            items=[d.model_dump() for d in devices],
            total=total,
            limit=limit,
            offset=offset,
            has_more=(offset + limit) < total,
        )


@v1.get("/devices/counts", response_model=DeviceTypeCounts, tags=["Devices"])
async def get_device_counts():
    """Get count of devices by type"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT device_type, COUNT(*) as count
            FROM swift_devices
            WHERE device_type IS NOT NULL
            GROUP BY device_type
        """)
        counts = {'people': 0, 'devices': 0, 'items': 0}
        for row in cursor.fetchall():
            dt = row['device_type']
            c = row['count']
            if dt == 'person':
                counts['people'] = c
            elif dt == 'device':
                counts['devices'] = c
            elif dt == 'item':
                counts['items'] = c
        return DeviceTypeCounts(
            people=counts['people'],
            devices=counts['devices'],
            items=counts['items'],
            total=counts['people'] + counts['devices'] + counts['items'],
        )


@v1.get("/devices/{device_name}", response_model=Device, tags=["Devices"])
async def get_device(device_name: str = PathParam(..., description="Device name")):
    """Get specific device information"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                device_name,
                device_type,
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

        return _parse_device_row(row)


@v1.get("/devices/{device_name}/history", response_model=DeviceHistory, tags=["Devices"])
async def get_device_history(
    device_name: str = PathParam(..., description="Device name"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of locations"),
    offset: int = Query(0, ge=0, description="Number of records to skip"),
    start_date: Optional[datetime] = Query(None, description="Start date filter"),
    end_date: Optional[datetime] = Query(None, description="End date filter"),
):
    """Get device location history with optional date-range filtering"""
    device = await get_device(device_name)

    with get_connection() as conn:
        cursor = conn.cursor()

        where_clauses = ["l.device_name = ?"]
        params: list = [device_name]

        if start_date:
            where_clauses.append("l.timestamp >= ?")
            params.append(start_date.isoformat())
        if end_date:
            where_clauses.append("l.timestamp <= ?")
            params.append(end_date.isoformat())

        where = " AND ".join(where_clauses)

        # Total count for this filter
        cursor.execute(f"SELECT COUNT(*) FROM swift_locations l WHERE {where}", params)
        total = cursor.fetchone()[0]

        cursor.execute(f"""
            SELECT l.id, l.device_name, l.location, l.time_status, l.distance,
                   l.latitude, l.longitude, l.device_type, l.timestamp, l.extracted_at,
                   l.distance_from_home_km, l.battery_status,
                   gc.street, gc.house_number, gc.postal_code, gc.city, gc.country
            FROM swift_locations l
            LEFT JOIN geocoding_cache gc
              ON ROUND(l.latitude, 4) = ROUND(gc.latitude, 4)
              AND ROUND(l.longitude, 4) = ROUND(gc.longitude, 4)
            WHERE {where}
            ORDER BY l.timestamp DESC
            LIMIT ? OFFSET ?
        """, params + [limit, offset])

        locations = [_parse_location_row(row) for row in cursor.fetchall()]

        return DeviceHistory(device=device, locations=locations, total=total)


@v1.get("/devices/{device_name}/export", tags=["Devices"])
async def export_device(
    device_name: str = PathParam(..., description="Device name"),
    format: str = Query("json", description="Export format: csv, json, or gpx"),
    start_date: Optional[datetime] = Query(None, description="Start date filter"),
    end_date: Optional[datetime] = Query(None, description="End date filter"),
):
    """Export device location history in CSV, JSON, or GPX format"""
    with get_connection() as conn:
        cursor = conn.cursor()

        where_clauses = ["device_name = ?"]
        params: list = [device_name]

        if start_date:
            where_clauses.append("timestamp >= ?")
            params.append(start_date.isoformat())
        if end_date:
            where_clauses.append("timestamp <= ?")
            params.append(end_date.isoformat())

        where = " AND ".join(where_clauses)
        cursor.execute(f"""
            SELECT id, device_name, location, time_status, distance,
                   latitude, longitude, device_type, timestamp, extracted_at
            FROM swift_locations WHERE {where}
            ORDER BY timestamp DESC
        """, params)

        rows = [row_to_dict(row) for row in cursor.fetchall()]

    if not rows:
        raise HTTPException(status_code=404, detail=f"No location data for '{device_name}'")

    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', device_name)

    if format == "csv":
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={safe_name}_locations.csv"},
        )

    elif format == "gpx":
        gpx_content = _generate_gpx(device_name, rows)
        return StreamingResponse(
            iter([gpx_content]),
            media_type="application/gpx+xml",
            headers={"Content-Disposition": f"attachment; filename={safe_name}_track.gpx"},
        )

    else:  # json
        return rows


@v1.get("/devices/{device_name}/trips", response_model=PaginatedResponse, tags=["Devices"])
async def get_device_trips(
    device_name: str = PathParam(..., description="Device name"),
    limit: int = Query(50, ge=1, le=500, description="Maximum number of trips"),
    offset: int = Query(0, ge=0, description="Number of records to skip"),
    start_date: Optional[datetime] = Query(None, description="Start date filter"),
    end_date: Optional[datetime] = Query(None, description="End date filter"),
):
    """Get detected trips for a device"""
    with get_connection() as conn:
        cursor = conn.cursor()

        where_clauses = ["device_name = ?"]
        params: list = [device_name]

        if start_date:
            where_clauses.append("start_time >= ?")
            params.append(start_date.isoformat())
        if end_date:
            where_clauses.append("end_time <= ?")
            params.append(end_date.isoformat())

        where = " AND ".join(where_clauses)

        cursor.execute(f"SELECT COUNT(*) FROM trips WHERE {where}", params)
        total = cursor.fetchone()[0]

        cursor.execute(f"""
            SELECT * FROM trips WHERE {where}
            ORDER BY start_time DESC LIMIT ? OFFSET ?
        """, params + [limit, offset])

        trips = []
        for row in cursor.fetchall():
            d = row_to_dict(row)
            d['start_time'] = parse_datetime(d['start_time'])
            d['end_time'] = parse_datetime(d['end_time'])
            trips.append(Trip(**d).model_dump())

        return PaginatedResponse(
            items=trips, total=total, limit=limit, offset=offset,
            has_more=(offset + limit) < total,
        )


@v1.get("/devices/{device_name}/visits", response_model=PaginatedResponse, tags=["Devices"])
async def get_device_visits(
    device_name: str = PathParam(..., description="Device name"),
    limit: int = Query(50, ge=1, le=500, description="Maximum number of visits"),
    offset: int = Query(0, ge=0, description="Number of records to skip"),
    start_date: Optional[datetime] = Query(None, description="Start date filter"),
    end_date: Optional[datetime] = Query(None, description="End date filter"),
):
    """Get detected visits / dwell times for a device"""
    with get_connection() as conn:
        cursor = conn.cursor()

        where_clauses = ["device_name = ?"]
        params: list = [device_name]

        if start_date:
            where_clauses.append("arrival_time >= ?")
            params.append(start_date.isoformat())
        if end_date:
            where_clauses.append("(departure_time IS NULL OR departure_time <= ?)")
            params.append(end_date.isoformat())

        where = " AND ".join(where_clauses)

        cursor.execute(f"SELECT COUNT(*) FROM visits WHERE {where}", params)
        total = cursor.fetchone()[0]

        cursor.execute(f"""
            SELECT * FROM visits WHERE {where}
            ORDER BY arrival_time DESC LIMIT ? OFFSET ?
        """, params + [limit, offset])

        visits = []
        for row in cursor.fetchall():
            d = row_to_dict(row)
            d['arrival_time'] = parse_datetime(d['arrival_time'])
            if d.get('departure_time'):
                d['departure_time'] = parse_datetime(d['departure_time'])
            visits.append(Visit(**d).model_dump())

        return PaginatedResponse(
            items=visits, total=total, limit=limit, offset=offset,
            has_more=(offset + limit) < total,
        )


@v1.get("/devices/{device_name}/zone", response_model=ZoneCheck, tags=["Devices"])
async def check_device_zone(
    device_name: str = PathParam(..., description="Device name"),
):
    """Check which zone a device is currently in (if any)"""
    with get_connection() as conn:
        cursor = conn.cursor()

        # Get latest location with coordinates
        cursor.execute("""
            SELECT latitude, longitude FROM swift_locations
            WHERE device_name = ? AND latitude IS NOT NULL AND longitude IS NOT NULL
            ORDER BY timestamp DESC LIMIT 1
        """, (device_name,))
        loc = cursor.fetchone()
        if not loc:
            return ZoneCheck(device_name=device_name, in_zone=False)

        lat, lon = loc['latitude'], loc['longitude']

        # Check all zones
        cursor.execute("SELECT id, name, latitude, longitude, radius_meters FROM zones")
        for zone_row in cursor.fetchall():
            dist = haversine_distance(lat, lon, zone_row['latitude'], zone_row['longitude'])
            if dist <= zone_row['radius_meters']:
                return ZoneCheck(
                    device_name=device_name,
                    zone=Zone(**row_to_dict(zone_row)),
                    distance_meters=round(dist, 1),
                    in_zone=True,
                )

    return ZoneCheck(device_name=device_name, in_zone=False)


@v1.get("/devices/{device_name}/stats-summary", response_model=DeviceStatsSummary, tags=["Devices"])
async def get_device_stats_summary(
    device_name: str = PathParam(..., description="Device name"),
):
    """Get summary statistics for a device"""
    from enrichment import get_home_coordinates

    with get_connection() as conn:
        cursor = conn.cursor()

        # Query 1: Basic counts
        cursor.execute("""
            SELECT COUNT(*) as total_records,
                   MIN(timestamp) as first_seen, MAX(timestamp) as last_seen,
                   CAST(julianday(MAX(timestamp)) - julianday(MIN(timestamp)) + 1 AS INTEGER) as days_tracked,
                   COUNT(DISTINCT location) as unique_locations,
                   COUNT(CASE WHEN latitude IS NOT NULL AND longitude IS NOT NULL THEN 1 END) as records_with_coords,
                   COUNT(CASE WHEN location = 'Home' THEN 1 END) as home_record_count
            FROM swift_locations WHERE device_name = ?
        """, (device_name,))
        base = cursor.fetchone()

        total_records = base['total_records']
        if total_records == 0:
            return DeviceStatsSummary(
                device_name=device_name, total_records=0,
                first_seen=None, last_seen=None, days_tracked=0,
                avg_updates_per_day=0, unique_locations=0, top_locations=[],
                home_record_count=0, home_percentage=None,
                records_with_coords=0, total_distance_km=None,
                furthest_from_home_km=None, furthest_location=None,
            )

        first_seen = parse_datetime(base['first_seen'])
        last_seen = parse_datetime(base['last_seen'])
        days_tracked = max(base['days_tracked'], 1)
        unique_locations = base['unique_locations']
        records_with_coords = base['records_with_coords']
        home_record_count = base['home_record_count']
        home_percentage = round(home_record_count / total_records * 100, 1) if total_records > 0 else None

        # Query 2: Top 5 locations
        cursor.execute("""
            SELECT location as name, COUNT(*) as count
            FROM swift_locations
            WHERE device_name = ? AND location NOT IN ('No location found', 'Address Unavailable')
            GROUP BY location ORDER BY count DESC LIMIT 5
        """, (device_name,))
        top_rows = cursor.fetchall()
        top_locations = [
            {"name": r['name'], "count": r['count'], "percentage": round(r['count'] / total_records * 100, 1)}
            for r in top_rows
        ]

        # Query 3: Coordinates for distance calculation
        cursor.execute("""
            SELECT latitude, longitude, location
            FROM swift_locations
            WHERE device_name = ? AND latitude IS NOT NULL AND longitude IS NOT NULL
            ORDER BY timestamp ASC
        """, (device_name,))
        coord_rows = cursor.fetchall()

        total_distance_km = None
        furthest_from_home_km = None
        furthest_location = None

        if len(coord_rows) >= 2:
            dist_sum = 0.0
            for i in range(1, len(coord_rows)):
                dist_sum += haversine_km(
                    coord_rows[i - 1]['latitude'], coord_rows[i - 1]['longitude'],
                    coord_rows[i]['latitude'], coord_rows[i]['longitude'],
                )
            total_distance_km = round(dist_sum, 2)

        home = get_home_coordinates()
        if home and len(coord_rows) > 0:
            max_dist = 0.0
            max_loc = None
            for r in coord_rows:
                d = haversine_km(r['latitude'], r['longitude'], home[0], home[1])
                if d > max_dist:
                    max_dist = d
                    max_loc = r['location']
            if max_dist > 0.5:
                furthest_from_home_km = round(max_dist, 2)
                furthest_location = max_loc

        return DeviceStatsSummary(
            device_name=device_name,
            total_records=total_records,
            first_seen=first_seen,
            last_seen=last_seen,
            days_tracked=days_tracked,
            avg_updates_per_day=round(total_records / days_tracked, 2),
            unique_locations=unique_locations,
            top_locations=top_locations,
            home_record_count=home_record_count,
            home_percentage=home_percentage,
            records_with_coords=records_with_coords,
            total_distance_km=total_distance_km,
            furthest_from_home_km=furthest_from_home_km,
            furthest_location=furthest_location,
        )


# ─── Locations ───

@v1.get("/locations/latest", response_model=List[DeviceLocation], tags=["Locations"])
async def get_latest_locations():
    """Get the most recent location for each device"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT l.id, l.device_name, l.location, l.time_status, l.distance,
                   l.latitude, l.longitude, l.device_type, l.timestamp, l.extracted_at,
                   l.distance_from_home_km, l.battery_status,
                   gc.street, gc.house_number, gc.postal_code, gc.city, gc.country
            FROM swift_locations l
            INNER JOIN (
                SELECT device_name, MAX(timestamp) as max_timestamp
                FROM swift_locations GROUP BY device_name
            ) latest ON l.device_name = latest.device_name AND l.timestamp = latest.max_timestamp
            LEFT JOIN geocoding_cache gc
              ON ROUND(l.latitude, 4) = ROUND(gc.latitude, 4)
              AND ROUND(l.longitude, 4) = ROUND(gc.longitude, 4)
            ORDER BY l.device_name
        """)
        return [_parse_location_row(row) for row in cursor.fetchall()]


@v1.get("/locations/search", response_model=PaginatedResponse, tags=["Locations"])
async def search_locations(
    location: Optional[str] = Query(None, description="Location text to search for"),
    device_name: Optional[str] = Query(None, description="Filter by device name"),
    start_date: Optional[datetime] = Query(None, description="Start date filter"),
    end_date: Optional[datetime] = Query(None, description="End date filter"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum results"),
    offset: int = Query(0, ge=0, description="Number of records to skip"),
):
    """Search location records with filters"""
    with get_connection() as conn:
        cursor = conn.cursor()

        where_clauses = ["1=1"]
        params: list = []

        if location:
            where_clauses.append("location LIKE ?")
            params.append(f"%{location}%")
        if device_name:
            where_clauses.append("device_name = ?")
            params.append(device_name)
        if start_date:
            where_clauses.append("timestamp >= ?")
            params.append(start_date.isoformat())
        if end_date:
            where_clauses.append("timestamp <= ?")
            params.append(end_date.isoformat())

        where = " AND ".join(where_clauses)

        # Total count
        cursor.execute(f"SELECT COUNT(*) FROM swift_locations WHERE {where}", params)
        total = cursor.fetchone()[0]

        cursor.execute(f"""
            SELECT * FROM swift_locations WHERE {where}
            ORDER BY timestamp DESC LIMIT ? OFFSET ?
        """, params + [limit, offset])

        locations = [_parse_location_row(row) for row in cursor.fetchall()]

        return PaginatedResponse(
            items=[loc.model_dump() for loc in locations],
            total=total,
            limit=limit,
            offset=offset,
            has_more=(offset + limit) < total,
        )


@v1.delete("/locations/{location_id}", tags=["Locations"])
async def delete_location(location_id: int):
    """Delete a single location record"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM swift_locations WHERE id = ?", (location_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail=f"Location {location_id} not found")
        conn.commit()
        return {"success": True, "deleted_id": location_id}


# ─── Statistics ───

@v1.get("/stats/{device_name}", response_model=Statistics, tags=["Statistics"])
async def get_device_stats(
    device_name: str = PathParam(..., description="Device name"),
    period: str = Query("7d", pattern="^\\d+[dhw]$", description="Time period (e.g., 7d, 24h, 1w)"),
):
    """Get statistics for a device over a time period"""
    try:
        period_delta = parse_period(period)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    start_date = datetime.now() - period_delta

    with get_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT COUNT(*) as total_updates,
                   COUNT(DISTINCT location) as unique_locations,
                   MAX(timestamp) as last_movement
            FROM swift_locations
            WHERE device_name = ? AND timestamp >= ?
        """, (device_name, start_date.isoformat()))

        row = cursor.fetchone()
        if not row or row['total_updates'] == 0:
            raise HTTPException(status_code=404, detail=f"No data for device '{device_name}' in period '{period}'")

        total_updates = row['total_updates']
        unique_locations = row['unique_locations']
        last_movement = parse_datetime(row['last_movement']) if row['last_movement'] else None

        cursor.execute("""
            SELECT location, COUNT(*) as count
            FROM swift_locations
            WHERE device_name = ? AND timestamp >= ?
            GROUP BY location ORDER BY count DESC
        """, (device_name, start_date.isoformat()))

        location_frequencies = {r['location']: r['count'] for r in cursor.fetchall()}

        days_in_period = max(period_delta.days, 1)
        return Statistics(
            device_name=device_name,
            period=period,
            total_updates=total_updates,
            unique_locations=unique_locations,
            location_frequencies=location_frequencies,
            average_updates_per_day=round(total_updates / days_in_period, 2),
            last_movement=last_movement,
        )


# ─── Zones ───

@v1.get("/zones", response_model=List[Zone], tags=["Zones"])
async def get_zones():
    """List all geofencing zones"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, latitude, longitude, radius_meters FROM zones")
        return [Zone(**row_to_dict(row)) for row in cursor.fetchall()]


@v1.post("/zones", response_model=Zone, tags=["Zones"])
async def create_zone(zone: ZoneCreate):
    """Create a new geofencing zone"""
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO zones (name, latitude, longitude, radius_meters) VALUES (?, ?, ?, ?)",
                (zone.name, zone.latitude, zone.longitude, zone.radius_meters),
            )
            conn.commit()
            return Zone(id=cursor.lastrowid, **zone.model_dump())
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail=f"Zone '{zone.name}' already exists")


@v1.delete("/zones/{zone_id}", tags=["Zones"])
async def delete_zone(zone_id: int):
    """Delete a geofencing zone"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM zones WHERE id = ?", (zone_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail=f"Zone {zone_id} not found")
        conn.commit()
        return {"success": True, "deleted_id": zone_id}


# ─── Tracking ───

def _run_tracking():
    """Background tracking task."""
    from swift_tracker import SwiftAirTagTracker
    tracker = SwiftAirTagTracker()
    tracker.track_once()


@v1.post("/track", tags=["Actions"])
async def trigger_tracking(background_tasks: BackgroundTasks):
    """Trigger a new tracking cycle in the background"""
    background_tasks.add_task(_run_tracking)
    return {
        "success": True,
        "message": "Tracking cycle started in background",
        "timestamp": datetime.now(),
    }


# ═══════════════════════════════════════════════════════════════
# Mount v1 router and add backward-compatible redirects
# ═══════════════════════════════════════════════════════════════

app.include_router(v1)


# ─── Legacy redirect routes (old endpoints → /api/v1/) ───

from fastapi.responses import RedirectResponse


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
            <h1>AirTag Swift Tracker API v3</h1>
            <p>REST API for accessing AirTag location data collected via Swift accessibility APIs.</p>

            <h2>Documentation</h2>
            <ul>
                <li><a href="/docs">Swagger UI Documentation</a></li>
                <li><a href="/redoc">ReDoc Documentation</a></li>
            </ul>

            <h2>API v1 Endpoints</h2>
            <div class="endpoint">
                <strong>GET</strong> <code>/api/v1/health</code> - Check API health status
            </div>
            <div class="endpoint">
                <strong>GET</strong> <code>/api/v1/devices</code> - List all tracked devices
            </div>
            <div class="endpoint">
                <strong>GET</strong> <code>/api/v1/devices/{device_name}/history</code> - Get device location history
            </div>
            <div class="endpoint">
                <strong>GET</strong> <code>/api/v1/devices/{device_name}/export</code> - Export location data (CSV/JSON/GPX)
            </div>
            <div class="endpoint">
                <strong>GET</strong> <code>/api/v1/locations/latest</code> - Get latest locations for all devices
            </div>
            <div class="endpoint">
                <strong>GET/POST/DELETE</strong> <code>/api/v1/zones</code> - Manage geofencing zones
            </div>
        </body>
    </html>
    """


# Backward compat: redirect old root-level endpoints to /api/v1
@app.get("/health", include_in_schema=False)
async def legacy_health():
    return RedirectResponse(url="/api/v1/health", status_code=307)

@app.get("/devices", include_in_schema=False)
async def legacy_devices():
    return RedirectResponse(url="/api/v1/devices", status_code=307)

@app.get("/devices/counts", include_in_schema=False)
async def legacy_device_counts():
    return RedirectResponse(url="/api/v1/devices/counts", status_code=307)

@app.get("/devices/{device_name}", include_in_schema=False)
async def legacy_device(device_name: str):
    return RedirectResponse(url=f"/api/v1/devices/{device_name}", status_code=307)

@app.get("/devices/{device_name}/history", include_in_schema=False)
async def legacy_device_history(device_name: str):
    return RedirectResponse(url=f"/api/v1/devices/{device_name}/history", status_code=307)

@app.get("/locations/latest", include_in_schema=False)
async def legacy_latest():
    return RedirectResponse(url="/api/v1/locations/latest", status_code=307)

@app.get("/locations/search", include_in_schema=False)
async def legacy_search():
    return RedirectResponse(url="/api/v1/locations/search", status_code=307)

@app.get("/stats/{device_name}", include_in_schema=False)
async def legacy_stats(device_name: str):
    return RedirectResponse(url=f"/api/v1/stats/{device_name}", status_code=307)

@app.post("/track", include_in_schema=False)
async def legacy_track(background_tasks: BackgroundTasks):
    return RedirectResponse(url="/api/v1/track", status_code=307)

@app.delete("/locations/{location_id}", include_in_schema=False)
async def legacy_delete_location(location_id: int):
    return RedirectResponse(url=f"/api/v1/locations/{location_id}", status_code=307)

@app.delete("/devices/{device_name}", include_in_schema=False)
async def legacy_delete_device(device_name: str):
    return RedirectResponse(url=f"/api/v1/devices/{device_name}", status_code=307)


# Run the server
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8001,
        log_level="info",
        access_log=True,
    )
