import { Router } from './router.js';
import { ApiService } from './api.js';
import { DevicesView } from './views/devices.js';
import { DeviceDetailView } from './views/device-detail.js';

class App {
    constructor() {
        this.router = new Router();
        this.apiService = new ApiService();
        this.mainContent = document.getElementById('main-content');
        
        this.setupRoutes();
    }
    
    setupRoutes() {
        this.router.addRoute('/', () => this.showDevicesView());
        this.router.addRoute('/device/:id', (params) => this.showDeviceDetail(params.id));
        
        // Start routing
        this.router.start();
    }
    
    async showDevicesView() {
        try {
            // Show search container
            this.showSearchField();
            
            const devices = await this.apiService.getDevices();
            const devicesView = new DevicesView(devices, this.router, this.apiService);
            this.mainContent.innerHTML = devicesView.render();
            devicesView.bindEvents();
        } catch (error) {
            this.showError('Failed to load devices: ' + error.message);
        }
    }
    
    async showDeviceDetail(deviceId) {
        try {
            // Hide search container on detail view
            this.hideSearchField();
            
            const [device, locations] = await Promise.all([
                this.apiService.getDevice(deviceId),
                this.apiService.getDeviceLocations(deviceId)
            ]);
            
            const detailView = new DeviceDetailView(device, locations.locations, this.router, this.apiService);
            this.mainContent.innerHTML = detailView.render();
            detailView.bindEvents();
        } catch (error) {
            this.showError('Failed to load device details: ' + error.message);
        }
    }
    
    showError(message) {
        this.mainContent.innerHTML = `
            <div class="error">
                <strong>Error:</strong> ${message}
            </div>
        `;
    }
    
    showSearchField() {
        const searchContainer = document.getElementById('search-container');
        if (searchContainer) {
            searchContainer.style.display = 'flex';
        }
    }
    
    hideSearchField() {
        const searchContainer = document.getElementById('search-container');
        if (searchContainer) {
            searchContainer.style.display = 'none';
            
            // Clear search when hiding
            const searchInput = document.getElementById('device-search');
            const clearButton = document.getElementById('clear-search');
            if (searchInput) {
                searchInput.value = '';
            }
            if (clearButton) {
                clearButton.style.display = 'none';
            }
        }
    }
}

export function initializeApp() {
    new App();
}