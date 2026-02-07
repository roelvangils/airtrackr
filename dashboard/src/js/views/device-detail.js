export class DeviceDetailView {
    constructor(device, locations, total, router, apiService, stats = null) {
        this.device = device;
        this.locations = locations;
        this.total = total;
        this.router = router;
        this.apiService = apiService;
        this.stats = stats;
        this.offset = locations.length;
        this.limit = 50;
        this.startDate = null;
        this.endDate = null;
    }

    render() {
        const deviceName = this.device.name || this.device.device_name || 'Unknown Device';
        return `
            <div class="timeline-container">
                <button class="back-btn" data-route="/">
                    Back to Dashboard
                </button>

                <div class="timeline-header">
                    <h2>${this.escapeHtml(deviceName)}</h2>
                    <div class="timeline-meta">
                        ${this.total} location${this.total !== 1 ? 's' : ''} recorded
                        ${this.device.first_seen ? `&bull; First seen ${new Date(this.device.first_seen).toLocaleDateString()}` : ''}
                    </div>
                </div>

                ${this.renderExportButtons()}
                ${this.renderDateFilter()}
                ${this.renderMap()}
                ${this.renderStats()}
                ${this.renderTimeline()}
                ${this.renderLoadMore()}
                ${this.renderDangerZone()}
            </div>
        `;
    }

    renderExportButtons() {
        const deviceName = this.device.name || this.device.device_name;
        const csvUrl = this.apiService.getExportUrl(deviceName, 'csv', this.startDate, this.endDate);
        const gpxUrl = this.apiService.getExportUrl(deviceName, 'gpx', this.startDate, this.endDate);

        return `
            <div class="export-buttons">
                <a href="${csvUrl}" class="export-btn" download>
                    <i class="fa-solid fa-file-csv"></i> Export CSV
                </a>
                <a href="${gpxUrl}" class="export-btn" download>
                    <i class="fa-solid fa-route"></i> Export GPX
                </a>
            </div>
        `;
    }

    renderDateFilter() {
        return `
            <div class="date-filter">
                <div class="date-filter-inputs">
                    <label>
                        <span>From</span>
                        <input type="date" id="filter-start-date" ${this.startDate ? `value="${this.startDate.split('T')[0]}"` : ''}>
                    </label>
                    <label>
                        <span>To</span>
                        <input type="date" id="filter-end-date" ${this.endDate ? `value="${this.endDate.split('T')[0]}"` : ''}>
                    </label>
                </div>
                <div class="date-filter-actions">
                    <button class="date-filter-btn apply-date-filter" id="apply-date-filter">Apply</button>
                    <button class="date-filter-btn clear-date-filter" id="clear-date-filter">Clear</button>
                </div>
            </div>
        `;
    }

    renderMap() {
        if (!this.locations || this.locations.length === 0) {
            return '';
        }

        // Find locations with coordinates for the route
        const locationsWithCoords = this.locations.filter(loc => loc.latitude && loc.longitude);

        if (locationsWithCoords.length === 0) {
            return `
                <div class="map-container">
                    <div class="map-placeholder">
                        <p><i class="fa-solid fa-location-dot"></i> No location coordinates available</p>
                    </div>
                </div>
            `;
        }

        const latest = locationsWithCoords[0];
        const locationText = this.processLocationText(latest.location || latest.location_text);

        return `
            <div class="map-container">
                <div class="map-header">
                    <h3><i class="fa-solid fa-location-dot"></i> Location Map</h3>
                    <div class="map-location-name">${this.escapeHtml(locationText)}</div>
                </div>
                <div class="map-wrapper" id="leaflet-map" style="height: 400px;"></div>
                <div class="map-footer">
                    <a href="https://maps.apple.com/?q=${latest.latitude},${latest.longitude}" target="_blank" class="map-link">
                        <i class="fa-solid fa-map"></i> Open in Apple Maps
                    </a>
                    <a href="https://maps.google.com/maps?q=${latest.latitude},${latest.longitude}" target="_blank" class="map-link">
                        <i class="fa-solid fa-globe"></i> Open in Google Maps
                    </a>
                </div>
            </div>
        `;
    }

    initLeafletMap() {
        const mapEl = document.getElementById('leaflet-map');
        if (!mapEl || typeof L === 'undefined') return;

        const locationsWithCoords = this.locations
            .filter(loc => loc.latitude && loc.longitude)
            .reverse(); // oldest first for polyline

        if (locationsWithCoords.length === 0) return;

        const latest = locationsWithCoords[locationsWithCoords.length - 1];
        const map = L.map('leaflet-map').setView([latest.latitude, latest.longitude], 14);

        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '&copy; OpenStreetMap contributors',
            maxZoom: 19
        }).addTo(map);

        // Build polyline coords
        const coords = locationsWithCoords.map(loc => [loc.latitude, loc.longitude]);

        if (coords.length > 1) {
            L.polyline(coords, {
                color: '#007AFF',
                weight: 3,
                opacity: 0.7
            }).addTo(map);
        }

        // Start marker (oldest)
        const first = locationsWithCoords[0];
        L.marker([first.latitude, first.longitude], {
            icon: L.divIcon({
                className: 'leaflet-marker-start',
                html: '<div style="background:#34C759;width:14px;height:14px;border-radius:50%;border:3px solid white;box-shadow:0 0 4px rgba(0,0,0,0.3);"></div>',
                iconSize: [14, 14],
                iconAnchor: [7, 7]
            })
        }).addTo(map).bindPopup(`Start: ${this.processLocationText(first.location)}`);

        // End marker (most recent)
        L.marker([latest.latitude, latest.longitude], {
            icon: L.divIcon({
                className: 'leaflet-marker-end',
                html: '<div style="background:#FF3B30;width:14px;height:14px;border-radius:50%;border:3px solid white;box-shadow:0 0 4px rgba(0,0,0,0.3);"></div>',
                iconSize: [14, 14],
                iconAnchor: [7, 7]
            })
        }).addTo(map).bindPopup(`Latest: ${this.processLocationText(latest.location)}`);

        // Auto-fit bounds
        if (coords.length > 1) {
            map.fitBounds(coords, { padding: [30, 30] });
        }
    }

    renderStats() {
        if (!this.stats || this.stats.total_records === 0) return '';

        const s = this.stats;

        const formatDate = (iso) => {
            if (!iso) return '—';
            return new Date(iso).toLocaleDateString();
        };

        const dateRange = `${formatDate(s.first_seen)} – ${formatDate(s.last_seen)}`;

        const cards = [];

        // Tracking Period
        cards.push(`
            <div class="stats-card">
                <div class="stats-card-icon"><i class="fa-solid fa-calendar-days"></i></div>
                <div class="stats-card-body">
                    <div class="stats-card-value">${s.days_tracked} day${s.days_tracked !== 1 ? 's' : ''}</div>
                    <div class="stats-card-label">Tracking Period</div>
                    <div class="stats-card-detail">${dateRange}</div>
                </div>
            </div>
        `);

        // Total Updates
        cards.push(`
            <div class="stats-card">
                <div class="stats-card-icon"><i class="fa-solid fa-arrow-rotate-right"></i></div>
                <div class="stats-card-body">
                    <div class="stats-card-value">${s.total_records.toLocaleString()}</div>
                    <div class="stats-card-label">Total Updates</div>
                    <div class="stats-card-detail">${s.avg_updates_per_day} per day</div>
                </div>
            </div>
        `);

        // Unique Locations
        cards.push(`
            <div class="stats-card">
                <div class="stats-card-icon"><i class="fa-solid fa-location-dot"></i></div>
                <div class="stats-card-body">
                    <div class="stats-card-value">${s.unique_locations}</div>
                    <div class="stats-card-label">Unique Locations</div>
                    <div class="stats-card-detail">${s.records_with_coords} with coordinates</div>
                </div>
            </div>
        `);

        // Total Distance
        if (s.total_distance_km !== null && s.total_distance_km > 0) {
            cards.push(`
                <div class="stats-card">
                    <div class="stats-card-icon"><i class="fa-solid fa-road"></i></div>
                    <div class="stats-card-body">
                        <div class="stats-card-value">${s.total_distance_km.toLocaleString()} km</div>
                        <div class="stats-card-label">Total Distance</div>
                        <div class="stats-card-detail">Between consecutive readings</div>
                    </div>
                </div>
            `);
        }

        // Time at Home
        if (s.home_percentage !== null) {
            cards.push(`
                <div class="stats-card">
                    <div class="stats-card-icon"><i class="fa-solid fa-house"></i></div>
                    <div class="stats-card-body">
                        <div class="stats-card-value">${s.home_percentage}%</div>
                        <div class="stats-card-label">Time at Home</div>
                        <div class="stats-card-detail">${s.home_record_count} of ${s.total_records} readings</div>
                    </div>
                </div>
            `);
        }

        // Furthest from Home
        if (s.furthest_from_home_km !== null && s.furthest_from_home_km > 0.5) {
            cards.push(`
                <div class="stats-card">
                    <div class="stats-card-icon"><i class="fa-solid fa-arrows-left-right-to-line"></i></div>
                    <div class="stats-card-body">
                        <div class="stats-card-value">${s.furthest_from_home_km} km</div>
                        <div class="stats-card-label">Furthest from Home</div>
                        <div class="stats-card-detail">${this.escapeHtml(s.furthest_location || '')}</div>
                    </div>
                </div>
            `);
        }

        // Top Locations
        let topLocationsHtml = '';
        if (s.top_locations && s.top_locations.length > 0) {
            const rows = s.top_locations.map((loc, i) => `
                <div class="stats-location-row">
                    <span class="stats-location-rank">${i + 1}</span>
                    <div class="stats-location-info">
                        <div class="stats-location-name">${this.escapeHtml(loc.name)}</div>
                        <div class="stats-location-bar-track">
                            <div class="stats-location-bar" style="width: ${loc.percentage}%"></div>
                        </div>
                    </div>
                    <span class="stats-location-count">${loc.count}x (${loc.percentage}%)</span>
                </div>
            `).join('');

            topLocationsHtml = `
                <div class="stats-top-locations">
                    <h4><i class="fa-solid fa-ranking-star"></i> Top Locations</h4>
                    ${rows}
                </div>
            `;
        }

        return `
            <div class="stats-summary">
                <div class="stats-grid">
                    ${cards.join('')}
                </div>
                ${topLocationsHtml}
            </div>
        `;
    }

    renderTimeline() {
        if (!this.locations || this.locations.length === 0) {
            return `
                <div class="loading">
                    <p>No location history found for this device.</p>
                </div>
            `;
        }

        const sortedLocations = [...this.locations].sort((a, b) =>
            new Date(b.timestamp) - new Date(a.timestamp)
        );

        const groupedLocations = this.groupConsecutiveLocations(sortedLocations);

        return `
            <div class="timeline">
                ${groupedLocations.map(group => this.renderTimelineGroup(group)).join('')}
            </div>
        `;
    }

    renderLoadMore() {
        if (this.locations.length >= this.total) {
            return '';
        }
        return `
            <div class="load-more-container">
                <button class="load-more-btn" id="load-more-btn">
                    <i class="fa-solid fa-arrow-down"></i>
                    Load More (${this.locations.length} of ${this.total})
                </button>
            </div>
        `;
    }

    groupConsecutiveLocations(locations) {
        if (locations.length === 0) return [];

        const groups = [];
        let currentGroup = {
            locations: [locations[0]],
            cleanLocation: this.processLocationText(locations[0].location || locations[0].location_text),
            startTime: new Date(locations[0].timestamp),
            endTime: new Date(locations[0].timestamp),
            coordinates: {
                latitude: locations[0].latitude,
                longitude: locations[0].longitude
            },
            distance: locations[0].distance_meters
        };

        for (let i = 1; i < locations.length; i++) {
            const current = locations[i];
            const currentCleanLocation = this.processLocationText(current.location || current.location_text);

            if (this.shouldGroupLocations(currentGroup.cleanLocation, currentCleanLocation)) {
                currentGroup.locations.push(current);
                currentGroup.endTime = new Date(current.timestamp);

                if (current.latitude && current.longitude) {
                    currentGroup.coordinates = {
                        latitude: current.latitude,
                        longitude: current.longitude
                    };
                }
            } else {
                groups.push(currentGroup);
                currentGroup = {
                    locations: [current],
                    cleanLocation: currentCleanLocation,
                    startTime: new Date(current.timestamp),
                    endTime: new Date(current.timestamp),
                    coordinates: {
                        latitude: current.latitude,
                        longitude: current.longitude
                    },
                    distance: current.distance_meters
                };
            }
        }

        groups.push(currentGroup);
        return groups;
    }

    renderDangerZone() {
        const deviceId = this.device.id || this.device.device_id;
        const deviceName = this.device.name || this.device.device_name || 'Unknown Device';

        return `
            <div class="danger-zone">
                <div class="danger-zone-header">
                    <h3><i class="fa-solid fa-triangle-exclamation"></i> Danger Zone</h3>
                    <p>Irreversible and destructive actions</p>
                </div>
                <div class="danger-zone-content">
                    <div class="danger-action">
                        <div class="danger-action-info">
                            <h4>Delete this device</h4>
                            <p>Permanently remove "${this.escapeHtml(deviceName)}" and all its location history. This action cannot be undone.</p>
                        </div>
                        <button class="danger-btn delete-device-btn" data-device-id="${deviceId}" data-device-name="${this.escapeHtml(deviceName)}">
                            <i class="fa-solid fa-trash-can"></i> Delete Device
                        </button>
                    </div>
                </div>
            </div>
        `;
    }

    shouldGroupLocations(location1, location2) {
        if (location1 === location2) return true;
        if (location1 === 'Location not available' && location2 === 'Location not available') return true;
        return false;
    }

    renderTimelineGroup(group) {
        const isMultipleEntries = group.locations.length > 1;
        const startDate = group.startTime;
        const endDate = group.endTime;

        if (isMultipleEntries) {
            const duration = this.calculateDuration(startDate, endDate);
            const startFormatted = startDate.toLocaleString();
            const endFormatted = endDate.toLocaleString();

            // Get structured address and enrichment from the first location in the group
            const firstLoc = group.locations[0];
            const addressLine = this.formatStructuredAddress(firstLoc);
            const distHomeBadge = this.formatDistanceHomeBadge(firstLoc);
            const batteryBadge = this.formatBatteryBadge(firstLoc);

            return `
                <div class="timeline-item timeline-group">
                    <div class="timeline-content">
                        <div class="timeline-location">
                            ${this.escapeHtml(group.cleanLocation)}
                        </div>
                        ${addressLine ? `<div class="timeline-address">${this.escapeHtml(addressLine)}</div>` : ''}
                        <div class="timeline-badges">
                            ${distHomeBadge}${batteryBadge}
                        </div>
                        <div class="timeline-duration">
                            From ${endFormatted} to ${startFormatted} (${duration})
                        </div>
                        ${group.coordinates.latitude && group.coordinates.longitude ? `
                            <div class="timeline-coords">
                                <span class="coords-text">${group.coordinates.latitude.toFixed(6)}, ${group.coordinates.longitude.toFixed(6)}</span>
                                <div class="map-links">
                                    <a href="https://maps.google.com/maps?q=${group.coordinates.latitude},${group.coordinates.longitude}" target="_blank" class="map-link google-maps">
                                        <i class="fa-solid fa-globe"></i> Google Maps
                                    </a>
                                    <a href="https://maps.apple.com/?q=${group.coordinates.latitude},${group.coordinates.longitude}" target="_blank" class="map-link apple-maps">
                                        <i class="fa-solid fa-map"></i> Apple Maps
                                    </a>
                                </div>
                            </div>
                        ` : ''}
                        ${group.distance !== null && group.distance !== undefined ? `
                            <div class="timeline-distance">
                                Distance: ${this.formatDistance(group.distance)}
                            </div>
                        ` : ''}
                        <div class="timeline-entry-count">
                            ${group.locations.length} entries
                            <button class="delete-group-btn" data-group-locations="${group.locations.map(l => l.id).join(',')}">
                                <i class="fa-solid fa-trash-can"></i> Delete All
                            </button>
                        </div>
                    </div>
                </div>
            `;
        } else {
            return this.renderTimelineItem(group.locations[0]);
        }
    }

    calculateDuration(startDate, endDate) {
        const diffMs = Math.abs(startDate - endDate);
        const diffMinutes = Math.floor(diffMs / (1000 * 60));
        const diffHours = Math.floor(diffMinutes / 60);
        const diffDays = Math.floor(diffHours / 24);

        if (diffDays > 0) {
            const remainingHours = diffHours % 24;
            if (remainingHours > 0) {
                return `${diffDays} day${diffDays !== 1 ? 's' : ''}, ${remainingHours} hour${remainingHours !== 1 ? 's' : ''}`;
            }
            return `${diffDays} day${diffDays !== 1 ? 's' : ''}`;
        } else if (diffHours > 0) {
            const remainingMinutes = diffMinutes % 60;
            if (remainingMinutes > 0) {
                return `${diffHours} hour${diffHours !== 1 ? 's' : ''}, ${remainingMinutes} minute${remainingMinutes !== 1 ? 's' : ''}`;
            }
            return `${diffHours} hour${diffHours !== 1 ? 's' : ''}`;
        } else if (diffMinutes > 0) {
            return `${diffMinutes} minute${diffMinutes !== 1 ? 's' : ''}`;
        }
        return 'Less than a minute';
    }

    renderTimelineItem(location) {
        const date = new Date(location.timestamp);
        const timeAgo = this.getTimeAgo(date);
        const fullDate = date.toLocaleString();
        const cleanLocation = this.processLocationText(location.location || location.location_text);
        const addressLine = this.formatStructuredAddress(location);
        const distHomeBadge = this.formatDistanceHomeBadge(location);
        const batteryBadge = this.formatBatteryBadge(location);

        return `
            <div class="timeline-item">
                <div class="timeline-content">
                    <div class="timeline-date" title="${fullDate}">
                        ${timeAgo}
                    </div>
                    <div class="timeline-location">
                        ${this.escapeHtml(cleanLocation)}
                    </div>
                    ${addressLine ? `<div class="timeline-address">${this.escapeHtml(addressLine)}</div>` : ''}
                    <div class="timeline-badges">
                        ${distHomeBadge}${batteryBadge}
                    </div>
                    ${location.latitude && location.longitude ? `
                        <div class="timeline-coords">
                            <span class="coords-text">${location.latitude.toFixed(6)}, ${location.longitude.toFixed(6)}</span>
                            <div class="map-links">
                                <a href="https://maps.google.com/maps?q=${location.latitude},${location.longitude}" target="_blank" class="map-link google-maps">
                                    <i class="fa-solid fa-globe"></i> Google Maps
                                </a>
                                <a href="https://maps.apple.com/?q=${location.latitude},${location.longitude}" target="_blank" class="map-link apple-maps">
                                    <i class="fa-solid fa-map"></i> Apple Maps
                                </a>
                            </div>
                        </div>
                    ` : ''}
                    ${location.distance_meters !== null && location.distance_meters !== undefined ? `
                        <div class="timeline-distance">
                            Distance: ${this.formatDistance(location.distance_meters)}
                        </div>
                    ` : ''}
                    <div class="timeline-actions">
                        <button class="delete-btn" data-location-id="${location.id}">
                            <i class="fa-solid fa-trash-can"></i> Delete
                        </button>
                    </div>
                </div>
            </div>
        `;
    }

    processLocationText(locationText) {
        if (!locationText) {
            return 'Location not available';
        }

        let cleaned = locationText.trim();

        cleaned = cleaned.replace(/,\s*\d+\s*(min|hr|hour|minute)s?\s*ago$/i, '');
        cleaned = cleaned.replace(/,\s*(Paused|paused)$/i, '');

        const invalidPatterns = [
            /^lastseen\d+\w*ago$/i,
            /^last\s*seen/i,
            /^seen\s*\d+/i,
            /^\d+\s*(hr|min|hour|minute)s?\s*ago$/i,
            /^No location found$/i
        ];

        for (const pattern of invalidPatterns) {
            if (pattern.test(cleaned)) {
                return 'Location not available';
            }
        }

        cleaned = cleaned.replace(/[-:.]\d*$/, '');
        cleaned = cleaned.replace(/[-:.]$/, '');
        cleaned = cleaned.replace(/\s+/g, ' ');

        if (cleaned.toLowerCase() === 'home') {
            return 'Home';
        }

        return cleaned || 'Location not available';
    }

    formatDistance(meters) {
        if (meters === 0) {
            return 'At location';
        } else if (meters < 1000) {
            return `${meters}m`;
        } else {
            return `${(meters / 1000).toFixed(1)}km`;
        }
    }

    bindEvents() {
        // Initialize Leaflet map
        this.initLeafletMap();

        // Handle delete single location
        document.querySelectorAll('.delete-btn').forEach(btn => {
            btn.addEventListener('click', async (e) => {
                e.stopPropagation();
                const locationId = btn.dataset.locationId;
                if (confirm('Are you sure you want to delete this location entry?')) {
                    await this.deleteLocation(locationId);
                }
            });
        });

        // Handle delete location group
        document.querySelectorAll('.delete-group-btn').forEach(btn => {
            btn.addEventListener('click', async (e) => {
                e.stopPropagation();
                const locationIds = btn.dataset.groupLocations.split(',');
                if (confirm(`Are you sure you want to delete all ${locationIds.length} entries in this group?`)) {
                    await this.deleteLocationGroup(locationIds);
                }
            });
        });

        // Handle delete device
        document.querySelectorAll('.delete-device-btn').forEach(btn => {
            btn.addEventListener('click', async (e) => {
                e.stopPropagation();
                const deviceId = btn.dataset.deviceId;
                const deviceName = btn.dataset.deviceName;
                await this.showDeleteDeviceConfirmation(deviceId, deviceName);
            });
        });

        // Handle "Load More" button
        const loadMoreBtn = document.getElementById('load-more-btn');
        if (loadMoreBtn) {
            loadMoreBtn.addEventListener('click', () => this.loadMore());
        }

        // Handle date filter
        const applyBtn = document.getElementById('apply-date-filter');
        const clearBtn = document.getElementById('clear-date-filter');
        if (applyBtn) {
            applyBtn.addEventListener('click', () => this.applyDateFilter());
        }
        if (clearBtn) {
            clearBtn.addEventListener('click', () => this.clearDateFilter());
        }
    }

    async loadMore() {
        const deviceName = this.device.name || this.device.device_name;
        const btn = document.getElementById('load-more-btn');
        if (btn) btn.textContent = 'Loading...';

        try {
            const response = await this.apiService.getDeviceLocations(
                deviceName, this.limit, this.offset, this.startDate, this.endDate
            );
            const newLocations = response.locations;

            if (newLocations.length > 0) {
                this.locations = [...this.locations, ...newLocations];
                this.offset += newLocations.length;

                // Re-render everything
                const contentElement = document.querySelector('#main-content');
                if (contentElement) {
                    contentElement.innerHTML = this.render();
                    this.bindEvents();
                }
            }
        } catch (error) {
            console.error('Failed to load more:', error);
            if (btn) btn.textContent = 'Load More (failed, try again)';
        }
    }

    async applyDateFilter() {
        const startInput = document.getElementById('filter-start-date');
        const endInput = document.getElementById('filter-end-date');

        this.startDate = startInput?.value ? `${startInput.value}T00:00:00` : null;
        this.endDate = endInput?.value ? `${endInput.value}T23:59:59` : null;

        const deviceName = this.device.name || this.device.device_name;

        try {
            const response = await this.apiService.getDeviceLocations(
                deviceName, this.limit, 0, this.startDate, this.endDate
            );
            this.locations = response.locations;
            this.total = response.total;
            this.offset = this.locations.length;

            const contentElement = document.querySelector('#main-content');
            if (contentElement) {
                contentElement.innerHTML = this.render();
                this.bindEvents();
            }
        } catch (error) {
            console.error('Failed to apply date filter:', error);
        }
    }

    async clearDateFilter() {
        this.startDate = null;
        this.endDate = null;

        const deviceName = this.device.name || this.device.device_name;

        try {
            const response = await this.apiService.getDeviceLocations(deviceName, this.limit, 0);
            this.locations = response.locations;
            this.total = response.total;
            this.offset = this.locations.length;

            const contentElement = document.querySelector('#main-content');
            if (contentElement) {
                contentElement.innerHTML = this.render();
                this.bindEvents();
            }
        } catch (error) {
            console.error('Failed to clear date filter:', error);
        }
    }

    async deleteLocation(locationId) {
        try {
            await this.apiService.deleteLocation(locationId);

            this.locations = this.locations.filter(loc => loc.id != locationId);
            this.total = Math.max(0, this.total - 1);

            const contentElement = document.querySelector('#main-content');
            if (contentElement) {
                contentElement.innerHTML = this.render();
                this.bindEvents();
            }
        } catch (error) {
            console.error('Failed to delete location:', error);
            alert(`Failed to delete location: ${error.message}`);
        }
    }

    async deleteLocationGroup(locationIds) {
        try {
            const uniqueLocationIds = [...new Set(locationIds)];

            const results = [];
            for (const id of uniqueLocationIds) {
                try {
                    await this.apiService.deleteLocation(id);
                    results.push({ id, success: true });
                } catch (error) {
                    results.push({ id, success: false });
                }
            }

            const successfullyDeleted = results.filter(r => r.success).map(r => r.id);
            this.locations = this.locations.filter(loc => !successfullyDeleted.includes(loc.id.toString()));
            this.total = Math.max(0, this.total - successfullyDeleted.length);

            const contentElement = document.querySelector('#main-content');
            if (contentElement) {
                contentElement.innerHTML = this.render();
                this.bindEvents();
            }
        } catch (error) {
            console.error('Failed to delete location group:', error);
            alert(`Failed to delete location group: ${error.message}`);
        }
    }

    async showDeleteDeviceConfirmation(deviceId, deviceName) {
        const confirmed = await this.showConfirmationModal(
            'Delete Device',
            `Are you absolutely sure you want to delete "${deviceName}"?`,
            'This action will permanently remove the device and all its location history. This cannot be undone.',
            'Delete Device',
            'danger'
        );

        if (confirmed) {
            const doubleConfirmed = await this.showConfirmationModal(
                'Final Confirmation',
                `Type "${deviceName}" to confirm deletion:`,
                'This is your last chance to cancel. All data will be permanently lost.',
                'Type device name to confirm',
                'danger',
                true,
                deviceName
            );

            if (doubleConfirmed) {
                await this.deleteDevice(deviceId, deviceName);
            }
        }
    }

    async showConfirmationModal(title, message, description, buttonText, type = 'default', requireTyping = false, expectedText = '') {
        return new Promise((resolve) => {
            const modal = document.createElement('div');
            modal.className = 'modal-backdrop';
            modal.innerHTML = `
                <div class="modal-content">
                    <div class="modal-header">
                        <h3>${title}</h3>
                    </div>
                    <div class="modal-body">
                        <p class="modal-message">${message}</p>
                        <p class="modal-description">${description}</p>
                        ${requireTyping ? `
                            <input type="text" class="modal-input" placeholder="Type device name here..." />
                        ` : ''}
                    </div>
                    <div class="modal-footer">
                        <button class="modal-btn modal-btn-cancel">Cancel</button>
                        <button class="modal-btn modal-btn-${type}" ${requireTyping ? 'disabled' : ''}>${buttonText}</button>
                    </div>
                </div>
            `;

            document.body.appendChild(modal);

            const cancelBtn = modal.querySelector('.modal-btn-cancel');
            const confirmBtn = modal.querySelector(`.modal-btn-${type}`);
            const input = modal.querySelector('.modal-input');

            if (requireTyping && input) {
                input.addEventListener('input', () => {
                    confirmBtn.disabled = input.value.trim() !== expectedText;
                });
                input.focus();
            }

            cancelBtn.addEventListener('click', () => {
                document.body.removeChild(modal);
                resolve(false);
            });

            confirmBtn.addEventListener('click', () => {
                if (requireTyping && input.value.trim() !== expectedText) return;
                document.body.removeChild(modal);
                resolve(true);
            });

            modal.addEventListener('keydown', (e) => {
                if (e.key === 'Escape') {
                    document.body.removeChild(modal);
                    resolve(false);
                }
            });

            modal.addEventListener('click', (e) => {
                if (e.target === modal) {
                    document.body.removeChild(modal);
                    resolve(false);
                }
            });
        });
    }

    async deleteDevice(deviceId, deviceName) {
        try {
            await this.apiService.deleteDevice(deviceId);
            alert(`Device "${deviceName}" has been successfully deleted.`);
            this.router.navigate('/');
        } catch (error) {
            console.error('Failed to delete device:', error);
            alert(`Failed to delete device: ${error.message}`);
        }
    }

    getTimeAgo(date) {
        const now = new Date();
        const diffMs = now - date;
        const diffMins = Math.floor(diffMs / (1000 * 60));
        const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
        const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

        if (diffMins < 1) return 'Just now';
        if (diffMins < 60) return `${diffMins} minute${diffMins !== 1 ? 's' : ''} ago`;
        if (diffHours < 24) return `${diffHours} hour${diffHours !== 1 ? 's' : ''} ago`;
        if (diffDays < 7) return `${diffDays} day${diffDays !== 1 ? 's' : ''} ago`;
        return date.toLocaleDateString();
    }

    formatStructuredAddress(location) {
        const parts = [];
        if (location.street) {
            let street = location.street;
            if (location.house_number) street += ` ${location.house_number}`;
            parts.push(street);
        }
        if (location.postal_code || location.city) {
            const cityPart = [location.postal_code, location.city].filter(Boolean).join(' ');
            parts.push(cityPart);
        }
        return parts.length > 0 ? parts.join(', ') : '';
    }

    formatDistanceHomeBadge(location) {
        if (location.distance_from_home_km === null || location.distance_from_home_km === undefined) {
            return '';
        }
        const km = location.distance_from_home_km;
        let formatted;
        if (km < 0.1) {
            formatted = 'Home';
        } else if (km < 1) {
            formatted = `${Math.round(km * 1000)}m`;
        } else {
            formatted = `${km.toFixed(1)}km`;
        }
        return `<span class="distance-home-badge"><i class="fa-solid fa-house"></i> ${formatted}</span>`;
    }

    formatBatteryBadge(location) {
        if (!location.battery_status) return '';
        const status = location.battery_status.toLowerCase();
        let icon, colorClass;
        if (status === 'normal' || status === 'full') {
            icon = 'fa-battery-full';
            colorClass = 'battery-normal';
        } else if (status === 'low') {
            icon = 'fa-battery-quarter';
            colorClass = 'battery-low';
        } else if (status === 'critical') {
            icon = 'fa-battery-empty';
            colorClass = 'battery-critical';
        } else {
            icon = 'fa-battery-half';
            colorClass = 'battery-normal';
        }
        return `<span class="battery-badge ${colorClass}"><i class="fa-solid ${icon}"></i> ${this.escapeHtml(location.battery_status)}</span>`;
    }

    escapeHtml(text) {
        if (typeof text !== 'string') return text;
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}
