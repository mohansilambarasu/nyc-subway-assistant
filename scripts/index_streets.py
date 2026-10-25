"""Index named street segments to label unnamed parallel OSM sidewalks."""

from pathlib import Path
import sqlite3, osmium

ROOT = Path(__file__).resolve().parents[1]


class Streets(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.rows = []

    def way(self, w):
        if not w.tags.get("name") or w.tags.get("highway") not in {
            "primary",
            "secondary",
            "tertiary",
            "residential",
            "unclassified",
            "living_street",
            "pedestrian",
        }:
            return
        ns = list(w.nodes)
        for a, b in zip(ns, ns[1:]):
            if a.location.valid() and b.location.valid():
                self.rows.append((w.tags["name"], a.lat, a.lon, b.lat, b.lon))


def main():
    h = Streets()
    h.apply_file(str(ROOT / "data/NewYork.osm.pbf"), locations=True, idx="flex_mem")
    p = ROOT / "data/streets.sqlite"
    p.unlink(missing_ok=True)
    db = sqlite3.connect(p)
    db.execute(
        "CREATE TABLE segments(id INTEGER PRIMARY KEY,name TEXT,a_lat REAL,a_lon REAL,b_lat REAL,b_lon REAL)"
    )
    db.executemany(
        "INSERT INTO segments(name,a_lat,a_lon,b_lat,b_lon) VALUES(?,?,?,?,?)", h.rows
    )
    db.execute(
        "CREATE VIRTUAL TABLE bounds USING rtree(id,minlat,maxlat,minlon,maxlon)"
    )
    db.execute(
        "INSERT INTO bounds SELECT id,min(a_lat,b_lat),max(a_lat,b_lat),min(a_lon,b_lon),max(a_lon,b_lon) FROM segments"
    )
    db.commit()
    print("Named street segments", len(h.rows), flush=True)


if __name__ == "__main__":
    main()
