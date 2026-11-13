/**
 * VenomLink Operational Map Controller
 * Powered by Leaflet & OpenStreetMap.
 * Renders facilities with semantic colors according to public health guidelines.
 */

document.addEventListener('DOMContentLoaded', function () {
  initOverviewMap();
  initFacilityDetailMap();
});

function createOsmTileLayers(map) {
  // Primary OpenStreetMap Standard Tile Layer (official non-subdomain endpoint)
  const osmStandard = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    crossOrigin: true,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors | VenomLink'
  });

  // Humanitarian OpenStreetMap Team (HOT) Layer — designed specifically for public health & relief logistics
  const osmHot = L.tileLayer('https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png', {
    maxZoom: 19,
    subdomains: ['a', 'b', 'c'],
    attribution: '&copy; OpenStreetMap contributors, Tiles style by <a href="https://www.hotosm.org/" target="_blank" rel="noopener">Humanitarian OpenStreetMap Team</a>'
  });

  // Secondary fallback raster layer if primary OSM tile server throttles
  const cartoVoyager = L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', {
    maxZoom: 19,
    subdomains: 'abcd',
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; CARTO'
  });

  let tileErrors = 0;
  let switchedToFallback = false;
  osmStandard.on('tileerror', function () {
    tileErrors += 1;
    if (tileErrors >= 4 && !switchedToFallback && map) {
      switchedToFallback = true;
      if (map.hasLayer(osmStandard)) {
        map.removeLayer(osmStandard);
      }
      cartoVoyager.addTo(map);
    }
  });

  osmStandard.addTo(map);

  L.control.layers(
    {
      'OpenStreetMap Standard': osmStandard,
      'OpenStreetMap Humanitarian (Public Health)': osmHot,
      'OpenStreetMap High-Contrast': cartoVoyager
    },
    null,
    { position: 'topright' }
  ).addTo(map);

  L.control.scale({ imperial: false, metric: true, position: 'bottomleft' }).addTo(map);
}

function initOverviewMap() {
  const mapElement = document.getElementById('operational-map');
  if (!mapElement || typeof L === 'undefined') return;

  let facilities = [];
  const scriptEl = document.getElementById('facilities-map-data');
  if (scriptEl && scriptEl.textContent) {
    try {
      facilities = JSON.parse(scriptEl.textContent);
    } catch (e) {
      console.error('Failed to parse facilities-map-data script:', e);
    }
  } else {
    const attrStr = mapElement.getAttribute('data-facilities');
    if (attrStr) {
      try {
        facilities = JSON.parse(attrStr);
      } catch (e) {
        console.error('Failed to parse data-facilities attribute:', e);
      }
    }
  }

  // Default center: North-East / North-Central Nigeria (Gombe / Plateau corridor)
  const defaultLat = 9.6500;
  const defaultLon = 10.2500;
  const map = L.map('operational-map', {
    zoomControl: true,
    scrollWheelZoom: true
  }).setView([defaultLat, defaultLon], 7);

  createOsmTileLayers(map);

  if (facilities.length > 0) {
    renderFacilityMarkers(map, facilities);
  } else {
    // Fallback fetch from REST API if inline JSON was empty
    fetch('/api/facilities/')
      .then(res => res.json())
      .then(data => {
        const list = data.results || data || [];
        const mapped = list.map(f => ({
          id: f.id,
          name: f.name,
          facility_type: f.facility_type_display || f.facility_type,
          state: f.state,
          lga: f.lga,
          lat: parseFloat(f.latitude),
          lon: parseFloat(f.longitude),
          stock: f.current_stock,
          latest_expiry: f.latest_expiry,
          last_confirmed: f.freshness_label,
          freshness_status: f.freshness_status,
          status: f.stock_status,
          status_label: f.stock_status_label,
          color: f.stock_status_color,
          phone: f.phone,
          days_of_cover: f.days_of_cover
        }));
        renderFacilityMarkers(map, mapped);
      })
      .catch(err => console.error('Fallback facilities fetch failed:', err));
  }

  // Ensure Leaflet recalculates container dimensions cleanly
  setTimeout(function () {
    map.invalidateSize();
  }, 150);
}

function renderFacilityMarkers(map, facilities) {
  const markers = [];
  const bounds = L.latLngBounds();

  facilities.forEach(function (fac) {
    const lat = parseFloat(fac.lat);
    const lon = parseFloat(fac.lon);
    if (isNaN(lat) || isNaN(lon)) return;

    const latLng = [lat, lon];
    bounds.extend(latLng);

    // Semantic marker colour
    let bgColor = '#1E7E34'; // Green: Normal (> 7d cover)
    if (fac.status === 'CRITICAL' || fac.status === 'STOCK_OUT') {
      bgColor = '#B71C1C';   // Red: Critical / Stock-out (<= 5d or 0 vials)
    } else if (fac.status === 'LOW' || fac.status === 'LOW_STOCK') {
      bgColor = '#D97706';   // Amber: Low stock (> 5d - 7d)
    } else if (fac.status === 'UNCONFIRMED' || fac.freshness_status === 'STALE') {
      bgColor = '#546E7A';   // Grey: Unconfirmed / Stale
    }

    const customIcon = L.divIcon({
      className: 'custom-leaflet-marker-wrapper',
      html: `<div style="
        background-color: ${bgColor};
        width: 32px;
        height: 32px;
        border-radius: 50%;
        border: 2px solid #FFFFFF;
        box-shadow: 0 2px 5px rgba(0,0,0,0.4);
        display: flex;
        align-items: center;
        justify-content: center;
        color: #FFFFFF;
        font-weight: 700;
        font-size: 11px;
        font-family: -apple-system, BlinkMacSystemFont, sans-serif;
      ">${fac.stock}</div>`,
      iconSize: [32, 32],
      iconAnchor: [16, 16],
      popupAnchor: [0, -16]
    });

    const marker = L.marker(latLng, { icon: customIcon }).addTo(map);

    marker.bindTooltip(
      `<strong>${escapeHtml(fac.name)}</strong><br>${fac.stock} vials &bull; ${escapeHtml(fac.status_label)}`,
      { direction: 'top', offset: [0, -14] }
    );

    const popupContent = `
      <div class="facility-popup-panel">
        <div class="popup-facility-name">${escapeHtml(fac.name)}</div>
        <div class="popup-row">
          <span class="popup-label">LGA / State:</span>
          <span class="popup-value">${escapeHtml(fac.lga)}, ${escapeHtml(fac.state)}</span>
        </div>
        <div class="popup-row">
          <span class="popup-label">Current stock:</span>
          <span class="popup-value">${fac.stock} vials</span>
        </div>
        <div class="popup-row">
          <span class="popup-label">Last confirmed:</span>
          <span class="popup-value">${escapeHtml(fac.last_confirmed)}</span>
        </div>
        <div class="popup-row">
          <span class="popup-label">Status:</span>
          <span class="popup-value" style="color: ${bgColor}; font-weight: 700;">${escapeHtml(fac.status_label)}</span>
        </div>
        <div class="popup-row">
          <span class="popup-label">Expiry:</span>
          <span class="popup-value">${escapeHtml(fac.latest_expiry || 'N/A')}</span>
        </div>
        <div class="popup-row">
          <span class="popup-label">Days of Cover:</span>
          <span class="popup-value">${fac.days_of_cover !== null && fac.days_of_cover !== undefined ? `${fac.days_of_cover} days` : escapeHtml(fac.days_of_cover_display || 'Unavailable')}</span>
        </div>
        <div class="popup-row">
          <span class="popup-label">Phone:</span>
          <span class="popup-value">${fac.phone ? `<a href="tel:${escapeHtml(fac.phone)}" style="color: #0B5D4B; text-decoration: none; font-weight: 600;">${escapeHtml(fac.phone)}</a>` : `<span style="color: #546E7A;">${escapeHtml(fac.phone_display || 'Demo contact unavailable')}</span>`}</span>
        </div>
        <a href="/facilities/${fac.id}/" class="popup-action-btn">View Facility Dashboard</a>
      </div>
    `;

    marker.bindPopup(popupContent, { maxWidth: 280 });
    markers.push(marker);
  });

  if (markers.length > 1) {
    map.fitBounds(bounds, { padding: [45, 45], maxZoom: 11 });
  } else if (markers.length === 1) {
    map.setView(bounds.getCenter(), 11);
  }
}

function initFacilityDetailMap() {
  const detailMapEl = document.getElementById('facility-detail-map');
  if (!detailMapEl || typeof L === 'undefined') return;

  const lat = parseFloat(detailMapEl.getAttribute('data-lat'));
  const lon = parseFloat(detailMapEl.getAttribute('data-lon'));
  const name = detailMapEl.getAttribute('data-name') || 'Facility';
  const stock = detailMapEl.getAttribute('data-stock') || '0';
  const color = detailMapEl.getAttribute('data-color') || '#0B5D4B';

  if (isNaN(lat) || isNaN(lon)) return;

  const map = L.map('facility-detail-map', {
    zoomControl: true,
    scrollWheelZoom: false
  }).setView([lat, lon], 11);

  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    crossOrigin: true,
    attribution: '&copy; OpenStreetMap'
  }).addTo(map);

  const icon = L.divIcon({
    className: 'custom-leaflet-marker-wrapper',
    html: `<div style="
      background-color: ${color};
      width: 32px;
      height: 32px;
      border-radius: 50%;
      border: 2px solid #FFFFFF;
      box-shadow: 0 2px 5px rgba(0,0,0,0.4);
      display: flex;
      align-items: center;
      justify-content: center;
      color: #FFFFFF;
      font-weight: 700;
      font-size: 11px;
    ">${stock}</div>`,
    iconSize: [32, 32],
    iconAnchor: [16, 16]
  });

  L.marker([lat, lon], { icon: icon })
    .addTo(map)
    .bindPopup(`<strong>${escapeHtml(name)}</strong><br>${stock} vials available`)
    .openPopup();

  setTimeout(function () {
    map.invalidateSize();
  }, 150);
}

function escapeHtml(text) {
  if (!text) return '';
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}
