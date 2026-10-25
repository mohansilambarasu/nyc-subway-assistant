"""Bounded local-model tool loop. Route facts come only from executed tools."""
import json, time
import httpx
from . import model, places, routing, live, planner

TOOLS = {
    "search_origin": "Resolve the user's starting place query using the local place index.",
    "search_destination": "Resolve the destination query using the local place index.",
    "nearby_stations": "Find up to five subway station locations within 1.5km of the confirmed origin, by straight-line distance.",
    "plan_routes": "Calculate valid subway/walking itineraries between confirmed places; compare boarding walks.",
    "check_service": "Inspect available platform predictions and service notices for the suggested itinerary.",
    "respond": "Return a grounded response or ask for the missing place after inspecting tool results.",
}


def nearby(origin):
    found = sorted(places.stations(), key=lambda p: places.distance(origin, p))
    return [{"id": p["id"], "name": p["name"], "lat": p["lat"], "lon": p["lon"],
             "distance_m": round(places.distance(origin, p)), "context": places.context(p)}
            for p in found if places.distance(origin, p) <= 1500][:5]


async def choose(context, available):
    """The local model chooses the next tool from those permitted by current state."""
    schema = {"type": "object", "properties": {"tool": {"type": "string", "enum": available}},
              "required": ["tool"], "additionalProperties": False}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(model.URL+"/api/chat", json={
                "model": model.MODEL, "stream": False, "format": schema,
                "messages": [{"role": "system", "content":
                    "You orchestrate an NYC subway assistant. Choose ONE available tool, inspect its result in the next turn, then choose again. "
                    "First resolve supplied places; then discover nearby stations, plan a route if a destination exists, and check service. "
                    "Respond when a place is ambiguous or more user input is needed. Never invent facts or select an unconfirmed ambiguous place. "
                    "Tool descriptions: "+json.dumps(TOOLS)},
                    {"role": "user", "content": json.dumps(context, ensure_ascii=False)}],
                "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 40}, "keep_alive": "15m"})
            response.raise_for_status()
            tool = json.loads(response.json()["message"]["content"])["tool"]
            return tool if tool in available else None
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None


async def run(s):
    observations = []
    s["agent_trace"] = []
    s["agent_mode"] = "local_agent"
    s["routing_error"] = None
    # Existing place choices belong to a previous turn; unresolved queries are searched anew.
    s["pending"] = None
    done = set()
    fallback = False
    route_made = False
    blocked = False
    for _ in range(7):
        available = []
        for role in ("origin", "destination"):
            if not s.get(role) and s.get(role+"_query") and "search_"+role not in done:
                available.append("search_"+role)
        if s.get("origin") and all(k in s["origin"] for k in ["lat", "lon"]) and "nearby_stations" not in done:
            available.append("nearby_stations")
        if s.get("origin") and s.get("destination") and not s.get("awaiting_platform") and "plan_routes" not in done:
            available.append("plan_routes")
        if route_made and "check_service" not in done:
            available.append("check_service")
        if blocked or not available:
            available = ["respond"]
        context = {"goal": {"origin_query": s.get("origin_query"), "destination_query": s.get("destination_query")},
                   "confirmed_origin": s.get("origin", {}).get("name") if s.get("origin") else None,
                   "confirmed_destination": s.get("destination", {}).get("name") if s.get("destination") else None,
                   "awaiting_off_train_confirmation": s.get("awaiting_platform"),
                   "tool_results": observations, "available_tools": available}
        tool = await choose(context, available) if not fallback else None
        if tool not in available:
            fallback = True
            s["agent_mode"] = "workflow_fallback"
            tool = available[0]
        if tool == "respond":
            break
        done.add(tool)
        started = time.monotonic()
        if tool.startswith("search_"):
            role = tool[7:]
            candidates = places.search(s[role+"_query"], near=s.get("origin") if role == "destination" else None)
            exact = [p for p in candidates if places.norm(p["name"]) == places.norm(s[role+"_query"]) and p.get("kind") != "station"]
            if len(exact) == 1 and exact[0].get("score", 0) >= 150:
                if planner.needs_points(exact[0]):
                    planner.prepare(s,exact[0],role,'chat')
                    result = {"requires_point_confirmation":True,"place":exact[0]['name']}
                    blocked=True
                else:
                    s[role] = exact[0]
                    result = {"confirmed": exact[0]["name"]}
            else:
                s["pending"] = {"role": role, "query": s[role+"_query"], "candidates": candidates}
                result = {"requires_user_confirmation": True, "candidates": [{"name": p["name"], "context": p["context"]} for p in candidates]}
                blocked = True
        elif tool == "nearby_stations":
            s["nearby_stations"] = nearby(s["origin"])
            s["nearby_origin_id"] = s["origin"]["id"]
            result = {"stations": s["nearby_stations"], "distance_type": "straight_line", "radius_m": 1500}
        elif tool == "plan_routes":
            s.update(journeys=[], live=None, selected=0, step=0)
            try:
                routes = await routing.plan(s["origin"], s["destination"])
                # Prefer the smallest boarding walk among the router's viable subway options.
                def walk_to_train(j):
                    walk = 0
                    for leg in j["legs"]:
                        if leg["transit"]:
                            return walk
                        walk += leg.get("distance", 0)
                    return float("inf")
                routes.sort(key=lambda j: (walk_to_train(j), j["duration"]))
                s["journeys"] = routes
                s["recovering"] = False
                route_made = True
                result = {"routes": [{"minutes": j["duration"], "boarding_walk_m": walk_to_train(j) if any(l["transit"] for l in j["legs"]) else None,
                    "trains": [{"line": l["line"], "from": l["from"], "to": l["to"], "headsign": l["headsign"]} for l in j["legs"] if l["transit"]]} for j in routes],
                    "selection": "shortest boarding walk among returned viable subway itineraries; not a proof of globally nearest boarding station"}
            except routing.RoutingError as e:
                s["routing_error"] = str(e)
                result = {"error": str(e)}
                blocked = True
        elif tool == "check_service":
            s["live"] = await live.for_journey(s["journeys"][0])
            result = {"predictions": ((s["live"] or {}).get("departures") or {}).get("status"),
                      "notices": len(((s["live"] or {}).get("alerts") or {}).get("items", [])), "schedule_not_rerouted": True}
        observations.append({"tool": tool, "result": result})
        s["agent_trace"].append({"tool": tool, "result": result, "seconds": round(time.monotonic()-started, 2)})
    if (s.get('map_draft') or {}).get('active') and s['map_draft'].get('stage')=='points':
        return f"Choose a numbered green point on the map for {s['map_draft']['base']['name']}, or click Place a pin for a more precise location. The selected point turns red."
    if s.get("pending"):
        p = s["pending"]
        if not p["candidates"]:
            return f"I couldn’t confidently find “{p['query']}”. Try a nearby intersection or landmark in NYC."
        return f"Which {p['role']} did you mean? Match the place on the map or reply with its number. Stations with the same name may serve different trains."
    if s.get("awaiting_platform") and s.get("origin"):
        return f"Are you off the train at {s['origin']['name']}? Confirm before I plan from that station."
    if s.get("routing_error"):
        return s["routing_error"]
    if route_made:
        j = s["journeys"][0]
        train = next((l for l in j["legs"] if l["transit"]), None)
        s["was_recovery"] = False
        if train:
            walk = sum(l.get("exterior_walk_minutes") or l.get("duration", 0) for l in j["legs"][:j["legs"].index(train)])
            return f"I compared the available routes and picked the shortest boarding walk among the subway options returned: about {walk} minute{'s' if walk != 1 else ''} on foot to {train['from']}, plus station access time. Take the {train['line']} toward {train['headsign']} and get off at {train['to']}. The whole trip to {j['destination']['name']} is about {j['duration']} minutes on the schedule. The map shows the walking and train paths."
        return f"The route to {j['destination']['name']} is on foot, about {j['duration']} minutes. Follow the walking path on the map."
    if s.get("origin") and not s.get("destination"):
        found = s.get("nearby_stations", [])
        s["pending"] = {"role": "destination", "query": "", "candidates": []}
        if found:
            return f"You’re at {s['origin']['name']}. I found {len(found)} nearby subway station locations within 1.5 km and marked them on the map. {found[0]['name']} is closest by straight-line distance ({found[0]['distance_m']} m). Where would you like to go? I’ll choose a boarding station for that journey."
        return f"You’re at {s['origin']['name']}. I didn’t find a subway station within 1.5 km. Where would you like to go? I can check a longer connection."
    role = "origin" if not s.get("origin") else "destination"
    s["pending"] = {"role": role, "query": "", "candidates": []}
    return "Where are you now? A café, landmark, address or intersection works." if role == "origin" else "Where would you like to go?"
