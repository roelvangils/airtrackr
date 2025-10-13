# AirTracker Debugging Guide

All services have detailed logging enabled to help you see exactly what's happening, especially with the Find My automation.

## Logging Levels

All components are configured with **DEBUG** or **INFO** level logging:

### 1. Orchestrated Tracker (`orchestrated_tracker.py`)
- **Level**: DEBUG
- **Shows**:
  - Tab switching events (Cmd+1, Cmd+2, Cmd+3)
  - Wait times and pauses
  - Device extraction results
  - Database save operations
  - Geocoding results
  - All errors and warnings

Example output:
```
2025-10-13 18:30:00 - __main__ - INFO - 🚀 STARTING ORCHESTRATED AIRTRACKER (SCHEDULED MODE)
2025-10-13 18:30:05 - __main__ - INFO - Processing People tab...
2025-10-13 18:30:05 - findmy_automation - INFO - Switched to person tab (Cmd+1)
2025-10-13 18:30:10 - __main__ - INFO - Successfully extracted 2 person(s)
2025-10-13 18:30:10 - __main__ - INFO - Found 2 person(s):
2025-10-13 18:30:10 - __main__ - INFO -   - Peter Van Gils: Home (Now, Nearby)
2025-10-13 18:30:10 - __main__ - DEBUG - Geocoded Home -> (51.234567, 3.456789)
```

### 2. Find My Automation (`findmy_automation.py`)
- **Level**: DEBUG
- **Shows**:
  - AppleScript execution
  - Find My app activation
  - Tab switching commands
  - All automation errors

### 3. API Server (`swift_api.py`)
- **Level**: INFO with access logs
- **Shows**:
  - All HTTP requests and responses
  - API endpoint access
  - Database queries
  - Request timing

Example output:
```
INFO:     127.0.0.1:54291 - "GET /devices HTTP/1.1" 200 OK
INFO:     127.0.0.1:54291 - "GET /devices/counts HTTP/1.1" 200 OK
```

### 4. Dashboard (Vite)
- **Shows**:
  - Hot module reload (HMR) updates
  - Build errors
  - SCSS compilation

## Viewing Logs

### Automatic Console.app Integration

When you run the launch scripts, **log files are automatically opened in Console.app** for easy monitoring:

- `./start_servers.sh` opens `api.log` and `dashboard.log` in Console.app
- `./start_tracker.sh` opens `tracker.log` in Console.app

Console.app provides:
- Real-time log updates
- Search and filtering
- Multiple logs in tabs
- Syntax highlighting

### Manual Log Monitoring

You can also monitor logs manually from the terminal:

```bash
# Watch API logs
tail -f logs/api.log

# Watch Dashboard logs
tail -f logs/dashboard.log

# Watch Tracker logs
tail -f logs/tracker.log
```

### Opening Logs Manually in Console.app

```bash
# Open specific log
open -a Console logs/api.log

# Open all logs
open -a Console logs/*.log
```

### Running in Foreground (Most Verbose)

For maximum visibility during testing, run services in foreground:

```bash
# Terminal 1 - API Server
source venv/bin/activate
python swift_api.py

# Terminal 2 - Dashboard
cd dashboard
bun run dev

# Terminal 3 - Tracker (test mode)
source venv/bin/activate
python orchestrated_tracker.py --single-cycle
```

This way you see all output directly in the terminal.

## Debugging Automation Issues

### Test Tab Automation Separately

```bash
source venv/bin/activate
python findmy_automation.py
```

This will:
1. Check if Find My is running
2. Try to launch it if not
3. Cycle through all three tabs
4. Show detailed logging for each step

### Test Single Tracking Cycle

```bash
source venv/bin/activate
python orchestrated_tracker.py --single-cycle
```

This runs one complete cycle and exits, perfect for debugging.

### Common Issues and Solutions

**Problem**: "Failed to ensure Find My is running"
- **Solution**: Open Find My app manually before starting the tracker
- **Command**: `open -a "Find My"`

**Problem**: "Swift extractor not found"
- **Solution**: Compile the Swift extractor
- **Command**: `cd swift && ./build_universal.sh`

**Problem**: Tab switching seems to work but no data extracted
- **Solution**:
  - Make sure Find My is the active (frontmost) app
  - Increase `TAB_LOAD_TIME` in orchestrated_tracker.py if your Mac is slow
  - Check that the Swift extractor binary has correct permissions: `chmod +x swift/airtag_extractor`

**Problem**: API returning 404 for /devices/counts
- **Solution**: Restart the API server to pick up code changes
- **Command**: `./stop_servers.sh && ./start_servers.sh`

## Log File Locations

When using launch scripts:
- `logs/api.log` - API server output
- `logs/dashboard.log` - Dashboard/Vite output
- `logs/tracker.log` - Orchestrated tracker output
- `logs/api.pid` - API process ID
- `logs/dashboard.pid` - Dashboard process ID
- `logs/tracker.pid` - Tracker process ID

## Performance Monitoring

To see what's happening in real-time while the tracker runs:

```bash
# Watch database changes
watch -n 5 'sqlite3 database/airtracker.db "SELECT device_type, COUNT(*) FROM swift_devices WHERE device_type IS NOT NULL GROUP BY device_type"'

# Watch recent locations
watch -n 5 'sqlite3 database/airtracker.db "SELECT device_name, device_type, location, time_status FROM swift_locations ORDER BY timestamp DESC LIMIT 10"'
```

## Troubleshooting Checklist

Before reporting issues, verify:

- [ ] Find My app is running and logged in
- [ ] Swift extractor is compiled and executable
- [ ] Database directory exists and is writable
- [ ] Python virtual environment is activated
- [ ] All required Python packages are installed
- [ ] Accessibility permissions granted (if needed)
- [ ] No firewall blocking localhost:8001 or localhost:3000

## Quick Test Commands

```bash
# Test API health
curl http://localhost:8001/health | python -m json.tool

# Test device counts
curl http://localhost:8001/devices/counts | python -m json.tool

# Test dashboard is running
curl -I http://localhost:3000

# Check running services
ps aux | grep -E "swift_api|bun run dev|orchestrated_tracker" | grep -v grep
```
