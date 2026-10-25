"""Fresh, direction-specific arrival predictions, separate from scheduled routing."""

import asyncio, math, time, re
from datetime import datetime
import httpx
from google.transit import gtfs_realtime_pb2 as pb

BASE = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/"
GROUPS = [
    ("1234567S", "gtfs"),
    ("ACE", "gtfs-ace"),
    ("BDFM", "gtfs-bdfm"),
    ("G", "gtfs-g"),
    ("JZ", "gtfs-jz"),
    ("NQRW", "gtfs-nqrw"),
    ("L", "gtfs-l"),
    ("SI", "gtfs-si"),
]
CACHE = {}
LOCK = asyncio.Lock()


async def fetch(path):
    async with LOCK:
        old = CACHE.get(path)
        if old and time.time() - old[0] < 30:
            return old[1]
        try:
            async with httpx.AsyncClient(
                timeout=8,
                headers={"User-Agent": "SubwayAssistant/0.1 personal prototype"},
            ) as c:
                r = await c.get(BASE + path)
                r.raise_for_status()
                feed = pb.FeedMessage()
                feed.ParseFromString(r.content)
            if not feed.header.timestamp:
                raise ValueError("Missing source timestamp")
            CACHE[path] = (time.time(), feed)
            return feed
        except Exception:
            return None


async def departures(leg):
    route = leg["route_id"]
    group = next((p for lines, p in GROUPS if route in lines), None)
    if not group:
        return {"status": "unavailable", "arrivals": []}
    feed = await fetch("nyct%2F" + group)
    if feed is None:
        return {"status": "unavailable", "arrivals": []}
    age = time.time() - feed.header.timestamp
    if age > 180 or age < -60:
        return {"status": "stale", "age_seconds": round(age), "arrivals": []}
    rows = []
    now = time.time()
    for e in feed.entity:
        if not e.HasField("trip_update"):
            continue
        t = e.trip_update
        if t.trip.route_id != route or t.trip.schedule_relationship in (
            pb.TripDescriptor.CANCELED,
            pb.TripDescriptor.DELETED,
        ):
            continue
        updates = list(t.stop_time_update)
        for j, s in enumerate(updates):
            if s.stop_id != leg["stop_id"] or s.schedule_relationship in (
                pb.TripUpdate.StopTimeUpdate.SKIPPED,
                pb.TripUpdate.StopTimeUpdate.NO_DATA,
            ):
                continue
            event = (
                s.departure
                if s.HasField("departure") and s.departure.time
                else s.arrival
            )
            if not event.time or not now < event.time < now + 7200:
                continue
            # A route and direction match is not an exact scheduled-trip/stopping-pattern match.
            rows.append(
                {
                    "minutes": max(1, math.ceil((event.time - now) / 60)),
                    "at": event.time,
                    "trip_id": t.trip.trip_id,
                }
            )
    rows.sort(key=lambda x: x["at"])
    return {
        "status": "live",
        "age_seconds": round(age),
        "source_time": datetime.fromtimestamp(feed.header.timestamp)
        .astimezone()
        .isoformat(),
        "arrivals": rows[:4],
        "note": "Predictions for this line and directional stop. These are not matched to your scheduled trip; confirm its destination and stopping pattern.",
    }


def text(translated):
    vals = translated.translation
    return next(
        (v.text for v in vals if v.language in ("en", "en-US")),
        vals[0].text if vals else "",
    )


async def alerts(routes):
    feed = await fetch("camsys%2Fsubway-alerts")
    if feed is None:
        return {"status": "unavailable", "items": []}
    age = time.time() - feed.header.timestamp
    if age > 900 or age < -60:
        return {"status": "stale", "items": []}
    now = time.time()
    items = []
    for e in feed.entity:
        if not e.HasField("alert"):
            continue
        a = e.alert
        if a.active_period and not any(
            (not p.start or p.start <= now) and (not p.end or p.end >= now)
            for p in a.active_period
        ):
            continue
        affected = {x.route_id for x in a.informed_entity if x.route_id}
        if affected and not affected.intersection(routes):
            continue
        if not affected:
            continue  # agency-wide messages lack reliable route specificity here
        clean = lambda s: re.sub("<[^>]+>", "", s).strip()
        items.append(
            {
                "header": clean(text(a.header_text)),
                "description": clean(text(a.description_text)),
                "routes": sorted(affected.intersection(routes)),
            }
        )
    return {"status": "live", "age_seconds": round(age), "items": items[:8]}


async def for_journey(journey, step_index=0):
    steps = journey.get("steps", [])
    current_leg = steps[min(step_index, len(steps)-1)].get("leg", 0) if steps else 0
    leg = next((l for i, l in enumerate(journey["legs"]) if l["transit"] and i >= current_leg), None)
    if not leg:
        return {
            "checked_at": time.time(),
            "departures": None,
            "alerts": {"status": "not_applicable", "items": []},
        }
    dep, al = await asyncio.gather(
        departures(leg),
        alerts({l["route_id"] for l in journey["legs"] if l["transit"]}),
    )
    return {"checked_at": time.time(), "departures": dep, "alerts": al,
            "boarding": {"stop_id": leg["stop_id"], "route_id": leg["route_id"], "from": leg["from"]}}
