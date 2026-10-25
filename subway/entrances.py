"""Find official entrances alongside an already calculated walking path.
Only append nearby inventory information; never infer platform access.
"""

import json, math, sqlite3
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def inventory():
    p = ROOT / "data/mta-entrances.json"
    out = {}
    if not p.exists():
        return out
    for e in json.loads(p.read_text()):
        if e.get("entry_allowed") == "YES" and e.get("exit_allowed") == "YES":
            out.setdefault(e["gtfs_stop_id"], []).append(
                {
                    "lat": float(e["entrance_latitude"]),
                    "lon": float(e["entrance_longitude"]),
                    "type": e.get("entrance_type", "entrance").lower(),
                    "station": e["stop_name"],
                }
            )
    return out


def distance(a, b):
    return math.hypot((a[0] - b[0]) * 111195, (a[1] - b[1]) * 84200)


def label(e):
    with sqlite3.connect(ROOT / "data/places.sqlite") as db:
        rows = db.execute(
            "SELECT name,lat,lon FROM places WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? AND kind='intersection'",
            (
                e["lat"] - 0.0007,
                e["lat"] + 0.0007,
                e["lon"] - 0.0009,
                e["lon"] + 0.0009,
            ),
        ).fetchall()
    if rows:
        nearest = min(rows, key=lambda r: distance((e["lat"], e["lon"]), (r[1], r[2])))
        if distance((e["lat"], e["lon"]), (nearest[1], nearest[2])) < 90:
            return f"{e['type']} entrance near {nearest[0]}"
    return e["type"] + " entrance at " + e["station"]


def along_path(leg, points):
    if len(points) < 2:
        return None
    entering = bool(leg["to"].get("stop"))
    leaving = bool(leg["from"].get("stop"))
    if entering == leaving:
        return None  # don't guess an interior transfer route
    stop = (leg["to"] if entering else leg["from"])["stop"]["gtfsId"].split(":")[-1]
    options = inventory().get(stop, []) or inventory().get(
        stop[:-1] if stop.endswith(("N", "S")) else stop, []
    )
    best = None
    for e in options:
        i = min(
            range(len(points)), key=lambda j: distance(points[j], (e["lat"], e["lon"]))
        )
        d = distance(points[i], (e["lat"], e["lon"]))
        # Only an entrance the actual walking geometry comes close to, near the station end.
        if (
            d > 22
            or (entering and i < len(points) * 0.55)
            or (leaving and i > len(points) * 0.45)
        ):
            continue
        if best is None or d < best[0]:
            best = (d, i, e)
    if not best:
        return None
    _, i, e = best
    return {
        "index": i,
        "entering": entering,
        "name": label(e),
        "lat": e["lat"],
        "lon": e["lon"],
        "station": e["station"],
    }


@lru_cache(maxsize=1)
def boroughs():
    p = ROOT / "data/mta-entrances.json"
    return (
        {e["gtfs_stop_id"]: e.get("borough") for e in json.loads(p.read_text())}
        if p.exists()
        else {}
    )


def parent(code):
    code = code.split(":")[-1]
    return code[:-1] if code.endswith(("N", "S")) else code


def direction(leg):
    start = leg["from"]
    stops = leg.get("intermediateStops") or []
    next_stop = stops[0] if stops else leg["to"]
    origin_code = (start.get("stop") or {}).get("gtfsId") or ""
    trip_stops = (leg.get("trip") or {}).get("stops") or []
    terminal = trip_stops[-1] if trip_stops else None
    if boroughs().get(parent(origin_code)) != "M":
        return ""
    parts = []
    if "lat" in next_stop and "lon" in next_stop:
        north = next_stop["lat"] - start["lat"]
        east = (next_stop["lon"] - start["lon"]) * 0.76
        if abs(north) > abs(east) * 0.75:
            parts.append("Uptown" if north > 0 else "Downtown")
    if terminal:
        b = boroughs().get(parent(terminal["gtfsId"]))
        name = {
            "B": "Brooklyn",
            "Q": "Queens",
            "Bx": "Bronx",
            "BX": "Bronx",
            "S": "Staten Island",
        }.get(b)
        if name:
            parts.append(name + "-bound")
    return " · ".join(parts)
