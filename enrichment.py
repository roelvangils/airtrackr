#!/usr/bin/env python3
"""
Data enrichment module for AirTrackr.

Provides:
- haversine_km() — distance between two lat/lon points
- get_home_coordinates() — resolve "Home" alias → geocode → (lat, lon)
- compute_distance_from_home(lat, lon) — distance from home in km
- detect_trips(device_name, since_minutes) — trip detection from consecutive locations
- update_visits(device_name, location, lat, lon, timestamp) — visit/dwell time tracking
"""

import math
import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple

from db import get_connection, resolve_location_alias
from geocoding import Geocoder

logger = logging.getLogger(__name__)

# Singleton geocoder — avoids re-reading config on every call
_geocoder: Optional[Geocoder] = None


def _get_geocoder() -> Geocoder:
    global _geocoder
    if _geocoder is None:
        _geocoder = Geocoder()
    return _geocoder


# ─── Haversine ───

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the great-circle distance between two points in kilometers.

    Uses the haversine formula. Earth radius = 6371 km.
    """
    R = 6371.0  # km
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ─── Distance from Home ───

_home_coords: Optional[Tuple[float, float]] = None


def get_home_coordinates() -> Optional[Tuple[float, float]]:
    """
    Get the coordinates for "Home" by resolving the location alias and geocoding it.

    Caches the result for the lifetime of the process.
    """
    global _home_coords
    if _home_coords is not None:
        return _home_coords

    address = resolve_location_alias("Home")
    if address == "Home":
        # No alias configured
        logger.warning("No 'Home' alias configured in location_aliases table")
        return None

    geocoder = _get_geocoder()
    lat, lon = geocoder.geocode(address)
    if lat is not None and lon is not None:
        _home_coords = (lat, lon)
        logger.info(f"Home coordinates resolved: ({lat:.6f}, {lon:.6f})")
        return _home_coords

    # Fallback: check geocoding_cache directly (ignoring expiry) — useful when offline
    with get_connection() as conn:
        cleaned = geocoder.clean_location_text(address)
        row = conn.execute(
            'SELECT latitude, longitude FROM geocoding_cache WHERE location_text = ? AND latitude IS NOT NULL',
            (cleaned,)
        ).fetchone()
        if row:
            _home_coords = (row[0], row[1])
            logger.info(f"Home coordinates from cache fallback: ({row[0]:.6f}, {row[1]:.6f})")
            return _home_coords

    logger.warning(f"Could not geocode Home address: {address}")
    return None


def compute_distance_from_home(lat: float, lon: float) -> Optional[float]:
    """
    Compute the distance in km from the given point to Home.

    Returns None if Home coordinates are not available.
    """
    home = get_home_coordinates()
    if home is None:
        return None
    return round(haversine_km(lat, lon, home[0], home[1]), 3)


# ─── Trip Detection ───

# Minimum distance (km) between consecutive points to count as movement
TRIP_THRESHOLD_KM = 0.1  # 100 meters


def detect_trips(device_name: str, since_minutes: int = 10) -> int:
    """
    Detect trips for a device by comparing consecutive location records.

    A trip is created when consecutive records are > 100m apart.
    Only processes records from the last `since_minutes` that haven't
    already been covered by existing trips.

    Args:
        device_name: The device to detect trips for
        since_minutes: How far back to look for new records

    Returns:
        Number of trips detected
    """
    cutoff = (datetime.now() - timedelta(minutes=since_minutes)).isoformat()

    with get_connection() as conn:
        cursor = conn.cursor()

        # Get the latest trip end_time for this device to avoid duplicates
        cursor.execute('''
            SELECT MAX(end_time) FROM trips WHERE device_name = ?
        ''', (device_name,))
        row = cursor.fetchone()
        last_trip_end = row[0] if row and row[0] else cutoff

        # Use the later of cutoff and last_trip_end
        effective_cutoff = max(cutoff, last_trip_end)

        # Get location records with coordinates since the effective cutoff
        cursor.execute('''
            SELECT id, location, latitude, longitude, timestamp
            FROM swift_locations
            WHERE device_name = ?
              AND latitude IS NOT NULL AND longitude IS NOT NULL
              AND timestamp > ?
            ORDER BY timestamp ASC
        ''', (device_name, effective_cutoff))

        rows = cursor.fetchall()
        if len(rows) < 2:
            return 0

        trips_detected = 0

        for i in range(1, len(rows)):
            prev = rows[i - 1]
            curr = rows[i]

            dist = haversine_km(
                prev['latitude'], prev['longitude'],
                curr['latitude'], curr['longitude'],
            )

            if dist >= TRIP_THRESHOLD_KM:
                # Calculate duration in minutes
                try:
                    t_start = datetime.fromisoformat(prev['timestamp'])
                    t_end = datetime.fromisoformat(curr['timestamp'])
                    duration = (t_end - t_start).total_seconds() / 60.0
                except (ValueError, TypeError):
                    duration = None

                cursor.execute('''
                    INSERT INTO trips
                    (device_name, start_time, end_time, start_location, end_location,
                     start_lat, start_lon, end_lat, end_lon, distance_km, duration_minutes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    device_name,
                    prev['timestamp'],
                    curr['timestamp'],
                    prev['location'],
                    curr['location'],
                    prev['latitude'],
                    prev['longitude'],
                    curr['latitude'],
                    curr['longitude'],
                    round(dist, 3),
                    round(duration, 1) if duration is not None else None,
                ))
                trips_detected += 1

        if trips_detected:
            conn.commit()
            logger.info(f"Detected {trips_detected} trip(s) for {device_name}")

        return trips_detected


# ─── Visit / Dwell Time Detection ───

# Max distance to consider "same location"
VISIT_RADIUS_KM = 0.1  # 100 meters


def update_visits(device_name: str, location: str,
                  lat: Optional[float], lon: Optional[float],
                  timestamp: str) -> None:
    """
    Update visit tracking for a device.

    If the device is still at the same location (< 100m from current open visit),
    extend the current visit. If it has moved, close the old visit and open a new one.

    Args:
        device_name: Device name
        location: Location text
        lat: Latitude (may be None)
        lon: Longitude (may be None)
        timestamp: ISO timestamp string
    """
    with get_connection() as conn:
        cursor = conn.cursor()

        # Find the most recent open visit (departure_time IS NULL)
        cursor.execute('''
            SELECT id, location, latitude, longitude, arrival_time
            FROM visits
            WHERE device_name = ? AND departure_time IS NULL
            ORDER BY arrival_time DESC LIMIT 1
        ''', (device_name,))
        open_visit = cursor.fetchone()

        if open_visit:
            # Check if we're still at the same location
            same_location = False

            if lat is not None and lon is not None and open_visit['latitude'] and open_visit['longitude']:
                dist = haversine_km(lat, lon, open_visit['latitude'], open_visit['longitude'])
                same_location = dist < VISIT_RADIUS_KM
            elif location == open_visit['location']:
                # Fall back to text comparison if no coordinates
                same_location = True

            if same_location:
                # Update departure time and duration
                try:
                    arrival = datetime.fromisoformat(open_visit['arrival_time'])
                    departure = datetime.fromisoformat(timestamp)
                    duration = (departure - arrival).total_seconds() / 60.0
                except (ValueError, TypeError):
                    duration = None

                cursor.execute('''
                    UPDATE visits
                    SET departure_time = ?, duration_minutes = ?
                    WHERE id = ?
                ''', (timestamp, round(duration, 1) if duration is not None else None, open_visit['id']))
            else:
                # Close the old visit
                try:
                    arrival = datetime.fromisoformat(open_visit['arrival_time'])
                    departure = datetime.fromisoformat(timestamp)
                    duration = (departure - arrival).total_seconds() / 60.0
                except (ValueError, TypeError):
                    duration = None

                cursor.execute('''
                    UPDATE visits
                    SET departure_time = ?, duration_minutes = ?
                    WHERE id = ?
                ''', (timestamp, round(duration, 1) if duration is not None else None, open_visit['id']))

                # Open a new visit
                cursor.execute('''
                    INSERT INTO visits (device_name, location, latitude, longitude, arrival_time)
                    VALUES (?, ?, ?, ?, ?)
                ''', (device_name, location, lat, lon, timestamp))
        else:
            # No open visit — start a new one
            cursor.execute('''
                INSERT INTO visits (device_name, location, latitude, longitude, arrival_time)
                VALUES (?, ?, ?, ?, ?)
            ''', (device_name, location, lat, lon, timestamp))

        conn.commit()
