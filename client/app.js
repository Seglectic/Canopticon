const gallery = document.querySelector("#gallery");
const empty = document.querySelector("#empty");
const statusEl = document.querySelector("#status");
const queueNote = document.querySelector("#queueNote");
const input = document.querySelector("#files");
const galleryTab = document.querySelector("#galleryTab");
const mapTab = document.querySelector("#mapTab");
const mapView = document.querySelector("#mapView");
const mapEmpty = document.querySelector("#mapEmpty");
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
  return item.gps_present ? "GPS found" : "No GPS";
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
  if (item.gps_present && item.gps_latitude !== null && item.gps_longitude !== null) {
    bits.push(`${item.gps_latitude.toFixed(5)}, ${item.gps_longitude.toFixed(5)}`);
  }
  return bits.join(" | ");
}

function pinText(item) {
  if (item.occluded_pct === null || item.occluded_pct === undefined) return "...";
  return `${Math.round(item.occluded_pct)}%`;
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

function ensureMap() {
  if (map || !window.L) return;
  map = L.map("map", {
    zoomControl: true,
    attributionControl: true,
  });
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);
  markerLayer = L.layerGroup().addTo(map);
  map.setView([20, 0], 2);
}

function renderMap() {
  ensureMap();
  if (!map || !markerLayer) return;

  markerLayer.clearLayers();
  markers.clear();
  const mapped = Array.from(items.values()).filter(itemHasGps);
  mapEmpty.classList.toggle("hidden", mapped.length > 0);

  const bounds = [];
  mapped.forEach((item) => {
    const marker = L.marker([item.gps_latitude, item.gps_longitude], {
      icon: L.divIcon({
        className: "",
        html: `<div class="map-pin"><span>${escapeHtml(pinText(item))}</span></div>`,
        iconSize: [48, 40],
        iconAnchor: [8, 32],
      }),
      title: item.filename,
    });
    marker.on("click", () => openViewer(item.id));
    marker.addTo(markerLayer);
    markers.set(item.id, marker);
    bounds.push([item.gps_latitude, item.gps_longitude]);
  });

  if (bounds.length === 1) {
    map.setView(bounds[0], 16);
  } else if (bounds.length > 1) {
    map.fitBounds(bounds, { padding: [28, 28], maxZoom: 16 });
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
    renderMap();
    setTimeout(() => map?.invalidateSize(), 50);
  }
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

loadItems();
connect();
