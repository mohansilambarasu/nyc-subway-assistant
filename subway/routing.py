"""OpenTripPlanner adapter. All journey instructions are deterministic."""

import math, os, csv, io, zipfile
from pathlib import Path
from functools import lru_cache
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import httpx
from . import walking, places, entrances

URL = os.environ.get("OTP_URL", "http://127.0.0.1:8080/otp/gtfs/v1")
TZ = ZoneInfo("America/New_York")
QUERY = """query Journey($from: InputCoordinates!, $to: InputCoordinates!, $date: String!, $time: String!) {
 plan(from:$from,to:$to,date:$date,time:$time,numItineraries:3,transportModes:[{mode:WALK},{mode:SUBWAY}],walkSpeed:1.25,searchWindow:3600) {
  routingErrors { code description } itineraries {
   duration startTime endTime walkDistance numberOfTransfers generalizedCost
   legs { mode duration distance startTime endTime realTime headsign transitLeg
    route { gtfsId shortName longName color } trip { gtfsId tripHeadsign stops { gtfsId name lat lon } }
    from { name lat lon stop { gtfsId name } } to { name lat lon stop { gtfsId name } }
    intermediateStops { gtfsId name lat lon }
    legGeometry { points }
    steps { streetName distance absoluteDirection relativeDirection bogusName lat lon feature { ... on Entrance { name entranceId } } }
   }
  }
 }
}"""


async def health():
    try:
        async with httpx.AsyncClient(timeout=2) as c:
            r = await c.post(URL, json={"query": "{__typename}"})
            return r.status_code == 200 and not r.json().get("errors")
    except (httpx.HTTPError, ValueError):
        return False


class RoutingError(Exception):
    pass


def clock(ms):
    return datetime.fromtimestamp(ms / 1000, TZ).strftime("%-I:%M %p")


def meters(n):
    return f"{max(10,round(n/10)*10):,} m" if n < 1000 else f"{n/1000:.1f} km"


def walking_steps(leg):
    matched = walking.directions(leg)
    if matched:
        return matched
    out = []
    for s in leg.get("steps") or []:
        if s["distance"] < 2:
            continue
        name = s.get("streetName") or "the footpath"
        if s.get("bogusName") and name.lower() not in ["steps", "stairs"]:
            name = "the footpath"
        rel = s.get("relativeDirection") or "CONTINUE"
        absolute = (s.get("absoluteDirection") or "").lower()
        directions = {
            "LEFT": "Turn left onto",
            "RIGHT": "Turn right onto",
            "SLIGHTLY_LEFT": "Bear left onto",
            "SLIGHTLY_RIGHT": "Bear right onto",
            "HARD_LEFT": "Turn sharply left onto",
            "HARD_RIGHT": "Turn sharply right onto",
            "CONTINUE": "Continue on",
            "DEPART": f"Head {absolute} along",
            "ELEVATOR": "Take the elevator at",
            "UTURN_LEFT": "Turn back onto",
            "UTURN_RIGHT": "Turn back onto",
        }
        out.append(
            {
                "text": f"{directions.get(rel,'Continue on')} {name} for {meters(s['distance'])}.",
                "distance": round(s["distance"]),
                "street": name,
            }
        )
    return out


def normalize(it, origin, destination):
    legs = []
    steps = []
    for i, leg in enumerate(it["legs"]):
        transit = bool(leg["transitLeg"])
        transfer = not transit and bool(leg["from"].get("stop")) and bool(leg["to"].get("stop"))
        line = (leg.get("route") or {}).get("shortName", "")
        l = {
            "geometry": walking.decode((leg.get("legGeometry") or {}).get("points", "")),
            "mode": leg["mode"],
            "duration": math.ceil(leg["duration"] / 60),
            "distance": round(leg["distance"]),
            "from": leg["from"]["name"],
            "to": leg["to"]["name"],
            "start": clock(leg["startTime"]),
            "end": clock(leg["endTime"]),
            "start_ms": leg["startTime"],
            "end_ms": leg["endTime"],
            "transit": transit,
            "line": line,
            "color": (leg.get("route") or {}).get("color") or "444444",
            "headsign": leg.get("headsign")
            or (leg.get("trip") or {}).get("tripHeadsign")
            or "",
            "stop_id": ((leg["from"].get("stop") or {}).get("gtfsId") or "").split(":")[
                -1
            ],
            "route_id": ((leg.get("route") or {}).get("gtfsId") or "").split(":")[-1],
            "trip_id": (leg.get("trip") or {}).get("gtfsId"),
            "boarding": {"lat": leg["from"]["lat"], "lon": leg["from"]["lon"]},
            "alighting": {"lat": leg["to"]["lat"], "lon": leg["to"]["lon"]},
            "stops": leg.get("intermediateStops") or [],
            "walking": (
                walking_steps(leg)
                if not transit and not transfer and not leg.get("_inside_station")
                else []
            ),
            "entrance": leg.get("target_entrance"),
            "exterior_walk_minutes": leg.get("exterior_walk_minutes"),
        }
        if transit:
            l["direction"] = entrances.direction(leg)
            next_stop = l["stops"][0]["name"] if l["stops"] else l["to"]
            l["next_stop"] = next_stop
            sign = l["headsign"] or "the destination shown in the schedule"
            steps.append(
                {
                    "kind": "board",
                    "title": f"Take the {line} toward {sign}",
                    "text": f"At {l['from']}, look for the {line} train showing {sign}. "
                    + (f"Direction: {l['direction']}. " if l["direction"] else "")
                    + f"Your first stop should be {next_stop}. Confirm that destination before boarding.",
                    "line": line,
                    "color": l["color"],
                    "leg": i,
                    "meta": f"Scheduled {l['start']} · {len(l['stops'])+1} stops",
                }
            )
            steps.append(
                {
                    "kind": "ride",
                    "title": f"Get off at {l['to']}",
                    "text": f"Ride {len(l['stops'])+1} stops to {l['to']}. "
                    + (
                        f"The stop before yours is {l['stops'][-1]['name']}."
                        if l["stops"]
                        else "Your destination is the next stop."
                    ),
                    "line": line,
                    "color": l["color"],
                    "leg": i,
                    "meta": f"Scheduled arrival {l['end']}",
                }
            )
        else:
            target = destination["name"] if i == len(it["legs"]) - 1 else l["to"]
            title = f"Walk to {target}"
            steps.append(
                {
                    "kind": "walk",
                    "title": title,
                    "text": (
                        (f"About {l['exterior_walk_minutes']} min on foot, {meters(l['distance'])}, plus 3 min to exit the station. Confirm the street signs."
                         if i == len(it["legs"])-1 else
                         f"About {l['exterior_walk_minutes']} min on foot, {meters(l['distance'])}, plus 3 min for station access. Confirm the platform signs.")
                        if l["entrance"]
                        else f"About {l['duration']} min, {meters(l['distance'])}. Follow the street instructions below."
                    ),
                    "walking": l["walking"],
                    "leg": i,
                    "meta": f"{l['duration']} min · {meters(l['distance'])}",
                }
            )
            if leg.get("_inside_station") or transfer:
                next_train = next(
                    (x for x in it["legs"][i + 1 :] if x["transitLeg"]), None
                )
                sign = (
                    (next_train or {}).get("headsign")
                    or ((next_train or {}).get("trip") or {}).get("tripHeadsign")
                    or "the indicated destination"
                )
                line = ((next_train or {}).get("route") or {}).get("shortName", "")
                steps[-1].update(
                    title=f"Find the {line} platform at {l['to']}",
                    text=f"Follow the transfer signs for the {line} toward {sign} at {l['to']}. Allow about {l['duration']} minutes. Confirm the posted platform direction; the exact passage and whether it stays inside the paid area are not verified.",
                    walking=[],
                )
        legs.append(l)
    steps.append(
        {
            "kind": "arrive",
            "title": f"You’re at {destination['name']}",
            "text": "You made it. Start another trip whenever you’re ready.",
            "meta": "Destination",
            "leg": len(legs) - 1,
        }
    )
    nearest = (
        min(places.stations(), key=lambda x: places.distance(origin, x), default=None)
        if "lat" in origin
        else None
    )
    nearest_info = (
        {"name": nearest["name"], "distance_m": round(places.distance(origin, nearest))}
        if nearest
        else None
    )
    return {
        "nearest_station": nearest_info,
        "planned_at": datetime.now(TZ).isoformat(),
        "duration": math.ceil(it["duration"] / 60),
        "start": clock(it["startTime"]),
        "end": clock(it["endTime"]),
        "walking_m": round(it["walkDistance"]),
        "transfers": it["numberOfTransfers"],
        "legs": legs,
        "steps": steps,
        "origin": origin,
        "destination": destination,
        "scheduled": True,
    }


@lru_cache(maxsize=2)
def _feed_info(mtime):
    with zipfile.ZipFile(
        Path(__file__).resolve().parents[1] / "data/subway.gtfs.zip"
    ) as z:
        return next(
            csv.DictReader(io.TextIOWrapper(z.open("feed_info.txt"), "utf-8-sig"))
        )


def feed_info():
    p = Path(__file__).resolve().parents[1] / "data/subway.gtfs.zip"
    return _feed_info(p.stat().st_mtime) if p.exists() else {}


async def plan(origin, destination, when=None):
    when = when or datetime.now(TZ)
    if when.tzinfo is None:
        when = when.replace(tzinfo=TZ)
    when = when.astimezone(TZ)
    info = feed_info()
    day = when.strftime("%Y%m%d")
    if info.get("feed_start_date") and not info["feed_start_date"] <= day <= info.get(
        "feed_end_date", "99999999"
    ):
        raise RoutingError(
            "The downloaded MTA schedule does not cover this date. Stop the project and run python3 scripts/run.py --refresh to download current service."
        )
    variables = {
        "from": {k: origin[k] for k in ["lat", "lon"]} | {"address": origin["name"]},
        "to": {k: destination[k] for k in ["lat", "lon"]}
        | {"address": destination["name"]},
        "date": when.strftime("%Y-%m-%d"),
        "time": when.strftime("%H:%M:%S"),
    }
    try:
        async with httpx.AsyncClient(timeout=90) as c:
            r = await c.post(URL, json={"query": QUERY, "variables": variables})
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as e:
        raise RoutingError(
            "The local route service is still starting or unavailable. Please try planning again shortly."
        ) from e
    if data.get("errors"):
        raise RoutingError("Router query failed: " + data["errors"][0]["message"])
    p = data["data"]["plan"]
    items = p["itineraries"]
    if not items:
        reason = "; ".join(
            e.get("description") or e.get("code", "")
            for e in p.get("routingErrors", [])
        )
        raise RoutingError(
            "No journey found for these locations and this date. " + reason
        )
    # GraphQL returns chronological options, not necessarily the most practical first.
    items.sort(
        key=lambda x: x.get("generalizedCost")
        or (
            x["endTime"] / 1000
            + x["numberOfTransfers"] * 120
            + x["walkDistance"] * 0.08
        )
    )

    def mark_inside(item):
        if not origin.get("inside_station") or not item["legs"]:
            return
        first = item["legs"][0]
        stop = (first.get("to", {}).get("stop") or {}).get("gtfsId") or ""
        if (
            not first["transitLeg"]
            and entrances.parent(stop) == origin.get("id", "")[1:]
        ):
            first["_inside_station"] = True

    async with httpx.AsyncClient(timeout=15) as c:
        for item in items:
            mark_inside(item)
            await entrance_walks(item, c, when.timestamp() * 1000)
        usable = [x for x in items if not x.get("_entry_unreachable")]
        if not usable:
            # Find a later departure while allowing the user to begin walking now.
            later = when + timedelta(minutes=10)
            variables["date"] = later.strftime("%Y-%m-%d")
            variables["time"] = later.strftime("%H:%M:%S")
            try:
                r = await c.post(URL, json={"query": QUERY, "variables": variables})
                r.raise_for_status()
                later_data = r.json()
                later_items = (
                    (later_data.get("data") or {})
                    .get("plan", {})
                    .get("itineraries", [])
                )
                later_items.sort(key=lambda x: x.get("generalizedCost", 0))
                for item in later_items:
                    mark_inside(item)
                    await entrance_walks(item, c, when.timestamp() * 1000)
                usable = [x for x in later_items if not x.get("_entry_unreachable")]
            except (httpx.HTTPError, ValueError):
                pass
        if not usable:
            raise RoutingError(
                "I couldn’t verify enough time to reach a listed entrance for these departures. Try a more precise starting intersection."
            )
    return [normalize(x, origin, destination) for x in usable]


async def entrance_walks(it, client, requested_ms):
    """Replace exterior walks with paths to real inventory entrances when time fits.
    Keep the planned train times and reserve 3 minutes for unverified station access.
    """
    from .entrances import inventory, label

    for leg in it["legs"]:
        if leg["transitLeg"] or leg.get("_inside_station"):
            continue
        entering = bool(leg["to"].get("stop"))
        leaving = bool(leg["from"].get("stop"))
        if entering == leaving:
            continue
        station = leg["to"] if entering else leg["from"]
        street = leg["from"] if entering else leg["to"]
        stop = station["stop"]["gtfsId"].split(":")[-1]
        entries = inventory().get(stop, []) or inventory().get(
            stop[:-1] if stop.endswith(("N", "S")) else stop, []
        )
        if not entries:
            continue
        entries = sorted(entries, key=lambda e: places.distance(street, e))[:2]
        best = None
        for e in entries:
            a, b = (street, e) if entering else (e, street)
            when = datetime.fromtimestamp(leg["startTime"] / 1000, TZ)
            variables = {
                "from": {"lat": a["lat"], "lon": a["lon"]},
                "to": {"lat": b["lat"], "lon": b["lon"]},
                "date": when.strftime("%Y-%m-%d"),
                "time": when.strftime("%H:%M:%S"),
            }
            q = QUERY.replace(
                "transportModes:[{mode:WALK},{mode:SUBWAY}]",
                "transportModes:[{mode:WALK}]",
            ).replace("numItineraries:3", "numItineraries:1")
            try:
                response = await client.post(
                    URL, json={"query": q, "variables": variables}
                )
                response.raise_for_status()
                data = response.json()
                if data.get("errors"):
                    continue
                paths = data["data"]["plan"]["itineraries"]
                if not paths:
                    continue
                p = paths[0]["legs"][0]
                if p["transitLeg"]:
                    continue
                needed = p["duration"] + 180
                if entering and (
                    leg is not it["legs"][0]
                    or leg["endTime"] - needed * 1000 < requested_ms
                ):
                    continue
                if not best or p["distance"] < best[0]["distance"]:
                    best = (p, e)
            except (httpx.HTTPError, ValueError, KeyError):
                continue
        if best:
            path, e = best
            leg["steps"] = path["steps"]
            leg["legGeometry"] = path["legGeometry"]
            leg["distance"] = path["distance"]
            leg["duration"] = path["duration"] + 180
            if entering:
                leg["startTime"] = leg["endTime"] - leg["duration"] * 1000
            else:
                leg["endTime"] = leg["startTime"] + leg["duration"] * 1000
            leg["target_entrance"] = {"name": label(e), "entering": entering}
            leg["exterior_walk_minutes"] = math.ceil(path["duration"] / 60)
        elif entering and leg is it["legs"][0]:
            it["_entry_unreachable"] = True
    it["startTime"] = it["legs"][0]["startTime"]
    it["endTime"] = it["legs"][-1]["endTime"]
    it["duration"] = (it["endTime"] - it["startTime"]) / 1000
    it["walkDistance"] = sum(l["distance"] for l in it["legs"] if not l["transitLeg"])
