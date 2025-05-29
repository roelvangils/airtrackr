#!/usr/bin/env python3

from airtracker import AirTracker
import logging

# Set up logging to see output
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

if __name__ == "__main__":
    print("Testing single capture cycle...")
    tracker = AirTracker()
    
    # Run one capture
    tracker.run_capture()
    
    print("\nCheck the screenshots/ directory for the captured image.")
    print("Check database/airtracker.db for the database record.")