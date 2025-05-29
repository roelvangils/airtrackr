export class DevicesView {
    constructor(devices, router, apiService) {
        this.devices = devices;
        this.router = router;
        this.apiService = apiService;
        this.userLocation = null;
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
        const deviceId = device.id || device.device_id;
        const deviceName = device.name || device.device_name;
        const lastSeen = device.last_seen ? new Date(device.last_seen).toLocaleString() : 'Never';
        const locationCount = device.location_count || 0;
        const firstSeen = device.first_seen ? new Date(device.first_seen).toLocaleString() : 'Unknown';
        
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
                            <div class="meta-row">
                                <span class="meta-label">􀐫 Last seen</span>
                                <span class="meta-value">${lastSeen}</span>
                            </div>
                            <div class="meta-row">
                                <span class="meta-label">􀉉 First seen</span>
                                <span class="meta-value">${firstSeen}</span>
                            </div>
                            <div class="meta-row">
                                <span class="meta-label">􀋑 Locations</span>
                                <span class="meta-value">${locationCount} location${locationCount !== 1 ? 's' : ''}</span>
                            </div>
                            <div class="meta-row current-distance-row">
                                <span class="meta-label">􀋰 Current distance</span>
                                <span class="meta-value current-distance-value" data-device-id="${deviceId}">Calculating...</span>
                            </div>
                            ${device.latest_location ? `
                                <div class="meta-row">
                                    <span class="meta-label">􀙯 Latest location</span>
                                    <span class="meta-value">${this.escapeHtml(device.latest_location)}</span>
                                </div>
                            ` : ''}
                            ${device.latest_coordinates ? `
                                <div class="meta-row">
                                    <span class="meta-label">􀋒 Coordinates</span>
                                    <span class="meta-value">${this.escapeHtml(device.latest_coordinates)}</span>
                                </div>
                            ` : ''}
                        </div>
                        <button class="view-history-btn" data-route="/device/${deviceId}">
                            View Location History
                        </button>
                    </div>
                </div>
            </div>
        `;
    }
    
    
    bindEvents() {
        // Initialize geolocation and calculate distances
        this.initializeGeolocation();
        
        // Initialize search functionality
        this.initializeSearch();
    }
    
    async initializeGeolocation() {
        if (!navigator.geolocation) {
            this.updateDistanceValues('Location not supported');
            return;
        }
        
        try {
            const position = await this.getCurrentPosition();
            this.userLocation = {
                latitude: position.coords.latitude,
                longitude: position.coords.longitude
            };
            
            // Calculate distances for all devices
            await this.calculateAllDistances();
            
        } catch (error) {
            console.error('Geolocation error:', error);
            let errorMessage = 'Location unavailable';
            
            if (error.code === error.PERMISSION_DENIED) {
                errorMessage = 'Location denied';
            } else if (error.code === error.POSITION_UNAVAILABLE) {
                errorMessage = 'Position unavailable';
            } else if (error.code === error.TIMEOUT) {
                errorMessage = 'Location timeout';
            }
            
            this.updateDistanceValues(errorMessage);
        }
    }
    
    getCurrentPosition() {
        return new Promise((resolve, reject) => {
            navigator.geolocation.getCurrentPosition(resolve, reject, {
                enableHighAccuracy: true,
                timeout: 10000,
                maximumAge: 300000 // 5 minutes
            });
        });
    }
    
    async calculateAllDistances() {
        for (const device of this.devices) {
            await this.calculateDeviceDistance(device);
        }
    }
    
    async calculateDeviceDistance(device) {
        const deviceId = device.id || device.device_id;
        
        try {
            // Get the latest location for this device
            const response = await this.apiService.getDeviceLocations(deviceId, 1);
            const locations = response.locations;
            
            if (!locations || locations.length === 0) {
                this.updateDistanceValue(deviceId, 'No location data');
                return;
            }
            
            const latestLocation = locations[0];
            
            if (!latestLocation.latitude || !latestLocation.longitude) {
                this.updateDistanceValue(deviceId, 'No coordinates');
                return;
            }
            
            // Calculate distance
            const distance = this.calculateDistance(
                this.userLocation.latitude,
                this.userLocation.longitude,
                latestLocation.latitude,
                latestLocation.longitude
            );
            
            this.updateDistanceValue(deviceId, this.formatDistance(distance));
            
        } catch (error) {
            console.error(`Error calculating distance for device ${deviceId}:`, error);
            this.updateDistanceValue(deviceId, 'Calculation error');
        }
    }
    
    calculateDistance(lat1, lon1, lat2, lon2) {
        // Haversine formula to calculate distance between two points
        const R = 6371; // Radius of the Earth in kilometers
        const dLat = this.toRadians(lat2 - lat1);
        const dLon = this.toRadians(lon2 - lon1);
        
        const a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
                  Math.cos(this.toRadians(lat1)) * Math.cos(this.toRadians(lat2)) *
                  Math.sin(dLon / 2) * Math.sin(dLon / 2);
        
        const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
        const distance = R * c; // Distance in kilometers
        
        return distance * 1000; // Convert to meters
    }
    
    toRadians(degrees) {
        return degrees * (Math.PI / 180);
    }
    
    formatDistance(meters) {
        if (meters < 1000) {
            return `${Math.round(meters)}m`;
        } else if (meters < 10000) {
            return `${(meters / 1000).toFixed(1)}km`;
        } else {
            return `${Math.round(meters / 1000)}km`;
        }
    }
    
    updateDistanceValue(deviceId, value) {
        const element = document.querySelector(`.current-distance-value[data-device-id="${deviceId}"]`);
        if (element) {
            element.textContent = value;
            
            // Add visual feedback for different states
            element.classList.remove('calculating', 'error', 'success');
            if (value === 'Calculating...') {
                element.classList.add('calculating');
            } else if (value.includes('error') || value.includes('unavailable') || value.includes('denied')) {
                element.classList.add('error');
            } else if (value.includes('m') || value.includes('km')) {
                element.classList.add('success');
            }
        }
    }
    
    updateDistanceValues(value) {
        const elements = document.querySelectorAll('.current-distance-value');
        elements.forEach(element => {
            element.textContent = value;
            element.classList.remove('calculating', 'error', 'success');
            element.classList.add('error');
        });
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