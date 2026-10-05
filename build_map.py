"""Pull the Compass public-tracking feed and export Google Maps friendly outputs.

Input : Compass share-tracking token (hardcoded below, override with argv[1]).
Output: buses.csv, buses.kml, google_maps_url.txt
"""

import csv
import json
import ssl
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

SB = "https://supabase.apps2db.uctechlabs.com"
ANON = (
    "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJzdXBhYmFzZSIsImlhdCI6MTc3MTc1ODU0MCwi"
    "ZXhwIjo0OTI3NDMyMTQwLCJyb2xlIjoiYW5vbiJ9.0OKRMEyYPy-8bjw0hWCugu8LZTn3k27y9A6xjG0Ek1w"
)
TOKEN = "4996b81acfbfc13d3f539a5dff6c6fdbed921b3aa46f9c50"

# Public tracking page polls every 30s; anything older than this is treated as offline.
OFFLINE_AFTER_MS = 10_800_000

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def fetch_positions(token: str) -> dict:
    body = json.dumps({"token": token}).encode()
    req = urllib.request.Request(
        f"{SB}/functions/v1/public-tracking",
        data=body,
        headers={
            "apikey": ANON,
            "Authorization": f"Bearer {ANON}",
            "Content-Type": "application/json",
            "Origin": "https://compass.apps2.uctechlabs.com",
            "User-Agent": "Mozilla/5.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60, context=CTX) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def esc(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def main() -> int:
    token = sys.argv[1] if len(sys.argv) > 1 else TOKEN
    data = fetch_positions(token)
    positions = data.get("positions") or []
    fetched_at = data.get("fetched_at") or datetime.now(timezone.utc).isoformat()
    if not positions:
        print("No positions returned for that token.", file=sys.stderr)
        return 1

    now_ms = datetime.now(timezone.utc).timestamp() * 1000
    rows = []
    for p in positions:
        lat = float(p["latitude"])
        lon = float(p["longitude"])
        last = p.get("lastUpdate")
        try:
            age_ms = now_ms - datetime.fromisoformat(
                str(last).replace("Z", "+00:00")
            ).timestamp() * 1000
        except Exception:
            age_ms = None
        offline = age_ms is None or age_ms > OFFLINE_AFTER_MS
        rows.append(
            {
                "name": p.get("name") or f"device-{p.get('id')}",
                "latitude": lat,
                "longitude": lon,
                "speed_kmh": round(float(p.get("speed") or 0), 1),
                "heading": round(float(p.get("heading") or 0)),
                "status": "OFFLINE" if offline else ("MOVING" if (p.get("speed") or 0) > 1 else "STOPPED"),
                "last_update_utc": last or "",
                "google_maps_pin": f"https://www.google.com/maps/search/?api=1&query={lat},{lon}",
            }
        )
    rows.sort(key=lambda r: r["name"])

    # ---- CSV (drop straight into Google My Maps: Create map > Import) ----
    with open("buses.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    # ---- KML ----
    pins = []
    for r in rows:
        color = "ff4a4aff" if r["status"] == "OFFLINE" else ("ff3aa716" if r["status"] == "MOVING" else "ff2626dc")
        desc = (
            f"Status: {r['status']}\n"
            f"Speed: {r['speed_kmh']} km/h\n"
            f"Heading: {r['heading']} deg\n"
            f"Last update (UTC): {r['last_update_utc']}\n"
            f"{r['google_maps_pin']}"
        )
        pins.append(
            f"""    <Placemark>
      <name>{esc(r['name'])}</name>
      <description>{esc(desc)}</description>
      <Style>
        <IconStyle>
          <color>{color}</color>
          <scale>1.1</scale>
          <Icon><href>http://maps.google.com/mapfiles/kml/shapes/bus.png</href></Icon>
        </IconStyle>
      </Style>
      <Point><coordinates>{r['longitude']},{r['latitude']},0</coordinates></Point>
    </Placemark>"""
        )

    kml = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Compass fleet snapshot</name>
    <description>Snapshot taken {esc(fetched_at)} from Compass public tracking link.</description>
{chr(10).join(pins)}
  </Document>
</kml>
"""
    with open("buses.kml", "w", encoding="utf-8") as fh:
        fh.write(kml)

    # ---- Google Maps search URL: pipe-separated query drops one pin per location ----
    query = "|".join(f"{r['latitude']},{r['longitude']}" for r in rows)
    url = "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote(
        query, safe=",|"
    )
    with open("google_maps_url.txt", "w", encoding="utf-8") as fh:
        fh.write(url + "\n")

    # ---- Markdown with one guaranteed Google Maps link per bus ----
    lines = [
        "# Compass fleet -> Google Maps",
        "",
        f"Snapshot: `{fetched_at}`  |  {len(rows)} buses",
        "",
        "## One-click link for all positions",
        "",
        f"<{url}>",
        "",
        "## One link per bus",
        "",
        "| Bus | Speed | Heading | Status | Last update (UTC) | Google Maps |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        lines.append(
            f"| {r['name']} | {r['speed_kmh']} km/h | {r['heading']} deg | "
            f"{r['status']} | {r['last_update_utc']} | "
            f"[open]({r['google_maps_pin']}) |"
        )
    lines += [
        "",
        "## Put all 20 on a live Google map",
        "",
        "1. Open <https://www.google.com/mymaps> and click **Create a new map**.",
        "2. In the layer panel choose **Import**, then upload `buses.csv`.",
        "3. Pick `name` as the column that positions the placemarks (lat/long are auto-detected).",
        "4. Re-run `build_map.py` and re-import to refresh the positions.",
        "",
        "Google My Maps has no public write API, so refreshing is a manual re-import.",
        "For a map that redraws itself, use the Compass tracker link itself.",
        "",
    ]
    with open("google_maps_links.md", "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    print(f"snapshot: {fetched_at}   devices: {len(rows)}")
    for r in rows:
        print(f"  {r['name']:<8} {r['latitude']:.5f},{r['longitude']:.5f}  "
              f"{r['speed_kmh']:>5} km/h  {r['status']}")
    print("\nURL length:", len(url))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
