#!/usr/bin/env python3

import os
import subprocess
from pathlib import Path

def view_latest_regions():
    """Open the latest extracted regions for visual inspection"""
    
    temp_regions_dir = Path("temp_regions")
    
    if not temp_regions_dir.exists():
        print("No temp_regions directory found. Run a capture first.")
        return
    
    # Get the latest folder (most recent timestamp)
    region_folders = [d for d in temp_regions_dir.iterdir() if d.is_dir()]
    
    if not region_folders:
        print("No region folders found. Run a capture first.")
        return
    
    latest_folder = max(region_folders, key=lambda x: x.name)
    
    print(f"Opening regions from: {latest_folder}")
    print(f"Region coordinates used:")
    print(f"  Start: (120, 220)")
    print(f"  Size: 460x120 pixels each")
    print(f"  Vertical spacing: 150 pixels (120px height + 30px gap)")
    
    # List all PNG files in the folder
    png_files = sorted(latest_folder.glob("*.png"))
    
    if png_files:
        print(f"\nFound {len(png_files)} extracted regions:")
        for i, png_file in enumerate(png_files, 1):
            y_coord = 220 + ((i-1) * 150)
            print(f"  {png_file.name} - Region {i} at y={y_coord}")
        
        # Open the folder in Finder for visual inspection
        subprocess.run(["open", str(latest_folder)])
        print(f"\nOpened {latest_folder} in Finder for inspection")
        print("Check if the regions contain the expected AirTag location data")
        print("If regions need adjustment, update coordinates in airtracker.py")
    else:
        print("No PNG files found in the latest folder")

if __name__ == "__main__":
    view_latest_regions()