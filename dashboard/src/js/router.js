export class Router {
    constructor() {
        this.routes = new Map();
        this.currentRoute = null;
        
        // Listen for navigation events
        window.addEventListener('popstate', () => this.handleRoute());
        
        // Intercept link clicks
        document.addEventListener('click', (e) => {
            if (e.target.matches('[data-route]')) {
                e.preventDefault();
                this.navigate(e.target.getAttribute('data-route'));
            }
        });
    }
    
    addRoute(path, handler) {
        this.routes.set(path, handler);
    }
    
    navigate(path) {
        window.history.pushState({}, '', path);
        this.handleRoute();
    }
    
    start() {
        this.handleRoute();
    }
    
    handleRoute() {
        const path = window.location.pathname;
        
        // Try exact match first
        if (this.routes.has(path)) {
            this.currentRoute = path;
            this.routes.get(path)();
            return;
        }
        
        // Try pattern matching for parameterized routes
        for (const [routePath, handler] of this.routes) {
            const match = this.matchRoute(routePath, path);
            if (match) {
                this.currentRoute = routePath;
                handler(match.params);
                return;
            }
        }
        
        // No route found, default to home
        this.navigate('/');
    }
    
    matchRoute(routePath, actualPath) {
        const routeParts = routePath.split('/');
        const pathParts = actualPath.split('/');
        
        if (routeParts.length !== pathParts.length) {
            return null;
        }
        
        const params = {};
        
        for (let i = 0; i < routeParts.length; i++) {
            if (routeParts[i].startsWith(':')) {
                // Parameter
                const paramName = routeParts[i].slice(1);
                params[paramName] = pathParts[i];
            } else if (routeParts[i] !== pathParts[i]) {
                // No match
                return null;
            }
        }
        
        return { params };
    }
}