# Check API health

curl http://localhost:8001/health

# Get all devices

curl http://localhost:8001/devices

# Get device history

curl http://localhost:8001/devices/Auto/history?limit=10

# Search locations

curl "http://localhost:8001/locations/search?location=Home&limit=5"

# Get statistics

curl "http://localhost:8001/stats/Auto?period=7d"

# Trigger tracking

curl -X POST http://localhost:8001/track
