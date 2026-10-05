"""Compass live tracking, rendered on Google Maps -- a drop-in replacement page.

Same job as https://compass.apps2.uctechlabs.com/track/<token>, same live data and
30-second refresh, but the map is Google Maps.

Run it:
    python compass_tracker.py
Then open http://127.0.0.1:5000

    python compass_tracker.py --port 8080 --no-browser
    python compass_tracker.py --key YOUR_GOOGLE_MAPS_KEY
    python compass_tracker.py --insecure     (only if you get SSL certificate errors)

For hosting, the server binds 0.0.0.0 and honours $PORT (see Dockerfile / Procfile).
Standard library only -- nothing to install.

Why the map is drawn from server-side Google Static Maps images: the Google key
that ships with the Compass page is referer-restricted to
compass.apps2.uctechlabs.com, so a browser running anywhere else is refused with
"This page didn't load Google Maps correctly". Server requests carry no Referer,
so this app asks its own backend for the Google map imagery and draws the buses
on top. Supply a key that allows your origin via --key and the app switches to the
native Google Maps JavaScript API automatically (see README.md).

For a map that is sharp at every zoom with no API key at all, use the static
Leaflet build in docs/ instead -- see README.md.
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

OFFLINE_AFTER_MS = 3 * 60 * 60 * 1000     # Compass treats a 3-hour-old sample as offline
REFRESH_SECONDS = 30
TILE_SIZE = 256
MAX_ZOOM = 19
# Map types the Static Maps API can render. Traffic is deliberately absent: the
# Static Maps API ignores a traffic parameter (verified byte-identical output).
MAP_TYPES = ("roadmap", "terrain", "satellite", "hybrid")

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

COMPASS_ICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 112 112" width="112" height="112">'
    '<circle cx="56" cy="56" r="56" fill="#1B2A6B"/>'
    '<g stroke="#3AABDB" stroke-width="1.5" stroke-linecap="round" opacity="0.35">'
    '<line x1="56" y1="4" x2="56" y2="12"/><line x1="56" y1="108" x2="56" y2="100"/>'
    '<line x1="4" y1="56" x2="12" y2="56"/><line x1="108" y1="56" x2="100" y2="56"/></g>'
    '<polygon points="56,14 45,56 56,48 67,56" fill="#3AABDB"/>'
    '<polygon points="56,98 45,56 56,64 67,56" fill="#E24B4A"/>'
    '<circle cx="56" cy="56" r="6" fill="#ffffff"/></svg>'
)


# --------------------------------------------------------------------------
# Data layer: live positions from the Compass public-tracking function
# --------------------------------------------------------------------------

def fetch_positions(token: str) -> dict:
    payload = json.dumps({"token": token}).encode()
    request = urllib.request.Request(
        f"{SUPABASE_URL}/functions/v1/public-tracking",
        data=payload,
        headers={
            "apikey": SUPABASE_ANON_KEY,
            "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
            "Content-Type": "application/json",
            "Origin": "https://compass.apps2.uctechlabs.com",
            "User-Agent": "CompassTracker/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=45, context=SSL_CTX) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def normalise(payload: dict) -> dict:
    """Shape the API payload the way the page wants it."""
    now_ms = datetime.now(timezone.utc).timestamp() * 1000
    devices = []
    for item in payload.get("positions") or []:
        try:
            lat = float(item["latitude"])
            lon = float(item["longitude"])
        except (KeyError, TypeError, ValueError):
            continue

        speed_raw = item.get("speed")
        speed_kmh = round(float(speed_raw or 0) * 3.6, 1)   # API gives m/s; Compass shows km/h
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
        "moving": sum(1 for d in devices if d["status"] == "moving"),
        "stopped": sum(1 for d in devices if d["status"] == "stopped"),
        "fetchedAt": payload.get("fetched_at")
        or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "cached": bool(payload.get("cached")),
        "refreshSeconds": REFRESH_SECONDS,
        "allMapsUrl": all_maps_url(devices),
    }


def all_maps_url(devices: list[dict]) -> str:
    query = "|".join(f"{d['lat']},{d['lng']}" for d in devices)
    return "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote(query, safe=",|")


# --------------------------------------------------------------------------
# Web Mercator helpers + Google Static Maps access
# --------------------------------------------------------------------------

def lon_to_x(lon: float, zoom: float) -> float:
    return (lon + 180.0) / 360.0 * TILE_SIZE * (2 ** zoom)


def lat_to_y(lat: float, zoom: float) -> float:
    lat = max(-85.05112878, min(85.05112878, lat))
    return (
        (1.0 - math.log(math.tan(math.radians(lat)) + 1.0 / math.cos(math.radians(lat))) / math.pi)
        / 2.0 * TILE_SIZE * (2 ** zoom)
    )


def x_to_lon(x: float, zoom: float) -> float:
    return x / (TILE_SIZE * (2 ** zoom)) * 360.0 - 180.0


def y_to_lat(y: float, zoom: float) -> float:
    n = math.pi - 2.0 * math.pi * y / (TILE_SIZE * (2 ** zoom))
    return math.degrees(math.atan(math.sinh(n)))


def fleet_view(devices: list[dict], width: int, height: int) -> dict:
    """Centre + zoom that fit every device into one map image."""
    lats = [d["lat"] for d in devices]
    lons = [d["lng"] for d in devices]
    south, north = min(lats), max(lats)
    west, east = min(lons), max(lons)

    if len(devices) < 2 or ((north - south) < 1e-6 and (east - west) < 1e-6):
        return {"center": {"lat": devices[0]["lat"], "lng": devices[0]["lng"]},
                "zoom": 15, "width": width, "height": height}

    pad_lat = max((north - south) * 0.08, 0.0015)
    pad_lon = max((east - west) * 0.08, 0.0015)
    south, north = south - pad_lat, north + pad_lat
    west, east = west - pad_lon, east + pad_lon

    span_y = abs(lat_to_y(south, 0) - lat_to_y(north, 0)) or 1e-9
    span_x = abs(lon_to_x(east, 0) - lon_to_x(west, 0)) or 1e-9
    zoom = max(2, min(MAX_ZOOM, math.floor(min(math.log2(height / span_y),
                                               math.log2(width / span_x)))))
    return {"center": {"lat": (south + north) / 2, "lng": (west + east) / 2},
            "zoom": zoom, "width": width, "height": height}


def fetch_static_map(key: str, lat: float, lng: float, zoom: int,
                     width: int, height: int, map_type: str = "roadmap",
                     scale: int = 1) -> bytes:
    """One Google Static Maps image. No Referer is sent, so a referer-restricted
    key that a browser would be refused for still works here.

    Only roadmap/terrain/satellite/hybrid are supported here: the Static Maps API
    has no traffic overlay, so traffic is a native-only feature (see NativeMap).
    """
    if map_type not in MAP_TYPES:
        map_type = "roadmap"
    params = {
        "center": f"{lat:.6f},{lng:.6f}",
        "zoom": str(int(zoom)),
        "size": f"{width}x{height}",
        "scale": "2" if int(scale) == 2 else "1",
        "maptype": map_type,
        "key": key,
    }
    request = urllib.request.Request(
        "https://maps.googleapis.com/maps/api/staticmap?" + urllib.parse.urlencode(params),
        headers={"User-Agent": "CompassTracker/1.0"},
    )
    with urllib.request.urlopen(request, timeout=45, context=SSL_CTX) as response:
        content_type = response.headers.get("Content-Type") or ""
        body = response.read()
    if not content_type.startswith("image/"):
        raise RuntimeError(body.decode("utf-8", "replace")[:300])
    return body


def key_allows_browser(key: str, port: int) -> bool:
    """Would Google accept this key from a browser on http://127.0.0.1:<port>?

    Google checks the Referer on browser requests, so ask the free Static Maps
    endpoint with that Referer: an image means the browser would be allowed too.
    """
    params = {"center": "20.95,72.93", "zoom": "10", "size": "64x64", "key": key}
    request = urllib.request.Request(
        "https://maps.googleapis.com/maps/api/staticmap?" + urllib.parse.urlencode(params),
        headers={"User-Agent": "CompassTracker/1.0", "Referer": f"http://127.0.0.1:{port}/"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20, context=SSL_CTX) as response:
            return (response.headers.get("Content-Type") or "").startswith("image/")
    except Exception:
        return False


# --------------------------------------------------------------------------
# The page
# --------------------------------------------------------------------------

PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Live Tracking &middot; Compass on Google Maps</title>
<link rel="icon" type="image/svg+xml" href="/compass-icon.svg" />
<style>
  :root{
    --background:0 0% 100%; --foreground:222.2 84% 4.9%;
    --card:0 0% 100%; --card-foreground:222.2 84% 4.9%;
    --primary:218 76% 42%; --primary-foreground:0 0% 100%;
    --secondary:210 40% 96.1%; --muted:210 40% 96.1%;
    --muted-foreground:215.4 16.3% 42%;
    --success:142 70% 30%; --destructive:0 72% 45%; --info:217 80% 44%;
    --border:214.3 31.8% 91.4%; --radius:.5rem;
    --font:Geist Variable,Geist,ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  }
  *{box-sizing:border-box;border-color:hsl(var(--border));}
  html,body{margin:0;height:100%;}
  body{font-family:var(--font);color:hsl(var(--foreground));background:hsl(var(--background));
       -webkit-font-smoothing:antialiased;}
  .app{display:grid;grid-template-rows:auto 1fr;height:100vh;}

  header{background:hsl(var(--card));border-bottom:1px solid hsl(var(--border));
         box-shadow:0 1px 2px rgb(0 0 0/.05);padding:10px 16px;
         display:flex;align-items:center;gap:12px;flex-wrap:wrap;}
  .logo{width:38px;height:38px;border-radius:10px;flex:none;background:hsl(var(--info)/.1);
        padding:5px;}
  h1{font-size:14px;font-weight:600;margin:0;letter-spacing:-.01em;}
  .metarow{display:flex;align-items:center;gap:12px;font-size:12px;
           color:hsl(var(--muted-foreground));margin-top:2px;flex-wrap:wrap;}
  .metarow b{color:hsl(var(--foreground));font-weight:600;}
  .pill{display:inline-flex;align-items:center;gap:5px;}
  .dot{width:8px;height:8px;border-radius:50%;display:inline-block;flex:none;}
  .dot.moving{background:hsl(var(--success));}
  .dot.stopped{background:hsl(var(--destructive));}
  .dot.offline{background:#94a3b8;}
  .live{color:hsl(var(--success));font-weight:600;}
  .live .dot{background:hsl(var(--success));animation:pulse 1.6s ease-in-out infinite;}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
  .actions{margin-left:auto;display:flex;align-items:center;gap:8px;}
  .btn{font:inherit;font-size:12px;font-weight:500;padding:6px 11px;border-radius:calc(var(--radius) - 2px);
       border:1px solid hsl(var(--border));background:hsl(var(--card));color:hsl(var(--foreground));
       cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;gap:5px;}
  .btn:hover{background:hsl(var(--secondary));}
  .btn.primary{background:hsl(var(--info));border-color:hsl(var(--info));color:#fff;}
  .btn.primary:hover{filter:brightness(1.06);}

  main{display:grid;grid-template-columns:290px 1fr;min-height:0;}
  .side{border-right:1px solid hsl(var(--border));background:hsl(var(--card));
        display:flex;flex-direction:column;min-height:0;}
  .sidehead{padding:10px 12px 8px;border-bottom:1px solid hsl(var(--border));}
  .sidehead h2{margin:0;font-size:12px;font-weight:600;text-transform:uppercase;
               letter-spacing:.04em;color:hsl(var(--muted-foreground));}
  .tabs{display:flex;gap:4px;padding:8px 10px 0;}
  .tab{font-size:11px;font-weight:600;padding:4px 9px;border-radius:999px;cursor:pointer;
       border:1px solid transparent;background:hsl(var(--muted));color:hsl(var(--muted-foreground));}
  .tab.on{background:hsl(var(--foreground));color:#fff;}
  .list{list-style:none;margin:0;padding:6px;overflow-y:auto;flex:1;min-height:0;}
  .row{display:flex;align-items:center;gap:9px;padding:7px 8px;border-radius:calc(var(--radius) - 2px);
       cursor:pointer;font-size:13px;}
  .row:hover{background:hsl(var(--secondary));}
  .row.on{background:hsl(var(--info)/.1);box-shadow:inset 0 0 0 1px hsl(var(--info)/.35);}
  .row .nm{font-weight:600;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  .row .sp{font-size:11px;color:hsl(var(--muted-foreground));font-variant-numeric:tabular-nums;}
  .sidefoot{padding:8px 12px;border-top:1px solid hsl(var(--border));font-size:11px;
            color:hsl(var(--muted-foreground));}

  .mapwrap{position:relative;overflow:hidden;background:#e8eaed;min-height:0;}
  #gmapimg{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);
           display:block;user-select:none;-webkit-user-drag:none;}
  #tiles{position:absolute;inset:0;overflow:hidden;}
  #pins{position:absolute;inset:0;}
  .pin{position:absolute;width:34px;height:34px;margin:-17px 0 0 -17px;cursor:pointer;}
  .pin .tag{position:absolute;left:28px;top:50%;transform:translateY(-50%);
            background:rgba(255,255,255,.94);border:1px solid hsl(var(--border));padding:1px 6px;
            border-radius:5px;font-size:11px;font-weight:600;white-space:nowrap;pointer-events:none;
            box-shadow:0 1px 3px rgb(0 0 0/.15);}
  .pin.sel .tag{background:hsl(var(--info));color:#fff;border-color:hsl(var(--info));}
  .zoombox{position:absolute;right:12px;top:12px;display:flex;flex-direction:column;gap:6px;z-index:6;}
  .zoombox button{width:36px;height:36px;border:1px solid hsl(var(--border));background:#fff;
                  border-radius:calc(var(--radius) - 1px);font-size:18px;line-height:1;cursor:pointer;
                  box-shadow:0 2px 8px rgb(0 0 0/.12);color:hsl(var(--foreground));}
  .zoombox button:hover{background:hsl(var(--secondary));}
  .maptypes{position:absolute;left:12px;top:12px;z-index:6;display:flex;gap:6px;
            background:rgba(255,255,255,.96);border:1px solid hsl(var(--border));
            border-radius:999px;padding:4px;box-shadow:0 2px 10px rgb(0 0 0/.12);}
  .maptypes button{font:inherit;font-size:11.5px;font-weight:600;padding:5px 11px;border-radius:999px;
                   border:none;background:transparent;color:hsl(var(--muted-foreground));cursor:pointer;}
  .maptypes button:hover{background:hsl(var(--secondary));}
  .maptypes button.on{background:hsl(var(--info));color:#fff;}
  .trafficbtn{font:inherit;font-size:11.5px;font-weight:600;padding:5px 11px;border-radius:999px;
              border:none;background:transparent;color:hsl(var(--muted-foreground));cursor:not-allowed;
              opacity:.5;}
  .trafficbtn.avail{cursor:pointer;opacity:1;}
  .trafficbtn.avail:hover{background:hsl(var(--secondary));}
  .trafficbtn.on{background:hsl(var(--success));color:#fff;}
  .card{position:absolute;left:12px;bottom:12px;z-index:7;width:260px;background:#fff;
        border:1px solid hsl(var(--border));border-radius:calc(var(--radius) + 2px);
        box-shadow:0 8px 26px rgb(0 0 0/.18);padding:12px;display:none;}
  .card h3{margin:0 0 6px;font-size:13px;}
  .card .kv{font-size:12px;color:hsl(var(--muted-foreground));line-height:1.55;}
  .card .kv b{color:hsl(var(--foreground));font-weight:600;}
  .card .x{position:absolute;top:7px;right:9px;cursor:pointer;color:#9ca3af;font-size:15px;
           border:none;background:none;font:inherit;line-height:1;}
  .card a{color:hsl(var(--info));font-size:12px;text-decoration:none;font-weight:500;}
  .card a:hover{text-decoration:underline;}
  .mapnote{position:absolute;bottom:10px;left:50%;transform:translateX(-50%);z-index:6;
           background:rgba(255,255,255,.96);border:1px solid hsl(var(--border));
           border-radius:999px;padding:5px 13px;font-size:11.5px;color:hsl(var(--muted-foreground));
           box-shadow:0 2px 10px rgb(0 0 0/.12);white-space:nowrap;max-width:92%;
           overflow:hidden;text-overflow:ellipsis;}
  .mapnote a{color:hsl(var(--info));}
  .attrib{position:absolute;right:8px;bottom:6px;z-index:5;font-size:10px;color:#5f6368;
          background:rgba(255,255,255,.75);padding:1px 5px;border-radius:3px;}

  .overlay{position:absolute;inset:0;z-index:20;display:flex;align-items:center;justify-content:center;
           background:hsl(var(--background)/.92);text-align:center;padding:20px;}
  .overlay .box{max-width:560px;background:hsl(var(--card));border:1px solid hsl(var(--border));
                border-radius:calc(var(--radius) + 4px);padding:22px;
                box-shadow:0 10px 34px rgb(0 0 0/.1);text-align:left;}
  .overlay h2{margin:0 0 8px;font-size:15px;}
  .overlay p{margin:6px 0;font-size:13px;line-height:1.55;color:hsl(var(--muted-foreground));}
  .overlay code{background:hsl(var(--muted));padding:1px 5px;border-radius:4px;font-size:12px;}
  .spin{width:26px;height:26px;border:3px solid hsl(var(--border));border-top-color:hsl(var(--info));
        border-radius:50%;animation:sp 1s linear infinite;margin:0 auto 12px;}
  @keyframes sp{to{transform:rotate(360deg)}}
  .err{color:hsl(var(--destructive));}
  .hidden{display:none !important;}

  @media (max-width:820px){
    main{grid-template-columns:1fr;grid-template-rows:auto 1fr;}
    .side{flex-direction:row;border-right:none;border-bottom:1px solid hsl(var(--border));}
    .sidehead{display:none;}
    .list{display:flex;gap:4px;overflow-x:auto;padding:6px;}
    .row{flex:0 0 auto;}
    .sidefoot{display:none;}
  }
</style>
</head>
<body>
<div class="app">
  <header>
    <img class="logo" src="/compass-icon.svg" alt="Compass" />
    <div>
      <h1>Live Tracking</h1>
      <div class="metarow">
        <span class="pill live" id="livepill" title="Auto-refreshing">
          <i class="dot moving"></i><span id="livetext">Live</span>
        </span>
        <span class="pill"><i class="dot"></i><b id="online">-</b>&nbsp;/&nbsp;<span id="total">-</span> online</span>
        <span class="pill" id="movingpill"><i class="dot moving"></i><b id="moving">-</b> moving</span>
        <span class="pill" id="stoppedpill"><i class="dot stopped"></i><b id="stopped">-</b> stopped</span>
        <span>Updated <b id="updated">-</b><span id="agesuffix"></span></span>
      </div>
    </div>
    <div class="actions">
      <a class="btn" id="allmaps" target="_blank" rel="noreferrer"
         href="https://www.google.com/maps" title="Open every bus in Google Maps">
        Open all in Google Maps
      </a>
      <button class="btn primary" id="refresh" title="Refresh now">Refresh</button>
    </div>
  </header>

  <main>
    <aside class="side">
      <div class="sidehead">
        <h2>Fleet</h2>
        <div class="tabs">
          <span class="tab on" data-filter="all">All</span>
          <span class="tab" data-filter="moving">Moving</span>
          <span class="tab" data-filter="stopped">Stopped</span>
          <span class="tab" data-filter="offline">Offline</span>
        </div>
      </div>
      <ul class="list" id="list"></ul>
      <div class="sidefoot" id="foot">Waiting for data&hellip;</div>
    </aside>

    <div class="mapwrap" id="mapwrap">
      <img id="gmapimg" alt="Google map of the fleet" style="display:none" />
      <div id="tiles"></div>
      <div id="pins"></div>
      <div class="maptypes" id="maptypes">
        <button data-mt="roadmap" class="on">Map</button>
        <button data-mt="terrain">Terrain</button>
        <button data-mt="satellite">Satellite</button>
        <button data-mt="hybrid">Hybrid</button>
        <button id="traffic" class="trafficbtn" title="Traffic layer needs native Google Maps">Traffic</button>
      </div>
      <div class="zoombox">
        <button id="zin" title="Zoom in">+</button>
        <button id="zout" title="Zoom out">&minus;</button>
        <button id="fit" title="Fit all buses" style="font-size:10px">FIT</button>
      </div>
      <div class="card" id="card"></div>
      <div class="mapnote" id="mapnote"></div>
      <div class="attrib">Map data &copy; Google</div>
      <div class="overlay" id="overlay">
        <div class="box">
          <div class="spin" id="spin"></div>
          <h2 id="ovtitle">Loading tracking data&hellip;</h2>
          <p id="ovbody"></p>
        </div>
      </div>
    </div>
  </main>
</div>

<script>
const REFRESH_MS = __REFRESH_MS__;
const state = { native:false, jsFailed:false, devices:[], selected:null, filter:'all' };

// ---------- shared helpers ----------
const COLORS = { moving:'#16a34a', stopped:'#dc2626', offline:'#94a3b8' };
const STROKE = { moving:'#15803d', stopped:'#b91c1c', offline:'#64748b' };
const LABEL  = { moving:'Moving', stopped:'Stopped', offline:'OFFLINE' };

function arrowSvg(d){
  return '<svg xmlns="http://www.w3.org/2000/svg" width="34" height="34" viewBox="0 0 32 32">' +
    '<g transform="rotate(' + (d.heading||0) + ', 16, 16)">' +
    '<path d="M16 2 L24 26 L16 20 L8 26 Z" fill="' + COLORS[d.status] +
    '" stroke="' + STROKE[d.status] + '" stroke-width="1.5" stroke-linejoin="round"/></g></svg>';
}
function arrowUrl(d){ return 'data:image/svg+xml;charset=UTF-8,' + encodeURIComponent(arrowSvg(d)); }
function ageText(d){
  if (d.ageMinutes === null) return 'unknown';
  return d.ageMinutes < 1 ? 'just now' : d.ageMinutes + ' min ago';
}
function cardHtml(d){
  return '<button class="x" id="cardx">&times;</button>' +
    '<h3>' + d.name + '</h3>' +
    '<div class="kv">' +
      'Status: <b>' + LABEL[d.status] + '</b><br>' +
      'Speed: <b>' + d.speedKmh + ' km/h</b><br>' +
      'Heading: <b>' + d.heading + '&deg;</b><br>' +
      'Last update: <b>' + (d.lastUpdate ? new Date(d.lastUpdate).toLocaleTimeString() : 'N/A') +
        '</b> (' + ageText(d) + ')' +
    '</div>' +
    '<div style="margin-top:8px"><a target="_blank" rel="noreferrer" href="' + d.mapsUrl +
      '">Open in Google Maps &rarr;</a></div>';
}

// ---------- server-proxied Google map (works with a referer-restricted key) ----------
const ProxyMap = (function(){
  const MIN_Z = 3, MAX_Z = 19;
  const view = { z:11, lat:20.95, lng:72.93 };
  const el = {};
  let devices = [], active = false, fitted = false;
  let offset = { x:0, y:0 };
  let mapType = 'roadmap';

  const lon2x = (lon,z) => (lon + 180) / 360 * 256 * Math.pow(2,z);
  function lat2y(lat,z){
    lat = Math.max(-85.05112878, Math.min(85.05112878, lat));
    const s = Math.sin(lat * Math.PI / 180);
    return (0.5 - Math.log((1+s)/(1-s)) / (4*Math.PI)) * 256 * Math.pow(2,z);
  }
  const x2lon = (x,z) => x / (256 * Math.pow(2,z)) * 360 - 180;
  function y2lat(y,z){
    const n = Math.PI - 2*Math.PI*y / (256 * Math.pow(2,z));
    return 180/Math.PI * Math.atan(0.5*(Math.exp(n) - Math.exp(-n)));
  }
  const size = () => ({ w: el.root.clientWidth || 800, h: el.root.clientHeight || 600 });
  const norm = () => { view.lat = Math.max(-85, Math.min(85, view.lat));
                       view.lng = ((view.lng + 180) % 360 + 360) % 360 - 180; };

  // ---- one continuous map image ----
  // Google's Static Maps API quantises the requested centre to its own grid, so
  // stitching several requests together leaves visible seams: measured against
  // genuinely continuous imagery (adjacent satellite tiles differ by ~4/255 on
  // average), two overlapping Static Maps renders of the same ground differed by
  // ~42/255 even after realignment. Proxying Google's raw tile service is not a
  // substitute either -- it returns dark, watermarked tiles in this region.
  // So the map is a single image scaled to fill the viewport: continuous, but
  // softer than native Google Maps. Pass --key with a key that allows this origin
  // for genuinely sharp, natively tiled Google Maps.
  const MAX_IMG = 640;         // Static Maps hard limit per request
  const SCALE = 2;             // request at 2x, downsample on screen
  const MAX_PARALLEL = 3;
  const imgs = new Map();      // key -> { url, ok }
  const queue = [];
  let inFlight = 0;

  // Requested image size (never above the 640 limit), and how it is placed on screen.
  // The request matches the window's shape so the map covers it edge to edge, and
  // scale=2 (below) means Google returns 2x the pixels -- so a 900x600 window gets a
  // 640x427 request rendered at 1280x854, which is only mildly upscaled on screen.
  // The image is scaled uniformly (never stretched, which would distort labels).
  function imgBox(){
    const { w, h } = size();
    const k = Math.min(1, MAX_IMG / w, MAX_IMG / h);
    const iw = Math.min(MAX_IMG, Math.max(64, Math.round(w * k)));
    const ih = Math.min(MAX_IMG, Math.max(64, Math.round(h * k)));
    const s = Math.min(w / iw, h / ih);
    const dw = Math.round(iw * s), dh = Math.round(ih * s);
    return { iw:iw, ih:ih, dw:dw, dh:dh,
             ox: Math.round((w - dw) / 2), oy: Math.round((h - dh) / 2) };
  }

  function keyFor(z, lat, lng){
    const box = imgBox();
    return [mapType, z, lat.toFixed(5), lng.toFixed(5), box.iw, box.ih].join('/');
  }

  function ensureImage(key, lat, lng, z, iw, ih, onReady){
    let rec = imgs.get(key);
    if (rec) {
      if (rec.ok && onReady) onReady();
      if (rec.ok) { imgs.delete(key); imgs.set(key, rec); }
      return rec;
    }
    rec = { url:null, ok:false };
    imgs.set(key, rec);
    if (imgs.size > 24) {
      const oldest = imgs.keys().next().value;
      const old = imgs.get(oldest);
      if (old && old.url) URL.revokeObjectURL(old.url);
      imgs.delete(oldest);
    }
    queue.push({ rec:rec, lat:lat, lng:lng, z:z, iw:iw, ih:ih, onReady:onReady });
    pump();
    return rec;
  }

  function pump(){
    while (inFlight < MAX_PARALLEL && queue.length) {
      const job = queue.shift();
      inFlight++;
      fetch('/api/staticmap?lat=' + job.lat.toFixed(6) + '&lng=' + job.lng.toFixed(6) +
            '&z=' + job.z + '&w=' + job.iw + '&h=' + job.ih + '&scale=' + SCALE +
            '&type=' + mapType, { cache:'no-store' })
        .then(res => {
          if (!res.ok || (res.headers.get('Content-Type') || '').indexOf('image/') !== 0)
            return res.text().then(t => { throw new Error(t.slice(0, 140)); });
          return res.blob();
        })
        .then(blob => {
          job.rec.url = URL.createObjectURL(blob);
          job.rec.ok = true;
          if (job.onReady) job.onReady();
        })
        .catch(err => {
          document.getElementById('mapnote').textContent = 'Map image failed: ' + err.message;
        })
        .then(() => { inFlight--; if (queue.length) pump(); });
    }
  }

  // Nearest loaded image from another zoom, shown while the exact one loads.
  function fallbackUrl(){
    for (let d = 1; d <= 3; d++) {
      for (const z of [view.z - d, view.z + d]) {
        if (z < MIN_Z || z > MAX_Z) continue;
        const rec = imgs.get(keyFor(z, view.lat, view.lng));
        if (rec && rec.ok) return rec.url;
      }
    }
    return null;
  }

  function paint(){
    const box = imgBox();
    const key = keyFor(view.z, view.lat, view.lng);
    const want = view.lat + '/' + view.lng;
    el.img.dataset.want = want;
    el.img.style.width = box.dw + 'px';
    el.img.style.height = box.dh + 'px';
    el.img.style.objectFit = 'contain';
    el.img.style.left = box.ox + 'px';
    el.img.style.top = box.oy + 'px';
    el.img.style.transform = 'translate(' + Math.round(offset.x) + 'px, ' +
                             Math.round(offset.y) + 'px)';
    const rec = imgs.get(key);
    if (rec && rec.ok) {
      el.img.src = rec.url;
      el.img.style.opacity = '1';
    } else {
      const fb = fallbackUrl();
      if (fb) el.img.src = fb;
      el.img.style.opacity = fb ? '.65' : '0';
      ensureImage(key, view.lat, view.lng, view.z, box.iw, box.ih, function(){
        if (el.img.dataset.want === want) {
          const done = imgs.get(key);
          if (done && done.ok) { el.img.src = done.url; el.img.style.opacity = '1'; }
        }
      });
    }
    drawPins();
  }

  // Fetch neighbouring views so panning and the next zoom feel instant.
  function warmNeighbours(){
    if (!active) return;
    const box = imgBox();
    const latPerPx = (y2lat(lat2y(view.lat, view.z) - 40, view.z) -
                      y2lat(lat2y(view.lat, view.z) + 40, view.z)) / 80;
    const lngPerPx = (x2lon(lon2x(view.lng, view.z) + 40, view.z) -
                      x2lon(lon2x(view.lng, view.z) - 40, view.z)) / 80;
    const dLat = latPerPx * box.ih, dLng = lngPerPx * box.iw;
    [[view.z, view.lat, view.lng + dLng], [view.z, view.lat, view.lng - dLng],
     [view.z, view.lat + dLat, view.lng], [view.z, view.lat - dLat, view.lng],
     [view.z + 1, view.lat, view.lng], [view.z - 1, view.lat, view.lng]].forEach(t => {
      const z = t[0];
      if (z < MIN_Z || z > MAX_Z || queue.length > 8) return;
      const key = keyFor(z, t[1], t[2]);
      if (imgs.has(key)) return;
      ensureImage(key, t[1], t[2], z, box.iw, box.ih, null);
    });
  }

  function drawPins(){
    const box = imgBox();
    const frag = document.createDocumentFragment();
    devices.forEach(d => {
      const x = box.ox + box.dw/2 + lon2x(d.lng, view.z) - lon2x(view.lng, view.z) + offset.x;
      const y = box.oy + box.dh/2 + lat2y(d.lat, view.z) - lat2y(view.lat, view.z) + offset.y;
      if (x < -60 || y < -60 || x > box.ox + box.dw + 60 || y > box.oy + box.dh + 60) return;
      const pin = document.createElement('div');
      pin.className = 'pin' + (state.selected === d.id ? ' sel' : '');
      pin.style.left = Math.round(x)+'px'; pin.style.top = Math.round(y)+'px';
      pin.title = d.name + ' \u2014 ' + LABEL[d.status];
      const img = document.createElement('img');
      img.src = arrowUrl(d); img.style.cssText = 'width:34px;height:34px;display:block';
      const tag = document.createElement('span');
      tag.className = 'tag'; tag.textContent = d.name;
      tag.style.color = d.status === 'offline' ? '#64748b' : '#1f2937';
      pin.appendChild(img); pin.appendChild(tag);
      pin.addEventListener('click', ev => { ev.stopPropagation(); select(d); });
      frag.appendChild(pin);
    });
    el.pins.innerHTML = ''; el.pins.appendChild(frag);
  }

  function redraw(){
    el.img.style.transform = 'translate(calc(-50% + ' + Math.round(offset.x) + 'px), ' +
                             'calc(-50% + ' + Math.round(offset.y) + 'px))';
    drawPins();
  }
  function commit(){ offset = {x:0,y:0}; paint(); warmNeighbours(); }

  // Centre/zoom that fits every bus inside the map image. Kept separate from
  // fit() so it can be checked without a real browser.
  function computeFit(list){
    if (!list.length) return null;
    const box = imgBox();
    const lats = list.map(d => d.lat), lons = list.map(d => d.lng);
    let s = Math.min(...lats), n = Math.max(...lats);
    let wst = Math.min(...lons), e = Math.max(...lons);
    if (n - s < 1e-6 && e - wst < 1e-6)
      return { z:15, lat:list[0].lat, lng:list[0].lng };
    const padLat = Math.max((n-s)*0.08, 0.0015), padLon = Math.max((e-wst)*0.08, 0.0015);
    s -= padLat; n += padLat; wst -= padLon; e += padLon;
    const boxW = box.dw - 140;   // the on-screen image, minus room for the pin labels
    const boxH = box.dh - 140;
    let best = MIN_Z;
    for (let z = MIN_Z; z <= MAX_Z; z++){
      if (Math.abs(lon2x(e,z)-lon2x(wst,z)) <= boxW &&
          Math.abs(lat2y(s,z)-lat2y(n,z)) <= boxH) best = z; else break;
    }
    return { z:best, lat:(s+n)/2, lng:(wst+e)/2 };
  }

  function fit(){
    const target = computeFit(devices);
    if (!target) return;
    view.z = target.z; view.lat = target.lat; view.lng = target.lng;
    norm(); offset = {x:0,y:0}; paint(); warmNeighbours();
  }

  function zoomBy(delta, ax, ay){
    const box = imgBox();
    const { w, h } = size();
    ax = ax === undefined ? w/2 : ax;
    ay = ay === undefined ? h/2 : ay;
    // Keep the point under the anchor fixed while the zoom changes.
    const lat = y2lat(lat2y(view.lat, view.z) - box.dh/2 + (ay - box.oy), view.z);
    const lng = x2lon(lon2x(view.lng, view.z) - box.dw/2 + (ax - box.ox), view.z);
    const next = Math.max(MIN_Z, Math.min(MAX_Z, view.z + delta));
    if (next === view.z) return;
    view.z = next;
    view.lat = y2lat(lat2y(lat, view.z) + box.dh/2 - (ay - box.oy), view.z);
    view.lng = x2lon(lon2x(lng, view.z) + box.dw/2 - (ax - box.ox), view.z);
    norm(); offset = {x:0,y:0}; paint();
  }

  function interactions(){
    let drag = false, sx = 0, sy = 0;
    el.root.addEventListener('mousedown', ev => {
      if (ev.target.closest('.pin') || ev.target.closest('.zoombox') ||
          ev.target.closest('.card') || ev.target.closest('.maptypes')) return;
      drag = true; sx = ev.clientX; sy = ev.clientY; el.root.style.cursor = 'grabbing'; ev.preventDefault();
    });
    window.addEventListener('mousemove', ev => {
      if (!drag) return;
      offset = { x: ev.clientX - sx, y: ev.clientY - sy }; redraw();
    });
    window.addEventListener('mouseup', () => {
      if (!drag) return;
      drag = false; el.root.style.cursor = '';
      if (offset.x || offset.y){
        view.lng = x2lon(lon2x(view.lng, view.z) - offset.x, view.z);
        view.lat = y2lat(lat2y(view.lat, view.z) - offset.y, view.z);
        norm();
      }
      commit();
    });
    el.root.addEventListener('wheel', ev => {
      ev.preventDefault();
      const r = el.root.getBoundingClientRect();
      zoomBy(ev.deltaY < 0 ? 1 : -1, ev.clientX - r.left, ev.clientY - r.top);
    }, { passive:false });
    el.root.addEventListener('dblclick', ev => {
      const r = el.root.getBoundingClientRect();
      zoomBy(1, ev.clientX - r.left, ev.clientY - r.top);
    });
    window.addEventListener('resize', () => { if (active) paint(); });
  }

  return {
    use(){
      if (active) return; active = true;
      el.root = document.getElementById('mapwrap');
      el.img = document.getElementById('gmapimg');
      el.pins = document.getElementById('pins');
      el.img.style.display = 'block';
      document.getElementById('zin').onclick = () => zoomBy(1);
      document.getElementById('zout').onclick = () => zoomBy(-1);
      document.getElementById('fit').onclick = fit;
      interactions();
    },
    setDevices(list){
      devices = list || [];
      if (!active) return;
      if (!fitted){ fitted = true; fit(); } else drawPins();
    },
    focus(d){
      if (!active) return;
      view.lat = d.lat; view.lng = d.lng;
      if (view.z < 15) view.z = 15;
      norm(); offset = {x:0,y:0}; paint(); warmNeighbours();
    },
    fitAll(){ fit(); },
    setMapType(mt){
      if (mt === mapType) return;
      mapType = mt;
      queue.length = 0;
      imgs.forEach(rec => { if (rec.url) URL.revokeObjectURL(rec.url); });
      imgs.clear();                 // the cached images are the wrong map type
      if (active) paint();
    },
    mapType(){ return mapType; },
    active(){ return active; },
    // Test hooks (used by work/test_page.js; harmless in the browser).
    _view(){ return { z:view.z, lat:view.lat, lng:view.lng }; },
    _zoomBy(delta, ax, ay){ zoomBy(delta, ax, ay); return { z:view.z, lat:view.lat, lng:view.lng }; },
    _geometry(list){
      const box = imgBox();
      const { w, h } = size();
      return { w:w, h:h, iw:box.iw, ih:box.ih, dw:box.dw, dh:box.dh,
               ox:box.ox, oy:box.oy,
               view:computeFit(list),
               project(d, v){
                 return { x: box.ox + box.dw/2 + lon2x(d.lng, v.z) - lon2x(v.lng, v.z),
                          y: box.oy + box.dh/2 + lat2y(d.lat, v.z) - lat2y(v.lat, v.z) };
               } };
    }
  };
})();

// ---------- native Google Maps (used when the key allows this origin) ----------
const NativeMap = (function(){
  let map = null, markers = [], info = null, ready = false, traffic = null, trafficOn = false;
  return {
    use(){
      map = new google.maps.Map(document.getElementById('mapwrap'), {
        center:{ lat:20.95, lng:72.93 }, zoom:11,
        mapTypeId:google.maps.MapTypeId.ROADMAP,
        styles:[{ featureType:'poi', stylers:[{ visibility:'off' }] }],
        streetViewControl:false, fullscreenControl:true, mapTypeControl:false
      });
      ready = true;
    },
    isReady(){ return ready; },
    setMapType(mt){
      if (!ready) return;
      const ids = { roadmap:google.maps.MapTypeId.ROADMAP, terrain:google.maps.MapTypeId.TERRAIN,
                    satellite:google.maps.MapTypeId.SATELLITE, hybrid:google.maps.MapTypeId.HYBRID };
      map.setMapTypeId(ids[mt] || google.maps.MapTypeId.ROADMAP);
    },
    toggleTraffic(){
      if (!ready) return false;
      if (!traffic) traffic = new google.maps.TrafficLayer();
      trafficOn = !trafficOn;
      traffic.setMap(trafficOn ? map : null);
      return trafficOn;
    },
    setDevices(list){
      if (!ready) return;
      markers.forEach(m => m.setMap(null)); markers = [];
      if (info){ info.close(); info = null; }
      if (!list.length) return;
      const b = new google.maps.LatLngBounds();
      list.forEach(d => {
        const ll = { lat:d.lat, lng:d.lng };
        b.extend(ll);
        const m = new google.maps.Marker({
          position:ll, map:map, title:d.name,
          icon:{ url:arrowUrl(d), scaledSize:new google.maps.Size(34,34),
                 anchor:new google.maps.Point(17,17) },
          zIndex: d.status === 'offline' ? 1 : (d.status === 'moving' ? 10 : 5)
        });
        m.addListener('click', () => select(d, true));
        markers.push(m);
      });
      if (list.length === 1){ map.setCenter({lat:list[0].lat, lng:list[0].lng}); map.setZoom(15); }
      else map.fitBounds(b, 60);
    },
    focus(d){
      if (!ready) return;
      map.panTo({ lat:d.lat, lng:d.lng });
      if (map.getZoom() < 15) map.setZoom(15);
    },
    fitAll(){ if (ready && state.devices.length) this.setDevices(state.devices); }
  };
})();

// ---------- selection + fleet list ----------
function select(d, keepCard){
  state.selected = d.id;
  renderList();
  if (state.native){ NativeMap.focus(d); NativeMap.setDevices(state.devices); }
  else { ProxyMap.setDevices(state.devices); ProxyMap.focus(d); }
  showCard(d);
}
function showCard(d){
  const card = document.getElementById('card');
  card.innerHTML = cardHtml(d); card.style.display = 'block';
  const x = document.getElementById('cardx');
  if (x) x.onclick = () => { card.style.display = 'none'; state.selected = null; renderList(); };
}
function renderList(){
  const list = document.getElementById('list');
  const rows = state.devices.filter(d => state.filter === 'all' || d.status === state.filter);
  list.innerHTML = '';
  if (!rows.length){
    const li = document.createElement('li');
    li.className = 'row'; li.style.cursor = 'default';
    li.innerHTML = '<span class="nm" style="color:hsl(var(--muted-foreground));font-weight:400">' +
                   'No buses in this view</span>';
    list.appendChild(li); return;
  }
  rows.forEach(d => {
    const li = document.createElement('li');
    li.className = 'row' + (state.selected === d.id ? ' on' : '');
    li.innerHTML = '<i class="dot ' + d.status + '"></i>' +
      '<span class="nm">' + d.name + '</span>' +
      '<span class="sp">' + d.speedKmh + ' km/h</span>';
    li.onclick = () => select(d);
    list.appendChild(li);
  });
}

// ---------- data refresh ----------
async function refresh(){
  try {
    const res = await fetch('/api/positions', { cache:'no-store' });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || 'tracking request failed');

    state.devices = data.devices;
    document.getElementById('online').textContent = data.online;
    document.getElementById('total').textContent = data.total;
    document.getElementById('moving').textContent = data.moving;
    document.getElementById('stopped').textContent = data.stopped;
    document.getElementById('updated').textContent = new Date(data.fetchedAt).toLocaleTimeString();
    document.getElementById('allmaps').href = data.allMapsUrl;
    document.getElementById('foot').textContent =
      data.total + ' buses \u00b7 auto-refresh every ' + data.refreshSeconds + 's' +
      (data.cached ? ' \u00b7 server cache' : '');

    document.getElementById('overlay').classList.add('hidden');
    document.getElementById('mapnote').textContent = state.native
      ? 'Native Google Maps \u00b7 live markers, map types and traffic'
      : 'Google Maps via this app\u2019s server \u00b7 drag to pan, scroll to zoom, click a bus';

    renderList();
    if (state.native) NativeMap.setDevices(state.devices);
    else ProxyMap.setDevices(state.devices);
  } catch (err){
    const ov = document.getElementById('overlay');
    ov.classList.remove('hidden');
    document.getElementById('spin').style.display = 'none';
    document.getElementById('ovtitle').textContent = 'Could not load tracking data';
    document.getElementById('ovbody').innerHTML =
      '<p class="err">' + (err && err.message ? err.message : err) + '</p>' +
      '<p>Check that this machine can reach <code>supabase.apps2db.uctechlabs.com</code>.</p>';
  }
}

document.querySelectorAll('.tab').forEach(t => {
  t.onclick = () => {
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('on'));
    t.classList.add('on'); state.filter = t.dataset.filter; renderList();
  };
});

// ---- map type buttons ----
let currentType = 'roadmap';
document.querySelectorAll('#maptypes button[data-mt]').forEach(b => {
  b.onclick = () => {
    currentType = b.dataset.mt;
    document.querySelectorAll('#maptypes button[data-mt]').forEach(x => x.classList.remove('on'));
    b.classList.add('on');
    ProxyMap.setMapType(currentType);
    NativeMap.setMapType(currentType);
  };
});

// ---- traffic: only available with the native Google Maps API ----
const trafficBtn = document.getElementById('traffic');
trafficBtn.onclick = () => {
  if (!trafficBtn.classList.contains('avail')) {
    // Explain why instead of doing nothing.
    const note = document.getElementById('mapnote');
    note.textContent = 'Traffic needs the native Google Maps API \u2014 start the app with ' +
      'your own key (--key YOUR_KEY) and the Traffic button becomes active.';
    setTimeout(refreshNote, 6000);
    return;
  }
  trafficBtn.classList.toggle('on', NativeMap.toggleTraffic());
};
function enableTraffic(){
  trafficBtn.classList.add('avail');
  trafficBtn.title = 'Toggle the Google traffic layer';
}
function refreshNote(){
  document.getElementById('mapnote').textContent = state.native
    ? 'Native Google Maps \u00b7 live markers, map types and traffic'
    : 'Google Maps via this app\u2019s server \u00b7 drag to pan, scroll to zoom, click a bus';
}
trafficBtn.title = 'Traffic layer (needs native Google Maps)';

document.getElementById('refresh').onclick = refresh;

__PAGE_READY__
refresh();
setInterval(refresh, REFRESH_MS);
</script>
__NATIVE_SCRIPT__
</body>
</html>
"""

NATIVE_LOADER = r"""
<script>
// Native Google Maps, only when the key accepts this origin. Otherwise the
// server-proxied map above is used, so Google's "didn't load" UI never appears.
(function(){
  const s = document.createElement('script');
  s.src = 'https://maps.googleapis.com/maps/api/js?key=__KEY__&callback=__onNative&loading=async';
  s.async = true;
  s.onerror = function(){ document.getElementById('mapnote').textContent =
    'Google Maps JS API failed to load; using the server-proxied map.'; };
  document.head.appendChild(s);
})();
function __onNative(){
  try {
    NativeMap.use();
    state.native = true;
    document.getElementById('gmapimg').style.display = 'none';
    document.getElementById('tiles').style.display = 'none';
    document.getElementById('pins').style.display = 'none';
    document.querySelector('.zoombox').style.display = 'none';
    enableTraffic();
    NativeMap.setMapType(currentType);
    document.getElementById('mapnote').textContent =
      'Native Google Maps \u00b7 live markers, map types and traffic';
    if (state.devices.length) NativeMap.setDevices(state.devices);
  } catch (e) {
    document.getElementById('mapnote').textContent = 'Using the server-proxied Google map.';
  }
}
window.gm_authFailure = function(){
  document.getElementById('mapnote').textContent =
    'Google refused this key for this page; using the server-proxied map.';
};
</script>
"""


# Injected where the page decides which map to use. In native mode the proxy map is
# only started if the Google JS API fails, so it costs no map-image requests.
READY_PROXY = """// No usable native key: use the server-proxied Google map straight away.
ProxyMap.use();
window.__ProxyMap = ProxyMap;   // test hook
"""

READY_NATIVE = """window.__ProxyMap = ProxyMap;   // test hook
// Wait for the Google JS API. If it never arrives, fall back to the proxy map.
setTimeout(function(){
  if (!state.native && !ProxyMap.active()) {
    ProxyMap.use();
    document.getElementById('mapnote').textContent =
      'Google Maps JS API unavailable \\u00b7 using the server-proxied Google map.';
  }
}, 9000);
"""


def render_page(key: str, port: int, native: bool) -> str:
    script = NATIVE_LOADER.replace("__KEY__", urllib.parse.quote(key, safe="")) if native else ""
    return (
        PAGE.replace("__REFRESH_MS__", str(REFRESH_SECONDS * 1000))
        .replace("__PAGE_READY__", READY_NATIVE if native else READY_PROXY)
        .replace("__NATIVE_SCRIPT__", script)
    )


# --------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "CompassTracker/1.0"
    page = ""
    token = TRACKING_TOKEN
    key = GOOGLE_MAPS_KEY
    cache: dict[tuple, bytes] = {}

    def log_message(self, fmt, *args):
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
        elif path == "/compass-icon.svg":
            self._send(200, COMPASS_ICON.encode("utf-8"), "image/svg+xml")
        elif path == "/api/positions":
            self._serve_positions()
        elif path == "/api/view":
            self._serve_view()
        elif path == "/api/staticmap":
            self._serve_staticmap()
        elif path == "/healthz":
            self._send(200, b"ok", "text/plain")
        else:
            self._send(404, b"not found", "text/plain")

    # ---- endpoints ----
    def _fail(self, exc: Exception) -> None:
        self._send(200, json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}).encode(),
                   "application/json")

    def _devices(self) -> list[dict]:
        return normalise(fetch_positions(self.token))["devices"]

    def _serve_positions(self) -> None:
        try:
            payload = normalise(fetch_positions(self.token))
        except urllib.error.HTTPError as exc:
            payload = {"ok": False,
                       "error": f"tracking API HTTP {exc.code}: "
                                f"{exc.read().decode('utf-8','replace')[:160]}"}
        except Exception as exc:
            payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        self._send(200, json.dumps(payload).encode("utf-8"), "application/json")

    def _serve_view(self) -> None:
        try:
            view = fleet_view(self._devices(), 640, 640)
        except Exception as exc:
            self._fail(exc)
            return
        view["ok"] = True
        self._send(200, json.dumps(view).encode("utf-8"), "application/json")

    def _serve_staticmap(self) -> None:
        """One map image, addressed either by tile index (tx,ty,z,n) or by centre."""
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        map_type = query.get("type", ["roadmap"])[0]
        if map_type not in MAP_TYPES:
            map_type = "roadmap"

        try:
            scale = 2 if int(query.get("scale", ["1"])[0]) == 2 else 1
            if "tx" in query or "ty" in query:
                # Tile addressing: convert the tile to its centre coordinates.
                n = max(64, min(640, int(query.get("n", ["512"])[0])))
                z = int(float(query.get("z", ["11"])[0]))
                tx = int(query.get("tx", ["0"])[0])
                ty = int(query.get("ty", ["0"])[0])
                if not (0 <= z <= MAX_ZOOM):
                    self._send(400, b"zoom out of range", "text/plain")
                    return
                lat = y_to_lat(ty * n + n / 2.0, z)
                lng = x_to_lon(tx * n + n / 2.0, z)
                width = height = n
            else:
                lat = float(query.get("lat", ["20.95"])[0])
                lng = float(query.get("lng", ["72.93"])[0])
                zoom = int(float(query.get("z", ["12"])[0]))
                width = max(64, min(640, int(query.get("w", ["640"])[0])))
                height = max(64, min(640, int(query.get("h", ["640"])[0])))
                z = zoom
        except ValueError:
            self._send(400, b"bad map parameters", "text/plain")
            return
        if not (-90 <= lat <= 90 and -180 <= lng <= 180 and 0 <= z <= MAX_ZOOM):
            self._send(400, b"map request out of range", "text/plain")
            return

        cache_key = (round(lat, 6), round(lng, 6), z, width, height, map_type, scale)
        hit = Handler.cache.get(cache_key)
        if hit is not None:
            self._send(200, hit, "image/png")
            return
        try:
            image = fetch_static_map(self.key, lat, lng, z, width, height, map_type, scale)
        except Exception as exc:
            self._fail(exc)
            return
        Handler.cache[cache_key] = image
        if len(Handler.cache) > 300:
            Handler.cache.pop(next(iter(Handler.cache)))
        self._send(200, image, "image/png")


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compass live tracking on Google Maps (drop-in replacement page)"
    )
    parser.add_argument("--token", default=TRACKING_TOKEN, help="Compass share-tracking token")
    parser.add_argument("--key", default=GOOGLE_MAPS_KEY, help="Google Maps API key")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"),
                        help="bind address (default 0.0.0.0 so a host can reach it)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "5000")),
                        help="port (default $PORT or 5000)")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--insecure", action="store_true",
                        help="skip SSL certificate verification (only if you get certificate errors)")
    parser.add_argument("--no-key-check", action="store_true",
                        help="skip the startup check of whether the Google key allows this origin")
    args = parser.parse_args(argv)

    if args.insecure:
        SSL_CTX.check_hostname = False
        SSL_CTX.verify_mode = ssl.CERT_NONE

    native = False if args.no_key_check else key_allows_browser(args.key, args.port)
    Handler.page = render_page(args.key, args.port, native)
    Handler.token = args.token
    Handler.key = args.key

    try:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        print(f"Port {args.port} is busy ({exc}).", file=sys.stderr)
        print(f"Try: python {os.path.basename(__file__)} --port {args.port + 1}", file=sys.stderr)
        return 1

    url = f"http://127.0.0.1:{args.port}/"      # what to open locally
    print("Compass live tracking on Google Maps")
    print(f"  tracking token : {args.token[:16]}...")
    print(f"  google key     : {args.key[:12]}...")
    print(f"  map            : {'native Google Maps JS API' if native else 'server-proxied Google map'}")
    if not native:
        print("                   (key is referer-restricted, so the browser cannot use it;")
        print("                    this app fetches the Google imagery itself)")
    print(f"  listening      : http://{args.host}:{args.port}/")
    print(f"  open           : {url}")
    print("  stop           : Ctrl+C")

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
