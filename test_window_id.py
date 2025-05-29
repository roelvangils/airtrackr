#!/usr/bin/env python3

import subprocess
import time

def test_window_id_methods():
    """Test different methods to get Find My window ID"""
    
    print("Testing window ID retrieval methods for Find My...")
    
    # Ensure Find My is running
    subprocess.run(["open", "-a", "FindMy"], capture_output=True)
    time.sleep(2)
    
    # Method 1: Using Quartz window list
    print("\n1. Using Quartz window list (via Python):")
    try:
        # This would require pyobjc-framework-Quartz
        print("   (Requires additional Python packages)")
    except:
        pass
    
    # Method 2: Using window_list command line tool
    print("\n2. Checking for window_list tool:")
    result = subprocess.run(["which", "window_list"], capture_output=True, text=True)
    if result.returncode == 0:
        print(f"   Found at: {result.stdout.strip()}")
        # Get window list
        result = subprocess.run(["window_list"], capture_output=True, text=True)
        if result.returncode == 0:
            lines = result.stdout.split('\n')
            for line in lines:
                if 'FindMy' in line or 'Find My' in line:
                    print(f"   {line}")
    else:
        print("   window_list tool not found")
    
    # Method 3: Using AppleScript to get window ID
    print("\n3. Using AppleScript to get window properties:")
    script = '''
    tell application "System Events"
        tell process "FindMy"
            if (count of windows) > 0 then
                set w to window 1
                return "Window: " & (name of w) & ", Position: " & (position of w) & ", Size: " & (size of w)
            else
                return "no windows"
            end if
        end tell
    end tell
    '''
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    print(f"   {result.stdout.strip()}")
    
    # Method 4: Try to get actual window ID
    print("\n4. Attempting to get window ID via AppleScript:")
    script = '''
    tell application "System Events"
        tell process "FindMy"
            if (count of windows) > 0 then
                set w to window 1
                return id of w
            else
                return "no windows"
            end if
        end tell
    end tell
    '''
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    print(f"   Result: {result.stdout.strip()}")
    if result.stderr:
        print(f"   Error: {result.stderr.strip()}")

if __name__ == "__main__":
    test_window_id_methods()