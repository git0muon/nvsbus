/* Compass live tracking -- static build.
 *
 * Runs entirely in the browser: it calls the Compass public-tracking function directly.
 * That endpoint answers with `Access-Control-Allow-Origin: *`, so no backend is needed and
 * this can be served from GitHub Pages. See README.md.
 *
 * Mobile niceties: full-screen toggle, a draggable fleet sheet, tap a bus to centre and
 * follow it, and a locate button that frames the whole fleet.
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
  var following = false;

  var $ = function (id) { return document.getElementById(id); };

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function byId(id) {
    for (var i = 0; i < devices.length; i++) if (devices[i].id === id) return devices[i];
    return null;
  }
  function isNarrow() {
    return window.matchMedia ? window.matchMedia("(max-width: 899px)").matches
                             : window.innerWidth < 900;
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
  var map = L.map("map", {
    zoomSnap: 0.5, zoomControl: false, tap: true, gestureHandling: true
  }).setView([20.95, 72.93], 11);

  L.control.zoom({ position: "topleft" }).addTo(map);

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

  function icon(d) {
    var svg = '<svg xmlns="http://www.w3.org/2000/svg" width="34" height="34" viewBox="0 0 32 32">' +
      '<g transform="rotate(' + (d.heading || 0) + ',16,16)">' +
      '<path d="M16 2 L24 26 L16 20 L8 26 Z" fill="' + COLORS[d.status] +
      '" stroke="' + STROKE[d.status] + '" stroke-width="1.5" stroke-linejoin="round"/></g></svg>';
    return L.divIcon({
      html: svg, className: "pin" + (selected === d.id ? " pin-sel" : ""),
      iconSize: [34, 34], iconAnchor: [17, 17]
    });
  }

  function popup(d) {
    var t = d.lastUpdate ? new Date(d.lastUpdate).toLocaleTimeString() : "N/A";
    var age = d.ageMinutes === null ? "unknown"
            : (d.ageMinutes < 1 ? "just now" : d.ageMinutes + " min ago");
    return "<b>" + esc(d.name) + "</b><br>Status: " + LABEL[d.status] +
      "<br>Speed: " + d.speedKmh + " km/h<br>Heading: " + d.heading + "&deg;" +
      "<br>Last update: " + t + " (" + age + ")" +
      "<br><br><button class=\"followbtn\" data-id=\"" + esc(d.id) +
      "\" style=\"font:inherit;font-size:12px;font-weight:600;padding:5px 10px;border-radius:8px;" +
      "border:1px solid #1d5be0;background:#1d5be0;color:#fff;cursor:pointer\">Follow this bus</button>" +
      ' <a target="_blank" rel="noreferrer" style="font-size:12px" href="https://www.google.com/maps/search/?api=1&query=' +
      d.lat + "," + d.lng + '">Google Maps</a>';
  }

  function fitAll(animate) {
    if (!devices.length) return;
    map.fitBounds(L.latLngBounds(devices.map(function (d) { return [d.lat, d.lng]; })),
      { padding: [60, 60], maxZoom: 16, animate: animate !== false });
  }

  function gentleView(d, zoom) {
    // Two-step ease so long jumps feel smooth rather than teleporting.
    if (!map.flyTo) { map.setView([d.lat, d.lng], zoom); return; }
    map.flyTo([d.lat, d.lng], zoom, { duration: 0.7 });
  }

  function centreOn(d, opts) {
    opts = opts || {};
    var z = opts.zoom || Math.max(map.getZoom(), 15);
    if (isNarrow() && !mainEl.classList.contains("collapsed")) {
      // the sheet covers the lower part of the screen: shift the bus into the free area
      var h = sheet.offsetHeight || 0;
      if (h > 40) {
        var pt = map.project([d.lat, d.lng], z).subtract([0, -h / 2]);
        var ll = map.unproject(pt, z);
        if (map.flyTo) { map.flyTo(ll, z, { duration: 0.7 }); return; }
        map.setView(ll, z);
        return;
      }
    }
    gentleView(d, z);
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
        m.bindPopup(function () { return popup(byId(d.id) || d); });
        m.on("click", function () { select(d.id, { centre: false, fromMarker: true }); });
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

  function counts() {
    return {
      all: devices.length,
      moving: devices.filter(function (d) { return d.status === "moving"; }).length,
      stopped: devices.filter(function (d) { return d.status === "stopped"; }).length,
      offline: devices.filter(function (d) { return d.status === "offline"; }).length
    };
  }

  function renderList() {
    var ul = $("list");
    ul.innerHTML = "";
    var n = counts();
    Array.prototype.forEach.call(document.querySelectorAll(".tab"), function (t) {
      var b = t.querySelector("b");
      if (b) b.textContent = n[t.dataset.f] || 0;
    });

    var rows = devices.filter(function (d) { return filter === "all" || d.status === filter; });
    if (!rows.length) {
      ul.innerHTML = '<li class="empty">No buses in this view.</li>';
      return;
    }
    var frag = document.createDocumentFragment();
    rows.forEach(function (d) {
      var li = document.createElement("li");
      li.className = "row" + (selected === d.id ? " on" : "");
      li.setAttribute("role", "button");
      li.tabIndex = 0;
      li.innerHTML = '<i class="dot ' + d.status + '"></i>' +
        '<span class="nm">' + esc(d.name) + "</span>" +
        '<span class="sp">' + d.speedKmh + " km/h</span>" +
        '<span class="chev">&#8250;</span>';
      li.onclick = function () { select(d.id, { centre: true }); };
      li.onkeydown = function (ev) {
        if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); select(d.id, { centre: true }); }
      };
      frag.appendChild(li);
    });
    ul.appendChild(frag);
  }

  // ---------------------------------------------------------------- selection
  function select(id, opts) {
    opts = opts || {};
    selected = id;
    var d = byId(id);
    renderList();
    Object.keys(markers).forEach(function (k) { markers[k].setIcon(icon(byId(k) || {})); });
    $("follow").disabled = !d;
    if (!d) return;

    if (opts.centre !== false) centreOn(d);
    if (markers[id] && !opts.fromMarker) markers[id].openPopup();
    if (isNarrow() && opts.centre !== false && !mainEl.classList.contains("collapsed")) collapseSheet(true);
    if (opts.follow) setFollowing(true);
  }

  function setFollowing(on) {
    following = on;
    var btn = $("follow");
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    if (on) {
      var d = byId(selected);
      toast(d ? "Following " + d.name : "Select a bus to follow");
    } else {
      toast("Stopped following");
    }
  }

  function toast(msg) {
    var el = $("toast");
    el.textContent = msg;
    el.classList.add("show");
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { el.classList.remove("show"); }, 2200);
  }

  // ---------------------------------------------------------------- sheet
  var app = $("app"), sheet = $("sheet"), mainEl = document.querySelector("main");

  function setSheetOpen(open) {
    mainEl.classList.toggle("collapsed", !open);
    $("toggleside").setAttribute("aria-pressed", open ? "true" : "false");
    $("toggleside").title = open ? "Hide the fleet list" : "Show the fleet list";
    try { localStorage.setItem("sheetOpen", open ? "1" : "0"); } catch (e) {}
    setTimeout(function () { map.invalidateSize(); }, 240);
  }
  // collapseSheet(true) means "put the list away".
  function collapseSheet(collapse) { setSheetOpen(!collapse); }

  // Drag the sheet down to shrink it, up to grow it. Pointer events cover mouse + touch.
  (function dragSheet() {
    var startY = 0, startH = 0, dragging = false;
    function maxH() { return Math.round(window.innerHeight * 0.76); }

    function down(ev) {
      if (!isNarrow()) return;
      dragging = true;
      startY = ev.clientY;
      startH = sheet.offsetHeight;
      sheet.classList.add("dragging");
      sheet.style.height = startH + "px";
      if (ev.pointerId !== undefined && sheet.setPointerCapture) {
        try { sheet.setPointerCapture(ev.pointerId); } catch (e) {}
      }
      document.addEventListener("pointermove", move);
      document.addEventListener("pointerup", up);
      document.addEventListener("pointercancel", up);
    }
    function move(ev) {
      if (!dragging) return;
      var h = Math.max(0, Math.min(maxH(), startH - (ev.clientY - startY)));
      sheet.style.height = h + "px";
      ev.preventDefault();
    }
    function up() {
      if (!dragging) return;
      dragging = false;
      sheet.classList.remove("dragging");
      document.removeEventListener("pointermove", move);
      document.removeEventListener("pointerup", up);
      document.removeEventListener("pointercancel", up);
      var h = sheet.offsetHeight;
      sheet.style.height = "";
      if (h < 90) setSheetOpen(false);
      else if (h > window.innerHeight * 0.5) setSheetOpen(true);
      else { sheet.style.height = ""; }
      setTimeout(function () { map.invalidateSize(); }, 240);
    }
    sheet.addEventListener("pointerdown", function (ev) {
      if (ev.target.closest(".grab") || ev.target.closest(".tabs")) down(ev);
    });
    // keyboard access to the same control
    $("grab").tabIndex = 0;
    $("grab").setAttribute("role", "button");
    $("grab").setAttribute("aria-label", "Toggle the fleet list");
    $("grab").addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        setSheetOpen(mainEl.classList.contains("collapsed"));
      }
    });
    $("grab").addEventListener("click", function () {
      if (!isNarrow()) return;
      setSheetOpen(mainEl.classList.contains("collapsed"));
    });
  })();

  // ---------------------------------------------------------------- fullscreen
  var fsBtn = $("fsbtn");
  function fsElement() {
    return document.fullscreenElement || document.webkitFullscreenElement ||
           document.msFullscreenElement || null;
  }
  // Some browsers only expose the API on <html>, not on an arbitrary element, so
  // check every candidate before deciding full screen is unsupported.
  function fsCandidates() {
    return [app, document.documentElement, document.body].filter(Boolean);
  }
  function fsRequestFn() {
    var list = fsCandidates();
    for (var i = 0; i < list.length; i++) {
      var el = list[i];
      var fn = el.requestFullscreen || el.webkitRequestFullscreen || el.msRequestFullscreen;
      if (fn) return { el: el, fn: fn };
    }
    return null;
  }
  function fsSupported() { return !!fsRequestFn(); }
  // iPhone Safari has no element fullscreen API -- the only true full screen there is
  // adding the page to the Home Screen.
  function isIOS() {
    return /iPad|iPhone|iPod/.test(navigator.userAgent) ||
           (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  }
  function syncFsBtn() {
    fsBtn.setAttribute("aria-pressed", fsElement() ? "true" : "false");
    var label = fsElement() ? "Exit full screen" : "Full screen";
    fsBtn.title = label;
    fsBtn.setAttribute("aria-label", label);
  }
  function toggleFullscreen() {
    if (fsElement()) {
      var exit = document.exitFullscreen || document.webkitExitFullscreen || document.msExitFullscreen;
      if (exit) exit.call(document);
      return;
    }
    var target = fsRequestFn();
    if (!target) {
      toast(isIOS() ? "On iPhone, tap Share \u2192 Add to Home Screen for full screen"
                    : "Full screen is not supported by this browser");
      return;
    }
    var p;
    try {
      p = target.fn.call(target.el, { navigationUI: "hide" });
    } catch (e) {
      try { p = target.fn.call(target.el); } catch (e2) { p = null; }
    }
    if (p && p.catch) p.catch(function () { toast("Full screen was blocked by the browser"); });
  }
  fsBtn.addEventListener("click", toggleFullscreen);
  ["fullscreenchange", "webkitfullscreenchange", "msfullscreenchange"].forEach(function (ev) {
    document.addEventListener(ev, function () {
      syncFsBtn();
      setTimeout(function () { map.invalidateSize(); }, 250);
    });
  });
  if (!fsSupported() && isIOS()) fsBtn.title = "Add to Home Screen for full screen";
  syncFsBtn();

  // ---------------------------------------------------------------- other buttons
  $("locate").addEventListener("click", function () {
    setFollowing(false);
    fitAll(true);
    toast("Showing all buses");
  });
  $("follow").addEventListener("click", function () {
    if (!selected) { toast("Select a bus first"); return; }
    setFollowing($("follow").getAttribute("aria-pressed") !== "true");
  });
  $("toggleside").addEventListener("click", function () {
    setSheetOpen(mainEl.classList.contains("collapsed"));
  });

  // "Follow this bus" inside a popup
  map.on("popupopen", function (e) {
    var node = e.popup.getElement();
    var btn = node && node.querySelector(".followbtn");
    if (btn) {
      btn.onclick = function () { select(btn.getAttribute("data-id"), { centre: true, follow: true }); };
    }
  });

  // ---------------------------------------------------------------- loop
  function setLive(ok) {
    $("livepill").classList.toggle("down", !ok);
    $("livetext").textContent = ok ? "Live" : "Reconnecting\u2026";
  }

  function refresh() {
    var err = $("err");
    return fetchPositions().then(function (data) {
      devices = data.devices;
      ["online", "total", "moving", "stopped"].forEach(function (k) {
        $(k).textContent = data[k];
      });
      $("updated").textContent = new Date(data.fetchedAt).toLocaleTimeString();
      err.style.display = "none";
      setLive(true);
      renderMarkers();
      renderList();
      if (selected && following) {
        var d = byId(selected);
        if (d) map.panTo([d.lat, d.lng], { animate: true, duration: 0.5 });
      }
      if (!fitted && devices.length) { fitted = true; fitAll(true); }
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

  // ---------------------------------------------------------------- wire up tabs
  Array.prototype.forEach.call(document.querySelectorAll(".tab"), function (t) {
    t.addEventListener("click", function () {
      Array.prototype.forEach.call(document.querySelectorAll(".tab"), function (x) {
        x.classList.remove("on");
      });
      t.classList.add("on");
      filter = t.dataset.f;
      renderList();
    });
  });

  // ---------------------------------------------------------------- start
  var startOpen = !isNarrow();          // phones: start with the map fully visible
  try {
    var saved = localStorage.getItem("sheetOpen");
    if (saved !== null) startOpen = (saved === "1");
  } catch (e) {}
  setSheetOpen(startOpen);

  // Make sure Leaflet measures the container once layout has settled.
  map.invalidateSize();
  window.addEventListener("load", function () { map.invalidateSize(); });
  window.addEventListener("resize", function () { map.invalidateSize(); });
  window.addEventListener("orientationchange", function () {
    setTimeout(function () { map.invalidateSize(); }, 300);
  });

  tick();
  setInterval(function () { if (!document.hidden) tick(); }, REFRESH_MS);
  document.addEventListener("visibilitychange", function () { if (!document.hidden) tick(); });

  // exposed for the offline test harness
  window.__tracker = {
    select: select, fitAll: fitAll, counts: counts,
    setFollowing: setFollowing, setSheetOpen: setSheetOpen,
    toggleFullscreen: toggleFullscreen, normalise: normalise, tick: tick,
    isNarrow: isNarrow, fsElement: fsElement, fsSupported: fsSupported,
    isFollowing: function () { return following; },
    state: function () {
      return { selected: selected, following: following, filter: filter,
               devices: devices.length, collapsed: mainEl.classList.contains("collapsed"),
               followBtnPressed: $("follow").getAttribute("aria-pressed") };
    }
  };
})();
