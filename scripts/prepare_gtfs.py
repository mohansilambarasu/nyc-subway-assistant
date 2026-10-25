"""Add MTA entrance coordinates as GTFS entrance nodes, retaining provenance."""

import csv, io, json, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    source = ROOT / "data/mta-source.zip"
    dest = ROOT / "data/subway.gtfs.zip"
    if not source.exists():
        dest.replace(source)
    z = zipfile.ZipFile(source)
    rows = list(csv.DictReader(io.TextIOWrapper(z.open("stops.txt"), "utf-8-sig")))
    fields = list(rows[0])
    parents = {s["stop_id"] for s in rows if s.get("location_type") == "1"}
    entrances = json.loads((ROOT / "data/mta-entrances.json").read_text())
    seen = set()
    added = 0
    for i, e in enumerate(entrances):
        parent = e.get("gtfs_stop_id")
        key = (parent, e.get("entrance_latitude"), e.get("entrance_longitude"))
        if (
            parent not in parents
            or e.get("entry_allowed") != "YES"
            or e.get("exit_allowed") != "YES"
            or key in seen
        ):
            continue
        seen.add(key)
        added += 1
        rows.append(
            {
                "stop_id": f"ENT{i}",
                "stop_name": e["stop_name"]
                + " — "
                + e.get("entrance_type", "").lower()
                + " entrance",
                "stop_lat": e["entrance_latitude"],
                "stop_lon": e["entrance_longitude"],
                "location_type": "2",
                "parent_station": parent,
            }
        )
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=fields, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    tmp = dest.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for n in z.namelist():
            target.writestr(n, out.getvalue() if n == "stops.txt" else z.read(n))
    tmp.replace(dest)
    print("Added", added, "official MTA bidirectional entrance nodes", flush=True)


if __name__ == "__main__":
    main()
