# NYC Subway Assistant

A local NYC subway trip planner with an interactive map. Choose your starting point and destination, compare journeys, and follow walking, boarding, transfer, and arrival instructions one step at a time.

Built as a personal portfolio prototype using open-source software and public transit data. No paid API keys or service accounts are required. **The current interface is map-only; chat is not exposed.**

## Features

- **NYC place search:** search indexed cafés, landmarks, addresses, intersections, and subway stations. Branches appear as numbered results in the list and on the map.
- **Precise location selection:** exact places become red **A** (start) or **B** (destination) markers. Parks offer nearby street corners; stations offer recorded entrances where available. You can also place a pin after selecting a broad place or station.
- **Search-first form:** highlighted From/To selectors, editable confirmed locations, and **Hide search / Show search** controls.
- **Route comparison:** estimated journey time, walking distance, transfers, and alternative journeys with sorting/filter controls.
- **Walking guidance:** dashed map paths, text street directions, and recorded station entrance guidance.
- **Train guidance:** line number or letter, posted destination, direction, first stop to check, alighting station, and transfer instructions.
- **User-confirmed progress:** advance when you reach the station, board, get off, and arrive. Go back or repeat a step.
- **Recovery:** update your current location while keeping your destination; confirm that you are off the train before replanning when prompted.
- **Live information:** direction-specific platform predictions and service notices, separate from the scheduled itinerary.
- **Saved journeys:** restore recent journey state after refreshing, with confirmation before resuming an unfinished trip.
- **Map choices:** online OpenStreetMap tiles or locally generated street outlines.

## Requirements

- Python **3.12+** with `venv` and `pip`.
- Java **21+**, available on your command path.
- Internet for initial downloads, data refreshes, live feeds, and online map tiles.
- Roughly **3 GB of disk space** for cached data, runtimes, and the default model, plus several GB of available RAM. Graph building can take several minutes.
- On macOS, the launcher downloads its Ollama runtime. On other operating systems, install [Ollama](https://ollama.com/download) and make its command available before starting.

Node.js is optional and is only used for frontend development checks. The interface has no frontend build step.

## Run locally

Clone the repository, or open a terminal in an existing checkout:

```sh
git clone https://github.com/mohansilambarasu/nyc-subway-assistant.git
cd nyc-subway-assistant
python3 scripts/run.py
```

On Windows, use `python` if `python3` is unavailable. On macOS, you can also double-click `start.command`.

The launcher creates `.venv`, installs the Python dependencies, downloads public data and required runtimes, builds the local indexes and transit graph, and starts the services. Subsequent launches reuse cached files.

Open **[http://127.0.0.1:8765/](http://127.0.0.1:8765/)** once the launcher reports that the app is ready. Keep the terminal open. **Ctrl+C** stops services launched by that command; existing services it reused remain running.

The default setup binds services to this computer's loopback interface. It is a local prototype, not a production hosting setup.

## First journey

1. Click **Start journey**.
2. Search **Joe's Pizza** and choose the branch at **1435 Broadway** from the list or its numbered map marker. It becomes **A**, and the map focuses on it.
3. Search **Charging Bull** and select the matching result. It becomes **B**.
4. Review the locations. Use the pencil on either selected location to change it.
5. Click **Find routes**, compare the available journeys, then choose **Start this route**.
6. Follow the walking card. Expand street and entrance details when needed.
7. Use **I'm at the station**, the boarding confirmation, and subsequent progress buttons as you complete each step. Read the train destination and direction before boarding.

Train choices and travel times vary with the departure time and available schedule.

### Map controls

| Control | Purpose |
| --- | --- |
| **Hide search / Show search** | Collapse or reopen the location form while keeping the map available. |
| **Hide directions / Show directions** | Hide or reopen the journey cards. |
| **View full route** | Fit an active journey into the map view. |
| **Use local streets** | Switch from online tiles to local street outlines; the same control switches back. |
| **Compare routes** | Inspect alternatives and available route filters. |
| **Repeat this step / Back** | Review instructions without creating a new trip. |
| **Something went wrong** | Start recovery and update your location while retaining the destination. |
| **New trip / Start journey** | Open a new location selection form. Closing an unsubmitted form returns to the previous journey. |

Broad parks require a more precise point. Offered street corners are **not verified park entrances**. Station entrance choices come from the MTA inventory. A manual pin must be within NYC and within 5 km of the searched place; select the resulting green point to confirm it.

## How it works

```text
Map interface — Leaflet + plain HTML/CSS/JavaScript
    → FastAPI API + local SQLite journey sessions
        → SQLite place and street indexes — OpenStreetMap + MTA
        → bounded local agent tool loop — Ollama / Qwen2.5
        → OpenTripPlanner — scheduled subway and walking routes
        → grounded instructions and map geometry
        → public MTA feeds — separate arrivals and service notices
```

The agent chooses from state-validated tools and observes their results before deciding the next action. Places, train facts, and geometry come from the indexed data and routing tools. Map selections provide explicit locations; the app does not rely on the model to invent coordinates or directions.

The default model is `qwen2.5:1.5b`. Journey ranking favors a short boarding walk among viable subway options returned by the router; it does not guarantee the globally nearest usable station. Any nearest-station distance shown is straight-line distance, not a walking route.

### Local service settings

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `OLLAMA_URL` | `http://127.0.0.1:11435` | Local model service address. |
| `OLLAMA_MODEL` | `qwen2.5:1.5b` | Model used by the launcher and app. |
| `OTP_URL` | `http://127.0.0.1:8080/otp/gtfs/v1` | Routing API address. |
| `OTP_HEAP` | `-Xmx3G` | Java heap setting for the local router. |

Check the license and hardware requirements before substituting another model.

## Data and privacy

Downloaded data, models, runtime binaries, logs, and saved SQLite sessions stay in ignored local directories. They are not included in the source repository. Setup scripts recreate the required downloads and indexes.

Inference runs locally by default. Online map tiles contact OpenStreetMap and reveal the viewed map area to the tile provider. Public MTA feeds require internet; local street-outline mode avoids external basemap requests. Saved sessions older than seven days are removed on app startup.

### Refresh transit data

Stop the local router before running:

```sh
python3 scripts/run.py --refresh
```

This refreshes the MTA schedule and entrance snapshot and rebuilds the transit graph. Normal startup also refreshes an old schedule when no existing router is running. Dates outside the downloaded feed's validity period are rejected.

For an OSM refresh, stop the project and move these cached files outside the project before restarting: `data/NewYork.osm.pbf`, `data/places.sqlite`, `data/streets.sqlite`, `data/relations-indexed`, and `data/graph.obj`. Startup recreates them from the downloaded extract.

## Development and verification

From the project folder:

```sh
.venv/bin/python -m unittest discover -s tests
node --test tests/*.cjs
```

On Windows, use `.venv\Scripts\python.exe` for the Python checks.

With the local app, router, and model running:

```sh
.venv/bin/python scripts/map_qa.py
```

This checks the real map-planning API workflow in separate test sessions. `scripts/smoke_test.py` additionally exercises the internal conversational API retained by the backend; it does not represent a public chat interface.

See [verification](docs/verification.md) for the manual walkthrough and test scope. Generated result files are ignored by Git. Tests cover current map behavior and the shared backend, including internal session isolation.

## Project layout

| Path | Contents |
| --- | --- |
| `subway/` | API, agent tools, location selection, search, routing, walking guidance, and live feeds. |
| `static/` | Map interface and locally bundled Leaflet assets and license. |
| `scripts/` | Startup, downloads, indexing, data preparation, and integration checks. |
| `tests/` | Python regression tests and JavaScript renderer/map/form checks. |
| `docs/` | Product verification guide. |
| `data/*-config.json` | Small routing configuration files included in the repository. |
| `data/`, `runtime/`, `.venv/` | Ignored local data, sessions, models, binaries, logs, and Python environment. |

## Limitations

- This portfolio prototype is not an official MTA navigation service.
- Itineraries use the downloaded schedule. Live arrivals are separate and are not matched to the exact scheduled train. Notices do not automatically reroute journeys.
- OpenStreetMap business listings may be incomplete or outdated. Search is not a live business directory.
- Entrance inventory is historical. Platform access, indoor passages, closures, elevators, and accessibility are not verified; confirm posted station signs.
- Progress is user-confirmed. There is no GPS tracking, automatic missed-stop detection, voice input, or proactive monitoring.
- Local models can make poor tool choices; software checks do not establish that a trip has been physically walked or ridden.
- Cloud deployment, authentication, and sustained multi-user operation have not been validated.

## License and sources

Application code is [MIT licensed](LICENSE). Bundled Leaflet retains its [BSD license](static/vendor/leaflet/LICENSE). Data, model, and runtime terms apply separately:

- [MTA developer data and terms](https://www.mta.info/developers), [supplemented GTFS schedule](https://rrgtfsfeeds.s3.amazonaws.com/gtfs_supplemented.zip), and [entrance inventory](https://data.ny.gov/Transportation/MTA-Subway-Entrances-and-Exits-2024/i9wp-a4ja).
- © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright), ODbL; NYC extract from [BBBike](https://download.bbbike.org/osm/bbbike/NewYork/). Online tiles follow the [OpenStreetMap tile usage policy](https://operations.osmfoundation.org/policies/tiles/).
- [OpenTripPlanner](https://github.com/opentripplanner/OpenTripPlanner), LGPL-3.0.
- [Ollama](https://github.com/ollama/ollama), MIT.
- [Qwen2.5-1.5B-Instruct](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct), Apache-2.0.

No MTA branding or logos are included.
