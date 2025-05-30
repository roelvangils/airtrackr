(function(){const e=document.createElement("link").relList;if(e&&e.supports&&e.supports("modulepreload"))return;for(const s of document.querySelectorAll('link[rel="modulepreload"]'))i(s);new MutationObserver(s=>{for(const n of s)if(n.type==="childList")for(const a of n.addedNodes)a.tagName==="LINK"&&a.rel==="modulepreload"&&i(a)}).observe(document,{childList:!0,subtree:!0});function t(s){const n={};return s.integrity&&(n.integrity=s.integrity),s.referrerPolicy&&(n.referrerPolicy=s.referrerPolicy),s.crossOrigin==="use-credentials"?n.credentials="include":s.crossOrigin==="anonymous"?n.credentials="omit":n.credentials="same-origin",n}function i(s){if(s.ep)return;s.ep=!0;const n=t(s);fetch(s.href,n)}})();class v{constructor(){this.routes=new Map,this.currentRoute=null,window.addEventListener("popstate",()=>this.handleRoute()),document.addEventListener("click",e=>{e.target.matches("[data-route]")&&(e.preventDefault(),this.navigate(e.target.getAttribute("data-route")))})}addRoute(e,t){this.routes.set(e,t)}navigate(e){window.history.pushState({},"",e),this.handleRoute()}start(){this.handleRoute()}handleRoute(){const e=window.location.pathname;if(this.routes.has(e)){this.currentRoute=e,this.routes.get(e)();return}for(const[t,i]of this.routes){const s=this.matchRoute(t,e);if(s){this.currentRoute=t,i(s.params);return}}this.navigate("/")}matchRoute(e,t){const i=e.split("/"),s=t.split("/");if(i.length!==s.length)return null;const n={};for(let a=0;a<i.length;a++)if(i[a].startsWith(":")){const o=i[a].slice(1);n[o]=s[a]}else if(i[a]!==s[a])return null;return{params:n}}}class p{constructor(){this.baseUrl="http://localhost:8001"}async request(e,t={}){const i=`${this.baseUrl}${e}`;try{console.log(`Making ${t.method||"GET"} request to: ${i}`);const s=await fetch(i,{headers:{"Content-Type":"application/json",...t.headers},...t});if(console.log(`Response status: ${s.status}`),!s.ok){const a=await s.text();throw console.error("API error response:",a),new Error(`HTTP error! status: ${s.status}, message: ${a}`)}const n=await s.json();return console.log("API response:",n),n}catch(s){throw console.error("API request failed:",s),s}}async getDevices(){return this.request("/devices")}async getDevice(e){return this.request(`/devices/${encodeURIComponent(e)}`)}async getDeviceLocations(e,t=50){return this.request(`/devices/${encodeURIComponent(e)}/history?limit=${t}`)}async getLocations(e=100){return this.request(`/locations?limit=${e}`)}async deleteLocation(e){return this.request(`/locations/${e}`,{method:"DELETE"})}async deleteDevice(e){return this.request(`/devices/${e}`,{method:"DELETE"})}}class f{constructor(e,t,i){this.devices=e,this.router=t,this.apiService=i,this.userLocation=null}render(){return!this.devices||this.devices.length===0?`
                <div class="loading">
                    <p>No devices found. Make sure your AirTracker is running and has captured some data.</p>
                </div>
            `:`
            <div class="device-grid">
                ${this.devices.map(e=>this.renderDeviceCard(e)).join("")}
            </div>
        `}renderDeviceCard(e){const t=e.device_name,i=e.device_name,s=e.last_seen?new Date(e.last_seen+"Z"):null,n=e.first_seen?new Date(e.first_seen+"Z"):null,a=s?s.toLocaleString():"Never",o=e.location_count||0,c=n?n.toLocaleString():"Unknown";return`
            <div class="device-card">
                <div class="device-header">
                    <div class="device-info">
                        <div class="device-title-row">
                            <div class="device-name">${this.escapeHtml(i||"Unknown Device")}</div>
                            <div class="device-status ${e.last_seen?"active":"inactive"}">
                                ${e.last_seen?"Active":"Inactive"}
                            </div>
                        </div>
                        <div class="device-meta-table" data-device-id="${t}">
                            <div class="meta-row">
                                <span class="meta-label">􀐫 Last seen</span>
                                <span class="meta-value">${a}</span>
                            </div>
                            <div class="meta-row">
                                <span class="meta-label">􀉉 First seen</span>
                                <span class="meta-value">${c}</span>
                            </div>
                            <div class="meta-row">
                                <span class="meta-label">􀋑 Locations</span>
                                <span class="meta-value">${o} location${o!==1?"s":""}</span>
                            </div>
                            <div class="meta-row current-distance-row">
                                <span class="meta-label">􀋰 Current distance</span>
                                <span class="meta-value current-distance-value" data-device-id="${t}">Calculating...</span>
                            </div>
                            ${e.latest_location?`
                                <div class="meta-row">
                                    <span class="meta-label">􀙯 Latest location</span>
                                    <span class="meta-value">${this.escapeHtml(e.latest_location)}</span>
                                </div>
                            `:""}
                            ${e.latest_coordinates?`
                                <div class="meta-row">
                                    <span class="meta-label">􀋒 Coordinates</span>
                                    <span class="meta-value">${this.escapeHtml(e.latest_coordinates)}</span>
                                </div>
                            `:""}
                        </div>
                        <button class="view-history-btn" data-route="/device/${encodeURIComponent(t)}">
                            View Location History
                        </button>
                    </div>
                </div>
            </div>
        `}bindEvents(){this.initializeGeolocation(),this.initializeSearch()}async initializeGeolocation(){if(!navigator.geolocation){this.updateDistanceValues("Location not supported");return}try{const e=await this.getCurrentPosition();this.userLocation={latitude:e.coords.latitude,longitude:e.coords.longitude},await this.calculateAllDistances()}catch(e){console.error("Geolocation error:",e);let t="Location unavailable";e.code===e.PERMISSION_DENIED?t="Location denied":e.code===e.POSITION_UNAVAILABLE?t="Position unavailable":e.code===e.TIMEOUT&&(t="Location timeout"),this.updateDistanceValues(t)}}getCurrentPosition(){return new Promise((e,t)=>{navigator.geolocation.getCurrentPosition(e,t,{enableHighAccuracy:!0,timeout:1e4,maximumAge:3e5})})}async calculateAllDistances(){for(const e of this.devices)await this.calculateDeviceDistance(e)}async calculateDeviceDistance(e){const t=e.device_name;try{const s=(await this.apiService.getDeviceLocations(t,1)).locations;if(!s||s.length===0){this.updateDistanceValue(t,"No location data");return}const n=s[0];if(!n.latitude||!n.longitude){this.updateDistanceValue(t,"No coordinates");return}const a=this.calculateDistance(this.userLocation.latitude,this.userLocation.longitude,n.latitude,n.longitude);this.updateDistanceValue(t,this.formatDistance(a))}catch(i){console.error(`Error calculating distance for device ${t}:`,i),this.updateDistanceValue(t,"Calculation error")}}calculateDistance(e,t,i,s){const a=this.toRadians(i-e),o=this.toRadians(s-t),c=Math.sin(a/2)*Math.sin(a/2)+Math.cos(this.toRadians(e))*Math.cos(this.toRadians(i))*Math.sin(o/2)*Math.sin(o/2);return 6371*(2*Math.atan2(Math.sqrt(c),Math.sqrt(1-c)))*1e3}toRadians(e){return e*(Math.PI/180)}formatDistance(e){return e<1e3?`${Math.round(e)}m`:e<1e4?`${(e/1e3).toFixed(1)}km`:`${Math.round(e/1e3)}km`}updateDistanceValue(e,t){const i=document.querySelector(`.current-distance-value[data-device-id="${e}"]`);i&&(i.textContent=t,i.classList.remove("calculating","error","success"),t==="Calculating..."?i.classList.add("calculating"):t.includes("error")||t.includes("unavailable")||t.includes("denied")?i.classList.add("error"):(t.includes("m")||t.includes("km"))&&i.classList.add("success"))}updateDistanceValues(e){document.querySelectorAll(".current-distance-value").forEach(i=>{i.textContent=e,i.classList.remove("calculating","error","success"),i.classList.add("error")})}initializeSearch(){const e=document.getElementById("search-container");if(e){e.style.display="flex";const t=document.getElementById("device-search"),i=document.getElementById("clear-search");t&&i&&(t.addEventListener("input",s=>{this.handleSearch(s.target.value)}),i.addEventListener("click",()=>{t.value="",this.handleSearch(""),t.focus()}),t.addEventListener("keydown",s=>{s.key==="Escape"?(t.value="",this.handleSearch("")):s.key==="Enter"&&this.handleEnterNavigation()}),document.addEventListener("keydown",s=>{(s.metaKey||s.ctrlKey)&&s.key==="f"&&(s.preventDefault(),t.focus())}))}}handleSearch(e){document.getElementById("device-search");const t=document.getElementById("clear-search");t&&(t.style.display=e?"block":"none");const i=document.querySelectorAll(".device-card");let s=0,n=null;i.forEach(a=>{a.querySelector(".device-header");const o=a.querySelector(".device-name");if(o){const c=o.textContent.trim();this.matchesSearch(c,e)?(a.classList.remove("filtered-out"),a.classList.add("search-highlight"),s++,n=a):(a.classList.add("filtered-out"),a.classList.remove("search-highlight"))}}),this.lastVisibleCard=n,this.visibleCount=s,this.updateSearchResults(s,e)}handleEnterNavigation(){const e=document.getElementById("device-search");if((e?e.value.trim():"")&&this.visibleCount===1&&this.lastVisibleCard){const i=this.getDeviceIdFromCard(this.lastVisibleCard);i&&this.router.navigate(`/device/${i}`)}}matchesSearch(e,t){if(!t.trim())return!0;const i=t.toLowerCase().trim(),s=e.toLowerCase();if(s.includes(i))return!0;const n=i.split(/\s+/),a=s.split(/\s+/);return n.every(o=>a.some(c=>c.includes(o)||o.includes(c)))}getDeviceIdFromCard(e){const t=e.querySelector(".device-meta-table");if(t)return t.dataset.deviceId;const i=e.querySelector(".view-history-btn");if(i){const n=i.dataset.route.match(/\/device\/(\d+)/);if(n)return n[1]}return null}updateSearchResults(e,t){t.trim()&&e===0&&console.log("No devices found for query:",t)}escapeHtml(e){if(typeof e!="string")return e;const t=document.createElement("div");return t.textContent=e,t.innerHTML}}class g{constructor(e,t,i,s){this.device=e,this.locations=t,this.router=i,this.apiService=s}render(){return`
            <div class="timeline-container">
                <button class="back-btn" data-route="/">
                    ← Back to Dashboard
                </button>
                
                <div class="timeline-header">
                    <h2>${this.escapeHtml(this.device.name||this.device.device_name||"Unknown Device")}</h2>
                    <div class="timeline-meta">
                        ${this.locations.length} location${this.locations.length!==1?"s":""} recorded
                        ${this.device.first_seen?`• First seen ${new Date(this.device.first_seen).toLocaleDateString()}`:""}
                    </div>
                </div>
                
                ${this.renderTimeline()}
                
                ${this.renderDangerZone()}
            </div>
        `}renderTimeline(){if(!this.locations||this.locations.length===0)return`
                <div class="loading">
                    <p>No location history found for this device.</p>
                </div>
            `;const e=[...this.locations].sort((i,s)=>new Date(s.timestamp)-new Date(i.timestamp));return`
            <div class="timeline">
                ${this.groupConsecutiveLocations(e).map(i=>this.renderTimelineGroup(i)).join("")}
            </div>
        `}groupConsecutiveLocations(e){if(e.length===0)return[];const t=[];let i={locations:[e[0]],cleanLocation:this.processLocationText(e[0].location||e[0].location_text),startTime:new Date(e[0].timestamp),endTime:new Date(e[0].timestamp),coordinates:{latitude:e[0].latitude,longitude:e[0].longitude},distance:e[0].distance_meters};for(let s=1;s<e.length;s++){const n=e[s],a=this.processLocationText(n.location||n.location_text);this.shouldGroupLocations(i.cleanLocation,a)?(i.locations.push(n),i.endTime=new Date(n.timestamp),n.latitude&&n.longitude&&(i.coordinates={latitude:n.latitude,longitude:n.longitude})):(t.push(i),i={locations:[n],cleanLocation:a,startTime:new Date(n.timestamp),endTime:new Date(n.timestamp),coordinates:{latitude:n.latitude,longitude:n.longitude},distance:n.distance_meters})}return t.push(i),t}renderDangerZone(){const e=this.device.id||this.device.device_id,t=this.device.name||this.device.device_name||"Unknown Device";return`
            <div class="danger-zone">
                <div class="danger-zone-header">
                    <h3>􀇾 Danger Zone</h3>
                    <p>Irreversible and destructive actions</p>
                </div>
                <div class="danger-zone-content">
                    <div class="danger-action">
                        <div class="danger-action-info">
                            <h4>Delete this device</h4>
                            <p>Permanently remove "${this.escapeHtml(t)}" and all its location history. This action cannot be undone.</p>
                        </div>
                        <button class="danger-btn delete-device-btn" data-device-id="${e}" data-device-name="${this.escapeHtml(t)}">
                            􀈑 Delete Device
                        </button>
                    </div>
                </div>
            </div>
        `}shouldGroupLocations(e,t){return e===t||e==="Location not available"&&t==="Location not available"}renderTimelineGroup(e){const t=e.locations.length>1,i=e.startTime,s=e.endTime;if(t){const n=this.calculateDuration(i,s),a=i.toLocaleString(),o=s.toLocaleString();return`
                <div class="timeline-item timeline-group">
                    <div class="timeline-content">
                        <div class="timeline-location">
                            ${this.escapeHtml(e.cleanLocation)}
                        </div>
                        <div class="timeline-duration">
                            From ${o} to ${a} (${n})
                        </div>
                        ${e.coordinates.latitude&&e.coordinates.longitude?`
                            <div class="timeline-coords">
                                <span class="coords-text">${e.coordinates.latitude.toFixed(6)}, ${e.coordinates.longitude.toFixed(6)}</span>
                                <div class="map-links">
                                    <a href="https://maps.google.com/maps?q=${e.coordinates.latitude},${e.coordinates.longitude}" target="_blank" class="map-link google-maps">
                                        􀋒 Google Maps
                                    </a>
                                    <a href="https://maps.apple.com/?q=${e.coordinates.latitude},${e.coordinates.longitude}" target="_blank" class="map-link apple-maps">
                                        􀙯 Apple Maps
                                    </a>
                                </div>
                            </div>
                        `:""}
                        ${e.distance!==null&&e.distance!==void 0?`
                            <div class="timeline-distance">
                                Distance: ${this.formatDistance(e.distance)}
                            </div>
                        `:""}
                        <div class="timeline-entry-count">
                            ${e.locations.length} entries
                            <button class="delete-group-btn" data-group-locations="${e.locations.map(c=>c.id).join(",")}">
                                􀈑 Delete All
                            </button>
                        </div>
                    </div>
                </div>
            `}else return this.renderTimelineItem(e.locations[0])}calculateDuration(e,t){const i=Math.abs(e-t),s=Math.floor(i/(1e3*60)),n=Math.floor(s/60),a=Math.floor(n/24);if(a>0){const o=n%24;return o>0?`${a} day${a!==1?"s":""}, ${o} hour${o!==1?"s":""}`:`${a} day${a!==1?"s":""}`}else if(n>0){const o=s%60;return o>0?`${n} hour${n!==1?"s":""}, ${o} minute${o!==1?"s":""}`:`${n} hour${n!==1?"s":""}`}else return s>0?`${s} minute${s!==1?"s":""}`:"Less than a minute"}renderTimelineItem(e){const t=new Date(e.timestamp),i=this.getTimeAgo(t),s=t.toLocaleString(),n=this.processLocationText(e.location||e.location_text);return`
            <div class="timeline-item">
                <div class="timeline-content">
                    <div class="timeline-date" title="${s}">
                        ${i}
                    </div>
                    <div class="timeline-location">
                        ${this.escapeHtml(n)}
                    </div>
                    ${e.latitude&&e.longitude?`
                        <div class="timeline-coords">
                            <span class="coords-text">${e.latitude.toFixed(6)}, ${e.longitude.toFixed(6)}</span>
                            <div class="map-links">
                                <a href="https://maps.google.com/maps?q=${e.latitude},${e.longitude}" target="_blank" class="map-link google-maps">
                                    􀋒 Google Maps
                                </a>
                                <a href="https://maps.apple.com/?q=${e.latitude},${e.longitude}" target="_blank" class="map-link apple-maps">
                                    􀙯 Apple Maps
                                </a>
                            </div>
                        </div>
                    `:""}
                    ${e.distance_meters!==null&&e.distance_meters!==void 0?`
                        <div class="timeline-distance">
                            Distance: ${this.formatDistance(e.distance_meters)}
                        </div>
                    `:""}
                    <div class="timeline-actions">
                        <button class="delete-btn" data-location-id="${e.id}">
                            􀈑 Delete
                        </button>
                    </div>
                </div>
            </div>
        `}processLocationText(e){if(!e)return"Location not available";let t=e.trim();t=t.replace(/,\s*\d+\s*(min|hr|hour|minute)s?\s*ago$/i,""),t=t.replace(/,\s*(Paused|paused)$/i,"");const i=[/^lastseen\d+\w*ago$/i,/^last\s*seen/i,/^seen\s*\d+/i,/^\d+\s*(hr|min|hour|minute)s?\s*ago$/i,/^No location found$/i];for(const s of i)if(s.test(t))return"Location not available";return t=t.replace(/[-:.]\d*$/,""),t=t.replace(/[-:.]$/,""),t=t.replace(/\s+/g," "),t=t.replace(/[^\w\s,.-]/g,""),t.toLowerCase()==="home"?"Home":(t=t.replace(/\b\w/g,s=>s.toUpperCase()),t||"Location not available")}formatDistance(e){return e===0?"At location":e<1e3?`${e}m`:`${(e/1e3).toFixed(1)}km`}bindEvents(){document.querySelectorAll(".delete-btn").forEach(e=>{e.addEventListener("click",async t=>{t.stopPropagation();const i=e.dataset.locationId;confirm("Are you sure you want to delete this location entry?")&&await this.deleteLocation(i)})}),document.querySelectorAll(".delete-group-btn").forEach(e=>{e.addEventListener("click",async t=>{t.stopPropagation();const i=e.dataset.groupLocations.split(",");confirm(`Are you sure you want to delete all ${i.length} entries in this group?`)&&await this.deleteLocationGroup(i)})}),document.querySelectorAll(".delete-device-btn").forEach(e=>{e.addEventListener("click",async t=>{t.stopPropagation();const i=e.dataset.deviceId,s=e.dataset.deviceName;await this.showDeleteDeviceConfirmation(i,s)})})}async deleteLocation(e){try{console.log("Attempting to delete location:",e);const t=await this.apiService.deleteLocation(e);console.log("Delete result:",t),this.locations=this.locations.filter(s=>s.id!=e);const i=document.querySelector("#main-content");i&&(i.innerHTML=this.render(),this.bindEvents()),console.log("Location deleted successfully")}catch(t){console.error("Failed to delete location:",t),alert(`Failed to delete location: ${t.message}`)}}async deleteLocationGroup(e){try{console.log("Attempting to delete location group:",e);const t=[...new Set(e)];console.log("Unique location IDs to delete:",t);const i=[];for(const c of t)try{const r=await this.apiService.deleteLocation(c);i.push({id:c,success:!0,result:r})}catch(r){console.warn(`Failed to delete location ${c}:`,r),i.push({id:c,success:!1,error:r})}console.log("Delete results:",i);const s=i.filter(c=>c.success).map(c=>c.id);this.locations=this.locations.filter(c=>!s.includes(c.id.toString()));const n=document.querySelector("#main-content");n&&(n.innerHTML=this.render(),this.bindEvents());const a=s.length,o=t.length;console.log(`Successfully deleted ${a}/${o} locations`)}catch(t){console.error("Failed to delete location group:",t),alert(`Failed to delete location group: ${t.message}`)}}async showDeleteDeviceConfirmation(e,t){await this.showConfirmationModal("Delete Device",`Are you absolutely sure you want to delete "${t}"?`,"This action will permanently remove the device and all its location history. This cannot be undone.","Delete Device","danger")&&await this.showConfirmationModal("Final Confirmation",`Type "${t}" to confirm deletion:`,"This is your last chance to cancel. All data will be permanently lost.","Type device name to confirm","danger",!0,t)&&await this.deleteDevice(e,t)}async showConfirmationModal(e,t,i,s,n="default",a=!1,o=""){return new Promise(c=>{const r=document.createElement("div");r.className="modal-backdrop",r.innerHTML=`
                <div class="modal-content">
                    <div class="modal-header">
                        <h3>${e}</h3>
                    </div>
                    <div class="modal-body">
                        <p class="modal-message">${t}</p>
                        <p class="modal-description">${i}</p>
                        ${a?`
                            <input type="text" class="modal-input" placeholder="Type device name here..." />
                        `:""}
                    </div>
                    <div class="modal-footer">
                        <button class="modal-btn modal-btn-cancel">Cancel</button>
                        <button class="modal-btn modal-btn-${n}" ${a?"disabled":""}>${s}</button>
                    </div>
                </div>
            `,document.body.appendChild(r);const h=r.querySelector(".modal-btn-cancel"),m=r.querySelector(`.modal-btn-${n}`),d=r.querySelector(".modal-input");a&&d&&(d.addEventListener("input",()=>{m.disabled=d.value.trim()!==o}),d.focus()),h.addEventListener("click",()=>{document.body.removeChild(r),c(!1)}),m.addEventListener("click",()=>{a&&d.value.trim()!==o||(document.body.removeChild(r),c(!0))}),r.addEventListener("keydown",u=>{u.key==="Escape"&&(document.body.removeChild(r),c(!1))}),r.addEventListener("click",u=>{u.target===r&&(document.body.removeChild(r),c(!1))})})}async deleteDevice(e,t){try{console.log("Attempting to delete device:",e,t);const i=await this.apiService.deleteDevice(e);console.log("Delete device result:",i),alert(`Device "${t}" has been successfully deleted.`),this.router.navigate("/")}catch(i){console.error("Failed to delete device:",i),alert(`Failed to delete device: ${i.message}`)}}getTimeAgo(e){const i=new Date-e,s=Math.floor(i/(1e3*60)),n=Math.floor(i/(1e3*60*60)),a=Math.floor(i/(1e3*60*60*24));return s<1?"Just now":s<60?`${s} minute${s!==1?"s":""} ago`:n<24?`${n} hour${n!==1?"s":""} ago`:a<7?`${a} day${a!==1?"s":""} ago`:e.toLocaleDateString()}escapeHtml(e){if(typeof e!="string")return e;const t=document.createElement("div");return t.textContent=e,t.innerHTML}}class y{constructor(){this.router=new v,this.apiService=new p,this.mainContent=document.getElementById("main-content"),this.setupRoutes()}setupRoutes(){this.router.addRoute("/",()=>this.showDevicesView()),this.router.addRoute("/device/:id",e=>this.showDeviceDetail(e.id)),this.router.start()}async showDevicesView(){try{this.showSearchField();const e=await this.apiService.getDevices(),t=new f(e,this.router,this.apiService);this.mainContent.innerHTML=t.render(),t.bindEvents()}catch(e){this.showError("Failed to load devices: "+e.message)}}async showDeviceDetail(e){try{this.hideSearchField();const t=decodeURIComponent(e),[i,s]=await Promise.all([this.apiService.getDevice(t),this.apiService.getDeviceLocations(t)]),n=new g(i,s.locations,this.router,this.apiService);this.mainContent.innerHTML=n.render(),n.bindEvents()}catch(t){this.showError("Failed to load device details: "+t.message)}}showError(e){this.mainContent.innerHTML=`
            <div class="error">
                <strong>Error:</strong> ${e}
            </div>
        `}showSearchField(){const e=document.getElementById("search-container");e&&(e.style.display="flex")}hideSearchField(){const e=document.getElementById("search-container");if(e){e.style.display="none";const t=document.getElementById("device-search"),i=document.getElementById("clear-search");t&&(t.value=""),i&&(i.style.display="none")}}}function $(){new y}document.addEventListener("DOMContentLoaded",()=>{$()});
