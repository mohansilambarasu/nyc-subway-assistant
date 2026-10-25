"""Build an offline place, address, entrance and intersection index from OSM."""

import csv, io, json, re, sqlite3, sys, zipfile
from pathlib import Path
import osmium

ROOT = Path(__file__).resolve().parents[1]


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


class Places(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.junctions = {}
        self.entrances = []

    def add(self, typ, ident, t, lat, lon):
        if not (40.48 < lat < 40.96 and -74.36 < lon < -73.67):
            return
        address = " ".join(
            filter(None, [t.get("addr:housenumber"), t.get("addr:street")])
        )
        name = t.get("name") or address
        if t.get("railway") == "subway_entrance":
            self.entrances.append(
                {
                    "id": f"{typ}{ident}",
                    "name": name or "Subway entrance",
                    "lat": lat,
                    "lon": lon,
                    "ref": t.get("ref"),
                    "wheelchair": t.get("wheelchair"),
                }
            )
        if not name:
            return
        kind = (
            t.get("amenity")
            or t.get("tourism")
            or t.get("shop")
            or t.get("leisure")
            or t.get("historic")
            or ("address" if address else "")
        )
        if not kind:
            return
        aliases = " ".join(
            [
                t.get("alt_name", ""),
                t.get("short_name", ""),
                t.get("brand", ""),
                address,
            ]
        )
        self.rows.append(
            (f"{typ}{ident}", name, kind, address, lat, lon, norm(name + " " + aliases))
        )

    def node(self, n):
        if n.location.valid():
            self.add("n", n.id, dict(n.tags), n.location.lat, n.location.lon)

    def way(self, w):
        tags = dict(w.tags)
        if not (tags.get("name") or tags.get("addr:housenumber")):
            return
        pts = [(n.lat, n.lon) for n in w.nodes if n.location.valid()]
        if pts:
            self.add(
                "w",
                w.id,
                tags,
                sum(p[0] for p in pts) / len(pts),
                sum(p[1] for p in pts) / len(pts),
            )
        if (
            tags.get("highway")
            and tags.get("name")
            and tags.get("highway") not in ["motorway", "motorway_link", "trunk"]
        ):
            for n in w.nodes:
                if n.location.valid():
                    entry = self.junctions.setdefault(n.ref, [n.lat, n.lon, set()])
                    entry[2].add(tags["name"])


def main():
    dest = ROOT / "data/places.sqlite"
    tmp = dest.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    p = Places()
    print("Indexing OSM places…", flush=True)
    p.apply_file(str(ROOT / "data/NewYork.osm.pbf"), locations=True, idx="flex_mem")
    for ident, (lat, lon, names) in p.junctions.items():
        names = sorted(names)
        if len(names) > 1:
            name = " & ".join(names[:3])
            p.rows.append((f"i{ident}", name, "intersection", "", lat, lon, norm(name)))
    z = zipfile.ZipFile(ROOT / "data/subway.gtfs.zip")
    for s in csv.DictReader(io.TextIOWrapper(z.open("stops.txt"), "utf-8-sig")):
        if s.get("location_type") == "1":
            p.rows.append(
                (
                    "s" + s["stop_id"],
                    s["stop_name"],
                    "station",
                    "",
                    float(s["stop_lat"]),
                    float(s["stop_lon"]),
                    norm(s["stop_name"]),
                )
            )
    db = sqlite3.connect(tmp)
    db.execute(
        "CREATE TABLE places(id TEXT PRIMARY KEY,name TEXT,kind TEXT,address TEXT,lat REAL,lon REAL,search TEXT)"
    )
    db.executemany("INSERT OR IGNORE INTO places VALUES(?,?,?,?,?,?,?)", p.rows)
    # Human-language aliases point to OSM features; never create coordinates from model output.
    for alias, target in [
        ("raging bull", "Charging Bull"),
        ("the bull", "Charging Bull"),
        ("ny public library", "Stephen A. Schwarzman Building"),
        ("grand central", "Grand Central Terminal"),
    ]:
        db.execute(
            "UPDATE places SET search=search || ? WHERE lower(name)=lower(?)",
            (" " + alias, target),
        )
    db.execute("CREATE INDEX coordinates ON places(lat,lon)")
    db.execute(
        "CREATE VIRTUAL TABLE place_fts USING fts5(name,search,content='places',content_rowid='rowid',tokenize='unicode61')"
    )
    db.execute("INSERT INTO place_fts(place_fts) VALUES('rebuild')")
    db.commit()
    count = db.execute("select count(*) from places").fetchone()[0]
    db.close()
    tmp.replace(dest)
    (ROOT / "data/entrances.json").write_text(json.dumps(p.entrances))
    print(
        f"Indexed {count:,} places, addresses and intersections; {len(p.entrances)} entrances.",
        flush=True,
    )


if __name__ == "__main__":
    main()
