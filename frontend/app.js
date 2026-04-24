const gallery = document.querySelector("#gallery");
const empty = document.querySelector("#empty");
const splash = document.querySelector("#splash");
const splashLogo = document.querySelector(".splash-logo");
const splashSignoff = document.querySelector("#splashSignoff");
const topbar = document.querySelector(".topbar");
const statusEl = document.querySelector("#status");
const statusSrEl = statusEl.querySelector(".sr-only");
const mapCoords = document.querySelector("#mapCoords");
const queueNote = document.querySelector("#queueNote");
const input = document.querySelector("#files");
const uploadForm = document.querySelector("#uploadForm");
const trayHandle = document.querySelector("#trayHandle");
const trayLabel = document.querySelector("#trayLabel");
const viewToggle = document.querySelector(".tabs");
const galleryTab = document.querySelector("#galleryTab");
const mapTab = document.querySelector("#mapTab");
const mapView = document.querySelector("#mapView");
const mapEmpty = document.querySelector("#mapEmpty");
const viewer = document.querySelector("#viewer");
const viewerImage = document.querySelector("#viewerImage");
const viewerTitle = document.querySelector("#viewerTitle");
const viewerStatValue = document.querySelector("#viewerStatValue");
const viewerCoords = document.querySelector("#viewerCoords");
const viewerMapLink = document.querySelector("#viewerMapLink");
const viewerDelete = document.querySelector("#viewerDelete");
const viewerClose = document.querySelector("#viewerClose");
const items = new Map();
const markers = new Map();
let activeView = "gallery";
let map;
let markerLayer;
let heatCanvas;
let lastMapFitKey = "";
let baseLayer;
let mapConfig = null;
let controlsCollapsed = false;
let noticeTimeout;
let activeViewerItemId = null;
let mapTouchTracking = false;

const SPLASH_BOB_AMPLITUDE = 12;
const SPLASH_BOB_PERIOD_MS = 2000;
const SPLASH_GLITCH_CYCLES = 1;
const SPLASH_GLITCH_DELAY_MS = 12;
const SPLASH_GLITCH_JITTER_MS = 8;
const SPLASH_POST_TEXT_PAUSE_MS = 400;
const SPLASH_GLITCH_CHARS = "!<>-_\\/[]{}=+*^?#________";

const STATUS_META = {
  done: {
    label: "Processed",
    iconClass: "status-chip-icon status-chip-icon-done",
  },
  processing: {
    label: "Processing",
    iconClass: "status-chip-icon status-chip-icon-processing",
  },
  queued: {
    label: "Queued",
    iconClass: "status-chip-icon status-chip-icon-queued",
  },
  error: {
    label: "Error",
    iconClass: "status-chip-icon status-chip-icon-error",
  },
};

function setStatus(state, message) {
  statusEl.classList.remove("connected", "disconnected", "notice");
  statusEl.classList.add(state);
  statusEl.setAttribute("aria-label", message);
  if (statusSrEl) statusSrEl.textContent = message;
}

function flashNotice(message) {
  clearTimeout(noticeTimeout);
  setStatus("notice", message);
  noticeTimeout = window.setTimeout(() => {
    setStatus("connected", "Live updates connected");
  }, 2600);
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  }[char]));
}

function sleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function jitter(base, spread) {
  return base + Math.floor(Math.random() * (spread + 1));
}

async function animateSplashSignoff() {
  if (!splashSignoff) return;
  const target = splashSignoff.dataset.text || "";
  splashSignoff.style.width = `${Math.max(target.length + 1, 18)}ch`;
  splashSignoff.textContent = "";
  for (let index = 0; index < target.length; index += 1) {
    const prefix = target.slice(0, index);
    const nextChar = target[index];
    for (let cycle = 0; cycle < SPLASH_GLITCH_CYCLES; cycle += 1) {
      const junk = SPLASH_GLITCH_CHARS[Math.floor(Math.random() * SPLASH_GLITCH_CHARS.length)] || nextChar;
      splashSignoff.textContent = prefix + junk;
      await sleep(jitter(SPLASH_GLITCH_DELAY_MS, SPLASH_GLITCH_JITTER_MS));
      splashSignoff.textContent = prefix;
      await sleep(jitter(Math.max(10, SPLASH_GLITCH_DELAY_MS - 6), SPLASH_GLITCH_JITTER_MS));
    }
    splashSignoff.textContent = prefix + nextChar;
    await sleep(jitter(SPLASH_GLITCH_DELAY_MS, SPLASH_GLITCH_JITTER_MS));
  }
}

function startSplashBob() {
  if (!splash || !splashLogo) return () => {};
  let frameId = 0;
  const startedAt = performance.now();
  const tick = (now) => {
    const phase = ((now - startedAt) / SPLASH_BOB_PERIOD_MS) * Math.PI * 2;
    const offsetY = Math.sin(phase) * SPLASH_BOB_AMPLITUDE;
    splashLogo.style.transform = `translateY(${offsetY.toFixed(2)}px)`;
    frameId = window.requestAnimationFrame(tick);
  };
  frameId = window.requestAnimationFrame(tick);
  return () => window.cancelAnimationFrame(frameId);
}

function cardImage(item) {
  return item.thumb_url || item.result_url || item.uploaded_url;
}

function gpsText(item) {
  return item.location_label || (item.gps_present ? "GPS found" : "No GPS");
}

function statusText(item) {
  if (item.status === "done") return "Processed";
  if (item.status === "error") return item.error || "Processing failed";
  if (item.status === "processing") return "Processing";
  return "Queued";
}

function detailText(item) {
  const bits = [];
  if (!item.gps_present) {
    bits.push(gpsText(item));
  }
  if (item.capture_source === "gpio-trigger") {
    bits.push("GPIO capture");
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

function setMapCoords(latlng) {
  if (!mapCoords) return;
  if (!latlng) {
    mapCoords.textContent = "";
    return;
  }
  mapCoords.textContent = `${latlng.lat.toFixed(5)}, ${latlng.lng.toFixed(5)}`;
}

function mapLatLngFromTouch(touch) {
  if (!map || !touch) return null;
  const rect = map.getContainer().getBoundingClientRect();
  const point = L.point(touch.clientX - rect.left, touch.clientY - rect.top);
  return map.containerPointToLatLng(point);
}

function coordsText(item) {
  if (!itemHasGps(item)) return "";
  return `${Number(item.gps_latitude).toFixed(5)}, ${Number(item.gps_longitude).toFixed(5)}`;
}

async function loadMapConfig() {
  const response = await fetch("/api/map-config");
  if (!response.ok) {
    throw new Error("Map configuration failed to load");
  }
  mapConfig = await response.json();
}

function occlusionText(item) {
  if (item.occluded_pct === null || item.occluded_pct === undefined) {
    return item.status === "done" ? "--" : "...";
  }
  return `${Math.round(item.occluded_pct)}%`;
}

function clusterThumbItems(cluster) {
  return cluster.items
    .map((item) => ({ item, src: cardImage(item) }))
    .filter(({ src }) => Boolean(src))
    .slice(0, 2);
}

function heatMappedItems() {
  return Array.from(items.values()).filter(
    (item) => itemHasGps(item) && typeof item.occluded_pct === "number"
  );
}

function clusterBubbleHtml(cluster) {
  const isCluster = cluster.items.length > 1;
  const thumbs = clusterThumbItems(cluster);
  const clipId = `mapBubble-${cluster.items.map((item) => item.id).join("-").replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const imageMarkup = thumbs.length
    ? thumbs.map(({ src }, index) => `
        <image
          href="${escapeHtml(src)}"
          width="74"
          height="90"
          preserveAspectRatio="xMidYMid slice"
          clip-path="url(#${clipId}-${thumbs.length === 2 ? `slice${index + 1}` : "shape"})"
        ></image>
      `).join("")
    : `<rect class="map-bubble-fallback" width="74" height="90" clip-path="url(#${clipId}-shape)"></rect>`;
  const badgeText = isCluster ? String(cluster.items.length) : pinText(cluster.items[0]);
  return `
    <div class="map-bubble ${isCluster ? "cluster" : "single"} thumb-count-${thumbs.length}">
      <svg class="map-bubble-svg" viewBox="0 0 74 90" aria-hidden="true" focusable="false">
        <defs>
          <path id="${clipId}-path" d="M37 2 C20 2 7 15 7 32 C7 52 27 63 37 88 C47 63 67 52 67 32 C67 15 54 2 37 2 Z"></path>
          <clipPath id="${clipId}-shape"><use href="#${clipId}-path"></use></clipPath>
          <clipPath id="${clipId}-slice1">
            <polygon points="0 0 74 0 0 90"></polygon>
          </clipPath>
          <clipPath id="${clipId}-slice2">
            <polygon points="74 0 74 90 0 90"></polygon>
          </clipPath>
        </defs>
        <use class="map-bubble-border" href="#${clipId}-path"></use>
        <g clip-path="url(#${clipId}-shape)" transform="translate(4 4) scale(0.891)">
          ${imageMarkup}
          <rect class="map-bubble-shade" width="74" height="90"></rect>
        </g>
        <g class="map-bubble-badge-svg" transform="translate(58 6)">
          <rect x="-2" y="-2" rx="12" ry="12" width="${isCluster ? 28 : 30}" height="24"></rect>
          <text x="${isCluster ? 12 : 13}" y="13">${escapeHtml(badgeText)}</text>
        </g>
      </svg>
      ${thumbs.length ? "" : '<span class="sr-only">No thumbnail</span>'}
    </div>
  `;
}

function renderHeatOverlay() {
  if (!map || !heatCanvas) return;
  const mapped = heatMappedItems();
  const size = map.getSize();
  const ratio = window.devicePixelRatio || 1;
  if (heatCanvas.width !== Math.round(size.x * ratio) || heatCanvas.height !== Math.round(size.y * ratio)) {
    heatCanvas.width = Math.round(size.x * ratio);
    heatCanvas.height = Math.round(size.y * ratio);
    heatCanvas.style.width = `${size.x}px`;
    heatCanvas.style.height = `${size.y}px`;
  }
  const ctx = heatCanvas.getContext("2d");
  if (!ctx) return;
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, size.x, size.y);

  if (!mapped.length) {
    return;
  }

  const zoom = map.getZoom();
  const points = mapped.map((item) => ({
    x: map.latLngToContainerPoint([item.gps_latitude, item.gps_longitude]).x,
    y: map.latLngToContainerPoint([item.gps_latitude, item.gps_longitude]).y,
    latitude: item.gps_latitude,
    strength: Math.min(1, Math.max(0.08, item.occluded_pct / 100)),
  }));
  const step = zoom <= 13 ? 10 : zoom <= 16 ? 8 : 6;

  for (let y = 0; y < size.y; y += step) {
    for (let x = 0; x < size.x; x += step) {
      let field = 0;
      for (const point of points) {
        const latitudeScale = Math.max(0.2, Math.cos((point.latitude * Math.PI) / 180));
        const metersPerPixel = (40075016.686 * latitudeScale) / (256 * (2 ** zoom));
        const radiusMeters = 250 + (point.strength * 64);
        const radius = Math.max(8, Math.min(104, radiusMeters / metersPerPixel));
        const dx = x - point.x;
        const dy = y - point.y;
        const distanceSq = (dx * dx) + (dy * dy);
        if (distanceSq > radius * radius) continue;
        const distance = Math.sqrt(distanceSq);
        const normalized = distance / radius;
        const influence = (point.strength ** 1.35) * Math.exp(-(normalized * normalized) * 3.8);
        field = 1 - ((1 - field) * (1 - influence));
      }
      if (field < 0.03) continue;
      const alpha = Math.min(0.68, 0.06 + (field * 0.78));
      ctx.fillStyle = `rgba(116, 84, 204, ${alpha.toFixed(3)})`;
      ctx.fillRect(x, y, step, step);
    }
  }

  ctx.save();
  ctx.globalCompositeOperation = "source-atop";
  ctx.translate(size.x / 2, size.y / 2);
  ctx.rotate(Math.PI / 4);
  ctx.translate(-size.x / 2, -size.y / 2);
  ctx.fillStyle = "rgba(185, 150, 255, 0.30)";
  for (let x = -size.y; x < size.x + size.y; x += 14) {
    ctx.fillRect(x, -size.y, 5, size.y * 3);
  }
  ctx.restore();
}

function itemClusteredAtZoom(targetItem, zoom) {
  if (!map) return false;
  const targetPoint = map.project([targetItem.gps_latitude, targetItem.gps_longitude], zoom);
  return Array.from(items.values()).some((item) => {
    if (item.id === targetItem.id || !itemHasGps(item)) return false;
    const point = map.project([item.gps_latitude, item.gps_longitude], zoom);
    return targetPoint.distanceTo(point) <= 72;
  });
}

function zoomForStandaloneItem(targetItem) {
  if (!map) return 16;
  const startZoom = Math.max(map.getZoom(), 16);
  for (let zoom = startZoom; zoom <= 24; zoom += 1) {
    if (!itemClusteredAtZoom(targetItem, zoom)) return zoom;
  }
  return 24;
}

function openViewer(id) {
  const item = items.get(id);
  if (!item) return;
  activeViewerItemId = id;
  updateViewer(item);
  viewer.showModal();
  document.body.classList.add("viewer-open");
}

function updateViewer(item) {
  const nextSrc = item.result_url || item.uploaded_url;
  if (viewerImage.src !== new URL(nextSrc, window.location.href).href) {
    viewerImage.classList.add("is-loading");
    viewerImage.src = nextSrc;
    if (viewerImage.complete) {
      window.requestAnimationFrame(() => viewerImage.classList.remove("is-loading"));
    }
  }
  viewerStatValue.textContent = occlusionText(item);
  viewerTitle.textContent = item.filename;
  viewerCoords.textContent = coordsText(item);
  viewerMapLink.hidden = !itemHasGps(item);
}

function mapFitKey(mapped) {
  return mapped.map((item) => `${item.id}:${item.occluded_pct ?? ""}`).sort().join("|");
}

function ensureMap() {
  if (map || !window.L) return;
  map = L.map("map", {
    zoomControl: false,
    attributionControl: true,
  });
  L.control.zoom({ position: "bottomright" }).addTo(map);
  heatCanvas = document.createElement("canvas");
  heatCanvas.classList.add("map-heat-layer");
  map.getContainer().appendChild(heatCanvas);
  markerLayer = L.layerGroup().addTo(map);
  map.on("zoomend", () => renderMap(false));
  map.on("move", renderHeatOverlay);
  map.on("moveend", () => renderMap(false));
  map.on("resize", () => renderMap(false));
  map.on("mousemove", (event) => {
    setMapCoords(event.latlng);
  });
  map.on("mouseout", () => {
    if (!mapTouchTracking) setMapCoords(null);
  });
  const mapContainer = map.getContainer();
  mapContainer.addEventListener("touchstart", (event) => {
    if (event.touches.length !== 1) {
      mapTouchTracking = false;
      return;
    }
    mapTouchTracking = true;
    setMapCoords(mapLatLngFromTouch(event.touches[0]));
  }, { passive: true });
  mapContainer.addEventListener("touchmove", (event) => {
    if (!mapTouchTracking || event.touches.length !== 1) return;
    setMapCoords(mapLatLngFromTouch(event.touches[0]));
  }, { passive: true });
  mapContainer.addEventListener("touchend", (event) => {
    if (!mapTouchTracking) {
      setMapCoords(null);
      return;
    }
    if (event.touches.length === 1) {
      setMapCoords(mapLatLngFromTouch(event.touches[0]));
      return;
    }
    mapTouchTracking = false;
    setMapCoords(null);
  }, { passive: true });
  mapContainer.addEventListener("touchcancel", () => {
    mapTouchTracking = false;
    setMapCoords(null);
  }, { passive: true });
  const offlineSource = mapConfig?.sources?.find((source) => source.default) || mapConfig?.sources?.[0];
  if (offlineSource) {
    map.setView(offlineSource.center, offlineSource.zoom);
  } else {
    map.setView([20, 0], 2);
  }
  if (!baseLayer && offlineSource) {
    baseLayer = protomapsL.leafletLayer({
      url: offlineSource.url,
      flavor: "light",
      lang: "en",
      noWrap: true,
      attribution: '&copy; <a href="https://openstreetmap.org/copyright">OpenStreetMap</a>',
    });
    baseLayer.addTo(map);
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
  renderHeatOverlay();
  mapEmpty.classList.toggle("hidden", mapped.length > 0);
  if (!mapped.length && allowFit) {
    map.fitBounds([[24.3, -87.7], [31.1, -79.8]], { padding: [18, 18], maxZoom: 7 });
    return;
  }

  const bounds = [];
  const clusters = buildClusters(mapped);
  clusters.forEach((cluster) => {
    const isCluster = cluster.items.length > 1;
    const marker = L.marker([cluster.lat, cluster.lng], {
      icon: L.divIcon({
        className: "map-bubble-icon",
        html: clusterBubbleHtml(cluster),
        iconSize: [74, 90],
        iconAnchor: [37, 88],
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
    bounds.push(...cluster.items.map((item) => [item.gps_latitude, item.gps_longitude]));
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
  document.body.classList.toggle("map-active", showMap);
  gallery.classList.toggle("active", !showMap);
  mapView.classList.toggle("active", showMap);
  galleryTab.classList.toggle("active", !showMap);
  mapTab.classList.toggle("active", showMap);
  galleryTab.setAttribute("aria-selected", String(!showMap));
  mapTab.setAttribute("aria-selected", String(showMap));
  if (showMap) {
    controlsCollapsed = false;
    uploadForm.classList.remove("collapsed");
    setMapCoords(null);
  } else {
    mapTouchTracking = false;
    setMapCoords(null);
  }
  updateChromeMetrics();
  if (showMap) {
    renderMap(true);
    window.setTimeout(() => map?.invalidateSize(), 50);
  }
}

function setControlsCollapsed(collapsed) {
  controlsCollapsed = false;
  uploadForm.classList.remove("collapsed");
  trayHandle.setAttribute("aria-expanded", String(!collapsed));
  trayHandle.setAttribute("aria-label", collapsed ? "Show controls" : "Collapse controls");
  trayLabel.textContent = collapsed ? "Show Controls" : "Hide Controls";
  updateChromeMetrics();
}

function updateChromeMetrics() {
  const topbarHeight = topbar?.offsetHeight || 0;
  const drawerHeight = uploadForm?.offsetHeight || 0;
  document.documentElement.style.setProperty("--topbar-height", `${topbarHeight}px`);
  document.documentElement.style.setProperty("--drawer-height", `${drawerHeight}px`);
  if (activeView === "map") {
    window.setTimeout(() => map?.invalidateSize(), 0);
  }
}

function statusChip(item) {
  const meta = STATUS_META[item.status] || STATUS_META.queued;
  return `
    <span
      class="status-chip ${item.status}"
      role="status"
      aria-label="${escapeHtml(meta.label)}"
      title="${escapeHtml(meta.label)}"
    >
      <span class="${meta.iconClass}" aria-hidden="true"></span>
    </span>
  `;
}

function occlusionChip(item) {
  return `<span class="occlusion-chip" aria-hidden="true">${escapeHtml(occlusionText(item))}</span>`;
}

function render() {
  const list = Array.from(items.values()).sort((left, right) => left.id.localeCompare(right.id));
  empty.style.display = list.length || activeView === "map" ? "none" : "grid";
  gallery.innerHTML = list.map((item) => `
    <button class="tile" type="button" data-id="${escapeHtml(item.id)}" aria-label="${escapeHtml(item.filename)}">
      <img src="${cardImage(item)}" alt="" loading="lazy" decoding="async">
      ${occlusionChip(item)}
      ${statusChip(item)}
      <span class="tile-overlay">
        <span class="tile-name">${escapeHtml(item.filename)}</span>
        <span class="tile-meta">${escapeHtml(detailText(item))}</span>
      </span>
    </button>
  `).join("");

  const queued = list.filter((item) => item.status === "queued" || item.status === "processing").length;
  queueNote.textContent = queued ? `${queued} waiting or processing` : "";
  if (activeViewerItemId && items.has(activeViewerItemId) && viewer.open) {
    const item = items.get(activeViewerItemId);
    if (item) {
      updateViewer(item);
    }
  }
  if (activeView === "map") renderMap();
}

async function loadItems() {
  const response = await fetch("/api/items");
  const data = await response.json();
  data.items.forEach((item) => items.set(item.id, item));
  render();
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.addEventListener("open", () => { setStatus("connected", "Live updates connected"); });
  ws.addEventListener("close", () => {
    clearTimeout(noticeTimeout);
    setStatus("disconnected", "Live updates reconnecting");
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
    if (data.type === "deleted") {
      items.delete(data.id);
      if (activeViewerItemId === data.id && viewer.open) {
        viewer.close();
      }
    }
    if (data.type === "duplicate") {
      flashNotice(`Duplicate skipped: ${data.filename}`);
    }
    if (data.type === "notice") {
      flashNotice(data.message);
    }
    render();
  });
}

gallery.addEventListener("click", (event) => {
  const tile = event.target.closest(".tile");
  if (!tile) return;
  openViewer(tile.dataset.id);
});

viewToggle.addEventListener("click", () => {
  setView(activeView === "map" ? "gallery" : "map");
});
viewerClose.addEventListener("click", () => viewer.close());
viewerImage.addEventListener("load", () => {
  viewerImage.classList.remove("is-loading");
});
viewerImage.addEventListener("error", () => {
  viewerImage.classList.remove("is-loading");
});
viewer.addEventListener("click", (event) => {
  if (event.target === viewer) viewer.close();
});
viewer.addEventListener("close", () => {
  activeViewerItemId = null;
  document.body.classList.remove("viewer-open");
});
viewerMapLink.addEventListener("click", () => {
  const item = items.get(activeViewerItemId || "");
  if (!item || !itemHasGps(item)) return;
  viewer.close();
  setView("map");
  ensureMap();
  const zoom = zoomForStandaloneItem(item);
  map?.setView([item.gps_latitude, item.gps_longitude], zoom);
});
viewerDelete.addEventListener("click", async () => {
  const imageId = activeViewerItemId;
  if (!imageId) return;
  const response = await fetch(`/api/items/${encodeURIComponent(imageId)}`, { method: "DELETE" });
  if (!response.ok) {
    flashNotice("Delete failed");
    return;
  }
  viewer.close();
  items.delete(imageId);
  render();
  flashNotice("Photo deleted");
});
trayHandle.addEventListener("click", () => {
  setControlsCollapsed(!controlsCollapsed);
});
window.addEventListener("resize", updateChromeMetrics);

input.addEventListener("change", async () => {
  if (!input.files.length) return;
  queueNote.textContent = `Uploading ${input.files.length} photo${input.files.length === 1 ? "" : "s"}...`;
  const formData = new FormData();
  for (const file of input.files) formData.append("files", file);
  input.value = "";

  const response = await fetch("/api/upload", { method: "POST", body: formData });
  if (!response.ok) {
    flashNotice("Upload failed");
    queueNote.textContent = "";
    return;
  }
  const data = await response.json();
  data.items.forEach((item) => items.set(item.id, item));
  render();
});

const startup = Promise.all([loadMapConfig(), loadItems()]).catch(() => {
  flashNotice("Initial load failed");
});

connect();
setControlsCollapsed(false);
updateChromeMetrics();
const stopSplashBob = startSplashBob();
const splashTextAnimation = animateSplashSignoff();

Promise.all([sleep(1000), startup, splashTextAnimation]).finally(() => {
  Promise.resolve()
    .then(() => sleep(SPLASH_POST_TEXT_PAUSE_MS))
    .finally(() => {
      stopSplashBob();
      splash?.classList.add("is-fading");
      window.setTimeout(() => splash?.remove(), 420);
    });
});
