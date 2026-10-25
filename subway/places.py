import math, re, sqlite3, json
from functools import lru_cache
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data/places.sqlite"


def norm(s):
    s = s.lower().replace("&", " and ")
    for a, b in [
        ("avenue", "ave"),
        ("street", "st"),
        ("west", "w"),
        ("east", "e"),
        ("saint", "st"),
    ]:
        s = re.sub(r"\b" + a + r"\b", b, s)
    return re.sub(r"[^a-z0-9 ]", " ", s).strip()


def distance(a, b):
    lat1, lat2 = math.radians(a["lat"]), math.radians(b["lat"])
    dl = math.radians(b["lon"] - a["lon"])
    dp = lat2 - lat1
    return (
        6371000
        * 2
        * math.asin(
            min(
                1,
                math.sqrt(
                    math.sin(dp / 2) ** 2
                    + math.cos(lat1) * math.cos(lat2) * math.sin(dl / 2) ** 2
                ),
            )
        )
    )


def get(ident):
    with sqlite3.connect(DB) as db:
        db.row_factory = sqlite3.Row
        r = db.execute("SELECT * FROM places WHERE id=?", (ident,)).fetchone()
        return dict(r) if r else None


def search(query, near=None, limit=6):
    q = norm(query)
    aliases = {
        "raging bull": "charging bull",
        "the bull": "charging bull",
        "bull on wall street": "charging bull",
        "wall street bull": "charging bull",
        "nypl": "stephen a schwarzman building",
    }
    q = aliases.get(q, q)
    if "raging bull" in q or "charging bull" in q:
        q = "charging bull"
    words = [
        w
        for w in q.split()
        if w
        not in {
            "the",
            "at",
            "in",
            "to",
            "next",
            "near",
            "and",
            "nyc",
            "new",
            "york",
            "city",
            "shop",
            "station",
            "subway",
            "of",
            "a",
        }
    ]
    if not words:
        return []
    expr = " AND ".join('"' + w + '"*' for w in words)
    with sqlite3.connect(DB) as db:
        db.row_factory = sqlite3.Row
        rows = [
            dict(r)
            for r in db.execute(
                "SELECT p.* FROM place_fts f JOIN places p ON p.rowid=f.rowid WHERE place_fts MATCH ? ORDER BY bm25(place_fts) LIMIT 120",
                (expr,),
            )
        ]
        if not rows:
            expr = expr.replace(" AND ", " OR ")
            rows = [
                dict(r)
                for r in db.execute(
                    "SELECT p.* FROM place_fts f JOIN places p ON p.rowid=f.rowid WHERE place_fts MATCH ? ORDER BY bm25(place_fts) LIMIT 120",
                    (expr,),
                )
            ]

    def score(r):
        n = norm(r["name"])
        tokens = set(n.split())
        matched = sum(
            w in tokens or any(x.startswith(w) for x in tokens) for w in words
        ) / len(words)
        val = matched * 70 + (80 if n == q else 0) + (35 if q in n else 0)
        if r["kind"] == "station":
            val += 5
        if r["kind"] in ["attraction", "museum", "park", "cafe", "restaurant"]:
            val += 3
        if r["kind"] == "artwork" and n not in ["charging bull"]:
            val -= 8
        if near:
            val -= min(40, distance(near, r) / 1000 * 3)
        return val

    rows.sort(key=score, reverse=True)
    out = []
    for r in rows:
        coverage = sum(
            any(t.startswith(w) for t in norm(r["name"]).split()) for w in words
        ) / len(words)
        if coverage < 0.75 and q not in norm(r.get("search", "")):
            continue
        if score(r) < max(40, score(rows[0]) - 45):
            continue
        if any(
            norm(x["name"]) == norm(r["name"]) and distance(x, r) < 120 for x in out
        ):
            continue
        r["score"] = round(score(r))
        r.pop("search", None)
        r["context"] = context(r)
        out.append(r)
        if len(out) == limit:
            break
    return out


@lru_cache(maxsize=1)
def stations():
    with sqlite3.connect(DB) as db:
        db.row_factory = sqlite3.Row
        return [
            dict(r) for r in db.execute("SELECT * FROM places WHERE kind='station'")
        ]


@lru_cache(maxsize=1)
def station_lines():
    p = DB.parent / "mta-entrances.json"
    if not p.exists():
        return {}
    return {
        e.get("gtfs_stop_id"): e.get("daytime_routes", "")
        for e in json.loads(p.read_text())
    }


def context(place):
    if place["kind"] == "station":
        lines = station_lines().get(place["id"][1:], "")
        with sqlite3.connect(DB) as db:
            db.row_factory = sqlite3.Row
            corners = [dict(r) for r in db.execute(
                "SELECT name,lat,lon FROM places WHERE kind='intersection' AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
                (place["lat"]-.002, place["lat"]+.002, place["lon"]-.003, place["lon"]+.003),
            )]
        corner = min(corners, key=lambda p: distance(place, p), default=None)
        return "Subway station" + (" · " + lines if lines else "") + (" · Near " + corner["name"] if corner else "")
    nearest = min(stations(), key=lambda x: distance(place, x), default=None)
    if nearest:
        d = distance(place, nearest)
        return f"Near {nearest['name']} station · {round(d/10)*10:,} m away"
    return place.get("address") or place["kind"]
