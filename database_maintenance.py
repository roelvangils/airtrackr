#!/usr/bin/env python3

import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
import logging
import shutil

class DatabaseMaintenance:
    """Handle database maintenance tasks"""
    
    def __init__(self):
        self.db_path = Path("database") / "airtracker.db"
        self.backup_dir = Path("database") / "backups"
        self.backup_dir.mkdir(exist_ok=True)
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
    
    def backup_database(self):
        """Create a backup of the database"""
        if not self.db_path.exists():
            logging.error("Database not found")
            return False
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"airtracker_backup_{timestamp}.db"
        backup_path = self.backup_dir / backup_filename
        
        try:
            # Use SQLite backup API
            source = sqlite3.connect(self.db_path)
            dest = sqlite3.connect(backup_path)
            source.backup(dest)
            source.close()
            dest.close()
            
            logging.info(f"Database backed up to: {backup_path}")
            
            # Keep only last 7 backups
            self.cleanup_old_backups()
            
            return True
            
        except Exception as e:
            logging.error(f"Backup failed: {e}")
            return False
    
    def cleanup_old_backups(self, keep_count=7):
        """Keep only the most recent backups"""
        backups = sorted(self.backup_dir.glob("airtracker_backup_*.db"))
        
        if len(backups) > keep_count:
            for old_backup in backups[:-keep_count]:
                old_backup.unlink()
                logging.info(f"Deleted old backup: {old_backup.name}")
    
    def analyze_database(self):
        """Analyze database statistics"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        print("\nDATABASE STATISTICS")
        print("=" * 60)
        
        # Get table sizes
        cursor.execute("""
            SELECT 
                name as table_name,
                (SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND tbl_name=m.name) as index_count
            FROM sqlite_master m
            WHERE type='table' AND name NOT LIKE 'sqlite_%'
        """)
        
        tables = cursor.fetchall()
        
        # Get list of valid table names for validation
        valid_tables = [t[0] for t in tables]
        
        for table_name, index_count in tables:
            # Validate table name against the list from sqlite_master
            if table_name not in valid_tables:
                logging.error(f"Invalid table name detected: {table_name}. Skipping.")
                continue
                
            # Use proper quoting for table names
            cursor.execute(f'SELECT COUNT(*) FROM "{table_name}"')
            row_count = cursor.fetchone()[0]
            
            print(f"\n{table_name}:")
            print(f"  Rows: {row_count:,}")
            print(f"  Indexes: {index_count}")
            
            # Get table size estimate - this query is too complex to safely construct
            # Use a simpler approach
            cursor.execute(f'PRAGMA table_info("{table_name}")')
            col_count = len(cursor.fetchall())
            size_estimate = row_count * col_count * 20  # Rough estimate
            print(f"  Estimated size: {size_estimate/1024:.1f} KB")
        
        # Database file size
        db_size = self.db_path.stat().st_size
        print(f"\nTotal database size: {db_size/1024/1024:.2f} MB")
        
        # Get date range
        cursor.execute("""
            SELECT 
                MIN(timestamp) as oldest,
                MAX(timestamp) as newest
            FROM screenshots
        """)
        
        result = cursor.fetchone()
        if result[0] and result[1]:
            print(f"\nData range: {result[0]} to {result[1]}")
        
        conn.close()
    
    def optimize_database(self):
        """Optimize database performance"""
        conn = sqlite3.connect(self.db_path)
        
        try:
            logging.info("Running ANALYZE...")
            conn.execute("ANALYZE")
            
            logging.info("Running VACUUM...")
            conn.execute("VACUUM")
            
            # Rebuild indexes
            cursor = conn.cursor()
            cursor.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='index' AND name NOT LIKE 'sqlite_%'
            """)
            
            indexes = cursor.fetchall()
            # Get list of valid index names for validation
            valid_indexes = [i[0] for i in indexes]
            
            for (index_name,) in indexes:
                # Validate index name
                if index_name not in valid_indexes:
                    logging.error(f"Invalid index name detected: {index_name}. Skipping.")
                    continue
                    
                logging.info(f"Rebuilding index: {index_name}")
                # Use proper quoting for index names
                conn.execute(f'REINDEX "{index_name}"')
            
            conn.close()
            logging.info("Database optimization complete")
            
        except Exception as e:
            logging.error(f"Optimization failed: {e}")
            conn.close()
    
    def check_integrity(self):
        """Check database integrity"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute("PRAGMA integrity_check")
            result = cursor.fetchone()[0]
            
            if result == "ok":
                logging.info("Database integrity check: PASSED")
                return True
            else:
                logging.error(f"Database integrity check: FAILED - {result}")
                return False
                
        except Exception as e:
            logging.error(f"Integrity check error: {e}")
            return False
        finally:
            conn.close()
    
    def archive_old_data(self, days_to_keep=30):
        """Archive old data to a separate database"""
        cutoff_date = datetime.now() - timedelta(days=days_to_keep)
        
        # Create archive database
        archive_path = self.backup_dir / f"archive_{cutoff_date.strftime('%Y%m')}.db"
        
        if archive_path.exists():
            logging.info(f"Archive already exists: {archive_path}")
            return
        
        main_conn = sqlite3.connect(self.db_path)
        archive_conn = sqlite3.connect(archive_path)
        
        try:
            # Copy schema
            main_conn.backup(archive_conn, pages=0)
            
            # Copy old data
            cursor = main_conn.cursor()
            archive_cursor = archive_conn.cursor()
            
            # Get old screenshot IDs
            cursor.execute("""
                SELECT id FROM screenshots 
                WHERE timestamp < ?
            """, (cutoff_date,))
            
            old_screenshot_ids = [row[0] for row in cursor.fetchall()]
            
            if old_screenshot_ids:
                placeholders = ','.join('?' * len(old_screenshot_ids))
                
                # Copy related data
                tables = ['extracted_text', 'locations', 'device_locations']
                
                for table in tables:
                    cursor.execute(f"""
                        SELECT * FROM {table}
                        WHERE screenshot_id IN ({placeholders})
                    """, old_screenshot_ids)
                    
                    rows = cursor.fetchall()
                    if rows:
                        # Get column count
                        cursor.execute(f"PRAGMA table_info({table})")
                        col_count = len(cursor.fetchall())
                        
                        placeholders2 = ','.join('?' * col_count)
                        archive_cursor.executemany(
                            f"INSERT INTO {table} VALUES ({placeholders2})",
                            rows
                        )
                
                archive_conn.commit()
                logging.info(f"Archived {len(old_screenshot_ids)} screenshots to {archive_path}")
                
        except Exception as e:
            logging.error(f"Archive failed: {e}")
            archive_conn.rollback()
        finally:
            main_conn.close()
            archive_conn.close()
    
    def run_full_maintenance(self):
        """Run all maintenance tasks"""
        print("Running full database maintenance...")
        
        # 1. Check integrity first
        if not self.check_integrity():
            print("ERROR: Database integrity check failed!")
            return
        
        # 2. Create backup
        if not self.backup_database():
            print("ERROR: Backup failed!")
            return
        
        # 3. Show statistics
        self.analyze_database()
        
        # 4. Archive old data
        self.archive_old_data()
        
        # 5. Optimize
        self.optimize_database()
        
        print("\nMaintenance complete!")

if __name__ == "__main__":
    import sys
    
    maintenance = DatabaseMaintenance()
    
    if len(sys.argv) > 1:
        command = sys.argv[1]
        
        if command == "backup":
            maintenance.backup_database()
        elif command == "analyze":
            maintenance.analyze_database()
        elif command == "optimize":
            maintenance.optimize_database()
        elif command == "integrity":
            maintenance.check_integrity()
        elif command == "archive":
            days = int(sys.argv[2]) if len(sys.argv) > 2 else 30
            maintenance.archive_old_data(days)
        elif command == "full":
            maintenance.run_full_maintenance()
        else:
            print("Usage: python database_maintenance.py [backup|analyze|optimize|integrity|archive|full]")
    else:
        # Default to full maintenance
        maintenance.run_full_maintenance()