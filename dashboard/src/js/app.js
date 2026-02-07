import { Router } from './router.js';
import { ApiService } from './api.js';
import { DevicesView } from './views/devices.js';
import { DeviceDetailView } from './views/device-detail.js';

class App {
    constructor() {
        this.router = new Router();
        this.apiService = new ApiService();
        this.mainContent = document.getElementById('main-content');
        this.currentFilter = 'all';
        this.pollingInterval = null;
        this.lastDevicesJson = null;

        this.setupRoutes();
    }

    setupRoutes() {
        this.router.addRoute('/', () => this.showDevicesView());
        this.router.addRoute('/device/:id', (params) => this.showDeviceDetail(params.id));

        // Start routing
        this.router.start();
    }

    async showDevicesView(deviceType = null) {
        try {
            // Show search container and filter tabs
            this.showSearchField();
            this.showFilterTabs();

            // Update counts
            await this.updateFilterCounts();

            // Fetch devices (optionally filtered by type)
            const devices = await this.apiService.getDevices(deviceType);
            const devicesView = new DevicesView(devices, this.router, this.apiService);
            this.mainContent.innerHTML = devicesView.render();
            devicesView.bindEvents();

            // Setup filter tab event listeners
            this.bindFilterTabs();

            // Start polling (60s interval, only re-render on change)
            this.startPolling(deviceType);
        } catch (error) {
            this.showError('Failed to load devices: ' + error.message);
        }
    }

    startPolling(deviceType = null) {
        this.stopPolling();
        this.pollingInterval = setInterval(async () => {
            try {
                const devices = await this.apiService.getDevices(deviceType);
                const newJson = JSON.stringify(devices);
                if (newJson !== this.lastDevicesJson) {
                    this.lastDevicesJson = newJson;
                    const devicesView = new DevicesView(devices, this.router, this.apiService);
                    this.mainContent.innerHTML = devicesView.render();
                    devicesView.bindEvents();
                    this.bindFilterTabs();
                    await this.updateFilterCounts();
                }
            } catch (error) {
                console.error('Polling error:', error);
            }
        }, 60000);
    }

    stopPolling() {
        if (this.pollingInterval) {
            clearInterval(this.pollingInterval);
            this.pollingInterval = null;
        }
        this.lastDevicesJson = null;
    }

    async updateFilterCounts() {
        try {
            const counts = await this.apiService.getDeviceCounts();

            const countAll = document.getElementById('count-all');
            const countPeople = document.getElementById('count-people');
            const countDevices = document.getElementById('count-devices');
            const countItems = document.getElementById('count-items');

            if (countAll) countAll.textContent = `(${counts.total})`;
            if (countPeople) countPeople.textContent = `(${counts.people})`;
            if (countDevices) countDevices.textContent = `(${counts.devices})`;
            if (countItems) countItems.textContent = `(${counts.items})`;
        } catch (error) {
            console.error('Failed to update filter counts:', error);
        }
    }

    bindFilterTabs() {
        const filterTabs = document.querySelectorAll('.filter-tab');

        filterTabs.forEach(tab => {
            tab.addEventListener('click', (e) => {
                const filter = tab.dataset.filter;
                this.handleFilterChange(filter);
            });
        });
    }

    handleFilterChange(filter) {
        // Update active state
        document.querySelectorAll('.filter-tab').forEach(tab => {
            tab.classList.remove('active');
        });
        document.querySelector(`.filter-tab[data-filter="${filter}"]`)?.classList.add('active');

        // Store current filter
        this.currentFilter = filter;

        // Reload devices with filter
        const deviceType = filter === 'all' ? null : filter;
        this.showDevicesView(deviceType);
    }

    async showDeviceDetail(deviceId) {
        try {
            // Stop polling when navigating away from devices list
            this.stopPolling();

            // Hide search container and filter tabs on detail view
            this.hideSearchField();
            this.hideFilterTabs();

            // Decode the device name from the URL parameter
            const deviceName = decodeURIComponent(deviceId);

            const [device, locations, stats] = await Promise.all([
                this.apiService.getDevice(deviceName),
                this.apiService.getDeviceLocations(deviceName),
                this.apiService.getDeviceStatsSummary(deviceName).catch(() => null),
            ]);

            const detailView = new DeviceDetailView(device, locations.locations, locations.total, this.router, this.apiService, stats);
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

            const searchInput = document.getElementById('device-search');
            const clearButton = document.getElementById('clear-search');
            if (searchInput) searchInput.value = '';
            if (clearButton) clearButton.style.display = 'none';
        }
    }

    showFilterTabs() {
        const filterTabs = document.getElementById('filter-tabs');
        if (filterTabs) {
            filterTabs.style.display = 'flex';
        }
    }

    hideFilterTabs() {
        const filterTabs = document.getElementById('filter-tabs');
        if (filterTabs) {
            filterTabs.style.display = 'none';
        }
    }
}

export function initializeApp() {
    new App();
}
