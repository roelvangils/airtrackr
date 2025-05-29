export class DeviceDetailView {
    constructor(device, locations, router, apiService) {
        this.device = device;
        this.locations = locations;
        this.router = router;
        this.apiService = apiService;
    }
    
    render() {
        return `
            <div class="timeline-container">
                <button class="back-btn" data-route="/">
                    ← Back to Dashboard
                </button>
                
                <div class="timeline-header">
                    <h2>${this.escapeHtml(this.device.name || this.device.device_name || 'Unknown Device')}</h2>
                    <div class="timeline-meta">
                        ${this.locations.length} location${this.locations.length !== 1 ? 's' : ''} recorded
                        ${this.device.first_seen ? `• First seen ${new Date(this.device.first_seen).toLocaleDateString()}` : ''}
                    </div>
                </div>
                
                ${this.renderTimeline()}
                
                ${this.renderDangerZone()}
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
        
        // Sort locations by timestamp (most recent first)
        const sortedLocations = [...this.locations].sort((a, b) => 
            new Date(b.timestamp) - new Date(a.timestamp)
        );
        
        // Group consecutive identical locations
        const groupedLocations = this.groupConsecutiveLocations(sortedLocations);
        
        return `
            <div class="timeline">
                ${groupedLocations.map(group => this.renderTimelineGroup(group)).join('')}
            </div>
        `;
    }

    groupConsecutiveLocations(locations) {
        if (locations.length === 0) return [];
        
        const groups = [];
        let currentGroup = {
            locations: [locations[0]],
            cleanLocation: this.processLocationText(locations[0].location_text),
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
            const currentCleanLocation = this.processLocationText(current.location_text);
            
            // Check if this location should be grouped with the previous one
            if (this.shouldGroupLocations(currentGroup.cleanLocation, currentCleanLocation)) {
                currentGroup.locations.push(current);
                currentGroup.endTime = new Date(current.timestamp);
                
                // Use coordinates from the most recent entry that has them
                if (current.latitude && current.longitude) {
                    currentGroup.coordinates = {
                        latitude: current.latitude,
                        longitude: current.longitude
                    };
                }
            } else {
                // Start a new group
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
        
        // Don't forget the last group
        groups.push(currentGroup);
        
        return groups;
    }
    
    renderDangerZone() {
        const deviceId = this.device.id || this.device.device_id;
        const deviceName = this.device.name || this.device.device_name || 'Unknown Device';
        
        return `
            <div class="danger-zone">
                <div class="danger-zone-header">
                    <h3>􀇾 Danger Zone</h3>
                    <p>Irreversible and destructive actions</p>
                </div>
                <div class="danger-zone-content">
                    <div class="danger-action">
                        <div class="danger-action-info">
                            <h4>Delete this device</h4>
                            <p>Permanently remove "${this.escapeHtml(deviceName)}" and all its location history. This action cannot be undone.</p>
                        </div>
                        <button class="danger-btn delete-device-btn" data-device-id="${deviceId}" data-device-name="${this.escapeHtml(deviceName)}">
                            􀈑 Delete Device
                        </button>
                    </div>
                </div>
            </div>
        `;
    }

    shouldGroupLocations(location1, location2) {
        // Group identical locations
        if (location1 === location2) return true;
        
        // Group all "Location not available" entries
        if (location1 === 'Location not available' && location2 === 'Location not available') return true;
        
        return false;
    }

    renderTimelineGroup(group) {
        const isMultipleEntries = group.locations.length > 1;
        const startDate = group.startTime;
        const endDate = group.endTime;
        
        if (isMultipleEntries) {
            // Show duration format for grouped entries
            const duration = this.calculateDuration(startDate, endDate);
            const startFormatted = startDate.toLocaleString();
            const endFormatted = endDate.toLocaleString();
            
            return `
                <div class="timeline-item timeline-group">
                    <div class="timeline-content">
                        <div class="timeline-location">
                            ${this.escapeHtml(group.cleanLocation)}
                        </div>
                        <div class="timeline-duration">
                            From ${endFormatted} to ${startFormatted} (${duration})
                        </div>
                        ${group.coordinates.latitude && group.coordinates.longitude ? `
                            <div class="timeline-coords">
                                <span class="coords-text">${group.coordinates.latitude.toFixed(6)}, ${group.coordinates.longitude.toFixed(6)}</span>
                                <div class="map-links">
                                    <a href="https://maps.google.com/maps?q=${group.coordinates.latitude},${group.coordinates.longitude}" target="_blank" class="map-link google-maps">
                                        􀋒 Google Maps
                                    </a>
                                    <a href="https://maps.apple.com/?q=${group.coordinates.latitude},${group.coordinates.longitude}" target="_blank" class="map-link apple-maps">
                                        􀙯 Apple Maps
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
                                􀈑 Delete All
                            </button>
                        </div>
                    </div>
                </div>
            `;
        } else {
            // Single entry - use the original format
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
            } else {
                return `${diffDays} day${diffDays !== 1 ? 's' : ''}`;
            }
        } else if (diffHours > 0) {
            const remainingMinutes = diffMinutes % 60;
            if (remainingMinutes > 0) {
                return `${diffHours} hour${diffHours !== 1 ? 's' : ''}, ${remainingMinutes} minute${remainingMinutes !== 1 ? 's' : ''}`;
            } else {
                return `${diffHours} hour${diffHours !== 1 ? 's' : ''}`;
            }
        } else if (diffMinutes > 0) {
            return `${diffMinutes} minute${diffMinutes !== 1 ? 's' : ''}`;
        } else {
            return 'Less than a minute';
        }
    }
    
    renderTimelineItem(location) {
        const date = new Date(location.timestamp);
        const timeAgo = this.getTimeAgo(date);
        const fullDate = date.toLocaleString();
        
        // Process location text to filter out invalid entries and clean up formatting
        const cleanLocation = this.processLocationText(location.location_text);
        
        return `
            <div class="timeline-item">
                <div class="timeline-content">
                    <div class="timeline-date" title="${fullDate}">
                        ${timeAgo}
                    </div>
                    <div class="timeline-location">
                        ${this.escapeHtml(cleanLocation)}
                    </div>
                    ${location.latitude && location.longitude ? `
                        <div class="timeline-coords">
                            <span class="coords-text">${location.latitude.toFixed(6)}, ${location.longitude.toFixed(6)}</span>
                            <div class="map-links">
                                <a href="https://maps.google.com/maps?q=${location.latitude},${location.longitude}" target="_blank" class="map-link google-maps">
                                    􀋒 Google Maps
                                </a>
                                <a href="https://maps.apple.com/?q=${location.latitude},${location.longitude}" target="_blank" class="map-link apple-maps">
                                    􀙯 Apple Maps
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
                            􀈑 Delete
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
        
        // Filter out invalid location patterns
        const invalidPatterns = [
            /^lastseen\d+\w*ago$/i,  // Matches "Lastseen1hrago", "Lastseen2hrago", etc.
            /^last\s*seen/i,         // Matches "last seen" variations
            /^seen\s*\d+/i,          // Matches "seen 1hr ago" variations
            /^\d+\s*(hr|min|hour|minute)s?\s*ago$/i // Time ago patterns
        ];
        
        for (const pattern of invalidPatterns) {
            if (pattern.test(locationText.trim())) {
                return 'Location not available';
            }
        }
        
        // Clean up and format addresses
        let cleaned = locationText.trim();
        
        // Remove trailing characters like "-1", "-", ".", ":"
        cleaned = cleaned.replace(/[-:.]\d*$/, '');
        cleaned = cleaned.replace(/[-:.]$/, '');
        
        // Clean up common OCR artifacts
        cleaned = cleaned.replace(/\s+/g, ' '); // Multiple spaces to single space
        cleaned = cleaned.replace(/[^\w\s,.-]/g, ''); // Remove unusual characters but keep punctuation
        
        // Format known location patterns
        if (cleaned.toLowerCase() === 'home') {
            return 'Home';
        }
        
        // Capitalize first letter of each word for addresses
        cleaned = cleaned.replace(/\b\w/g, l => l.toUpperCase());
        
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
    }
    
    async deleteLocation(locationId) {
        try {
            console.log('Attempting to delete location:', locationId);
            const result = await this.apiService.deleteLocation(locationId);
            console.log('Delete result:', result);
            
            // Remove from local array
            this.locations = this.locations.filter(loc => loc.id != locationId);
            // Re-render the view
            const contentElement = document.querySelector('#main-content');
            if (contentElement) {
                contentElement.innerHTML = this.render();
                this.bindEvents();
            }
            
            console.log('Location deleted successfully');
        } catch (error) {
            console.error('Failed to delete location:', error);
            alert(`Failed to delete location: ${error.message}`);
        }
    }
    
    async deleteLocationGroup(locationIds) {
        try {
            console.log('Attempting to delete location group:', locationIds);
            
            // Get unique location IDs only
            const uniqueLocationIds = [...new Set(locationIds)];
            console.log('Unique location IDs to delete:', uniqueLocationIds);
            
            // Delete each location sequentially to avoid race conditions
            const results = [];
            for (const id of uniqueLocationIds) {
                try {
                    const result = await this.apiService.deleteLocation(id);
                    results.push({ id, success: true, result });
                } catch (error) {
                    console.warn(`Failed to delete location ${id}:`, error);
                    results.push({ id, success: false, error });
                }
            }
            
            console.log('Delete results:', results);
            
            // Remove successfully deleted locations from local array
            const successfullyDeleted = results.filter(r => r.success).map(r => r.id);
            this.locations = this.locations.filter(loc => !successfullyDeleted.includes(loc.id.toString()));
            
            // Re-render the view
            const contentElement = document.querySelector('#main-content');
            if (contentElement) {
                contentElement.innerHTML = this.render();
                this.bindEvents();
            }
            
            const successCount = successfullyDeleted.length;
            const totalCount = uniqueLocationIds.length;
            console.log(`Successfully deleted ${successCount}/${totalCount} locations`);
            
        } catch (error) {
            console.error('Failed to delete location group:', error);
            alert(`Failed to delete location group: ${error.message}`);
        }
    }
    
    async showDeleteDeviceConfirmation(deviceId, deviceName) {
        // Create a more sophisticated confirmation modal
        const confirmed = await this.showConfirmationModal(
            'Delete Device',
            `Are you absolutely sure you want to delete "${deviceName}"?`,
            'This action will permanently remove the device and all its location history. This cannot be undone.',
            'Delete Device',
            'danger'
        );
        
        if (confirmed) {
            // Show second confirmation
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
            // Create modal backdrop
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
            
            // Handle typing confirmation
            if (requireTyping && input) {
                input.addEventListener('input', () => {
                    confirmBtn.disabled = input.value.trim() !== expectedText;
                });
                input.focus();
            }
            
            // Handle buttons
            cancelBtn.addEventListener('click', () => {
                document.body.removeChild(modal);
                resolve(false);
            });
            
            confirmBtn.addEventListener('click', () => {
                if (requireTyping && input.value.trim() !== expectedText) {
                    return;
                }
                document.body.removeChild(modal);
                resolve(true);
            });
            
            // Handle escape key
            modal.addEventListener('keydown', (e) => {
                if (e.key === 'Escape') {
                    document.body.removeChild(modal);
                    resolve(false);
                }
            });
            
            // Close on backdrop click
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
            console.log('Attempting to delete device:', deviceId, deviceName);
            const result = await this.apiService.deleteDevice(deviceId);
            console.log('Delete device result:', result);
            
            // Show success message
            alert(`Device "${deviceName}" has been successfully deleted.`);
            
            // Navigate back to devices list
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
        
        if (diffMins < 1) {
            return 'Just now';
        } else if (diffMins < 60) {
            return `${diffMins} minute${diffMins !== 1 ? 's' : ''} ago`;
        } else if (diffHours < 24) {
            return `${diffHours} hour${diffHours !== 1 ? 's' : ''} ago`;
        } else if (diffDays < 7) {
            return `${diffDays} day${diffDays !== 1 ? 's' : ''} ago`;
        } else {
            return date.toLocaleDateString();
        }
    }
    
    escapeHtml(text) {
        if (typeof text !== 'string') return text;
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}