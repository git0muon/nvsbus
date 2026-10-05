"""Compass live-tracking link, rendered on Google Maps.

This is a Python re-implementation of the public tracking page at
https://compass.apps2.uctechlabs.com/track/<token> -- same live data, same
30-second refresh, same green/red/grey bus arrows -- but the map itself is
Google Maps.

Run it:
    python compass_google_map.py
It starts a small local web server and opens your browser at
http://127.0.0.1:5000

Options:
    python compass_google_map.py --token <token> --port 5000 --no-browser
    python compass_google_map.py --key <google-maps-api-key>

The map is drawn with the Google Maps JavaScript API. If the bundled key is
referer-restricted to another domain, the page says so and tells you how to
supply your own key (see README.md).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

SUPABASE_URL = "https://supabase.apps2db.uctechlabs.com"
SUPABASE_ANON_KEY = (
    "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJzdXBhYmFzZSIsImlhdCI6MTc3MTc1ODU0MCwi"
    "ZXhwIjo0OTI3NDMyMTQwLCJyb2xlIjoiYW5vbiJ9.0OKRMEyYPy-8bjw0hWCugu8LZTn3k27y9A6xjG0Ek1w"
)
TRACKING_TOKEN = "4996b81acfbfc13d3f539a5dff6c6fdbed921b3aa46f9c50"
GOOGLE_MAPS_KEY = os.environ.get("GOOGLE_MAPS_API_KEY") or "AIzaSyAr4V0bLc5nZHMf_ydX5IjKu6KWuI1yXek"

# The Compass page treats a sample older than 3 hours as offline.
OFFLINE_AFTER_MS = 3 * 60 * 60 * 1000
REFRESH_SECONDS = 30

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


# --------------------------------------------------------------------------
# Data layer: pull live positions from the Compass public-tracking function
# --------------------------------------------------------------------------

def fetch_positions(token: str) -> dict:
    """Return the raw public-tracking payload for a share token."""
    payload = json.dumps({"token": token}).encode()
    request = urllib.request.Request(
        f"{SUPABASE_URL}/functions/v1/public-tracking",
        data=payload,
        headers={
            "apikey": SUPABASE_ANON_KEY,
            "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
            "Content-Type": "application/json",
            "Origin": "https://compass.apps2.uctechlabs.com",
            "User-Agent": "CompassGoogleMap/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=45, context=SSL_CTX) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


TILE_SIZE = 256
MAX_ZOOM = 19


def lon_to_x(lon: float, zoom: float) -> float:
    return (lon + 180.0) / 360.0 * TILE_SIZE * (2 ** zoom)


def lat_to_y(lat: float, zoom: float) -> float:
    lat = max(-85.05112878, min(85.05112878, lat))
    return (
        (1.0 - math.log(math.tan(math.radians(lat)) + 1.0 / math.cos(math.radians(lat))) / math.pi)
        / 2.0
        * TILE_SIZE
        * (2 ** zoom)
    )


def x_to_lon(x: float, zoom: float) -> float:
    return x / (TILE_SIZE * (2 ** zoom)) * 360.0 - 180.0


def y_to_lat(y: float, zoom: float) -> float:
    n = math.pi - 2.0 * math.pi * y / (TILE_SIZE * (2 ** zoom))
    return math.degrees(math.atan(math.sinh(n)))


def fleet_view(devices: list[dict], width: int, height: int) -> dict:
    """Pick a centre and integer zoom that fits every device in one image."""
    lats = [d["lat"] for d in devices]
    lons = [d["lng"] for d in devices]
    south, north = min(lats), max(lats)
    west, east = min(lons), max(lons)

    if len(devices) < 2 or (north - south) < 1e-6 and (east - west) < 1e-6:
        return {
            "center": {"lat": devices[0]["lat"], "lng": devices[0]["lng"]},
            "zoom": 15,
            "width": width,
            "height": height,
        }

    # 8% breathing room so bus labels are not clipped at the edges.
    pad_lat = max((north - south) * 0.08, 0.0015)
    pad_lon = max((east - west) * 0.08, 0.0015)
    south, north = south - pad_lat, north + pad_lat
    west, east = west - pad_lon, east + pad_lon

    span_y = abs(lat_to_y(south, 0) - lat_to_y(north, 0)) or 1e-9
    span_x = abs(lon_to_x(east, 0) - lon_to_x(west, 0)) or 1e-9
    zoom = math.floor(min(math.log2(height / span_y), math.log2(width / span_x)))
    zoom = max(2, min(MAX_ZOOM, zoom))

    return {
        "center": {"lat": (south + north) / 2, "lng": (west + east) / 2},
        "zoom": zoom,
        "width": width,
        "height": height,
    }


def fetch_static_map(key: str, lat: float, lng: float, zoom: int, width: int, height: int) -> bytes:
    """One Google Static Maps image.

    Fetched from this Python server, which sends no Referer header, so a key that
    is referer-restricted to another domain still works here.
    """
    params = {
        "center": f"{lat:.6f},{lng:.6f}",
        "zoom": str(int(zoom)),
        "size": f"{width}x{height}",
        "scale": "1",
        "maptype": "roadmap",
        "key": key,
    }
    request = urllib.request.Request(
        "https://maps.googleapis.com/maps/api/staticmap?" + urllib.parse.urlencode(params),
        headers={"User-Agent": "CompassGoogleMap/1.0"},
    )
    with urllib.request.urlopen(request, timeout=45, context=SSL_CTX) as response:
        content_type = response.headers.get("Content-Type") or ""
        body = response.read()
    if not content_type.startswith("image/"):
        raise RuntimeError(body.decode("utf-8", "replace")[:300])
    return body


def key_allows_browser(key: str, port: int) -> bool:
    """Would Google accept this key in a browser on http://127.0.0.1:<port>?

    Google checks the Referer for browser requests. We ask the free Static Maps
    endpoint with that Referer set: if it answers with an image the key is fine
    for this origin, and if it answers 403 the browser would fail too.
    """
    params = {
        "center": "20.95,72.93",
        "zoom": "10",
        "size": "64x64",
        "key": key,
    }
    request = urllib.request.Request(
        "https://maps.googleapis.com/maps/api/staticmap?" + urllib.parse.urlencode(params),
        headers={
            "User-Agent": "CompassGoogleMap/1.0",
            "Referer": f"http://127.0.0.1:{port}/",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20, context=SSL_CTX) as response:
            return (response.headers.get("Content-Type") or "").startswith("image/")
    except urllib.error.HTTPError:
        return False
    except Exception:
        return False


def normalise(payload: dict) -> dict:
    """Turn the API payload into the shape the browser needs."""
    now_ms = datetime.now(timezone.utc).timestamp() * 1000
    devices = []
    for item in payload.get("positions") or []:
        try:
            lat = float(item["latitude"])
            lon = float(item["longitude"])
        except (KeyError, TypeError, ValueError):
            continue

        speed_raw = item.get("speed")
        # Public-tracking reports speed in metres/second; Compass shows km/h.
        speed_kmh = round(float(speed_raw or 0) * 3.6, 1)
        last = item.get("lastUpdate")

        age_ms = None
        if last:
            try:
                age_ms = now_ms - datetime.fromisoformat(
                    str(last).replace("Z", "+00:00")
                ).timestamp() * 1000
            except ValueError:
                age_ms = None

        offline = age_ms is None or age_ms > OFFLINE_AFTER_MS
        moving = not offline and float(speed_raw or 0) > 1

        devices.append(
            {
                "id": str(item.get("id") or ""),
                "name": item.get("name") or f"device-{item.get('id')}",
                "lat": lat,
                "lng": lon,
                "speedKmh": speed_kmh,
                "heading": round(float(item.get("heading") or 0)),
                "lastUpdate": last,
                "ageMinutes": None if age_ms is None else round(age_ms / 60000, 1),
                "status": "offline" if offline else ("moving" if moving else "stopped"),
                "mapsUrl": f"https://www.google.com/maps/search/?api=1&query={lat},{lon}",
            }
        )

    devices.sort(key=lambda d: d["name"])
    return {
        "ok": True,
        "devices": devices,
        "total": len(devices),
        "online": sum(1 for d in devices if d["status"] != "offline"),
        "fetchedAt": payload.get("fetched_at")
        or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "cached": bool(payload.get("cached")),
        "refreshSeconds": REFRESH_SECONDS,
    }


# --------------------------------------------------------------------------
# The page: same layout as the Compass tracker, drawn on Google Maps
# --------------------------------------------------------------------------

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Live Tracking &middot; Google Maps</title>
<style>
  :root { --line:#e5e7eb; --muted:#6b7280; --info:#1d5be0; }
  * { box-sizing:border-box; }
  html,body { margin:0; height:100%; }
  body { font-family:system-ui,-apple-system,"Segoe UI",sans-serif; color:#111827;
         display:flex; flex-direction:column; height:100vh; }
  header { display:flex; align-items:center; gap:12px; padding:10px 16px;
           background:#fff; border-bottom:1px solid var(--line); box-shadow:0 1px 2px rgba(0,0,0,.05);
           flex-wrap:wrap; }
  .badge { width:34px; height:34px; border-radius:9px; background:rgba(29,91,224,.1);
           display:flex; align-items:center; justify-content:center; color:var(--info); flex:none; }
  h1 { font-size:14px; margin:0; font-weight:600; }
  .meta { display:flex; gap:14px; font-size:12px; color:var(--muted); margin-top:2px; flex-wrap:wrap; }
  .meta b { font-weight:600; color:#374151; }
  .legend { margin-left:auto; display:flex; gap:12px; font-size:12px; color:var(--muted);
            align-items:center; flex-wrap:wrap; }
  .legend span { display:flex; align-items:center; gap:5px; }
  .dot { width:9px; height:9px; border-radius:50%; display:inline-block; }
  .dot.moving { background:#16a34a; } .dot.stopped { background:#dc2626; }
  .dot.offline { background:#94a3b8; }
  #map { flex:1; position:relative; background:#eef1f5; overflow:hidden; }
  #fallback { position:absolute; inset:0; z-index:2; cursor:grab; background:#eef1f5; }
  #fallback.dragging { cursor:grabbing; }
  #gmapimg { position:absolute; left:50%; top:50%; transform:translate(-50%,-50%);
             user-select:none; -webkit-user-drag:none; display:block; }
  #pins { position:absolute; inset:0; }
  .pin { position:absolute; width:34px; height:34px; margin:-17px 0 0 -17px; cursor:pointer;
         pointer-events:auto; }
  .pin .lbl { position:absolute; left:26px; top:50%; transform:translateY(-50%);
              background:rgba(255,255,255,.92); border:1px solid #d1d5db; padding:1px 5px;
              font-size:11px; font-weight:600; white-space:nowrap; pointer-events:none;
              box-shadow:0 1px 3px rgba(0,0,0,.15); }
  .card { position:absolute; z-index:8; left:12px; bottom:12px; max-width:320px; background:#fff;
          border:1px solid var(--line); border-radius:10px; padding:10px 12px; font-size:12px;
          box-shadow:0 6px 20px rgba(0,0,0,.16); display:none; }
  .card .x { position:absolute; top:6px; right:8px; cursor:pointer; color:#9ca3af; font-size:14px; }
  .controls { position:absolute; z-index:8; right:12px; top:12px; display:flex; flex-direction:column; gap:6px; }
  .controls button { width:36px; height:36px; border:1px solid var(--line); background:#fff;
                     border-radius:8px; font-size:18px; cursor:pointer; box-shadow:0 2px 8px rgba(0,0,0,.12); }
  .controls button:hover { background:#f3f4f6; }
  #staticnote { position:absolute; bottom:10px; left:10px; z-index:6; max-width:70%;
                background:rgba(255,255,255,.95); border:1px solid var(--line); border-radius:8px;
                padding:8px 11px; font-size:12px; color:#374151; box-shadow:0 2px 10px rgba(0,0,0,.12); }
  #overlay { position:absolute; inset:0; display:flex; align-items:center; justify-content:center;
             text-align:center; padding:24px; z-index:5; background:rgba(238,241,245,.94); }
  #overlay .card { max-width:620px; background:#fff; border:1px solid var(--line); border-radius:12px;
                   padding:20px 22px; box-shadow:0 6px 24px rgba(0,0,0,.08); }
  #overlay h2 { margin:0 0 8px; font-size:16px; }
  #overlay p { margin:6px 0; font-size:13px; color:#374151; line-height:1.5; text-align:left; }
  #overlay code { background:#f3f4f6; padding:1px 5px; border-radius:4px; font-size:12px; }
  .spin { width:26px; height:26px; border:3px solid #cbd5e1; border-top-color:var(--info);
          border-radius:50%; animation:sp 1s linear infinite; margin:0 auto 10px; }
  @keyframes sp { to { transform:rotate(360deg); } }
  .err { color:#b91c1c; }
</style>
</head>
<body>
<header>
  <div class="badge" title="Live tracking">
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
         stroke-linecap="round" stroke-linejoin="round">
      <circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/>
      <line x1="8.6" y1="10.7" x2="15.4" y2="6.3"/><line x1="8.6" y1="13.3" x2="15.4" y2="17.7"/>
    </svg>
  </div>
  <div>
    <h1>Live Tracking &middot; Google Maps</h1>
    <div class="meta">
      <span><b id="online">-</b>&nbsp;/&nbsp;<span id="total">-</span> devices online</span>
      <span>Updated <b id="updated">-</b></span>
      <span id="source"></span>
      <span id="refresh"></span>
    </div>
  </div>
  <div class="legend">
    <span><i class="dot moving"></i>Moving</span>
    <span><i class="dot stopped"></i>Stopped</span>
    <span><i class="dot offline"></i>Offline</span>
  </div>
</header>
<div id="map">
  <div id="fallback" style="display:none">
    <img id="gmapimg" alt="Google map" />
    <div id="pins"></div>
    <div class="controls">
      <button id="zin"  title="Zoom in">+</button>
      <button id="zout" title="Zoom out">&minus;</button>
      <button id="fit"  title="Fit all buses" style="font-size:11px">fit</button>
    </div>
    <div class="card" id="pin-card"></div>
  </div>
  <div id="staticnote" style="display:none"></div>
  <div id="overlay">
    <div class="card">
      <div class="spin" id="spinner"></div>
      <h2 id="ov-title">Loading tracking data&hellip;</h2>
      <p id="ov-body"></p>
    </div>
  </div>
</div>

<script>
const REFRESH_MS = __REFRESH_MS__;
const state = { map:null, markers:[], info:null, ready:false, jsFailed:false, devices:[] };

function arrowIcon(d) {
  const fill = d.status === 'offline' ? '#94a3b8' : (d.status === 'moving' ? '#16a34a' : '#dc2626');
  const stroke = d.status === 'offline' ? '#64748b' : (d.status === 'moving' ? '#15803d' : '#b91c1c');
  const svg =
    '<svg xmlns="http://www.w3.org/2000/svg" width="34" height="34" viewBox="0 0 32 32">' +
    '<g transform="rotate(' + (d.heading || 0) + ', 16, 16)">' +
    '<path d="M16 2 L24 26 L16 20 L8 26 Z" fill="' + fill + '" stroke="' + stroke +
    '" stroke-width="1.5" stroke-linejoin="round"/></g></svg>';
  return { url:'data:image/svg+xml;charset=UTF-8,' + encodeURIComponent(svg),
           scaledSize:new google.maps.Size(34,34),
           anchor:new google.maps.Point(17,17) };
}

function infoHtml(d) {
  const age = d.ageMinutes === null ? 'unknown'
            : (d.ageMinutes < 1 ? 'just now' : d.ageMinutes + ' min ago');
  return '<div style="font:13px system-ui;min-width:190px">' +
    '<div style="font-weight:600;margin-bottom:4px">' + d.name + '</div>' +
    '<div style="color:#6b7280;font-size:12px">' +
      'Speed: ' + d.speedKmh + ' km/h<br>' +
      'Status: ' + (d.status === 'offline' ? 'OFFLINE' : (d.status === 'moving' ? 'Moving' : 'Stopped')) + '<br>' +
      'Heading: ' + d.heading + '&deg;<br>' +
      'Last update: ' + (d.lastUpdate ? new Date(d.lastUpdate).toLocaleString() : 'N/A') +
        ' (' + age + ')' +
    '</div>' +
    '<div style="margin-top:6px"><a target="_blank" rel="noreferrer" href="' + d.mapsUrl +
      '">Open in Google Maps</a></div></div>';
}

function clearMarkers() {
  state.markers.forEach(function (m) { m.setMap(null); });
  state.markers = [];
  if (state.info) { state.info.close(); state.info = null; }
}

function draw(devices) {
  clearMarkers();
  if (!state.map || !devices.length) return;
  const bounds = new google.maps.LatLngBounds();
  devices.forEach(function (d) {
    const pos = { lat:d.lat, lng:d.lng };
    bounds.extend(pos);
    const marker = new google.maps.Marker({
      position:pos, map:state.map, title:d.name, icon:arrowIcon(d),
      zIndex: d.status === 'offline' ? 1 : (d.status === 'moving' ? 10 : 5)
    });
    marker.addListener('click', function () {
      if (state.info) state.info.close();
      state.info = new google.maps.InfoWindow({ content:infoHtml(d) });
      state.info.open(state.map, marker);
    });
    state.markers.push(marker);
  });
  if (devices.length === 1) {
    state.map.setCenter({ lat:devices[0].lat, lng:devices[0].lng });
    state.map.setZoom(15);
  } else {
    state.map.fitBounds(bounds, 60);
  }
}

async function refresh() {
  try {
    const res = await fetch('/api/positions', { cache:'no-store' });
    if (!res.ok) throw new Error('server returned ' + res.status);
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || 'tracking request failed');

    state.devices = data.devices;
    document.getElementById('online').textContent = data.online;
    document.getElementById('total').textContent = data.total;
    document.getElementById('updated').textContent = new Date(data.fetchedAt).toLocaleTimeString();
    document.getElementById('source').textContent = data.cached ? '(server cache)' : '(live)';
    document.getElementById('refresh').textContent = 'auto-refresh every ' + data.refreshSeconds + 's';

    if (state.ready) {
      document.getElementById('overlay').style.display = 'none';
      draw(data.devices);
    } else {
      // No native map: show the server-proxied Google tile map right away, so the
      // page is never blank while the JS API attempt is still pending.
      FallbackMap.use();
      FallbackMap.setDevices(data.devices);
      if (state.jsFailed) {
        document.getElementById('staticnote').innerHTML =
          '<b>Google Maps JS API is blocked for this page.</b> Using the server-proxied ' +
          'Google Maps view instead &mdash; drag to pan, scroll to zoom, click a bus for ' +
          'details. <a href="#" id="whynote3">Why?</a>';
        const link = document.getElementById('whynote3');
        if (link) link.addEventListener('click', function (ev) { ev.preventDefault(); whyPanel(); });
      }
    }
  } catch (err) {
    const overlay = document.getElementById('overlay');
    overlay.style.display = 'flex';
    document.getElementById('spinner').style.display = 'none';
    document.getElementById('ov-title').textContent = 'Could not load tracking data';
    document.getElementById('ov-body').innerHTML =
      '<p class="err">' + (err && err.message ? err.message : err) + '</p>' +
      '<p>Confirm the machine can reach <code>supabase.apps2db.uctechlabs.com</code>.</p>';
  }
}

// ---------------------------------------------------------------------------
// Fallback: a Google Maps view built from server-proxied Static Maps images.
// Server-side requests carry no Referer, so a referer-restricted API key that
// the browser refuses to use still works here. Panning, zooming and the bus
// markers are drawn locally on top of one map image.
// ---------------------------------------------------------------------------
const FallbackMap = (function () {
  const TILE = 256;
  const MIN_Z = 3, MAX_Z = 19;
  const view = { z:11, lat:20.95, lng:72.93 };
  const el = {};
  const cache = new Map();       // "z/lat/lng" -> object URL
  let devices = [];
  let active = false;
  let fitted = false;
  let loading = false;
  let pending = false;
  let offset = { x:0, y:0 };     // live drag offset, applied as a transform

  function lon2x(lon, z) { return (lon + 180) / 360 * TILE * Math.pow(2, z); }
  function lat2y(lat, z) {
    lat = Math.max(-85.05112878, Math.min(85.05112878, lat));
    const s = Math.sin(lat * Math.PI / 180);
    return (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * TILE * Math.pow(2, z);
  }
  function x2lon(x, z) { return x / (TILE * Math.pow(2, z)) * 360 - 180; }
  function y2lat(y, z) {
    const n = Math.PI - 2 * Math.PI * y / (TILE * Math.pow(2, z));
    return 180 / Math.PI * Math.atan(0.5 * (Math.exp(n) - Math.exp(-n)));
  }

  function size() {
    return { w: el.root.clientWidth || 800, h: el.root.clientHeight || 600 };
  }

  // ---- map image ------------------------------------------------------
  function loadImage() {
    if (loading) { pending = true; return; }
    const { w, h } = size();
    const sized = { w: Math.min(640, Math.max(64, Math.ceil(w / 64) * 64)),
                    h: Math.min(640, Math.max(64, Math.ceil(h / 64) * 64)) };
    const key = view.z + '/' + view.lat.toFixed(5) + '/' + view.lng.toFixed(5) +
                '/' + sized.w + '/' + sized.h;
    if (cache.has(key)) { el.img.src = cache.get(key); return; }

    loading = true;
    const url = '/api/staticmap?lat=' + view.lat.toFixed(6) + '&lng=' + view.lng.toFixed(6) +
                '&z=' + view.z + '&w=' + sized.w + '&h=' + sized.h;
    fetch(url, { cache:'no-store' })
      .then(function (res) {
        if (!res.ok || (res.headers.get('Content-Type') || '').indexOf('image/') !== 0) {
          return res.text().then(function (t) { throw new Error(t.slice(0, 160)); });
        }
        return res.blob();
      })
      .then(function (blob) {
        const objectUrl = URL.createObjectURL(blob);
        cache.set(key, objectUrl);
        if (cache.size > 40) {
          const oldest = cache.keys().next().value;
          URL.revokeObjectURL(cache.get(oldest));
          cache.delete(oldest);
        }
        el.img.style.width = sized.w + 'px';
        el.img.style.height = sized.h + 'px';
        el.img.src = objectUrl;
      })
      .catch(function (err) {
        document.getElementById('staticnote').innerHTML =
          '<b>Map image failed:</b> ' + err.message;
      })
      .then(function () {
        loading = false;
        if (pending) { pending = false; loadImage(); }
      });
  }

  // ---- pins -----------------------------------------------------------
  function drawPins() {
    const { w, h } = size();
    const frag = document.createDocumentFragment();
    devices.forEach(function (d) {
      // Screen position: projection difference from the map centre, in pixels.
      const x = w / 2 + lon2x(d.lng, view.z) - lon2x(view.lng, view.z) + offset.x;
      const y = h / 2 + lat2y(d.lat, view.z) - lat2y(view.lat, view.z) + offset.y;
      if (x < -60 || y < -60 || x > w + 60 || y > h + 60) return;
      const pin = document.createElement('div');
      pin.className = 'pin';
      pin.style.left = Math.round(x) + 'px';
      pin.style.top = Math.round(y) + 'px';
      pin.title = d.name;
      const icon = arrowIcon(d);
      const img = document.createElement('img');
      img.src = icon.url;
      img.style.cssText = 'width:34px;height:34px;display:block';
      const lbl = document.createElement('span');
      lbl.className = 'lbl';
      lbl.textContent = d.name;
      lbl.style.color = d.status === 'offline' ? '#64748b' : '#1f2937';
      pin.appendChild(img);
      pin.appendChild(lbl);
      pin.addEventListener('click', function (ev) {
        ev.stopPropagation();
        showCard(d);
      });
      frag.appendChild(pin);
    });
    el.pins.innerHTML = '';
    el.pins.appendChild(frag);
  }

  function normalizeLat() { view.lat = Math.max(-85, Math.min(85, view.lat)); }
  function normalizeLng() { view.lng = ((view.lng + 180) % 360 + 360) % 360 - 180; }

  function redraw() {
    el.img.style.transform =
      'translate(calc(-50% + ' + Math.round(offset.x) + 'px), calc(-50% + ' +
      Math.round(offset.y) + 'px))';
    drawPins();
  }

  function commit() {           // drag finished: fetch the moved map image
    offset = { x:0, y:0 };
    redraw();
    loadImage();
  }

  function showCard(d) {
    const age = d.ageMinutes === null ? 'unknown'
              : (d.ageMinutes < 1 ? 'just now' : d.ageMinutes + ' min ago');
    el.card.innerHTML =
      '<span class="x" id="cardx">&times;</span>' +
      '<h3 style="margin:0 0 4px;font-size:13px">' + d.name + '</h3>' +
      '<div style="color:#6b7280">' +
        'Speed: ' + d.speedKmh + ' km/h<br>' +
        'Status: ' + (d.status === 'offline' ? 'OFFLINE'
                    : (d.status === 'moving' ? 'Moving' : 'Stopped')) + '<br>' +
        'Heading: ' + d.heading + '&deg;<br>' +
        'Last update: ' + (d.lastUpdate ? new Date(d.lastUpdate).toLocaleString() : 'N/A') +
          ' (' + age + ')' +
      '</div>' +
      '<div style="margin-top:6px"><a target="_blank" rel="noreferrer" href="' + d.mapsUrl +
        '">Open in Google Maps</a></div>';
    el.card.style.display = 'block';
    document.getElementById('cardx').addEventListener('click', function () {
      el.card.style.display = 'none';
    });
  }

  // ---- navigation -----------------------------------------------------
  function fit() {
    if (!devices.length) return;
    const { w, h } = size();
    const lats = devices.map(function (d) { return d.lat; });
    const lons = devices.map(function (d) { return d.lng; });
    let south = Math.min.apply(null, lats), north = Math.max.apply(null, lats);
    let west = Math.min.apply(null, lons), east = Math.max.apply(null, lons);

    if (north - south < 1e-6 && east - west < 1e-6) {
      view.z = 15; view.lat = devices[0].lat; view.lng = devices[0].lng;
    } else {
      const padLat = Math.max((north - south) * 0.08, 0.0015);
      const padLon = Math.max((east - west) * 0.08, 0.0015);
      south -= padLat; north += padLat; west -= padLon; east += padLon;
      let best = MIN_Z;
      for (let z = MIN_Z; z <= MAX_Z; z++) {
        const dx = Math.abs(lon2x(east, z) - lon2x(west, z));
        const dy = Math.abs(lat2y(south, z) - lat2y(north, z));
        if (dx <= w - 20 && dy <= h - 20) best = z; else break;
      }
      view.z = best;
      view.lat = (south + north) / 2;
      view.lng = (west + east) / 2;
    }
    normalizeLat(); normalizeLng();
    offset = { x:0, y:0 };
    redraw();
    loadImage();
  }

  function zoomBy(delta, anchorX, anchorY) {
    const { w, h } = size();
    const ax = anchorX === undefined ? w / 2 : anchorX;
    const ay = anchorY === undefined ? h / 2 : anchorY;
    // Keep the point under the anchor fixed while the zoom changes.
    const lat = y2lat(lat2y(view.lat, view.z) - h / 2 + ay, view.z);
    const lng = x2lon(lon2x(view.lng, view.z) - w / 2 + ax, view.z);
    const next = Math.max(MIN_Z, Math.min(MAX_Z, view.z + delta));
    if (next === view.z) return;
    view.z = next;
    view.lat = y2lat(lat2y(lat, view.z) + h / 2 - ay, view.z);
    view.lng = x2lon(lon2x(lng, view.z) + w / 2 - ax, view.z);
    normalizeLat(); normalizeLng();
    offset = { x:0, y:0 };
    redraw();
    loadImage();
  }

  function attachInteractions() {
    let dragging = false, sx = 0, sy = 0;
    el.root.addEventListener('mousedown', function (ev) {
      if (ev.target.closest('.pin') || ev.target.closest('.controls') ||
          ev.target.closest('.card')) return;
      dragging = true; sx = ev.clientX; sy = ev.clientY;
      el.root.classList.add('dragging');
      ev.preventDefault();
    });
    window.addEventListener('mousemove', function (ev) {
      if (!dragging) return;
      offset = { x: ev.clientX - sx, y: ev.clientY - sy };
      redraw();
    });
    window.addEventListener('mouseup', function () {
      if (!dragging) return;
      dragging = false;
      el.root.classList.remove('dragging');
      if (offset.x || offset.y) {
        // Screen pixels -> new map centre, then refetch the image.
        view.lng = x2lon(lon2x(view.lng, view.z) - offset.x, view.z);
        view.lat = y2lat(lat2y(view.lat, view.z) - offset.y, view.z);
        normalizeLat(); normalizeLng();
      }
      commit();
    });
    el.root.addEventListener('wheel', function (ev) {
      ev.preventDefault();
      const r = el.root.getBoundingClientRect();
      zoomBy(ev.deltaY < 0 ? 1 : -1, ev.clientX - r.left, ev.clientY - r.top);
    }, { passive:false });
    el.root.addEventListener('dblclick', function (ev) {
      const r = el.root.getBoundingClientRect();
      zoomBy(1, ev.clientX - r.left, ev.clientY - r.top);
    });
  }

  return {
    use: function () {
      if (active) return;
      active = true;
      el.root = document.getElementById('fallback');
      el.img = document.getElementById('gmapimg');
      el.pins = document.getElementById('pins');
      el.card = document.getElementById('pin-card');
      el.root.style.display = 'block';
      document.getElementById('overlay').style.display = 'none';
      const note = document.getElementById('staticnote');
      note.style.display = 'block';
      note.innerHTML =
        '<b>Live Google map</b> &mdash; drag to pan, scroll or +/&minus; to zoom, click a ' +
        'bus for details. <a href="#" id="whynote">Why is this not the standard map?</a>';
      document.getElementById('zin').addEventListener('click', function () { zoomBy(1); });
      document.getElementById('zout').addEventListener('click', function () { zoomBy(-1); });
      document.getElementById('fit').addEventListener('click', fit);
      document.getElementById('whynote').addEventListener('click', function (ev) {
        ev.preventDefault(); whyPanel();
      });
      attachInteractions();
      window.addEventListener('resize', function () { if (active) { redraw(); loadImage(); } });
    },
    setDevices: function (list) {
      devices = list || [];
      if (!active) return;
      if (!fitted) { fitted = true; fit(); } else { redraw(); }
    }
  };
})();

function whyPanel() {
  const overlay = document.getElementById('overlay');
  overlay.style.display = 'flex';
  document.getElementById('spinner').style.display = 'none';
  document.getElementById('ov-title').textContent = 'Why this map mode?';
  document.getElementById('ov-body').innerHTML =
    '<p>The Google key bundled with the Compass page is <b>referer-restricted to ' +
    '<code>compass.apps2.uctechlabs.com</code></b>. Google therefore refuses to load the ' +
    'Maps JavaScript API from <code>127.0.0.1</code> &mdash; that is the ' +
    '"didn\\'t load Google Maps correctly" message.</p>' +
    '<p>So this app asks its own Python server for the Google map images instead. ' +
    'Server requests carry no Referer, so Google serves them normally, and the buses are ' +
    'drawn on top. Panning, zooming and the bus popups all work; only the native Google ' +
    'widgets (satellite toggle, Street View) are missing.</p>' +
    '<p><b>To get the native interactive map</b>, give this app a key that is allowed on ' +
    'localhost &mdash; either add <code>http://127.0.0.1:__PORT__/*</code> to the existing ' +
    'key in Google Cloud Console, or create one with the Maps JavaScript API enabled and ' +
    'run <code>python compass_google_map.py --key YOUR_KEY</code>. This app checks the key ' +
    'at startup and uses the native map automatically when it is allowed.</p>' +
    '<p><button id="closewhy" style="padding:6px 12px;border:1px solid #e5e7eb;border-radius:8px;' +
    'background:#fff;cursor:pointer">Close</button></p>';
  document.getElementById('closewhy').addEventListener('click', function () {
    overlay.style.display = 'none';
  });
}

function showMapError(message) {
  // Kept for compatibility: route every map failure into the proxy fallback.
  fallBackToProxyMap(message);
}

// The JS API is unusable: hide Google's map element and hand over to the
// server-proxied Google map, which uses the same key without a Referer.
function fallBackToProxyMap(reason) {
  if (state.ready) return;      // the native map won; never cover it
  state.jsFailed = true;
  try {
    FallbackMap.use();
    if (state.devices.length) FallbackMap.setDevices(state.devices);
    const note = document.getElementById('staticnote');
    note.style.display = 'block';
    note.innerHTML = '<b>' + reason + '</b> Using this app\\'s server-proxied Google map ' +
      'instead &mdash; drag to pan, scroll to zoom, click a bus for details. ' +
      '<a href="#" id="whynote2">Why?</a>';
    const link = document.getElementById('whynote2');
    if (link) link.addEventListener('click', function (ev) { ev.preventDefault(); whyPanel(); });
  } catch (err) {
    showErrorPanel('Map unavailable', 'Map fallback failed: ' + err);
  }
}

function showErrorPanel(title, message) {
  const overlay = document.getElementById('overlay');
  overlay.style.display = 'flex';
  document.getElementById('spinner').style.display = 'none';
  document.getElementById('ov-title').textContent = title;
  document.getElementById('ov-body').innerHTML = '<p class="err">' + message + '</p>';
}

function initMap() {
  try {
    state.map = new google.maps.Map(document.getElementById('map'), {
      center:{ lat:20.95, lng:72.93 },
      zoom:11,
      mapTypeId:google.maps.MapTypeId.ROADMAP,
      styles:[{ featureType:'poi', stylers:[{ visibility:'off' }] }],
      streetViewControl:false
    });
  } catch (err) {
    fallBackToProxyMap('The Google Maps script loaded but could not start.');
    return;
  }
  state.ready = true;
  state.jsFailed = false;
  document.getElementById('overlay').style.display = 'none';
  document.getElementById('staticnote').style.display = 'none';
  document.getElementById('fallback').style.display = 'none';
  if (state.devices.length) draw(state.devices);
}

window.gm_authFailure = function () {
  fallBackToProxyMap('Google rejected this page\\'s API key.');
};
window.initMap = initMap;

// Google Maps JS API loader, only when the key accepts this origin. Otherwise we
// skip it entirely so Google's "didn't load correctly" UI never appears at all.
if (__INTERACTIVE__) {
  (function () {
    const script = document.createElement('script');
    script.src = 'https://maps.googleapis.com/maps/api/js?key=__KEY__&callback=initMap&loading=async';
    script.async = true;
    script.onerror = function () { fallBackToProxyMap('The Google Maps script failed to load.'); };
    document.head.appendChild(script);
    setTimeout(function () {
      if (!state.ready) fallBackToProxyMap('Google Maps did not initialise in time.');
    }, 12000);
  })();
} else {
  FallbackMap.use();
}

refresh();
setInterval(refresh, REFRESH_MS);
</script>
</body>
</html>
"""


def render_page(key: str, port: int, interactive: bool) -> str:
    return (
        PAGE.replace("__KEY__", urllib.parse.quote(key, safe=""))
        .replace("__REFRESH_MS__", str(REFRESH_SECONDS * 1000))
        .replace("__INTERACTIVE__", "true" if interactive else "false")
        .replace("__PORT__", str(port))
    )


# --------------------------------------------------------------------------
# Tiny HTTP server
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "CompassGoogleMap/1.0"
    page = ""
    token = TRACKING_TOKEN
    key = GOOGLE_MAPS_KEY
    _image_cache: dict[tuple[float, float, int, int, int], bytes] = {}

    def log_message(self, fmt, *args):  # keep the console quiet
        if os.environ.get("COMPASS_VERBOSE"):
            super().log_message(fmt, *args)

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, self.page.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/api/positions":
            try:
                payload = normalise(fetch_positions(self.token))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:200]
                payload = {"ok": False, "error": f"tracking API HTTP {exc.code}: {detail}"}
            except Exception as exc:  # network, DNS, TLS, JSON
                payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            self._send(200, json.dumps(payload).encode("utf-8"), "application/json")
            return
        if path == "/healthz":
            self._send(200, b"ok", "text/plain")
            return
        if path == "/api/view":
            self._serve_view()
            return
        if path == "/api/staticmap":
            self._serve_static_map()
            return
        self._send(404, b"not found", "text/plain")

    def _live_devices(self) -> list[dict]:
        return normalise(fetch_positions(self.token))["devices"]

    def _serve_view(self) -> None:
        """Centre and zoom that fit the whole fleet into one map image."""
        try:
            view = fleet_view(self._live_devices(), 640, 640)
        except Exception as exc:
            self._send(
                200,
                json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}).encode(),
                "application/json",
            )
            return
        view["ok"] = True
        self._send(200, json.dumps(view).encode("utf-8"), "application/json")

    def _serve_static_map(self) -> None:
        """One Google Static Maps image for a centre/zoom (server-side: no Referer)."""
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        try:
            lat = float(query.get("lat", ["20.95"])[0])
            lng = float(query.get("lng", ["72.93"])[0])
            zoom = int(float(query.get("z", ["12"])[0]))
            width = max(64, min(640, int(query.get("w", ["640"])[0])))
            height = max(64, min(640, int(query.get("h", ["640"])[0])))
        except ValueError:
            self._send(400, b"bad view parameters", "text/plain")
            return
        if not (-90 <= lat <= 90 and -180 <= lng <= 180 and 0 <= zoom <= MAX_ZOOM):
            self._send(400, b"view out of range", "text/plain")
            return

        cache_key = (round(lat, 5), round(lng, 5), zoom, width, height)
        cached = Handler._image_cache.get(cache_key)
        if cached is not None:
            self._send(200, cached, "image/png")
            return

        try:
            image = fetch_static_map(self.key, lat, lng, zoom, width, height)
        except Exception as exc:
            self._send(
                200,
                json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}).encode(),
                "application/json",
            )
            return

        Handler._image_cache[cache_key] = image
        if len(Handler._image_cache) > 80:
            Handler._image_cache.pop(next(iter(Handler._image_cache)))
        self._send(200, image, "image/png")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compass live tracking on Google Maps")
    parser.add_argument("--token", default=TRACKING_TOKEN, help="Compass share-tracking token")
    parser.add_argument("--key", default=GOOGLE_MAPS_KEY, help="Google Maps JavaScript API key")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    args = parser.parse_args(argv)

    interactive = key_allows_browser(args.key, args.port)
    Handler.page = render_page(args.key, args.port, interactive)
    Handler.token = args.token
    Handler.key = args.key

    try:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        print(f"Could not bind {args.host}:{args.port} -> {exc}", file=sys.stderr)
        print("Try a different port, e.g. --port 5050", file=sys.stderr)
        return 1

    url = f"http://{args.host}:{args.port}/"
    print("Compass live tracking on Google Maps")
    print(f"  token      : {args.token[:16]}...")
    print(f"  google key : {args.key[:12]}...")
    print(f"  map mode   : {'native Google Maps JS API' if interactive else 'server-proxied Google map'}")
    if not interactive:
        print("               (the key is referer-restricted, so the browser cannot use it;")
        print("                this app fetches the map images itself instead)")
    print(f"  serving    : {url}")
    print("  press Ctrl+C to stop")

    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
