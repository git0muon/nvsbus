/* Compass live tracking -- static build.
 *
 * Runs entirely in the browser: it calls the Compass public-tracking function directly.
 * That endpoint answers with `Access-Control-Allow-Origin: *`, so no backend is needed and
 * this can be served from GitHub Pages. See README.md.
 */
(function () {
  "use strict";

  var CFG = window.TRACKER_CONFIG || {};
  var REFRESH_MS = (CFG.refreshSeconds || 10) * 1000;
  var OFFLINE_AFTER_MS = (CFG.offlineAfterHours || 3) * 60 * 60 * 1000;

  var COLORS = { moving: "#16a34a", stopped: "#dc2626", offline: "#94a3b8" };
  var STROKE = { moving: "#15803d", stopped: "#b91c1c", offline: "#64748b" };
  var LABEL = { moving: "Moving", stopped: "Stopped", offline: "Offline" };

  var devices = [], markers = {}, selected = null, filter = "all", fitted = false;

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // ---------------------------------------------------------------- data
  function normalise(payload) {
    var nowMs = Date.now();
    var out = [];
    (payload.positions || []).forEach(function (item) {
      var lat = parseFloat(item.latitude), lng = parseFloat(item.longitude);
      if (!isFinite(lat) || !isFinite(lng)) return;

      var speed = parseFloat(item.speed || 0);        // metres/second from the API
      var last = item.lastUpdate, ageMs = null;
      if (last) {
        var t = Date.parse(last);
        if (!isNaN(t)) ageMs = nowMs - t;
      }
      var offline = ageMs === null || ageMs > OFFLINE_AFTER_MS;
      var moving = !offline && speed > 1;

      out.push({
        id: String(item.id || item.name || lat + "," + lng),
        name: item.name || ("device-" + item.id),
        lat: lat, lng: lng,
        speedKmh: Math.round(speed * 3.6 * 10) / 10,
        heading: Math.round(parseFloat(item.heading || 0)),
        lastUpdate: last,
        ageMinutes: ageMs === null ? null : Math.round(ageMs / 6000) / 10,
        status: offline ? "offline" : (moving ? "moving" : "stopped")
      });
    });
    out.sort(function (a, b) { return a.name.localeCompare(b.name, undefined, { numeric: true }); });

    return {
      ok: true,
      devices: out,
      total: out.length,
      online: out.filter(function (d) { return d.status !== "offline"; }).length,
      moving: out.filter(function (d) { return d.status === "moving"; }).length,
      stopped: out.filter(function (d) { return d.status === "stopped"; }).length,
      fetchedAt: payload.fetched_at || new Date().toISOString()
    };
  }

  function fetchPositions() {
    if (!CFG.token || !CFG.supabaseUrl || !CFG.supabaseAnonKey) {
      return Promise.reject(new Error("config.js is missing token / supabaseUrl / supabaseAnonKey"));
    }
    return fetch(CFG.supabaseUrl + "/functions/v1/public-tracking", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "apikey": CFG.supabaseAnonKey,
        "Authorization": "Bearer " + CFG.supabaseAnonKey
      },
      body: JSON.stringify({ token: CFG.token })
    }).then(function (res) {
      if (!res.ok) throw new Error("tracking API HTTP " + res.status);
      return res.json();
    }).then(normalise);
  }

  // ---------------------------------------------------------------- map
  var map = L.map("map", { zoomSnap: 0.5 }).setView([20.95, 72.93], 11);

  var ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/";
  var imagery = L.tileLayer(ESRI + "World_Imagery/MapServer/tile/{z}/{y}/{x}",
    { maxZoom: 19, maxNativeZoom: 18, attribution: "Imagery &copy; Esri, Maxar, Earthstar Geographics" });
  var labels = L.tileLayer(ESRI + "Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
    { maxZoom: 19, maxNativeZoom: 18, attribution: "Labels &copy; Esri" });
  var roads = L.tileLayer(ESRI + "Reference/World_Transportation/MapServer/tile/{z}/{y}/{x}",
    { maxZoom: 19, maxNativeZoom: 18 });
  var satellite = L.layerGroup([imagery, roads, labels]).addTo(map);
  var satPlain = L.layerGroup([imagery]);
  var osm = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    { maxZoom: 19, attribution: "&copy; OpenStreetMap contributors" });

  L.control.layers(
    { "Satellite + labels": satellite, "Satellite only": satPlain, "Standard": osm },
    null, { position: "topleft" }
  ).addTo(map);

  var FitCtl = L.Control.extend({
    onAdd: function () {
      var b = L.DomUtil.create("button", "fit");
      b.textContent = "FIT";
      b.title = "Fit all buses";
      L.DomEvent.disableClickPropagation(b);
      b.onclick = fitAll;
      return b;
    }
  });
  new FitCtl({ position: "topleft" }).addTo(map);

  function icon(d) {
    var svg = '<svg xmlns="http://www.w3.org/2000/svg" width="34" height="34" viewBox="0 0 32 32">' +
      '<g transform="rotate(' + (d.heading || 0) + ',16,16)">' +
      '<path d="M16 2 L24 26 L16 20 L8 26 Z" fill="' + COLORS[d.status] +
      '" stroke="' + STROKE[d.status] + '" stroke-width="1.5" stroke-linejoin="round"/></g></svg>';
    return L.divIcon({ html: svg, className: "", iconSize: [34, 34], iconAnchor: [17, 17] });
  }

  function popup(d) {
    var t = d.lastUpdate ? new Date(d.lastUpdate).toLocaleTimeString() : "N/A";
    var age = d.ageMinutes === null ? "unknown"
            : (d.ageMinutes < 1 ? "just now" : d.ageMinutes + " min ago");
    return "<b>" + esc(d.name) + "</b><br>Status: " + LABEL[d.status] +
      "<br>Speed: " + d.speedKmh + " km/h<br>Heading: " + d.heading + "&deg;" +
      "<br>Last update: " + t + " (" + age + ")" +
      '<br><a target="_blank" rel="noreferrer" href="https://www.google.com/maps/search/?api=1&query=' +
      d.lat + "," + d.lng + '">Open in Google Maps</a>';
  }

  function fitAll() {
    if (!devices.length) return;
    map.fitBounds(L.latLngBounds(devices.map(function (d) { return [d.lat, d.lng]; })),
      { padding: [60, 60], maxZoom: 16 });
  }

  function renderMarkers() {
    var seen = {};
    devices.forEach(function (d) {
      seen[d.id] = true;
      var m = markers[d.id];
      if (!m) {
        m = L.marker([d.lat, d.lng], { icon: icon(d) }).addTo(map);
        m.bindTooltip(esc(d.name),
          { permanent: true, direction: "right", offset: [14, 0], className: "bt" });
        m.bindPopup(function () {
          return popup(devices.filter(function (x) { return x.id === d.id; })[0] || d);
        });
        m.on("click", function () { selected = d.id; renderList(); });
        markers[d.id] = m;
      } else {
        m.setLatLng([d.lat, d.lng]);
        m.setIcon(icon(d));
        m.setTooltipContent(esc(d.name));
      }
    });
    Object.keys(markers).forEach(function (id) {
      if (!seen[id]) { map.removeLayer(markers[id]); delete markers[id]; }
    });
  }

  function renderList() {
    var ul = document.getElementById("list");
    ul.innerHTML = "";
    var rows = devices.filter(function (d) { return filter === "all" || d.status === filter; });
    if (!rows.length) {
      ul.innerHTML = '<li class="row" style="cursor:default;color:#64748b">No buses in this view</li>';
      return;
    }
    rows.forEach(function (d) {
      var li = document.createElement("li");
      li.className = "row" + (selected === d.id ? " on" : "");
      li.innerHTML = '<i class="dot ' + d.status + '"></i><span class="nm">' + esc(d.name) +
        '</span><span class="sp">' + d.speedKmh + " km/h</span>";
      li.onclick = function () {
        selected = d.id;
        renderList();
        map.setView([d.lat, d.lng], Math.max(map.getZoom(), 15));
        if (markers[d.id]) markers[d.id].openPopup();
      };
      ul.appendChild(li);
    });
  }

  // ---------------------------------------------------------------- loop
  function setLive(ok) {
    document.getElementById("livepill").classList.toggle("down", !ok);
    document.getElementById("livetext").textContent = ok ? "Live" : "Reconnecting\u2026";
  }

  function refresh() {
    var err = document.getElementById("err");
    return fetchPositions().then(function (data) {
      devices = data.devices;
      ["online", "total", "moving", "stopped"].forEach(function (k) {
        document.getElementById(k).textContent = data[k];
      });
      document.getElementById("updated").textContent =
        new Date(data.fetchedAt).toLocaleTimeString();
      err.style.display = "none";
      setLive(true);
      renderMarkers();
      renderList();
      if (!fitted && devices.length) { fitted = true; fitAll(); }
    }).catch(function (e) {
      err.textContent = "Could not load tracking data: " + (e && e.message ? e.message : e);
      err.style.display = "block";
      setLive(false);
    });
  }

  var busy = false;
  function tick() {
    if (busy) return Promise.resolve();
    busy = true;
    return refresh().then(function () { busy = false; }, function () { busy = false; });
  }

  // ---------------------------------------------------------------- UI
  Array.prototype.forEach.call(document.querySelectorAll(".tab"), function (t) {
    t.onclick = function () {
      Array.prototype.forEach.call(document.querySelectorAll(".tab"), function (x) {
        x.classList.remove("on");
      });
      t.classList.add("on");
      filter = t.dataset.f;
      renderList();
    };
  });

  var mainEl = document.querySelector("main");
  function setSide(open) {
    mainEl.classList.toggle("collapsed", !open);
    document.getElementById("toggleside").textContent = open ? "\u2630 Hide list" : "\u2630 Show list";
    setTimeout(function () { map.invalidateSize(); }, 50);   // let Leaflet fill the new space
    try { localStorage.setItem("sideOpen", open ? "1" : "0"); } catch (e) {}
  }
  document.getElementById("toggleside").onclick = function () {
    setSide(mainEl.classList.contains("collapsed"));
  };

  var startOpen = true;
  try { startOpen = localStorage.getItem("sideOpen") !== "0"; } catch (e) {}
  setSide(startOpen);

  tick();
  setInterval(function () { if (!document.hidden) tick(); }, REFRESH_MS);
  document.addEventListener("visibilitychange", function () { if (!document.hidden) tick(); });
})();
