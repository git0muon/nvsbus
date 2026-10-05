# Compass fleet -> Google Maps

Snapshot: `2026-10-05T06:13:05.506Z`  |  20 buses

## One-click link for all positions

<https://www.google.com/maps/search/?api=1&query=20.937593333333332,72.91148444444444|20.95024,72.92173777777778|20.952262222222224,72.91151555555555|20.93674222222222,72.91507111111112|20.900097777777777,72.91902222222222|20.84977111111111,72.91133777777777|21.029593333333334,72.97114666666667|20.950435555555554,72.91382666666667|20.946284444444444,72.96318666666667|20.920906666666667,73.0095911111111|20.949368888888888,72.91373333333334|20.950455555555557,72.91388888888889|20.95033777777778,72.91384|20.958391111111112,73.05950222222222|20.95337333333333,72.92504|20.949931111111113,72.93201333333333|20.949922222222224,72.91713777777778|20.949177777777777,72.91373333333334|20.949202222222223,72.91375111111111|20.94534,72.96674222222222>

## One link per bus

| Bus | Speed | Heading | Status | Last update (UTC) | Google Maps |
| --- | --- | --- | --- | --- | --- |
| Bus 01 | 3.6 km/h | 143 deg | MOVING | 2026-10-05T06:13:00Z | [open](https://www.google.com/maps/search/?api=1&query=20.937593333333332,72.91148444444444) |
| Bus 02 | 2.8 km/h | 267 deg | MOVING | 2026-10-05T06:12:57Z | [open](https://www.google.com/maps/search/?api=1&query=20.95024,72.92173777777778) |
| Bus 03 | 10.6 km/h | 343 deg | MOVING | 2026-10-05T06:12:50Z | [open](https://www.google.com/maps/search/?api=1&query=20.952262222222224,72.91151555555555) |
| Bus 04 | 0.0 km/h | 131 deg | STOPPED | 2026-10-05T06:12:53Z | [open](https://www.google.com/maps/search/?api=1&query=20.93674222222222,72.91507111111112) |
| Bus 05 | 11.9 km/h | 357 deg | MOVING | 2026-10-05T06:12:52Z | [open](https://www.google.com/maps/search/?api=1&query=20.900097777777777,72.91902222222222) |
| Bus 06 | 0.0 km/h | 320 deg | STOPPED | 2026-10-05T06:12:56Z | [open](https://www.google.com/maps/search/?api=1&query=20.84977111111111,72.91133777777777) |
| Bus 07 | 12.5 km/h | 161 deg | MOVING | 2026-10-05T06:13:01Z | [open](https://www.google.com/maps/search/?api=1&query=21.029593333333334,72.97114666666667) |
| Bus 08 | 0.0 km/h | 294 deg | STOPPED | 2026-10-05T06:01:39Z | [open](https://www.google.com/maps/search/?api=1&query=20.950435555555554,72.91382666666667) |
| Bus 09 | 8.6 km/h | 280 deg | MOVING | 2026-10-05T06:12:52Z | [open](https://www.google.com/maps/search/?api=1&query=20.946284444444444,72.96318666666667) |
| Bus 10 | 10.6 km/h | 108 deg | MOVING | 2026-10-05T06:12:58Z | [open](https://www.google.com/maps/search/?api=1&query=20.920906666666667,73.0095911111111) |
| Bus 11 | 0.0 km/h | 205 deg | STOPPED | 2026-10-05T06:12:58Z | [open](https://www.google.com/maps/search/?api=1&query=20.949368888888888,72.91373333333334) |
| Bus 12 | 0.0 km/h | 15 deg | STOPPED | 2026-10-05T05:56:40Z | [open](https://www.google.com/maps/search/?api=1&query=20.950455555555557,72.91388888888889) |
| Bus 13 | 0.0 km/h | 28 deg | STOPPED | 2026-10-05T05:11:30Z | [open](https://www.google.com/maps/search/?api=1&query=20.95033777777778,72.91384) |
| Bus 14 | 0.0 km/h | 269 deg | STOPPED | 2026-10-05T06:13:00Z | [open](https://www.google.com/maps/search/?api=1&query=20.958391111111112,73.05950222222222) |
| Bus 15 | 5.0 km/h | 277 deg | MOVING | 2026-10-05T06:12:52Z | [open](https://www.google.com/maps/search/?api=1&query=20.95337333333333,72.92504) |
| Bus 16 | 0.0 km/h | 97 deg | STOPPED | 2026-10-05T06:12:56Z | [open](https://www.google.com/maps/search/?api=1&query=20.949931111111113,72.93201333333333) |
| Bus 17 | 0.0 km/h | 79 deg | STOPPED | 2026-10-05T06:12:55Z | [open](https://www.google.com/maps/search/?api=1&query=20.949922222222224,72.91713777777778) |
| Bus 18 | 0.0 km/h | 191 deg | STOPPED | 2026-10-05T05:13:10Z | [open](https://www.google.com/maps/search/?api=1&query=20.949177777777777,72.91373333333334) |
| Bus 19 | 0.0 km/h | 251 deg | STOPPED | 2026-10-05T04:59:42Z | [open](https://www.google.com/maps/search/?api=1&query=20.949202222222223,72.91375111111111) |
| Bus 20 | 0.0 km/h | 142 deg | STOPPED | 2026-10-05T06:12:35Z | [open](https://www.google.com/maps/search/?api=1&query=20.94534,72.96674222222222) |

## Put all 20 on a live Google map

1. Open <https://www.google.com/mymaps> and click **Create a new map**.
2. In the layer panel choose **Import**, then upload `buses.csv`.
3. Pick `name` as the column that positions the placemarks (lat/long are auto-detected).
4. Re-run `build_map.py` and re-import to refresh the positions.

Google My Maps has no public write API, so refreshing is a manual re-import.
For a map that redraws itself, use the Compass tracker link itself.
