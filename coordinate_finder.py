#!/usr/bin/env python3

import subprocess
import time
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import os

def create_coordinate_helper():
    """Create a tool to help find exact coordinates"""
    
    print("Coordinate Finder Tool")
    print("======================")
    print("This will help you find the exact coordinates for AirTag regions.")
    print()
    
    # Take a fresh screenshot
    print("1. Taking a screenshot of Find My...")
    
    # Ensure Find My is running and active
    subprocess.run(["open", "-a", "FindMy"], capture_output=True)
    time.sleep(2)
    
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
            test_dir = Path("coordinate_test")
            test_dir.mkdir(exist_ok=True)
            
            filepath = test_dir / "findmy_coordinate_test.png"
            
            subprocess.run([
                "screencapture",
                "-R", f"{x},{y},{width},{height}",
                "-o",
                "-x",
                str(filepath)
            ], check=True)
            
            print(f"✓ Screenshot saved: {filepath}")
            print(f"  Window size: {width} x {height} pixels")
            
            # Create a grid overlay to help with measurements
            create_measurement_grid(filepath, width, height)
            
            # Open both images
            subprocess.run(["open", str(filepath)])
            subprocess.run(["open", str(test_dir / "findmy_with_grid.png")])
            
            print()
            print("2. Two images have been opened:")
            print("   - Original Find My screenshot")
            print("   - Same screenshot with measurement grid overlay")
            print()
            print("3. Instructions:")
            print("   - Look at the grid overlay to see pixel coordinates")
            print("   - Find the TOP-LEFT corner of the first AirTag item")
            print("   - Note the x,y coordinates from the grid")
            print("   - Measure the width and height of one AirTag item")
            print()
            print("4. When you have the coordinates, run:")
            print("   python coordinate_finder.py --test x y width height")
            print("   (replace x y width height with your measured values)")
            
        else:
            print("✗ Could not get window bounds")
    else:
        print("✗ Could not find Find My window")

def create_measurement_grid(image_path, width, height):
    """Create a grid overlay to help with coordinate measurement"""
    
    img = Image.open(image_path)
    actual_width, actual_height = img.size
    
    print(f"  Window bounds: {width} x {height}")
    print(f"  Actual image: {actual_width} x {actual_height}")
    
    # Create a new image with semi-transparent overlay
    overlay = Image.new('RGBA', (actual_width, actual_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    
    # Try to use a system font
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Monaco.ttc", 30)
    except:
        font = ImageFont.load_default()
    
    # Draw grid lines every 100 pixels
    grid_spacing = 100
    line_color = (255, 0, 0, 200)  # Red with alpha
    text_color = (255, 255, 255, 255)  # White text
    
    print(f"  Drawing grid from 0 to {actual_width} x {actual_height}")
    
    # Vertical lines
    x = 0
    while x <= actual_width:
        draw.line([(x, 0), (x, actual_height)], fill=line_color, width=3)
        if x > 0:
            # Draw text with background for better visibility
            text = str(x)
            bbox = draw.textbbox((0, 0), text, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
            draw.rectangle([(x + 5, 5), (x + 5 + text_width + 4, 5 + text_height + 4)], 
                         fill=(0, 0, 0, 180))
            draw.text((x + 7, 7), text, fill=text_color, font=font)
        x += grid_spacing
    
    # Horizontal lines
    y = 0
    while y <= actual_height:
        draw.line([(0, y), (actual_width, y)], fill=line_color, width=3)
        if y > 0:
            # Draw text with background
            text = str(y)
            bbox = draw.textbbox((0, 0), text, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
            draw.rectangle([(5, y + 5), (5 + text_width + 4, y + 5 + text_height + 4)], 
                         fill=(0, 0, 0, 180))
            draw.text((7, y + 7), text, fill=text_color, font=font)
        y += grid_spacing
    
    # Draw finer grid every 50 pixels
    fine_spacing = 50
    fine_color = (255, 192, 203, 100)  # Light pink with alpha
    
    x = 0
    while x <= actual_width:
        if x % grid_spacing != 0:
            draw.line([(x, 0), (x, actual_height)], fill=fine_color, width=1)
        x += fine_spacing
    
    y = 0
    while y <= actual_height:
        if y % grid_spacing != 0:
            draw.line([(0, y), (actual_width, y)], fill=fine_color, width=1)
        y += fine_spacing
    
    # Composite the overlay onto the original image
    grid_img = Image.alpha_composite(img.convert('RGBA'), overlay)
    
    # Save grid version
    grid_path = image_path.parent / "findmy_with_grid.png"
    grid_img.save(grid_path)
    print(f"✓ Grid overlay saved: {grid_path}")
    print(f"  Grid covers full image: {actual_width} x {actual_height}")

def test_coordinates(x, y, width, height):
    """Test specific coordinates by extracting a single region"""
    
    print(f"Testing coordinates: x={x}, y={y}, width={width}, height={height}")
    
    # Find the latest screenshot
    test_dir = Path("coordinate_test")
    screenshot_path = test_dir / "findmy_coordinate_test.png"
    
    if not screenshot_path.exists():
        print("No test screenshot found. Run without --test flag first.")
        return
    
    img = Image.open(screenshot_path)
    
    # Extract the test region
    region = img.crop((x, y, x + width, y + height))
    
    # Save test region
    test_region_path = test_dir / f"test_region_{x}_{y}_{width}_{height}.png"
    region.save(test_region_path)
    
    print(f"✓ Test region saved: {test_region_path}")
    
    # Open the test region
    subprocess.run(["open", str(test_region_path)])
    
    print()
    print("Check if this region contains exactly one AirTag item.")
    print("If it looks good, update the coordinates in airtracker.py")
    print("If not, try again with different coordinates.")

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) == 6 and sys.argv[1] == "--test":
        # Test mode: python coordinate_finder.py --test x y width height
        try:
            x = int(sys.argv[2])
            y = int(sys.argv[3])
            width = int(sys.argv[4])
            height = int(sys.argv[5])
            test_coordinates(x, y, width, height)
        except ValueError:
            print("Error: Please provide valid integers for x y width height")
    else:
        # Measurement mode
        create_coordinate_helper()