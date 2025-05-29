#!/usr/bin/env python3

from airtracker import AirTracker
import time

def test_location_cleaning():
    """Test location text cleaning and geocoding"""
    
    tracker = AirTracker()
    
    # Test the specific locations from our database
    test_locations = [
        "FrangoisLaurentplein,Ghent-",  # From our actual data
        "Home",  # Should be skipped
        "Kouter,Ghent",  # Should work
        "Gent",  # Should be corrected to Ghent
    ]
    
    print("Testing location cleaning and geocoding:")
    print("=" * 60)
    
    for location in test_locations:
        print(f"Original: '{location}'")
        cleaned = tracker.clean_location_text(location)
        print(f"Cleaned:  '{cleaned}'")
        
        if location.lower() == 'home':
            print(f"  → Skipped (home location)")
        else:
            lat, lon = tracker.geocode_location(location)
            
            if lat and lon:
                print(f"  ✓ Geocoded: ({lat:.6f}, {lon:.6f})")
            else:
                print(f"  ✗ Failed to geocode")
        
        print()
        time.sleep(1.1)  # Rate limiting

if __name__ == "__main__":
    test_location_cleaning()