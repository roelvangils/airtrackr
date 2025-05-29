#!/usr/bin/env python3

import subprocess
import time
from pathlib import Path
from datetime import datetime

def test_automated_capture():
    """Test automated screenshot capture without user interaction"""
    
    print("Testing automated Find My screenshot capture...")
    print("This should work without any user interaction.")
    
    # Ensure Find My is running
    subprocess.run(["open", "-a", "FindMy"], capture_output=True)
    time.sleep(2)
    
    test_dir = Path("test_screenshots")
    test_dir.mkdir(exist_ok=True)
    
    # Activate and get bounds
    activate_script = '''
    tell application "FindMy"
        activate
    end tell
    delay 0.5
    '''
    subprocess.run(["osascript", "-e", activate_script], capture_output=True)
    
    bounds_script = '''
    tell application "System Events"
        tell process "FindMy"
            if (count of windows) > 0 then
                set w to window 1
                return (position of w) & (size of w)
            else
                return "no windows"
            end if
        end tell
    end tell
    '''
    
    result = subprocess.run(["osascript", "-e", bounds_script], capture_output=True, text=True)
    
    if result.returncode == 0 and "no windows" not in result.stdout:
        coords = result.stdout.strip().split(", ")
        if len(coords) == 4:
            x, y, width, height = map(int, coords)
            
            # Take screenshot
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filepath = test_dir / f"automated_test_{timestamp}.png"
            
            subprocess.run([
                "screencapture",
                "-R", f"{x},{y},{width},{height}",
                "-o",
                "-x",
                str(filepath)
            ], check=True)
            
            print(f"✓ Screenshot captured successfully: {filepath}")
            print(f"  Window bounds: x={x}, y={y}, width={width}, height={height}")
        else:
            print("✗ Could not parse window bounds")
    else:
        print("✗ Could not find Find My window")

if __name__ == "__main__":
    test_automated_capture()