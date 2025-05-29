#!/usr/bin/env python3

import subprocess
import time
from pathlib import Path

def test_single_screenshot():
    """Test taking a single screenshot of Find My app"""
    
    print("Testing screenshot functionality...")
    
    # Ensure Find My is open
    print("Opening Find My app...")
    subprocess.run(["open", "-a", "FindMy"])
    time.sleep(3)
    
    # Take test screenshot
    test_dir = Path("test_screenshots")
    test_dir.mkdir(exist_ok=True)
    
    filepath = test_dir / "test_findmy.png"
    
    try:
        # Alternative approach: use window title
        result = subprocess.run([
            "screencapture",
            "-o",  # No shadow
            "-x",  # No sound
            str(filepath)
        ], check=True)
        
        print(f"Screenshot saved to: {filepath}")
        print("Test successful!")
        print("\nNote: This captured the entire screen. For windowed capture,")
        print("we'll need to use a different approach or ensure FindMy window is active.")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_single_screenshot()