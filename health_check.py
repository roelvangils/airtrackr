#!/usr/bin/env python3

import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
import subprocess
import psutil
import json

class HealthChecker:
    """Monitor AirTracker system health"""
    
    def __init__(self):
        self.db_path = Path("database") / "airtracker.db"
        self.screenshots_dir = Path("screenshots")
        self.logs_dir = Path("logs")
        
        self.checks = {
            'database': False,
            'disk_space': False,
            'findmy_app': False,
            'recent_capture': False,
            'ocr_success': False,
            'geocoding': False
        }
        
        self.warnings = []
        self.errors = []
    
    def check_database(self):
        """Check database health"""
        try:
            if not self.db_path.exists():
                self.errors.append("Database file not found")
                return False
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Check integrity
            cursor.execute("PRAGMA integrity_check")
            result = cursor.fetchone()[0]
            
            if result != "ok":
                self.errors.append(f"Database integrity check failed: {result}")
                return False
            
            # Check if tables exist
            cursor.execute("""
                SELECT COUNT(*) FROM sqlite_master 
                WHERE type='table' AND name IN ('screenshots', 'devices', 'device_locations')
            """)
            
            table_count = cursor.fetchone()[0]
            if table_count < 3:
                self.errors.append("Missing required database tables")
                return False
            
            conn.close()
            self.checks['database'] = True
            return True
            
        except Exception as e:
            self.errors.append(f"Database error: {e}")
            return False
    
    def check_disk_space(self):
        """Check available disk space"""
        try:
            disk_usage = psutil.disk_usage('/')
            
            if disk_usage.percent > 95:
                self.errors.append(f"Critical: Disk usage at {disk_usage.percent}%")
                return False
            elif disk_usage.percent > 80:
                self.warnings.append(f"Low disk space: {disk_usage.percent}%")
            
            # Check screenshot directory size
            screenshots_size = sum(f.stat().st_size for f in self.screenshots_dir.glob("*.png"))
            screenshots_mb = screenshots_size / 1024 / 1024
            
            if screenshots_mb > 1000:  # 1GB
                self.warnings.append(f"Screenshots directory large: {screenshots_mb:.1f} MB")
            
            self.checks['disk_space'] = disk_usage.percent < 90
            return self.checks['disk_space']
            
        except Exception as e:
            self.errors.append(f"Disk check error: {e}")
            return False
    
    def check_findmy_app(self):
        """Check if Find My app is available"""
        try:
            script = '''
            tell application "System Events"
                return exists process "FindMy"
            end tell
            '''
            
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            self.checks['findmy_app'] = result.stdout.strip() == "true"
            
            if not self.checks['findmy_app']:
                self.warnings.append("Find My app not running")
            
            return self.checks['findmy_app']
            
        except Exception as e:
            self.warnings.append(f"Cannot check Find My app: {e}")
            return False
    
    def check_recent_capture(self):
        """Check if captures are happening regularly"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get last capture time
            cursor.execute("""
                SELECT MAX(timestamp) FROM screenshots
            """)
            
            result = cursor.fetchone()
            
            if not result[0]:
                self.errors.append("No screenshots found")
                return False
            
            last_capture = datetime.fromisoformat(result[0])
            time_since = datetime.now() - last_capture
            
            if time_since > timedelta(minutes=5):
                self.errors.append(f"No capture in {time_since.seconds // 60} minutes")
                return False
            elif time_since > timedelta(minutes=3):
                self.warnings.append(f"Last capture {time_since.seconds // 60} minutes ago")
            
            # Check capture success rate
            cursor.execute("""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN processed = 1 THEN 1 ELSE 0 END) as processed
                FROM screenshots
                WHERE timestamp > datetime('now', '-1 hour')
            """)
            
            total, processed = cursor.fetchone()
            
            if total > 0:
                success_rate = (processed or 0) / total
                if success_rate < 0.8:
                    self.warnings.append(f"Low processing rate: {success_rate:.1%}")
            
            conn.close()
            
            self.checks['recent_capture'] = time_since < timedelta(minutes=5)
            return self.checks['recent_capture']
            
        except Exception as e:
            self.errors.append(f"Capture check error: {e}")
            return False
    
    def check_ocr_success(self):
        """Check OCR extraction success rate"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Check recent OCR success
            cursor.execute("""
                SELECT 
                    COUNT(DISTINCT e.id) as extractions,
                    COUNT(DISTINCT l.id) as parsed
                FROM extracted_text e
                LEFT JOIN locations l ON e.screenshot_id = l.screenshot_id 
                    AND e.region_index = l.region_index
                WHERE e.extracted_at > datetime('now', '-1 hour')
            """)
            
            extractions, parsed = cursor.fetchone()
            
            if extractions > 0:
                success_rate = (parsed or 0) / extractions
                
                if success_rate < 0.5:
                    self.errors.append(f"Poor OCR success rate: {success_rate:.1%}")
                    return False
                elif success_rate < 0.8:
                    self.warnings.append(f"Low OCR success rate: {success_rate:.1%}")
            
            conn.close()
            
            self.checks['ocr_success'] = True
            return True
            
        except Exception as e:
            self.warnings.append(f"OCR check error: {e}")
            return False
    
    def check_geocoding(self):
        """Check geocoding success rate"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Check geocoding success
            cursor.execute("""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN latitude IS NOT NULL THEN 1 ELSE 0 END) as geocoded
                FROM device_locations
                WHERE location_text IS NOT NULL 
                    AND location_text != ''
                    AND created_at > datetime('now', '-1 hour')
            """)
            
            total, geocoded = cursor.fetchone()
            
            if total > 0:
                geocode_rate = (geocoded or 0) / total
                
                if geocode_rate < 0.5:
                    self.warnings.append(f"Low geocoding rate: {geocode_rate:.1%}")
            
            conn.close()
            
            self.checks['geocoding'] = True
            return True
            
        except Exception as e:
            self.warnings.append(f"Geocoding check error: {e}")
            return False
    
    def generate_report(self):
        """Generate health report"""
        # Run all checks
        self.check_database()
        self.check_disk_space()
        self.check_findmy_app()
        self.check_recent_capture()
        self.check_ocr_success()
        self.check_geocoding()
        
        # Overall health
        is_healthy = all(self.checks.values()) and not self.errors
        
        report = {
            'timestamp': datetime.now().isoformat(),
            'is_healthy': is_healthy,
            'checks': self.checks,
            'warnings': self.warnings,
            'errors': self.errors
        }
        
        return report
    
    def print_report(self):
        """Print formatted health report"""
        report = self.generate_report()
        
        print("\nAIRTRACKER HEALTH CHECK")
        print("=" * 60)
        print(f"Timestamp: {report['timestamp']}")
        print(f"Overall Health: {'✅ HEALTHY' if report['is_healthy'] else '❌ UNHEALTHY'}")
        
        print("\nComponent Status:")
        print("-" * 40)
        
        for component, status in report['checks'].items():
            status_icon = "✅" if status else "❌"
            component_name = component.replace('_', ' ').title()
            print(f"{status_icon} {component_name}")
        
        if report['warnings']:
            print("\n⚠️  Warnings:")
            for warning in report['warnings']:
                print(f"   - {warning}")
        
        if report['errors']:
            print("\n❌ Errors:")
            for error in report['errors']:
                print(f"   - {error}")
        
        # Additional statistics
        if self.db_path.exists():
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Device count
            cursor.execute("SELECT COUNT(DISTINCT canonical_name) FROM devices WHERE is_active = 1")
            device_count = cursor.fetchone()[0]
            
            # Today's captures
            cursor.execute("""
                SELECT COUNT(*) FROM screenshots 
                WHERE date(timestamp) = date('now')
            """)
            today_captures = cursor.fetchone()[0]
            
            # Today's locations
            cursor.execute("""
                SELECT COUNT(*) FROM device_locations 
                WHERE date(created_at) = date('now')
            """)
            today_locations = cursor.fetchone()[0]
            
            conn.close()
            
            print("\nToday's Statistics:")
            print("-" * 40)
            print(f"Active Devices: {device_count}")
            print(f"Screenshots: {today_captures}")
            print(f"Locations Tracked: {today_locations}")
        
        print("\n" + "=" * 60)
        
        return report
    
    def save_report(self, report):
        """Save report to file"""
        reports_dir = Path("logs") / "health_reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_file = reports_dir / f"health_report_{timestamp}.json"
        
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=2)
        
        print(f"\nReport saved to: {report_file}")

if __name__ == "__main__":
    import sys
    
    checker = HealthChecker()
    
    if len(sys.argv) > 1 and sys.argv[1] == "--json":
        # Output JSON report
        report = checker.generate_report()
        print(json.dumps(report, indent=2))
    else:
        # Print formatted report
        report = checker.print_report()
        
        if len(sys.argv) > 1 and sys.argv[1] == "--save":
            checker.save_report(report)