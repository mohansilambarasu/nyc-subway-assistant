"""Download public routing inputs atomically; no API keys needed."""

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json, urllib.request, time

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "data/mta-entrances.json": "https://data.ny.gov/resource/i9wp-a4ja.json?$limit=5000",
    "data/NewYork.osm.pbf": "https://download.bbbike.org/osm/bbbike/NewYork/NewYork.osm.pbf",
    "runtime/otp.jar": "https://repo1.maven.org/maven2/org/opentripplanner/otp-shaded/2.8.0/otp-shaded-2.8.0.jar",
    "runtime/ollama-darwin.tgz": "https://github.com/ollama/ollama/releases/download/v0.40.7/ollama-darwin.tgz",
    "data/mta-source.zip": "https://rrgtfsfeeds.s3.amazonaws.com/gtfs_supplemented.zip",
}


def download(item, force=False):
    name, url = item
    dest = ROOT / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not force:
        print("Using", name, flush=True)
        return
    print("Downloading", name, flush=True)
    req = urllib.request.Request(
        url, headers={"User-Agent": "SubwayAssistant-local-prototype/0.1"}
    )
    with urllib.request.urlopen(req, timeout=120) as r, dest.with_suffix(
        dest.suffix + ".part"
    ).open("wb") as f:
        while chunk := r.read(1024 * 1024):
            f.write(chunk)
    dest.with_suffix(dest.suffix + ".part").replace(dest)
    print("Ready", name, dest.stat().st_size, flush=True)


if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(download, SOURCES.items()))
    (ROOT / "data/downloaded.json").write_text(
        json.dumps({"fetched_at": time.time(), "sources": SOURCES}, indent=2)
    )
