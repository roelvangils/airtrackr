#!/usr/bin/env python3

import requests
import json
from datetime import datetime

# Base URL for the API
BASE_URL = "http://localhost:8000"

def test_health():
    """Test health endpoint"""
    print("Testing health endpoint...")
    response = requests.get(f"{BASE_URL}/health")
    print(f"Status: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2)}")
    print()

def test_devices():
    """Test devices endpoint"""
    print("Testing devices endpoint...")
    response = requests.get(f"{BASE_URL}/devices")
    print(f"Status: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        print(f"Found {len(data)} devices")
        
        if data and len(data) > 0:
            print("\nFirst device:")
            print(json.dumps(data[0], indent=2))
        else:
            print("No devices found in database")
    else:
        print(f"Error: {response.text}")
    print()

def test_device_locations(device_id=1):
    """Test device locations endpoint"""
    print(f"Testing locations for device {device_id}...")
    response = requests.get(f"{BASE_URL}/devices/{device_id}/locations?limit=5")
    
    if response.status_code == 200:
        data = response.json()
        print(f"Found {data['total_count']} total locations")
        print(f"Showing {len(data['locations'])} recent locations")
        
        if data['locations']:
            print("\nMost recent location:")
            print(json.dumps(data['locations'][0], indent=2))
    else:
        print(f"Error: {response.status_code}")
    print()

def test_search():
    """Test location search"""
    print("Testing location search...")
    response = requests.get(f"{BASE_URL}/locations/search?q=Home")
    print(f"Status: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        print(f"Found {len(data)} locations matching 'Home'")
    print()

def test_stats(device_id=1):
    """Test device statistics"""
    print(f"Testing statistics for device {device_id}...")
    response = requests.get(f"{BASE_URL}/stats/devices/{device_id}?period=24h")
    
    if response.status_code == 200:
        print(json.dumps(response.json(), indent=2))
    else:
        print(f"Error: {response.status_code}")
    print()

def main():
    print("AirTag Tracker API Test Suite")
    print("=" * 50)
    
    # Check if API is running
    try:
        response = requests.get(BASE_URL)
        print("✅ API is running!")
        print()
    except requests.ConnectionError:
        print("❌ API is not running. Start it with: python api.py")
        return
    
    # Run tests
    test_health()
    test_devices()
    test_device_locations()
    test_search()
    test_stats()
    
    print("\n📚 Interactive API documentation available at:")
    print("   http://localhost:8000/docs (Swagger UI)")
    print("   http://localhost:8000/redoc (ReDoc)")

if __name__ == "__main__":
    main()