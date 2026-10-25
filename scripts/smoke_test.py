"""Exercise the running local app with real model, place search and routing."""

import json, sys, time
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
c = httpx.Client(base_url="http://127.0.0.1:8765", timeout=150)
report = []


def post(s=None, **kw):
    r = c.post("/api/chat", json={"session_id": s and s["session_id"], **kw})
    r.raise_for_status()
    return r.json()


def confirm(s):
    for _ in range(12):
        d=s.get('map_draft') or {}
        if d.get('active') and d.get('stage')=='points' and d.get('points'):
            r=c.post('/api/planner',json={'session_id':s['session_id'],'action':'point','id':d['points'][0]['id']});r.raise_for_status();s=r.json()
        elif s['pending'] and s['pending']['candidates']:
            p=s['pending'];s=post(s,selection={'role':p['role'],'id':p['candidates'][0]['id']})
        else:return s
    raise AssertionError('Location confirmation did not converge')


h = c.get("/api/health").json()
assert h["router"] and h["model"] and h["places"], h
report.append(
    {
        "check": "all local services",
        "result": "pass",
        "model": h["model_name"],
        "feed": h["feed"],
    }
)
s = post(
    message="I'm at Bryant Park next to waffles and dingles. I want to go to Wall Street to the raging bull."
)
assert s["model_used"]
s = confirm(s)
assert s["journeys"], s["messages"][-1]
j = s["journeys"][0]
assert s["destination"]["name"] == "Charging Bull"
assert any(
    l["transit"] and l["headsign"] and l["stop_id"].endswith(("N", "S"))
    for l in j["legs"]
)
assert any(
    "Street" in x["text"] or "Avenue" in x["text"]
    for st in j["steps"]
    for x in st.get("walking", [])
)
report.append(
    {
        "check": "original Bryant Park / unknown shop / bull example",
        "result": "pass",
        "minutes": j["duration"],
        "trains": [l["line"] for l in j["legs"] if l["transit"]],
        "walk": j["steps"][0]["walking"],
        "live": s["live"] and s["live"].get("departures", {}).get("status"),
    }
)
s = post(s, action="next")
idx = s["step"]
s = post(s, message="Could you repeat that?")
assert s["step"] == idx
report.append({"check": "repeat preserves progress", "result": "pass"})
s = post(s, message="I missed my stop")
assert (
    s["recovering"]
    and s["destination"]["name"] == "Charging Bull"
    and s["origin"] is None
)
s = post(s, message="I'm at Canal St")
assert s["pending"]["candidates"]
s = confirm(s)
assert s["awaiting_platform"]
s = post(s, action="platform")
assert not s["recovering"] and s["journeys"]
report.append(
    {
        "check": "missed stop, ambiguous station, off-train confirmation and replan",
        "result": "pass",
        "new_origin": s["origin"]["name"],
        "destination": s["destination"]["name"],
    }
)
s = post(message="Take me from Astoria-Ditmars Blvd to Brooklyn Museum.")
s = confirm(s)
assert s["journeys"], s["messages"][-1]
j = s["journeys"][0]
assert j["transfers"] >= 1
report.append(
    {
        "check": "cross-borough transfer journey",
        "result": "pass",
        "minutes": j["duration"],
        "trains": [l["line"] for l in j["legs"] if l["transit"]],
    }
)
s = post(
    message="I am at Grand Central Terminal and want to go to the Metropolitan Museum of Art."
)
s = confirm(s)
assert s["journeys"]
j = s["journeys"][0]
assert any(any(l["transit"] for l in option["legs"]) for option in s["journeys"])
report.append(
    {
        "check": "multipolygon landmark and useful itinerary ordering",
        "result": "pass",
        "minutes": j["duration"],
        "trains": [l["line"] for l in j["legs"] if l["transit"]],
    }
)
assert c.get("/").status_code == 200
(ROOT / "docs/smoke-results.json").write_text(
    json.dumps({"tested_at": time.time(), "checks": report}, indent=2)
)
print(json.dumps(report, indent=2))
