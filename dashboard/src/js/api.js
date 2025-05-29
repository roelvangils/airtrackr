export class ApiService {
    constructor() {
        this.baseUrl = 'http://localhost:8000';
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
    
    async getDevice(deviceId) {
        return this.request(`/devices/${deviceId}`);
    }
    
    async getDeviceLocations(deviceId, limit = 50) {
        return this.request(`/devices/${deviceId}/locations?limit=${limit}`);
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