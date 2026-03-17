console.log("app.js loaded ✅");

const SPOTS = [
  {
    id: "garage-a",
    name: "Garage A",
    type: "garage",
    pricePerHour: 6,
    available: true,
    tags: ["ev"],
    lat: 40.7433,
    lng: -74.0288,
  },
  {
    id: "garage-b",
    name: "Garage B",
    type: "garage",
    pricePerHour: 5,
    available: false,
    tags: ["accessible"],
    lat: 40.7426,
    lng: -74.0302,
  },
  {
    id: "street-1",
    name: "Street Spot 1",
    type: "street",
    pricePerHour: 3,
    available: true,
    tags: [],
    lat: 40.7419,
    lng: -74.0291,
  },
  {
    id: "street-2",
    name: "Street Spot 2",
    type: "street",
    pricePerHour: 4,
    available: true,
    tags: ["accessible"],
    lat: 40.7442,
    lng: -74.0273,
  },
];

let activeType = "all";
let query = "";
let selectedSpotId = null;

const elSpotsList = document.getElementById("spotsList");
const elChips = document.getElementById("chips");
const elSearch = document.getElementById("searchInput");
// --- FAQ interactive panel ---
const FAQ = {
  rt: {
    title: "Is this real-time availability?",
    body: "Right now it’s a demo. “Reserve” toggles availability in-memory (front-end) so you can show the full flow. Next step is a DB + API.",
  },
  key: {
    title: "Do I need an API key?",
    body: "If you use Google Maps Embed like we are now, you typically don’t need a key for basic embed usage—but features are limited. For full Google Maps JavaScript API (custom markers, routing inside your UI), you’ll need an API key + billing enabled.",
  },
  reserve: {
    title: "How do reservations work later?",
    body: "Add a Spots table and Reservations table. Then build endpoints like /api/spots and /api/reservations. The UI can fetch availability and create reservations, with auth and rate limits.",
  },
  privacy: {
    title: "Do you track my location?",
    body: "Only if you allow browser location permission. If denied, we fall back to a default city and you can manually change it. In this demo, we don’t store location history.",
  },
};

function initFAQ() {
  const btns = document.querySelectorAll(".faq-q");
  const titleEl = document.getElementById("faqTitle");
  const bodyEl = document.getElementById("faqBody");
  const helpful = document.getElementById("faqHelpful");
  const notHelpful = document.getElementById("faqNotHelpful");
  const feedback = document.getElementById("faqFeedback");

  if (!btns.length || !titleEl || !bodyEl) return;

  function setActive(key) {
    const item = FAQ[key];
    if (!item) return;

    btns.forEach((b) => {
      const on = b.dataset.faq === key;
      b.classList.toggle("active", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    });

    titleEl.textContent = item.title;
    bodyEl.textContent = item.body;
    if (feedback) feedback.textContent = "";
  }

  btns.forEach((b) => {
    b.addEventListener("click", () => setActive(b.dataset.faq));
  });

  helpful?.addEventListener("click", () => {
    if (feedback) feedback.textContent = "Thanks — noted!";
  });
  notHelpful?.addEventListener("click", () => {
    if (feedback) feedback.textContent = "Got it — we’ll improve this answer.";
  });

  // ensure initial state
  const activeBtn = document.querySelector(".faq-q.active") || btns[0];
  setActive(activeBtn.dataset.faq);
}
const modalBackdrop = document.getElementById("modalBackdrop");
const modalClose = document.getElementById("modalClose");
const modalTitle = document.getElementById("modalTitle");
const modalMeta = document.getElementById("modalMeta");
const modalType = document.getElementById("modalType");
const modalPrice = document.getElementById("modalPrice");
const modalStatus = document.getElementById("modalStatus");
const reserveBtn = document.getElementById("reserveBtn");
const directionsBtn = document.getElementById("directionsBtn");

const gmap = document.getElementById("gmap");
const gmap2 = document.getElementById("gmap2");
const currentCity = document.getElementById("currentCity");
const changeCityBtn = document.getElementById("changeCityBtn");

document.getElementById("year").textContent = new Date().getFullYear();

/* ---------- Google Maps embed (IN-SITE) ---------- */
/* Corrected Google Maps embed logic */
function setGoogleMap(lat, lng, z = 16) {
  const src = `https://www.google.com/maps/embed/v1/view?key=YOUR_API_KEY&center=${lat},${lng}&zoom=${z}`;

  const demoSrc = `https://maps.google.com/maps?q=${lat},${lng}&z=${z}&output=embed`;

  if (gmap) gmap.src = demoSrc;
  if (gmap2) gmap2.src = demoSrc;
}

/* ---------- Location label (no more “Hoboken” hardcode) ---------- */
function setCityLabel(text) {
  if (currentCity) currentCity.textContent = text;
}

// We can’t do real reverse geocoding without an API, so we do:
// - show “Near you” + lat/lng
// - allow user to override via prompt
function detectLocation() {
  if (!navigator.geolocation) {
    setCityLabel("Location unavailable");
    return;
  }

  navigator.geolocation.getCurrentPosition(
    (pos) => {
      const { latitude, longitude } = pos.coords;
      setCityLabel(
        `Near you • ${latitude.toFixed(3)}, ${longitude.toFixed(3)}`,
      );
      setGoogleMap(latitude, longitude, 15);
    },
    () => setCityLabel("Location blocked"),
    { enableHighAccuracy: true, timeout: 7000 },
  );
}

if (changeCityBtn) {
  changeCityBtn.addEventListener("click", () => {
    const v = prompt("Enter a city or address (demo):", "New York, NY");
    if (!v) return;
    setCityLabel(v);
    const src = `https://www.google.com/maps?q=${encodeURIComponent(v)}&z=13&output=embed`;
    if (gmap) gmap.src = src;
    if (gmap2) gmap2.src = src;
  });
}

/* ---------- FAQ accordion (fix) ---------- */
document.querySelectorAll(".faq-q").forEach((btn) => {
  btn.addEventListener("click", () => {
    const isOpen = btn.getAttribute("aria-expanded") === "true";

    // close others (Uber-like)
    document
      .querySelectorAll(".faq-q")
      .forEach((b) => b.setAttribute("aria-expanded", "false"));

    // toggle this one
    btn.setAttribute("aria-expanded", String(!isOpen));
  });
});

/* ---------- Spots UI ---------- */
function matchesType(spot) {
  if (activeType === "all") return true;
  if (activeType === "ev") return spot.tags.includes("ev");
  if (activeType === "accessible") return spot.tags.includes("accessible");
  return spot.type === activeType;
}

function matchesQuery(spot) {
  if (!query) return true;
  const q = query.toLowerCase();
  return (
    spot.name.toLowerCase().includes(q) || spot.type.toLowerCase().includes(q)
  );
}

function renderSpots() {
  if (!elSpotsList) return;

  const filtered = SPOTS.filter((s) => matchesType(s) && matchesQuery(s));
  elSpotsList.innerHTML = "";

  filtered.forEach((s) => {
    const el = document.createElement("div");
    el.className = "card";
    el.innerHTML = `
      <div class="card-title">${s.name}</div>
      <div class="card-row">
        <div>Type: ${s.type}</div>
        <div>$${s.pricePerHour}/hr</div>
      </div>
      <div class="badges">
        <span class="badge ${s.available ? "ok" : "no"}">${s.available ? "Available" : "Taken"}</span>
        ${s.tags.includes("ev") ? `<span class="badge">EV</span>` : ""}
        ${s.tags.includes("accessible") ? `<span class="badge">ACCESSIBLE</span>` : ""}
      </div>
    `;
    el.addEventListener("click", () => openSpot(s.id));
    elSpotsList.appendChild(el);
  });
}

function openSpot(spotId) {
  const spot = SPOTS.find((x) => x.id === spotId);
  if (!spot) return;

  selectedSpotId = spotId;

  if (modalTitle) modalTitle.textContent = spot.name;
  if (modalMeta)
    modalMeta.textContent = `${spot.type.toUpperCase()} • $${spot.pricePerHour}/hr`;
  if (modalType) modalType.textContent = spot.type;
  if (modalPrice) modalPrice.textContent = `$${spot.pricePerHour}/hr`;
  if (modalStatus)
    modalStatus.textContent = spot.available ? "Available" : "Taken";

  // Update map in-site (like Uber flow)
  setGoogleMap(spot.lat, spot.lng, 16);

  if (modalBackdrop) modalBackdrop.classList.remove("hidden");
}

function closeModal() {
  if (modalBackdrop) modalBackdrop.classList.add("hidden");
}

if (modalClose) modalClose.addEventListener("click", closeModal);
if (modalBackdrop) {
  modalBackdrop.addEventListener("click", (e) => {
    if (e.target === modalBackdrop) closeModal();
  });
}

if (reserveBtn) {
  reserveBtn.addEventListener("click", () => {
    const spot = SPOTS.find((x) => x.id === selectedSpotId);
    if (!spot) return;
    spot.available = !spot.available;
    renderSpots();
    openSpot(spot.id);
  });
}

if (directionsBtn) {
  directionsBtn.addEventListener("click", () => {
    const spot = SPOTS.find((x) => x.id === selectedSpotId);
    if (!spot) return;

    // Keep it IN-SITE: just focus map + scroll to it
    setGoogleMap(spot.lat, spot.lng, 16);
    document
      .getElementById("spots")
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
    closeModal();
  });
}

/* Chips */
if (elChips) {
  elChips.addEventListener("click", (e) => {
    const btn = e.target.closest(".chip");
    if (!btn) return;
    activeType = btn.dataset.type || "all";
    document
      .querySelectorAll(".chip")
      .forEach((b) => b.classList.remove("chip-active"));
    btn.classList.add("chip-active");
    renderSpots();
  });
}

/* Search */
if (elSearch) {
  elSearch.addEventListener("input", (e) => {
    query = e.target.value || "";
    renderSpots();
  });
}

/* Boot */
detectLocation();
renderSpots();
initFAQ();
