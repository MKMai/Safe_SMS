# SafeSMS Offline Emergency Navigator — Demo v3

## Do the Python files need to be one script?

No. Keeping them modular is better because each program has one job:

- `safesms_streamlit.py` — main Command Center + User Report application
- `safesms_core.py` — shared routing/SMS/encoding logic
- `safesms_prepare_city.py` — download and prepare a city while online
- `safesms_tiles.py` — cache basemap tiles while online
- `safesms_encoder.py` — terminal emergency-code encoder + optional SMS sender
- `safesms_decoder.py` — terminal decoder
- `safesms_cli.py` — optional single terminal entry point

You only need to start **Streamlit** for the application. The other scripts are preparation/administration tools.

## Install

```powershell
pip install -r requirements_streamlit.txt
```

## 1. Prepare a city — internet required

```powershell
python safesms_prepare_city.py --city "Ljubljana, Slovenia" --network drive --hex-size 400
```

Then cache the map tiles for that package:

```powershell
python safesms_tiles.py --package city_packages/<PACKAGE_ID> --zmin 12 --zmax 15
```

You can repeat this for any city in the world that can be obtained from the OSM geocoder and network data.

## 2. Start the application

```powershell
streamlit run safesms_streamlit.py
```

The application can operate from the local graph/grid/tile package without contacting a routing server or map server.

## 3. Two application modes

### COMMAND CENTER

The operator can:

- load/decode an emergency SMS
- see blocked tiles
- edit individual tiles
- choose reason: flood, traffic accident, fire, debris, collapse, etc.
- assign severity 1–5
- generate a new emergency SMS
- send it to a phone number through Twilio
- calculate fastest and easiest routes

### USER REPORT

A user can:

1. click anywhere inside a grid tile
2. the application identifies that tile
3. choose what is happening
4. rate how dangerous it feels from 1–5
5. block the tile locally
6. see the map change immediately
7. send the incident report to the operator by SMS

The report code contains the affected tile, reason and severity, so the operator can decode it and see why the tile was reported.

## 4. Fastest vs easiest

**Fastest** uses shortest distance on the safe graph.

**Easiest** uses the same safe graph but adds a road-class penalty. Major roads are more expensive, while residential/cycle/quiet paths are favoured. This is an offline proxy for traffic stress; it is not live traffic.

## 5. SMS

SafeSMS v3 uses a compressed, checksummed payload containing:

- SafeSMS version
- city package ID
- emergency-grid fingerprint
- route profile
- transport mode
- origin/destination when present
- blocked tile IDs
- per-tile severity
- per-tile reason
- issue/expiry timestamps

### Twilio configuration

Set these environment variables before sending SMS:

```powershell
$env:TWILIO_ACCOUNT_SID="AC..."
$env:TWILIO_AUTH_TOKEN="..."
$env:TWILIO_FROM_NUMBER="+1..."
```

Then use the Streamlit phone field or terminal encoder `--send-to`.

For Streamlit secrets, create `.streamlit/secrets.toml`:

```toml
TWILIO_ACCOUNT_SID = "AC..."
TWILIO_AUTH_TOKEN = "..."
TWILIO_FROM_NUMBER = "+1..."
```

## Terminal examples

Encode:

```powershell
python safesms_encoder.py --city "Ljubljana, Slovenia" --package city_packages/<PACKAGE_ID> --blocked 12,18,19 --incidents incidents.json --origin 46.051 14.492 --destination 46.061 14.505 --preference easiest --send-to +38640123456
```

Decode:

```powershell
python safesms_decoder.py "SMS3-..."
```

Optional unified CLI:

```powershell
python safesms_cli.py encode --city "Ljubljana, Slovenia" --package city_packages/<PACKAGE_ID> --blocked 12,18,19
python safesms_cli.py decode "SMS3-..."
```

## Demo architecture

```text
             ONLINE PREPARATION
                    |
        +-----------+-----------+
        |                       |
    OSM graph               Map tiles
        |                       |
        +-----------+-----------+
                    |
               CITY PACKAGE
                    |
             offline computer
                    |
        +-----------+-----------+
        |                       |
   USER REPORT              OPERATOR
        |                       |
 click tile + reason       incident editor
 severity 1–5                   |
        |                  generate SMS
        +---------> SMS <-------+
                    |
                 decoder
                    |
          local blocked graph
             /           \
        FASTEST         EASIEST
```

## Recommended next production improvements

1. Digitally sign emergency SMS codes (HMAC/public-key signature) so users can distinguish official messages from forged ones.
2. Support multi-SMS fragmentation for incidents containing hundreds/thousands of tiles.
3. Add a local vector-map fallback so routing/incident visualization remains usable if a tile cache is incomplete.
4. Add package validation before deployment: graph, grid, fingerprint, tile coverage and test route.
5. Add a local incident store so reports from several users can be merged before the operator broadcasts a new emergency code.
6. Add an emergency incident ID so several reports about the same event can be grouped.
7. Add confidence/report count per tile; e.g. three users reporting the same tile should make it more trusted than one report.
8. Add configurable road penalties per city/transport mode rather than hard-coding one global profile.
9. Add a hardware SMS modem option later, so the emergency center does not depend on Twilio/internet for outgoing SMS.
10. For production, use a basemap provider that explicitly permits offline caching/bulk tile downloads rather than relying on the standard OSM tile server for large-scale prefetching.
