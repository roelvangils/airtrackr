#!/usr/bin/env python3

from airtracker import AirTracker
import time

def test_individual_locations():
    """Test geocoding specific location strings"""
    
    tracker = AirTracker()
    
    # Test locations from our data
    test_locations = [
        "Francois Laurentplein, Ghent, Belgium",
        "FrangoisLaurentplein, Ghent",
        "François Laurentplein, Gent",
        "Kouter, Ghent, Belgium",
        "Kouter, Gent",
        "Ghent, Belgium"
    ]
    
    print("Testing individual location geocoding:")
    print("=" * 50)
    
    for location in test_locations:
        print(f"Testing: '{location}'")
        lat, lon = tracker.geocode_location(location)
        
        if lat and lon:
            print(f"  ✓ Result: ({lat:.6f}, {lon:.6f})")
        else:
            print(f"  ✗ Failed")
        
        time.sleep(1.1)  # Rate limiting
        print()

if __name__ == "__main__":
    test_individual_locations()