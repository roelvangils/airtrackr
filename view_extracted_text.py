#!/usr/bin/env python3

import sqlite3
from pathlib import Path

def view_extracted_text():
    """View all extracted text from the database"""
    
    db_path = Path("database") / "airtracker.db"
    
    if not db_path.exists():
        print("No database found. Run a capture first.")
        return
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Get latest screenshot data
    cursor.execute('''
        SELECT s.filename, s.timestamp, COUNT(e.id) as region_count
        FROM screenshots s
        LEFT JOIN extracted_text e ON s.id = e.screenshot_id
        GROUP BY s.id
        ORDER BY s.timestamp DESC
        LIMIT 1
    ''')
    
    latest = cursor.fetchone()
    
    if latest:
        filename, timestamp, region_count = latest
        print(f"Latest Screenshot: {filename}")
        print(f"Timestamp: {timestamp}")
        print(f"Regions with text: {region_count}")
        print("-" * 60)
        
        # Get all extracted text for latest screenshot
        cursor.execute('''
            SELECT e.region_index, e.raw_text, e.extracted_at
            FROM extracted_text e
            JOIN screenshots s ON e.screenshot_id = s.id
            WHERE s.filename = ?
            ORDER BY e.region_index
        ''', (filename,))
        
        regions = cursor.fetchall()
        
        for region_index, text, extracted_at in regions:
            print(f"Region {region_index:2d}: {text}")
            
        print("-" * 60)
        print(f"Total regions extracted: {len(regions)}")
    else:
        print("No data found in database.")
    
    conn.close()

if __name__ == "__main__":
    view_extracted_text()