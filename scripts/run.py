#!/usr/bin/env python3
"""One command local startup. Downloads are cached within this project."""

import argparse, hashlib, json, os, platform, shutil, subprocess, sys, tarfile, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
PYTHON = ROOT / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
CHILDREN = []
LOGS = []


def command(args, **kw):
    return subprocess.run([str(x) for x in args], check=True, **kw)


def get(url, body=None, timeout=3):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def healthy(url):
    try:
        get(url, {"query": "{__typename}"} if "/otp/gtfs/" in url else None)
        return True
    except Exception:
        return False


def spawn(args, name, env=None):
    f = (ROOT / "runtime" / f"{name}.log").open("w")
    LOGS.append(f)
    p = subprocess.Popen(
        [str(x) for x in args], stdout=f, stderr=subprocess.STDOUT, env=env
    )
    CHILDREN.append(p)
    return p


def wait(url, p, label, timeout):
    started = time.time()
    last = 0
    while time.time() - started < timeout:
        if p.poll() is not None:
            raise RuntimeError(f"{label} stopped. Read runtime/{label}.log.")
        if healthy(url):
            return
        if time.time() - last > 20:
            print(f"Waiting for {label}… see runtime/{label}.log", flush=True)
            last = time.time()
        time.sleep(1)
    raise RuntimeError(f"{label} took too long. Read runtime/{label}.log.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--refresh",
        action="store_true",
        help="Refresh MTA schedule and entrances, then rebuild transit graph",
    )
    args = ap.parse_args()
    (ROOT / "runtime").mkdir(exist_ok=True)
    (ROOT / "data").mkdir(exist_ok=True)
    if not PYTHON.exists():
        command([sys.executable, "-m", "venv", ROOT / ".venv"])
    lock = ROOT / "requirements.lock.txt"
    lock = lock if lock.exists() else ROOT / "requirements.txt"
    fingerprint = hashlib.sha256(lock.read_bytes()).hexdigest()
    marker = ROOT / "runtime/packages-ready"
    if not marker.exists() or marker.read_text() != fingerprint:
        command([PYTHON, "-m", "pip", "install", "-r", lock])
        marker.write_text(fingerprint)
    if not shutil.which("java"):
        raise RuntimeError(
            "Java 21 or newer is required. Install OpenJDK 21, then rerun this command. See README.md."
        )
    otp_running = healthy("http://127.0.0.1:8080/otp/gtfs/v1")
    if args.refresh and otp_running:
        raise RuntimeError(
            "Stop the running project with Ctrl+C before --refresh, so its graph can be replaced safely."
        )
    from download_data import SOURCES, download

    if platform.system() != "Darwin" and not shutil.which("ollama"):
        raise RuntimeError(
            "Install Ollama from https://ollama.com/download for your OS, then rerun. No account is required."
        )
    for name, url in SOURCES.items():
        if "ollama-darwin" in name and platform.system() != "Darwin":
            continue
        download(
            (name, url),
            force=(
                args.refresh
                or (
                    not otp_running
                    and name == "data/mta-source.zip"
                    and (ROOT / name).exists()
                    and time.time() - (ROOT / name).stat().st_mtime > 21600
                )
            )
            and name in ["data/mta-source.zip", "data/mta-entrances.json"],
        )
    (ROOT / "data/downloaded.json").write_text(
        json.dumps(
            {
                "checked_at": time.time(),
                "downloaded_at": {
                    name: (ROOT / name).stat().st_mtime
                    for name in SOURCES
                    if (ROOT / name).exists()
                },
                "sources": SOURCES,
            },
            indent=2,
        )
    )
    if (
        not (ROOT / "data/subway.gtfs.zip").exists()
        or (ROOT / "data/mta-source.zip").stat().st_mtime
        > (ROOT / "data/subway.gtfs.zip").stat().st_mtime
        or args.refresh
    ):
        command([PYTHON, ROOT / "scripts/prepare_gtfs.py"])
    if not (ROOT / "data/places.sqlite").exists():
        command([PYTHON, ROOT / "scripts/index_places.py"])
    if not (ROOT / "data/relations-indexed").exists():
        command([PYTHON, ROOT / "scripts/index_relations.py"])
    if not (ROOT / "data/streets.sqlite").exists():
        command([PYTHON, ROOT / "scripts/index_streets.py"])
    if not otp_running:
        graph = ROOT / "data/graph.obj"
        rebuild = not graph.exists() or any(
            p.stat().st_mtime > graph.stat().st_mtime
            for p in [ROOT / "data/subway.gtfs.zip", ROOT / "data/build-config.json"]
        )
        if rebuild:
            # Release the graph builder's temporary OSM state before serving.
            builder = spawn([
                "java", os.environ.get("OTP_HEAP", "-Xmx3G"), "-jar",
                ROOT / "runtime/otp.jar", "--build", "--save", ROOT / "data"
            ], "otp-build")
            started = time.time()
            while builder.poll() is None:
                if time.time() - started > 1200:
                    raise RuntimeError("Graph build timed out. Read runtime/otp-build.log.")
                print("Building the graph… see runtime/otp-build.log", flush=True)
                time.sleep(20)
            CHILDREN.remove(builder)
            if builder.returncode:
                raise RuntimeError("Graph build failed. Read runtime/otp-build.log.")
        flags = ["--load", "--serve"]
        print(
            (
                "Building NYC walking/transit graph (first run: several minutes)…"
                if rebuild
                else "Loading the cached NYC graph…"
            ),
            flush=True,
        )
        router = spawn(
            [
                "java",
                os.environ.get("OTP_HEAP", "-Xmx3G"),
                "-jar",
                ROOT / "runtime/otp.jar",
                *flags,
                "--bindAddress",
                "127.0.0.1",
                "--port",
                "8080",
                ROOT / "data",
            ],
            "otp",
        )
    else:
        router = None
    model_url = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11435")
    if not healthy(model_url + "/api/tags"):
        ollama = ROOT / "runtime/ollama/ollama"
        if not ollama.exists() and platform.system() == "Darwin":
            ollama.parent.mkdir(exist_ok=True)
            with tarfile.open(ROOT / "runtime/ollama-darwin.tgz") as t:
                t.extractall(ollama.parent, filter="data")
        if not ollama.exists():
            ollama = shutil.which("ollama")
        env = os.environ.copy()
        env.update(
            OLLAMA_HOST="127.0.0.1:11435",
            OLLAMA_MODELS=str(ROOT / "runtime/models"),
            OLLAMA_NO_CLOUD="1",
        )
        p = spawn([ollama, "serve"], "ollama", env)
        wait(model_url + "/api/tags", p, "ollama", 60)
    model = os.environ.get("OLLAMA_MODEL", "qwen2.5:1.5b")
    if not any(
        x["name"] == model for x in get(model_url + "/api/tags").get("models", [])
    ):
        print(f"Downloading {model} locally (first run only; no API key)…", flush=True)
        get(model_url + "/api/pull", {"model": model, "stream": False}, timeout=1800)
    if router:
        wait("http://127.0.0.1:8080/otp/gtfs/v1", router, "otp", 1200)
    if not healthy("http://127.0.0.1:8765/api/health"):
        p = spawn(
            [
                PYTHON,
                "-m",
                "uvicorn",
                "subway.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8765",
                "--no-access-log",
            ],
            "app",
        )
        wait("http://127.0.0.1:8765/api/health", p, "app", 60)
    print(
        "\nSubway Assistant is ready: http://127.0.0.1:8765\nKeep this terminal open. Ctrl+C stops processes launched by this command.\n",
        flush=True,
    )
    while CHILDREN:
        for p in CHILDREN:
            if p.poll() is not None:
                raise RuntimeError("A local service stopped. Inspect runtime/*.log.")
        time.sleep(2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopping local services…")
    except Exception as e:
        print("\nSetup error:", str(e), file=sys.stderr)
        sys.exitcode = 1
    finally:
        for p in reversed(CHILDREN):
            if p.poll() is None:
                p.terminate()
        for p in CHILDREN:
            try:
                p.wait(timeout=12)
            except subprocess.TimeoutExpired:
                p.kill()
        for f in LOGS:
            f.close()
    if getattr(sys, "exitcode", 0):
        sys.exit(1)
