export class ApiService {
    constructor() {
        // Dynamically determine API URL based on current location
        // If accessing from localhost, use localhost
        // If accessing from network, use the same hostname
        const hostname = window.location.hostname;
        const isLocalhost = hostname === 'localhost' || hostname === '127.0.0.1';
        
        // Use the same hostname as the dashboard, but with API port
        this.baseUrl = isLocalhost 
            ? 'http://localhost:8001'
            : `http://${hostname}:8001`;
            
        console.log('API endpoint:', this.baseUrl);
    }
    
    async request(endpoint, options = {}) {
        const url = `${this.baseUrl}${endpoint}`;
        
        try {
            console.log(`Making ${options.method || 'GET'} request to: ${url}`);
            
            const response = await fetch(url, {
                headers: {
                    'Content-Type': 'application/json',
                    ...options.headers
                },
                ...options
            });
            
            console.log(`Response status: ${response.status}`);
            
            if (!response.ok) {
                const errorText = await response.text();
                console.error('API error response:', errorText);
                throw new Error(`HTTP error! status: ${response.status}, message: ${errorText}`);
            }
            
            const result = await response.json();
            console.log('API response:', result);
            return result;
        } catch (error) {
            console.error('API request failed:', error);
            throw error;
        }
    }
    
    async getDevices() {
        return this.request('/devices');
    }
    
    async getDevice(deviceName) {
        return this.request(`/devices/${encodeURIComponent(deviceName)}`);
    }
    
    async getDeviceLocations(deviceName, limit = 50) {
        return this.request(`/devices/${encodeURIComponent(deviceName)}/history?limit=${limit}`);
    }
    
    async getLocations(limit = 100) {
        return this.request(`/locations?limit=${limit}`);
    }
    
    async deleteLocation(locationId) {
        return this.request(`/locations/${locationId}`, {
            method: 'DELETE'
        });
    }
    
    async deleteDevice(deviceId) {
        return this.request(`/devices/${deviceId}`, {
            method: 'DELETE'
        });
    }
}