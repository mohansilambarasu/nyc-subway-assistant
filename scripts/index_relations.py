"""Add named OSM multipolygon places (museums, parks) missing from node/way indexes."""

import json, sqlite3
from pathlib import Path
import osmium
from index_places import norm

ROOT = Path(__file__).resolve().parents[1]


class Areas(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.rows = []

    def area(self, a):
        if a.from_way():
            return
        t = dict(a.tags)
        name = t.get("name")
        kind = t.get("tourism") or t.get("leisure") or t.get("amenity") or t.get("shop")
        if not name or not kind:
            return
        pts = [
            (n.lat, n.lon)
            for ring in a.outer_rings()
            for n in ring
            if n.location.valid()
        ]
        if not pts:
            return
        lat = sum(p[0] for p in pts) / len(pts)
        lon = sum(p[1] for p in pts) / len(pts)
        if not (40.48 < lat < 40.96 and -74.36 < lon < -73.67):
            return
        aliases = " ".join(t.get(x, "") for x in ["alt_name", "short_name", "brand"])
        self.rows.append(
            (
                "r" + str(a.orig_id()),
                name,
                kind,
                "",
                lat,
                lon,
                norm(name + " " + aliases),
            )
        )


def main():
    h = Areas()
    h.apply_file(str(ROOT / "data/NewYork.osm.pbf"), locations=True, idx="flex_mem")
    with sqlite3.connect(ROOT / "data/places.sqlite") as db:
        db.executemany("INSERT OR REPLACE INTO places VALUES(?,?,?,?,?,?,?)", h.rows)
        db.execute("INSERT INTO place_fts(place_fts) VALUES('rebuild')")
    print("Added", len(h.rows), "multipolygon places", flush=True)
    (ROOT / "data/relations-indexed").touch()


if __name__ == "__main__":
    main()
