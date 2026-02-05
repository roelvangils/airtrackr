export class ApiService {
    constructor() {
        // Dynamically determine API URL based on current location
        const hostname = window.location.hostname;
        const isLocalhost = hostname === 'localhost' || hostname === '127.0.0.1';

        this.baseUrl = isLocalhost
            ? 'http://localhost:8001/api/v1'
            : `http://${hostname}:8001/api/v1`;

        console.log('API endpoint:', this.baseUrl);
    }

    async request(endpoint, options = {}) {
        const url = `${this.baseUrl}${endpoint}`;

        try {
            const response = await fetch(url, {
                headers: {
                    'Content-Type': 'application/json',
                    ...options.headers
                },
                ...options
            });

            if (!response.ok) {
                const errorText = await response.text();
                throw new Error(`HTTP error! status: ${response.status}, message: ${errorText}`);
            }

            return await response.json();
        } catch (error) {
            console.error('API request failed:', error);
            throw error;
        }
    }

    async getDevices(deviceType = null) {
        let url = '/devices?limit=500';
        if (deviceType) url += `&device_type=${deviceType}`;
        const response = await this.request(url);
        // Unwrap paginated response for backward compat with views
        return response.items || response;
    }

    async getDeviceCounts() {
        return this.request('/devices/counts');
    }

    async getDevice(deviceName) {
        return this.request(`/devices/${encodeURIComponent(deviceName)}`);
    }

    async getDeviceLocations(deviceName, limit = 50, offset = 0, startDate = null, endDate = null) {
        let url = `/devices/${encodeURIComponent(deviceName)}/history?limit=${limit}&offset=${offset}`;
        if (startDate) url += `&start_date=${startDate}`;
        if (endDate) url += `&end_date=${endDate}`;
        return this.request(url);
    }

    async getLocations(limit = 100) {
        return this.request(`/locations/search?limit=${limit}`);
    }

    async deleteLocation(locationId) {
        return this.request(`/locations/${locationId}`, {
            method: 'DELETE'
        });
    }

    async deleteDevice(deviceId) {
        return this.request(`/devices/${encodeURIComponent(deviceId)}`, {
            method: 'DELETE'
        });
    }

    async getZones() {
        return this.request('/zones');
    }

    async createZone(zone) {
        return this.request('/zones', {
            method: 'POST',
            body: JSON.stringify(zone)
        });
    }

    async deleteZone(zoneId) {
        return this.request(`/zones/${zoneId}`, {
            method: 'DELETE'
        });
    }

    getExportUrl(deviceName, format = 'csv', startDate = null, endDate = null) {
        let url = `${this.baseUrl}/devices/${encodeURIComponent(deviceName)}/export?format=${format}`;
        if (startDate) url += `&start_date=${startDate}`;
        if (endDate) url += `&end_date=${endDate}`;
        return url;
    }
}
