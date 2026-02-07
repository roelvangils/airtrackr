export class DevicesView {
    constructor(devices, router, apiService) {
        this.devices = devices;
        this.router = router;
        this.apiService = apiService;
    }
    
    render() {
        if (!this.devices || this.devices.length === 0) {
            return `
                <div class="loading">
                    <p>No devices found. Make sure your AirTracker is running and has captured some data.</p>
                </div>
            `;
        }
        
        return `
            <div class="device-grid">
                ${this.devices.map(device => this.renderDeviceCard(device)).join('')}
            </div>
        `;
    }
    
    renderDeviceCard(device) {
        const deviceId = device.device_name; // Swift API uses device_name as identifier
        const deviceName = device.device_name;
        // Parse timestamps - API returns UTC without timezone suffix, need to append 'Z'
        const lastSeenDate = device.last_seen ? new Date(device.last_seen + 'Z') : null;
        const firstSeenDate = device.first_seen ? new Date(device.first_seen + 'Z') : null;

        const lastSeen = lastSeenDate ? lastSeenDate.toLocaleString() : 'Never';
        const locationCount = device.location_count || 0;
        const firstSeen = firstSeenDate ? firstSeenDate.toLocaleString() : 'Unknown';

        const metaRows = `
                            <div class="meta-row">
                                <span class="meta-label"><i class="fa-solid fa-clock"></i> Last seen</span>
                                <span class="meta-value">${lastSeen}</span>
                            </div>
                            <div class="meta-row">
                                <span class="meta-label"><i class="fa-solid fa-calendar"></i> First seen</span>
                                <span class="meta-value">${firstSeen}</span>
                            </div>
                            <div class="meta-row">
                                <span class="meta-label"><i class="fa-solid fa-list"></i> Locations</span>
                                <span class="meta-value">${locationCount} location${locationCount !== 1 ? 's' : ''}</span>
                            </div>
                            <div class="meta-row distance-home-row">
                                <span class="meta-label"><i class="fa-solid fa-house"></i> Distance from home</span>
                                <span class="meta-value distance-home-value" data-device-id="${deviceId}">Loading...</span>
                            </div>
                            <div class="meta-row battery-row" style="display:none">
                                <span class="meta-label"><i class="fa-solid fa-battery-half"></i> Battery</span>
                                <span class="meta-value battery-value" data-device-id="${deviceId}"></span>
                            </div>
                            ${device.latest_location ? `
                                <div class="meta-row">
                                    <span class="meta-label"><i class="fa-solid fa-location-dot"></i> Latest location</span>
                                    <span class="meta-value">${this.escapeHtml(device.latest_location)}</span>
                                </div>
                            ` : ''}
                            ${device.latest_coordinates ? `
                                <div class="meta-row">
                                    <span class="meta-label"><i class="fa-solid fa-globe"></i> Coordinates</span>
                                    <span class="meta-value">${this.escapeHtml(device.latest_coordinates)}</span>
                                </div>
                            ` : ''}
        `;

        return `
            <div class="device-card">
                <div class="device-header">
                    <div class="device-info">
                        <div class="device-title-row">
                            <div class="device-name">${this.escapeHtml(deviceName || 'Unknown Device')}</div>
                            <div class="device-status ${device.last_seen ? 'active' : 'inactive'}">
                                ${device.last_seen ? 'Active' : 'Inactive'}
                            </div>
                        </div>
                        <div class="device-meta-table" data-device-id="${deviceId}">
                            ${metaRows}
                        </div>
                        <button class="view-history-btn" data-route="/device/${encodeURIComponent(deviceId)}">
                            View Location History
                        </button>
                    </div>
                </div>
            </div>
        `;
    }
    
    
    bindEvents() {
        // Fetch latest locations and update distance/battery for all devices
        this.fetchAndDisplayDeviceInfo();

        // Initialize search functionality
        this.initializeSearch();
    }

    async fetchAndDisplayDeviceInfo() {
        try {
            const latestLocations = await this.apiService.request('/locations/latest');

            // Build a lookup by device_name
            const locationMap = {};
            for (const loc of latestLocations) {
                locationMap[loc.device_name] = loc;
            }

            for (const device of this.devices) {
                const deviceId = device.device_name;
                const latestLocation = locationMap[deviceId];

                // Update distance from home
                if (latestLocation && latestLocation.distance_from_home_km !== null && latestLocation.distance_from_home_km !== undefined) {
                    const km = latestLocation.distance_from_home_km;
                    const formatted = km < 0.1 ? 'Home' : km < 1 ? `${Math.round(km * 1000)}m` : `${km.toFixed(1)}km`;
                    this.updateDistanceHomeValue(deviceId, formatted);
                } else {
                    this.updateDistanceHomeValue(deviceId, 'Unknown');
                }

                // Update battery status (only if available)
                if (latestLocation && latestLocation.battery_status) {
                    this.updateBatteryValue(deviceId, latestLocation.battery_status);
                }
            }
        } catch (error) {
            console.error('Error fetching device info:', error);
            // Set all to unknown on error
            for (const device of this.devices) {
                this.updateDistanceHomeValue(device.device_name, 'Unavailable');
            }
        }
    }

    updateDistanceHomeValue(deviceId, value) {
        const element = document.querySelector(`.distance-home-value[data-device-id="${deviceId}"]`);
        if (element) {
            element.textContent = value;
            element.classList.remove('calculating', 'error', 'success');
            if (value === 'Loading...') {
                element.classList.add('calculating');
            } else if (value === 'Unknown' || value === 'Unavailable') {
                element.classList.add('error');
            } else {
                element.classList.add('success');
            }
        }
    }

    updateBatteryValue(deviceId, batteryStatus) {
        const element = document.querySelector(`.battery-value[data-device-id="${deviceId}"]`);
        if (!element) return;

        // Show the battery row
        const row = element.closest('.battery-row');
        if (row) row.style.display = '';

        // Set icon and color based on status
        const label = row ? row.querySelector('.meta-label') : null;
        const status = batteryStatus.toLowerCase();

        if (status === 'normal' || status === 'full') {
            element.textContent = 'Normal';
            element.classList.add('success');
            if (label) label.innerHTML = '<i class="fa-solid fa-battery-full" style="color: #30A46C"></i> Battery';
        } else if (status === 'low') {
            element.textContent = 'Low';
            element.style.color = '#FF9500';
            if (label) label.innerHTML = '<i class="fa-solid fa-battery-quarter" style="color: #FF9500"></i> Battery';
        } else if (status === 'critical') {
            element.textContent = 'Critical';
            element.classList.add('error');
            if (label) label.innerHTML = '<i class="fa-solid fa-battery-empty" style="color: #FF3B30"></i> Battery';
        } else {
            element.textContent = batteryStatus;
        }
    }
    
    initializeSearch() {
        // Show search container only on devices view
        const searchContainer = document.getElementById('search-container');
        if (searchContainer) {
            searchContainer.style.display = 'flex';
            
            // Get search elements
            const searchInput = document.getElementById('device-search');
            const clearButton = document.getElementById('clear-search');
            
            if (searchInput && clearButton) {
                // Handle search input
                searchInput.addEventListener('input', (e) => {
                    this.handleSearch(e.target.value);
                });
                
                // Handle clear button
                clearButton.addEventListener('click', () => {
                    searchInput.value = '';
                    this.handleSearch('');
                    searchInput.focus();
                });
                
                // Handle escape key to clear search and Enter to navigate
                searchInput.addEventListener('keydown', (e) => {
                    if (e.key === 'Escape') {
                        searchInput.value = '';
                        this.handleSearch('');
                    } else if (e.key === 'Enter') {
                        this.handleEnterNavigation();
                    }
                });
                
                // Focus search on cmd/ctrl + f
                document.addEventListener('keydown', (e) => {
                    if ((e.metaKey || e.ctrlKey) && e.key === 'f') {
                        e.preventDefault();
                        searchInput.focus();
                    }
                });
            }
        }
    }
    
    handleSearch(query) {
        const searchInput = document.getElementById('device-search');
        const clearButton = document.getElementById('clear-search');
        
        // Show/hide clear button
        if (clearButton) {
            clearButton.style.display = query ? 'block' : 'none';
        }
        
        // Get all device cards
        const deviceCards = document.querySelectorAll('.device-card');
        let visibleCount = 0;
        let lastVisibleCard = null;
        
        // Filter devices
        deviceCards.forEach(card => {
            const deviceHeader = card.querySelector('.device-header');
            const deviceName = card.querySelector('.device-name');
            
            if (deviceName) {
                const name = deviceName.textContent.trim();
                const matches = this.matchesSearch(name, query);
                
                if (matches) {
                    card.classList.remove('filtered-out');
                    card.classList.add('search-highlight');
                    visibleCount++;
                    lastVisibleCard = card;
                } else {
                    card.classList.add('filtered-out');
                    card.classList.remove('search-highlight');
                }
            }
        });
        
        // Store the last visible card for Enter navigation
        this.lastVisibleCard = lastVisibleCard;
        this.visibleCount = visibleCount;
        
        // If no results, show a message (optional enhancement)
        this.updateSearchResults(visibleCount, query);
    }
    
    handleEnterNavigation() {
        const searchInput = document.getElementById('device-search');
        const query = searchInput ? searchInput.value.trim() : '';
        
        // Only navigate if there's a search query and exactly one visible result
        if (query && this.visibleCount === 1 && this.lastVisibleCard) {
            const deviceId = this.getDeviceIdFromCard(this.lastVisibleCard);
            if (deviceId) {
                this.router.navigate(`/device/${deviceId}`);
            }
        }
    }
    
    matchesSearch(deviceName, query) {
        if (!query.trim()) return true;
        
        const normalizedQuery = query.toLowerCase().trim();
        const normalizedName = deviceName.toLowerCase();
        
        // Check for exact substring match
        if (normalizedName.includes(normalizedQuery)) {
            return true;
        }
        
        // Check for fuzzy matching (matches individual words)
        const queryWords = normalizedQuery.split(/\s+/);
        const nameWords = normalizedName.split(/\s+/);
        
        return queryWords.every(queryWord => 
            nameWords.some(nameWord => 
                nameWord.includes(queryWord) || queryWord.includes(nameWord)
            )
        );
    }
    
    getDeviceIdFromCard(card) {
        // Try to find device ID from the card's data attributes or content
        const metaTable = card.querySelector('.device-meta-table');
        if (metaTable) {
            return metaTable.dataset.deviceId;
        }
        
        // Fallback: look for view history button
        const viewHistoryBtn = card.querySelector('.view-history-btn');
        if (viewHistoryBtn) {
            const route = viewHistoryBtn.dataset.route;
            const match = route.match(/\/device\/(\d+)/);
            if (match) {
                return match[1];
            }
        }
        
        return null;
    }
    
    updateSearchResults(count, query) {
        // Optional: Add a results counter or no results message
        // This could be implemented as a subtle indicator near the search field
        if (query.trim() && count === 0) {
            // Could show "No devices found" message
            console.log('No devices found for query:', query);
        }
    }
    
    escapeHtml(text) {
        if (typeof text !== 'string') return text;
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}