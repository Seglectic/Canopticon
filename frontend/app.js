const gallery = document.querySelector("#gallery");
const empty = document.querySelector("#empty");
const statusEl = document.querySelector("#status");
const queueNote = document.querySelector("#queueNote");
const input = document.querySelector("#files");
const uploadForm = document.querySelector("#uploadForm");
const trayHandle = document.querySelector("#trayHandle");
const trayLabel = document.querySelector("#trayLabel");
const galleryTab = document.querySelector("#galleryTab");
const mapTab = document.querySelector("#mapTab");
const mapView = document.querySelector("#mapView");
const mapEmpty = document.querySelector("#mapEmpty");
const mapSourceBar = document.querySelector("#mapSourceBar");
const viewer = document.querySelector("#viewer");
const viewerImage = document.querySelector("#viewerImage");
const viewerTitle = document.querySelector("#viewerTitle");
const viewerDetail = document.querySelector("#viewerDetail");
const viewerClose = document.querySelector("#viewerClose");
const items = new Map();
const markers = new Map();
let activeView = "gallery";
let map;
let markerLayer;
let lastMapFitKey = "";
let mapConfig;
let activeMapSourceId;
let baseLayer;
let controlsCollapsed = false;

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  }[char]));
}

function cardImage(item) {
  return item.result_url || item.uploaded_url;
}

function gpsText(item) {
  return item.location_label || (item.gps_present ? "GPS found" : "No GPS");
}

function statusText(item) {
  if (item.status === "done" && item.occluded_pct !== null) {
    return `Occluded ${item.occluded_pct.toFixed(1)}%`;
  }
  if (item.status === "done") return "Processed";
  if (item.status === "error") return item.error || "Processing failed";
  if (item.status === "processing") return "Processing";
  return "Queued";
}

function detailText(item) {
  const bits = [statusText(item), gpsText(item)];
  if (item.capture_source === "gpio-trigger") {
    bits.push("GPIO capture");
  }
  if (item.gps_present && item.gps_latitude !== null && item.gps_longitude !== null) {
    bits.push(`${item.gps_latitude.toFixed(5)}, ${item.gps_longitude.toFixed(5)}`);
  }
  return bits.join(" | ");
}

function pinText(item) {
  if (item.occluded_pct === null || item.occluded_pct === undefined) return "...";
  return `${Math.round(item.occluded_pct)}%`;
}

function clusterText(cluster) {
  const values = cluster.items
    .map((item) => item.occluded_pct)
    .filter((value) => typeof value === "number");
  if (!values.length) return "...";
  const average = values.reduce((sum, value) => sum + value, 0) / values.length;
  return `${Math.round(average)}%`;
}

function itemHasGps(item) {
  return Boolean(
    item.gps_present &&
    item.gps_latitude !== null &&
    item.gps_longitude !== null
  );
}

function openViewer(id) {
  const item = items.get(id);
  if (!item) return;
  viewerImage.src = cardImage(item);
  viewerTitle.textContent = item.filename;
  viewerDetail.textContent = detailText(item);
  viewer.showModal();
}

function mapFitKey(mapped) {
  return mapped.map((item) => `${item.id}:${item.occluded_pct ?? ""}`).sort().join("|");
}

function ensureMap() {
  if (map || !window.L) return;
  map = L.map("map", {
    zoomControl: true,
    attributionControl: true,
  });
  markerLayer = L.layerGroup().addTo(map);
  map.on("zoomend", () => renderMap(false));
  map.setView([20, 0], 2);
  applyMapSource(activeMapSourceId || mapConfig?.default_source || "live-osm");
}

function sourceById(sourceId) {
  return mapConfig?.sources?.find((source) => source.id === sourceId) || null;
}

function updateMapSourceButtons() {
  if (!mapSourceBar || !mapConfig?.sources?.length) return;
  mapSourceBar.innerHTML = mapConfig.sources.map((source) => `
    <button
      class="map-source ${source.id === activeMapSourceId ? "active" : ""}"
      type="button"
      data-source-id="${escapeHtml(source.id)}"
    >${escapeHtml(source.label)}</button>
  `).join("");
}

function createBaseLayer(source) {
  if (!window.L || !source) return null;
  if (source.kind === "pmtiles" && window.protomapsL?.leafletLayer) {
    return protomapsL.leafletLayer({
      url: source.url,
      flavor: "light",
      lang: "en",
      noWrap: true,
      attribution: '&copy; <a href="https://openstreetmap.org/copyright">OpenStreetMap</a>',
    });
  }
  if (source.kind === "raster") {
    return L.tileLayer(source.url, {
      maxZoom: source.max_zoom || 19,
      attribution: source.attribution || "",
    });
  }
  return null;
}

function applyMapSource(sourceId) {
  activeMapSourceId = sourceId;
  const source = sourceById(sourceId) || sourceById("live-osm");
  if (!source) return;
  updateMapSourceButtons();
  if (!map) return;

  if (baseLayer) {
    map.removeLayer(baseLayer);
    baseLayer = null;
  }
  baseLayer = createBaseLayer(source);
  if (!baseLayer && source.id !== "live-osm") {
    activeMapSourceId = "live-osm";
    updateMapSourceButtons();
    baseLayer = createBaseLayer(sourceById("live-osm"));
  }
  if (baseLayer) {
    baseLayer.addTo(map);
  }

  if (source.bounds?.length === 2 && !Array.from(items.values()).some(itemHasGps)) {
    map.fitBounds(source.bounds, { padding: [18, 18], maxZoom: source.max_zoom || 12 });
  } else if (source.center?.length === 2) {
    map.setView(source.center, source.zoom || map.getZoom());
  }
}

function buildClusters(mapped) {
  const radius = 72;
  const clusters = [];
  mapped.forEach((item) => {
    const point = map.latLngToLayerPoint([item.gps_latitude, item.gps_longitude]);
    let cluster = clusters.find((candidate) => point.distanceTo(candidate.point) <= radius);
    if (!cluster) {
      cluster = {
        items: [],
        point,
        lat: 0,
        lng: 0,
      };
      clusters.push(cluster);
    }
    cluster.items.push(item);
    const count = cluster.items.length;
    cluster.lat = ((cluster.lat * (count - 1)) + item.gps_latitude) / count;
    cluster.lng = ((cluster.lng * (count - 1)) + item.gps_longitude) / count;
    cluster.point = map.latLngToLayerPoint([cluster.lat, cluster.lng]);
  });
  return clusters;
}

function renderMap(allowFit = false) {
  ensureMap();
  if (!map || !markerLayer) return;

  markerLayer.clearLayers();
  markers.clear();
  const mapped = Array.from(items.values()).filter(itemHasGps);
  mapEmpty.classList.toggle("hidden", mapped.length > 0);

  const bounds = [];
  const clusters = buildClusters(mapped);
  clusters.forEach((cluster) => {
    const isCluster = cluster.items.length > 1;
    const marker = L.marker([cluster.lat, cluster.lng], {
      icon: L.divIcon({
        className: "",
        html: `
          <div class="map-pin ${isCluster ? "cluster" : ""}">
            <svg viewBox="0 0 64 72" aria-hidden="true" focusable="false">
              <path class="map-pin-border" d="M32 1 C42 1 54 7 59 18 C65 31 60 48 46 58 C39 63 35 68 32 71 C29 68 25 63 18 58 C4 48 -1 31 5 18 C10 7 22 1 32 1 Z"></path>
              <path class="map-pin-fill" d="M32 6 C40 6 50 11 54 20 C59 31 55 45 43 54 C37 59 34 63 32 66 C30 63 27 59 21 54 C9 45 5 31 10 20 C14 11 24 6 32 6 Z"></path>
            </svg>
            <span class="map-pin-value">${escapeHtml(clusterText(cluster))}</span>
            ${isCluster ? `<span class="map-pin-count">${cluster.items.length}</span>` : ""}
          </div>
        `,
        iconSize: [64, 72],
        iconAnchor: [32, 70],
      }),
      title: isCluster ? `${cluster.items.length} photos` : cluster.items[0].filename,
    });
    marker.on("click", () => {
      if (!isCluster) {
        openViewer(cluster.items[0].id);
        return;
      }
      const clusterBounds = L.latLngBounds(
        cluster.items.map((item) => [item.gps_latitude, item.gps_longitude])
      );
      if (clusterBounds.isValid()) {
        map.fitBounds(clusterBounds, {
          padding: [42, 42],
          maxZoom: Math.min(map.getZoom() + 3, 18),
        });
      }
    });
    marker.addTo(markerLayer);
    markers.set(cluster.items.map((item) => item.id).join("|"), marker);
    bounds.push([cluster.lat, cluster.lng]);
  });

  const nextFitKey = mapFitKey(mapped);
  const shouldFit = allowFit && nextFitKey !== lastMapFitKey;
  if (shouldFit && bounds.length === 1) {
    map.setView(bounds[0], 16);
    lastMapFitKey = nextFitKey;
  } else if (shouldFit && bounds.length > 1) {
    map.fitBounds(bounds, { padding: [28, 28], maxZoom: 16 });
    lastMapFitKey = nextFitKey;
  }
}

function setView(view) {
  activeView = view;
  const showMap = view === "map";
  gallery.classList.toggle("active", !showMap);
  mapView.classList.toggle("active", showMap);
  galleryTab.classList.toggle("active", !showMap);
  mapTab.classList.toggle("active", showMap);
  galleryTab.setAttribute("aria-selected", String(!showMap));
  mapTab.setAttribute("aria-selected", String(showMap));
  if (showMap) {
    renderMap(true);
    setTimeout(() => map?.invalidateSize(), 50);
  }
}

function setControlsCollapsed(collapsed) {
  controlsCollapsed = collapsed;
  uploadForm.classList.toggle("collapsed", collapsed);
  trayHandle.setAttribute("aria-expanded", String(!collapsed));
  trayHandle.setAttribute("aria-label", collapsed ? "Show controls" : "Collapse controls");
  trayLabel.textContent = collapsed ? "Show Controls" : "Hide Controls";
}

function render() {
  const list = Array.from(items.values()).reverse();
  empty.style.display = list.length || activeView === "map" ? "none" : "grid";
  gallery.innerHTML = list.map((item) => `
    <button class="tile" type="button" data-id="${escapeHtml(item.id)}" aria-label="${escapeHtml(item.filename)}">
      <img src="${cardImage(item)}" alt="">
      <span class="pill ${item.status}">${item.status}</span>
      <span class="tile-overlay">
        <span class="tile-name">${escapeHtml(item.filename)}</span>
        <span class="tile-meta">${escapeHtml(detailText(item))}</span>
      </span>
    </button>
  `).join("");

  const queued = list.filter((item) => item.status === "queued" || item.status === "processing").length;
  queueNote.textContent = queued ? `${queued} waiting or processing` : "";
  if (activeView === "map") renderMap();
}

async function loadItems() {
  const response = await fetch("/api/items");
  const data = await response.json();
  data.items.forEach((item) => items.set(item.id, item));
  render();
}

async function loadMapConfig() {
  const response = await fetch("/api/map-config");
  if (!response.ok) return;
  mapConfig = await response.json();
  activeMapSourceId = mapConfig.default_source;
  updateMapSourceButtons();
  if (map) {
    applyMapSource(activeMapSourceId);
  }
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.addEventListener("open", () => { statusEl.textContent = "Live updates connected"; });
  ws.addEventListener("close", () => {
    statusEl.textContent = "Reconnecting...";
    setTimeout(connect, 1200);
  });
  ws.addEventListener("message", (event) => {
    const data = JSON.parse(event.data);
    if (data.type === "snapshot") {
      data.items.forEach((item) => items.set(item.id, item));
    }
    if (data.type === "item") {
      items.set(data.item.id, data.item);
    }
    if (data.type === "duplicate") {
      statusEl.textContent = `Duplicate skipped: ${data.filename}`;
    }
    if (data.type === "notice") {
      statusEl.textContent = data.message;
    }
    render();
  });
}

gallery.addEventListener("click", (event) => {
  const tile = event.target.closest(".tile");
  if (!tile) return;
  openViewer(tile.dataset.id);
});

galleryTab.addEventListener("click", () => setView("gallery"));
mapTab.addEventListener("click", () => setView("map"));
viewerClose.addEventListener("click", () => viewer.close());
viewer.addEventListener("click", (event) => {
  if (event.target === viewer) viewer.close();
});
mapSourceBar.addEventListener("click", (event) => {
  const button = event.target.closest("[data-source-id]");
  if (!button) return;
  applyMapSource(button.dataset.sourceId);
});
trayHandle.addEventListener("click", () => {
  setControlsCollapsed(!controlsCollapsed);
});

input.addEventListener("change", async () => {
  if (!input.files.length) return;
  queueNote.textContent = `Uploading ${input.files.length} photo${input.files.length === 1 ? "" : "s"}...`;
  const formData = new FormData();
  for (const file of input.files) formData.append("files", file);
  input.value = "";

  const response = await fetch("/api/upload", { method: "POST", body: formData });
  if (!response.ok) {
    statusEl.textContent = "Upload failed";
    queueNote.textContent = "";
    return;
  }
  const data = await response.json();
  data.items.forEach((item) => items.set(item.id, item));
  render();
});

loadMapConfig().finally(loadItems);
setControlsCollapsed(false);
connect();
