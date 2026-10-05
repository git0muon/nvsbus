# Compass live tracking

Two builds of the same thing: a live map of the Compass fleet, refreshed on a timer.

| Build | Runs where | Map | Live data |
| --- | --- | --- | --- |
| **Static site** — [`docs/`](docs) | GitHub Pages, or any static host | Leaflet + OpenStreetMap / Esri imagery, natively tiled and sharp at every zoom | Yes — the browser calls the tracking API directly (it sends `Access-Control-Allow-Origin: *`) |
| **Python server** — [`compass_tracker.py`](compass_tracker.py) | Docker, Render, Railway, or your machine | Google Maps (native when your key allows the origin, otherwise server-proxied imagery) | Yes — the server proxies the API |

The static build is the one to publish: **no API key, no backend, no build step.**

## Publish the static site on GitHub Pages

1. Push this repository to GitHub (see below).
2. Repo → **Settings** → **Pages** → **Build and deployment** → **Source: GitHub Actions**.
   The included workflow ([`.github/workflows/pages.yml`](.github/workflows/pages.yml)) then
   deploys on every push to `main`.
3. Your site appears at `https://<your-username>.github.io/<repo-name>/`.

### Run it locally

```powershell
python -m http.server 8000 --directory docs
# then open http://127.0.0.1:8000/
```

## On a phone

The site is built mobile-first, so it works as a shared link or added to the Home Screen:

- **Full-screen button** in the header (enters/exits full screen, with an icon that swaps).
  On iPhone, where Safari has no element full-screen API, the button explains to use
  *Share → Add to Home Screen* instead — and the page ships the Apple meta tags needed for
  that to open without browser chrome.
- **Draggable fleet sheet** — swipe it down to see more map, up for more list. Tapping a bus
  collapses it to a peek automatically so the map is visible.
- **Tap a bus** in the list to fly the map to it and open its details.
- **Follow** button (or *Follow this bus* inside a popup) keeps that bus centred as new
  positions arrive, with the map gently panning rather than jumping.
- **Locate** button frames the whole fleet again.
- Safe-area padding for notched screens, 40 px+ touch targets, no rubber-band scrolling, and
  animations that respect `prefers-reduced-motion`.

## Configure

Everything the static build needs is in [`docs/config.js`](docs/config.js): the share token,
the Supabase URL and its public anonymous key, the refresh interval, and the offline
threshold. **These values are public by design** — the same token is in the Compass tracking
URL, and the anonymous key only grants what row-level security allows. Never put a secret
there.

## The Python server build

```powershell
python compass_tracker.py                       # http://127.0.0.1:5000
python compass_tracker.py --port 8080 --no-browser
python compass_tracker.py --insecure            # only if you hit SSL certificate errors
python compass_tracker.py --key YOUR_GOOGLE_KEY # native Google Maps + traffic
```

Standard library only — nothing to install. It binds `0.0.0.0` and honours `$PORT`, so it
runs as-is on a host:

```powershell
docker build -t compass-tracker .
docker run -p 5000:5000 compass-tracker
```

A [`Procfile`](Procfile), [`requirements.txt`](requirements.txt) (empty on purpose) and
[`Dockerfile`](Dockerfile) are included for Render / Railway / Heroku-style deploys.

### Why the server build draws Google imagery instead of using the API directly

The Google key that ships with the Compass page is **referer-restricted to
`compass.apps2.uctechlabs.com`**, so a browser anywhere else is refused with "This page
didn't load Google Maps correctly". The server works around this by fetching the imagery
itself (server requests carry no `Referer`) and drawing the buses on top. Two consequences:

- The map is one continuous image per view, so it is **soft when upscaled on a large window**.
- **Traffic is unavailable** — the static-map service has no traffic layer. I verified
  `traffic=1`, `traffic=true`, `traffic=on`, `layer=traffic` and a traffic style all return
  byte-identical images to plain roadmap.
- Tiling those images is not an option either: Google quantises each request's centre, so
  adjacent requests disagree by ~42/255 on satellite (versus ~4/255 for genuinely continuous
  imagery), which shows up as hard seams.

Pass `--key` with a key allowed on your origin to get the native Maps JavaScript API —
sharp tiled imagery, all four map types, and a working traffic layer.

**The static Leaflet build has none of these problems**, which is why it is the recommended
one to publish.

## Tests

The browser code is verified offline: `work/test_app.js` runs `docs/app.js` in Node with
Leaflet and DOM stubs and asserts the API call, the data mapping (m/s → km/h, offline rule),
markers, the fleet list, tab filters, tap-to-centre, live follow, locate, the full-screen
toggle including the `<html>`-only fallback, the sheet, and error recovery.

```powershell
node work/test_app.js
```

## Other files

| File | Purpose |
| --- | --- |
| `compass_google_map.py` | Earlier minimal Google Maps build (server-proxied imagery) |
| `build_map.py` | Exports a one-off snapshot to CSV/KML/Google Maps links |
| `docs/` | The static site: `index.html`, `style.css`, `app.js`, `config.js` |

## Notes

- No Compass login is needed; the share token is public.
- A position sample older than 3 hours counts as *offline* (the same rule the Compass page
  uses) and is drawn grey.
- Map data: © OpenStreetMap contributors, and imagery © Esri, Maxar, Earthstar Geographics.

## Licence

MIT — see [LICENSE](LICENSE).
