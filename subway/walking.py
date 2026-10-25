"""Name otherwise unnamed sidewalks using nearby parallel named OSM streets.
Never infer an interior station passage, entrance or crossing from street proximity.
"""

import math, sqlite3
from pathlib import Path
from . import entrances

DB = Path(__file__).resolve().parents[1] / "data/streets.sqlite"


def decode(s):
    pts = []
    lat = lon = i = 0
    while i < len(s):
        values = []
        for _ in range(2):
            v = shift = 0
            while True:
                b = ord(s[i]) - 63
                i += 1
                v |= (b & 31) << shift
                shift += 5
                if b < 32:
                    break
            values.append(~(v >> 1) if v & 1 else v >> 1)
        lat += values[0]
        lon += values[1]
        pts.append((lat / 1e5, lon / 1e5))
    return pts


def bearing(a, b):
    return (
        math.degrees(
            math.atan2((b[1] - a[1]) * math.cos(math.radians(a[0])), b[0] - a[0])
        )
        % 360
    )


def length(a, b):
    return math.hypot(
        (a[0] - b[0]) * 111195, (a[1] - b[1]) * 111195 * math.cos(math.radians(a[0]))
    )


def nearby(db, a, b):
    lat = (a[0] + b[0]) / 2
    lon = (a[1] + b[1]) / 2
    h = bearing(a, b)
    co = math.cos(math.radians(lat))
    best = None
    rows = db.execute(
        "SELECT s.* FROM bounds r JOIN segments s ON r.id=s.id WHERE minlat<? AND maxlat>? AND minlon<? AND maxlon>?",
        (lat + 0.00022, lat - 0.00022, lon + 0.00029, lon - 0.00029),
    )
    for _, name, al, ao, bl, bo in rows:
        angle = abs((h - bearing((al, ao), (bl, bo)) + 90) % 180 - 90)
        if angle > 28:
            continue
        x1 = (ao - lon) * 111195 * co
        y1 = (al - lat) * 111195
        x2 = (bo - lon) * 111195 * co
        y2 = (bl - lat) * 111195
        dx = x2 - x1
        dy = y2 - y1
        den = dx * dx + dy * dy
        t = max(0, min(1, -(x1 * dx + y1 * dy) / den)) if den else 0
        dist = math.hypot(x1 + t * dx, y1 + t * dy)
        if dist > 22:
            continue
        score = dist + angle * 0.4
        if not best or score < best[0]:
            best = (score, name)
    return best[1] if best else None


def directions(leg):
    if not DB.exists() or not leg.get("legGeometry"):
        return None
    pts = decode(leg["legGeometry"]["points"])
    segments = []
    entrance = None
    exit_name = None
    target = leg.get("target_entrance")
    if target:
        if target["entering"]:
            entrance = target["name"]
        else:
            exit_name = target["name"]
    access = None if target else entrances.along_path(leg, pts)
    if access:
        leg["verified_entrance"] = access
        if access["entering"]:
            pts = pts[: access["index"] + 1]
            entrance = access["name"]
        else:
            pts = pts[access["index"] :]
            exit_name = access["name"]
    # An entrance feature is explicit routing data. Stop exterior instructions there.
    for step in leg.get("steps") or []:
        feature = step.get("feature")
        if feature:
            entrance = feature.get("name") or "the station entrance"
            target = (step["lat"], step["lon"])
            cut = min(range(len(pts)), key=lambda i: length(pts[i], target))
            pts = pts[: cut + 1]
            break
    with sqlite3.connect(DB) as db:
        for a, b in zip(pts, pts[1:]):
            dist = length(a, b)
            if dist < 1:
                continue
            name = nearby(db, a, b)
            heading = bearing(a, b)
            if (
                segments
                and name == segments[-1]["name"]
                and abs((heading - segments[-1]["heading"] + 180) % 360 - 180) < 65
            ):
                segments[-1]["distance"] += dist
            else:
                segments.append({"name": name, "heading": heading, "distance": dist})
    # Remove small geometry detours around a crossing while retaining the same street.
    i = 0
    while i + 2 < len(segments):
        a, b, c = segments[i : i + 3]
        if (
            a["name"]
            and a["name"] == c["name"]
            and b["distance"] < 25
            and abs((a["heading"] - c["heading"] + 180) % 360 - 180) < 40
        ):
            a["distance"] += b["distance"] + c["distance"]
            segments[i : i + 3] = [a]
        else:
            i += 1
    out = []
    first_named = next((i for i, s in enumerate(segments) if s["name"]), 0)
    if first_named > 1:
        initial = sum(s["distance"] for s in segments[:first_named])
        target = segments[first_named]["name"]
        out.append(
            {
                "text": f"Follow the footpaths to {target}, about {max(10,round(initial/10)*10)} m. A nearby street corner gives a more precise starting point.",
                "distance": round(initial),
                "street": target,
            }
        )
        segments = segments[first_named:]
    if exit_name:
        out.append(
            {
                "text": f"Look for the {exit_name}. Confirm signs to that exit before leaving the station; the interior passage is not verified.",
                "distance": 0,
                "street": exit_name,
            }
        )
    for s in segments:
        if s["distance"] < 5:
            continue
        compass = [
            "north",
            "northeast",
            "east",
            "southeast",
            "south",
            "southwest",
            "west",
            "northwest",
        ][round(s["heading"] / 45) % 8]
        d = max(10, round(s["distance"] / 10) * 10)
        name = s["name"]
        phrase = f"the walkway alongside {name}" if name else "the footpath"
        out.append(
            {
                "text": f"Head {compass} along {phrase} for about {d:,} m.",
                "distance": round(s["distance"]),
                "street": name or "footpath",
                "inferred_sidewalk_name": bool(name),
            }
        )
    if entrance:
        out.append(
            {
                "text": f"Look for the MTA-listed {entrance}. Inside, confirm the signs for your train and its destination; this prototype does not verify the interior platform passage.",
                "distance": 0,
                "street": entrance,
            }
        )
    return out or None
