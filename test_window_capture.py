#!/usr/bin/env python3

import subprocess
import time
from pathlib import Path

def test_window_capture():
    """Test different methods to capture Find My window"""
    
    print("Testing Find My window capture methods...")
    
    # Ensure Find My is open
    print("Opening Find My app...")
    subprocess.run(["open", "-a", "FindMy"])
    time.sleep(3)
    
    test_dir = Path("test_screenshots")
    test_dir.mkdir(exist_ok=True)
    
    # Method 1: Interactive window selection
    print("\n1. Testing interactive window selection...")
    print("   Click on the Find My window when the cursor changes to a camera")
    
    filepath1 = test_dir / "findmy_interactive.png"
    try:
        subprocess.run([
            "screencapture",
            "-i",  # Interactive mode
            "-W",  # Start in window selection mode
            "-o",  # No shadow
            str(filepath1)
        ], check=True)
        print(f"   Saved to: {filepath1}")
    except Exception as e:
        print(f"   Error: {e}")
    
    # Method 2: Using window title with AppleScript
    print("\n2. Testing AppleScript window capture...")
    
    # First, let's get the window bounds
    script = '''
    tell application "System Events"
        tell process "FindMy"
            set frontmost to true
            delay 0.5
            if (count of windows) > 0 then
                set w to window 1
                return (position of w) & (size of w)
            else
                return "no windows"
            end if
        end tell
    end tell
    '''
    
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    print(f"   Window info: {result.stdout.strip()}")
    
    if result.returncode == 0 and "no windows" not in result.stdout:
        # Parse the bounds
        coords = result.stdout.strip().split(", ")
        if len(coords) == 4:
            x, y, width, height = map(int, coords)
            
            # Take screenshot of that region
            filepath2 = test_dir / "findmy_region.png"
            subprocess.run([
                "screencapture",
                "-R", f"{x},{y},{width},{height}",
                "-o",
                str(filepath2)
            ], check=True)
            print(f"   Saved to: {filepath2}")
    
    print("\nTest complete! Check the test_screenshots folder.")

if __name__ == "__main__":
    test_window_capture()