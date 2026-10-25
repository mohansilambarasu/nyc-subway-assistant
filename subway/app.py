import asyncio, json, re, sqlite3, time, uuid, math
from contextlib import asynccontextmanager
from pathlib import Path
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field
from . import places, model, routing, live, agent, planner

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/sessions.sqlite"
LOCKS = {}


@asynccontextmanager
async def lifespan(app):
    with sqlite3.connect(DB) as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, state TEXT, updated REAL)"
        )
        db.execute("DELETE FROM sessions WHERE updated < ?", (time.time() - 7 * 86400,))
    yield


app = FastAPI(title="Subway Assistant", lifespan=lifespan)
app.add_middleware(
    TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


class Selection(BaseModel):
    role: str
    id: str = Field(max_length=80)


class Chat(BaseModel):
    ui_mode: str | None = None
    session_id: str | None = None
    message: str = Field(default="", max_length=1500)
    selection: Selection | None = None
    action: str | None = None
    itinerary_index: int | None = None


def load(ident):
    if ident:
        if not re.fullmatch("[a-f0-9]{32}", ident):
            raise HTTPException(400, "Invalid session")
        with sqlite3.connect(DB) as db:
            r = db.execute("SELECT state FROM sessions WHERE id=?", (ident,)).fetchone()
            if r:
                return json.loads(r[0])
    return {
        "id": uuid.uuid4().hex,
        "messages": [],
        "origin": None,
        "destination": None,
        "origin_query": "",
        "destination_query": "",
        "pending": None,
        "journeys": [],
        "selected": 0,
        "step": 0,
        "recovering": False,
        "awaiting_platform": False,
        "model_used": False,
    }


def save(s):
    s["messages"] = s["messages"][-80:]
    with sqlite3.connect(DB) as db:
        db.execute(
            "INSERT OR REPLACE INTO sessions VALUES(?,?,?)",
            (s["id"], json.dumps(s), time.time()),
        )


def expired(s):
    if not s["journeys"]:
        return False
    j = s["journeys"][s["selected"]]
    if j["steps"][s["step"]]["kind"] == "arrive":
        return False
    end = j.get("legs", [{}])[-1].get("end_ms", 0) if j.get("legs") else 0
    return bool(end and end < (time.time() - 300) * 1000)


def result(s):
    return {
        "session_id": s["id"],
        "ui_mode": s.get("ui_mode"),
        "messages": s["messages"],
        "origin": s["origin"],
        "destination": s["destination"],
        "pending": s["pending"],
        "journeys": s["journeys"],
        "selected": s["selected"],
        "step": s["step"],
        "recovering": s["recovering"],
        "journey_expired": expired(s),
        "awaiting_platform": s["awaiting_platform"],
        "model_used": s["model_used"],
        "live": s.get("live"),
        "routing_error": s.get("routing_error"),
        "nearby_stations": s.get("nearby_stations", []) if s.get("origin") and s.get("nearby_origin_id")==s["origin"]["id"] else [],
        "agent_trace": s.get("agent_trace", []),
        "agent_mode": s.get("agent_mode", "not_started"),
        "map_draft": s.get("map_draft"),
    }


def conversation_text(s, text):
    d=s.get('map_draft') or {}
    if d.get('active') and d.get('stage')=='points':
        points=d.get('points',[])
        return f"Which specific point near {d['base']['name']} works for your {d['role']}?\n\n"+'\n'.join(f"{i}. {p['name']}" for i,p in enumerate(points,1))+"\n\nReply with a number, or give a more specific address. Station choices are recorded entrances; park choices are nearby street corners."
    pending=s.get('pending')
    if pending and pending.get('candidates'):
        return f"Which {pending['role']} did you mean?\n\n"+'\n'.join(f"{i}. {p['name']} — {p.get('address') or p.get('context') or p.get('kind','')}" for i,p in enumerate(pending['candidates'],1))+"\n\nReply with its number."
    if text.startswith('Refreshed platform predictions'):
        data=s.get('live') or {};dep=data.get('departures') or {};board=data.get('boarding') or {}
        age=dep.get('age_seconds',0)+max(0,time.time()-data.get('checked_at',time.time()))
        arrivals=[max(1,math.ceil((a['at']-time.time())/60)) for a in dep.get('arrivals',[]) if a['at']>time.time()] if dep.get('status')=='live' and age<=180 else []
        text=(f"Platform predictions at {board.get('from','your boarding station')}: "+', '.join(str(n)+' min' for n in arrivals)+'.' if arrivals else 'Fresh platform predictions are unavailable. Check station signs and announcements.')+' These are separate from the scheduled journey and may leave before you finish walking.'
        notices=(data.get('alerts') or {}).get('items',[])
        if notices:text+='\n\nService notices:\n'+'\n'.join('• '+a['header']+': '+a.get('description','') for a in notices)
    text=text.replace('The map shows the walking and train paths.', 'Say “explain my route” for the full directions, When you reach the station, say “I’m at the station”.').replace('Follow the walking path on the map.','Say “explain my route” for walking directions.').replace('and marked them on the map','near your location')
    if text.startswith('I compared the available routes') and s.get('journeys'):
        text+='\n\n'+'\n'.join(f"Route {i}: {' → '.join(l['line'] for l in j['legs'] if l['transit']) or 'Walk'} · {j['duration']} min · {j.get('transfers',0)} transfers · {j.get('walking_m',0)} m walking" for i,j in enumerate(s['journeys'],1))+'\n\nRoute 1 is selected. Type “route 2” to switch or “explain my route” for all directions.'
        first=s['journeys'][0].get('steps',[{}])[0]
        text+='\n\nFirst step: '+first.get('text','')+'\n'+'\n'.join('• '+w['text'] for w in first.get('walking',[]))
    return text


def bind_mode(s, mode):
    if mode not in ('chat','map'):raise HTTPException(400,'Invalid interface mode')
    if s.get('ui_mode') and s['ui_mode']!=mode:raise HTTPException(409,'Chat and Map use independent sessions')
    s['ui_mode']=mode


def say(s, text):
    if s.get('ui_mode')=='chat':text=conversation_text(s,text)
    s["messages"].append({"role": "assistant", "text": text})


def user(s, text):
    s["messages"].append({"role": "user", "text": text})


async def ready(s):
    say(s, await agent.run(s))


@app.get("/")
async def index():
    return FileResponse(ROOT / "static/index.html")


@app.get("/api/health")
async def health():
    otp, ai = await asyncio.gather(routing.health(), model.health())
    return {
        "router": otp,
        "model": ai,
        "model_name": model.MODEL,
        "places": places.DB.exists(),
        "feed": routing.feed_info(),
        "mode": "local",
        "routing": "supplemented schedule + OSM walking; live predictions separate",
    }


@app.get("/api/session/{ident}")
async def session(ident: str):
    return result(load(ident))


@app.get("/api/places")
async def search(q: str = ""):
    if len(q) > 200:
        raise HTTPException(400, "Query too long")
    return {"places": places.search(q)}


@app.get("/api/map/streets")
async def map_streets(south: float, west: float, north: float, east: float):
    import math
    if not all(math.isfinite(x) for x in [south, west, north, east]) or not (40 < south < north < 42 and -75 < west < east < -72) or north-south > .4 or east-west > .5:
        raise HTTPException(400, "Zoom into NYC to view local street outlines")
    with sqlite3.connect(ROOT / "data/streets.sqlite") as db:
        rows = db.execute("SELECT s.name,s.a_lat,s.a_lon,s.b_lat,s.b_lon FROM bounds b JOIN segments s ON s.id=b.id WHERE b.maxlat>=? AND b.minlat<=? AND b.maxlon>=? AND b.minlon<=? LIMIT 12001", (south,north,west,east)).fetchall()
    return {"segments": rows[:12000], "truncated": len(rows)>12000, "source": "Local OpenStreetMap street outlines"}


class MapPick(BaseModel):
    session_id: str | None = None
    action: str
    role: str = "origin"
    id: str = Field(default="", max_length=100)
    lat: float | None = None
    lon: float | None = None


async def finish_map_point(s, ident):
    try:
        role, point, mode = planner.select_point(s, ident)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if mode == 'recovery':
        s.update(origin=point,origin_query=point['name'],pending=None,awaiting_platform=True)
        s['map_draft']['active']=False
        say(s,'Confirm you are off the train before replanning from this point.')
    elif mode == "chat":
        s[role] = point
        s[role+"_query"] = point['name']
        s['pending'] = None
        if role == 'destination' or s.get('destination_query'):
            s['map_draft']['active'] = False
            await ready(s)
        else:
            say(s, "Starting point confirmed. Where would you like to go?" if s.get("ui_mode")=="chat" else "Starting point confirmed. Search for your destination on the map, or tell me where you want to go.")
    else:
        say(s, "Both points are confirmed. Click Submit journey on the map when you’re ready." if s["map_draft"]["stage"]=="review" else "Starting point selected in red. Now search for your destination." if role=="origin" else "Destination selected in red. Now search for your starting point.")


@app.get('/api/map/search')
async def map_search(q: str = ''):
    if len(q)>200:raise HTTPException(400,'Search is too long')
    results=places.search(q,limit=41) if len(q.strip())>=2 else []
    return {'places':results[:40],'truncated':len(results)>40,'source':'Indexed NYC places and addresses'}


@app.post('/api/planner')
async def map_plan(body: MapPick, request: Request):
    origin = request.headers.get('origin')
    if origin and origin not in ('http://127.0.0.1:8765','http://localhost:8765'):
        raise HTTPException(403,'Use the local application origin')
    s=load(body.session_id)
    async with LOCKS.setdefault(s['id'],asyncio.Lock()):
        if body.session_id:s=load(body.session_id)
        bind_mode(s,'map')
        if body.action=='recover':
            d=planner.new_draft(s,'recovery');d['destination']=s.get('destination');s.update(recovering=True,was_recovery=True,pending=None)
        elif body.action=='begin':
            planner.new_draft(s)
            say(s,'Select starting point: search an NYC place or address, then choose a numbered green point on the map. The selected point turns red.')
        elif body.action=='cancel':
            s['map_draft']=None
            say(s,'Location selection closed. Your previous journey is unchanged.')
        elif body.action=='edit':
            if body.role not in ('origin','destination'):raise HTTPException(400,'Invalid location role')
            d=s.get('map_draft') or planner.new_draft(s)
            d.update(active=True,role=body.role,stage='search',base=None,points=[])
            d[body.role]=None
        elif body.action=='place':
            if body.role not in ('origin','destination'):raise HTTPException(400,'Invalid location role')
            p=places.get(body.id)
            if not p:raise HTTPException(400,'Place unavailable')
            d=planner.prepare(s,p,body.role,(s.get('map_draft') or {}).get('mode','interface'))
            if not planner.needs_points(p) and len(d['points'])==1:
                await finish_map_point(s,d['points'][0]['id'])
            else:
                say(s,f"Choose a numbered green point for {p['name']}."+(' These are recorded station entrances; confirm posted signs.' if p['kind']=='station' else ' Nearby street corners are not verified park entrances.' if planner.needs_points(p) else ' Confirm the location on the map.'))
        elif body.action=='pin':
            import math
            d=s.get('map_draft') or {};base=d.get('base')
            if not base or d.get('stage')!='points' or body.lat is None or body.lon is None or not all(math.isfinite(x) for x in (body.lat,body.lon)) or not (40.45<body.lat<40.95 and -74.3<body.lon<-73.65):raise HTTPException(400,'Choose a point within NYC after selecting a place')
            point={'id':uuid.uuid4().hex,'name':f"Map pin near {base['name']}",'base_place_name':base['name'],'kind':'user_pin','lat':body.lat,'lon':body.lon}
            if places.distance(point,base)>5000:raise HTTPException(400,'Choose a point within 5 km of the selected place')
            d['points']=[point]
            say(s,'Your map pin is shown in green. Click it or its numbered row to select it.')
        elif body.action=='point':
            await finish_map_point(s,body.id)
        elif body.action=='submit':
            d=s.get('map_draft') or {}
            if not d.get('active') or d.get('stage')!='review' or not d.get('origin') or not d.get('destination'):raise HTTPException(400,'Confirm both starting and destination points first')
            s.update(origin=d['origin'],destination=d['destination'],origin_query=d['origin']['name'],destination_query=d['destination']['name'],pending=None,awaiting_platform=False,recovering=False)
            d['active']=False
            user(s,f"Plan from {s['origin'].get('base_place_name',s['origin']['name'])} to {s['destination'].get('base_place_name',s['destination']['name'])}")
            await ready(s)
        else:raise HTTPException(400,'Unknown map action')
        save(s)
        return result(s)


@app.post("/api/chat")
async def chat(body: Chat, request: Request):
    origin = request.headers.get("origin")
    if origin and origin not in ("http://127.0.0.1:8765", "http://localhost:8765"):
        raise HTTPException(403, "Use the local application origin")
    s = load(body.session_id)
    lock = LOCKS.setdefault(s["id"], asyncio.Lock())
    async with lock:
        # Re-read after locking to prevent concurrent replies overwriting one another.
        if body.session_id:
            s = load(body.session_id)
        if body.ui_mode:bind_mode(s,body.ui_mode)
        action = body.action
        msg = body.message.strip()
        low = msg.lower()
        if s.get('ui_mode')=='chat':
            if low in ('back','previous','previous step'):action='previous'
            elif low in ('refresh','refresh arrivals','live arrivals'):action='refresh'
            elif s.get('awaiting_platform') and low in ("i'm off the train","i’m off the train","off the train","yes i'm off","yes","i am off the train"):action='platform'
            route_choice=re.fullmatch(r'(?:route|option)\s+(\d+)',low)
            if route_choice and not s.get('pending') and not (s.get('map_draft') or {}).get('active'):body.itinerary_index=int(route_choice.group(1))-1

        # Numbered chat replies confirm only a currently offered place.
        pending_choice = s.get("pending")
        number = re.fullmatch(r"(?:option\s+)?(\d+)[.)]?", low)
        if not body.selection and number and pending_choice:
            candidates = pending_choice.get("candidates", [])
            index = int(number.group(1)) - 1
            if 0 <= index < len(candidates):
                body.selection = Selection(role=pending_choice["role"], id=candidates[index]["id"])
        if action == "reset" or low in [
            "start over",
            "new trip",
            "reset",
            "restart",
            "let's start again",
            "start again",
        ]:
            ident = s["id"]
            s = load(None)
            s["id"] = ident
            if body.ui_mode:s["ui_mode"]=body.ui_mode
            say(s, "A fresh start. Where are you now, and where would you like to go?")
        elif s.get('ui_mode')=='chat' and re.fullmatch(r"(?:thanks|thank you|thank you so much|how are you|who are you)[!.? ]*",low):
            user(s,msg);say(s,"You’re welcome! I’m here whenever you need help with your trip." if 'thank' in low else 'I’m your NYC subway companion. Tell me where you are and where you want to go, and we can work through the journey here.')
        elif s.get('ui_mode')=='chat' and low in ('compare routes','other routes','alternatives','show routes'):
            user(s,msg)
            say(s,'\n\n'.join(f"Route {i}: "+' → '.join(l['line'] for l in j['legs'] if l['transit'])+f" · {j['duration']} min · {j.get('transfers',0)} transfers · {j.get('walking_m',0)} m walking. "+'; '.join(f"Board at {l['from']}, toward {l['headsign']}; get off at {l['to']}" for l in j['legs'] if l['transit']) for i,j in enumerate(s['journeys'],1))+'\n\nReply route 1, route 2, and so on.' if s['journeys'] else 'Tell me your starting place and destination first.')
        elif re.fullmatch(r"(?:hi|hello|hey|hi there|hello there|good morning|good evening)[!. ]*", low):
            user(s,msg)
            say(s,"Hi! Tell me where you are and where you want to go. I’ll help you choose places and trains here in chat. Reply with numbers when I offer choices; say next, back, repeat, or explain my route as you travel." if s.get("ui_mode")=="chat" else "Hi! I can guide you through the app or plan your trip. On the map, click Start journey → Select starting point, search a place, and choose a green point. Then choose your destination and click Submit journey. Or tell me where you are and where you want to go.")
        elif low in ('help','help me','how do i use this','how does this work','what should i do','how to use this app'):
            user(s,msg)
            d=s.get('map_draft') or {}
            instruction=("Choose a green point on the map, or click a numbered point in the search panel." if d.get('stage')=='points' else "Both locations are ready. Click Submit journey on the map." if d.get('origin') and d.get('destination') else "Click Select starting point on the map. Search a place, choose a green point, then repeat for your destination. Submit journey becomes available once both are selected.")
            say(s,"Tell me your starting place and destination. Reply with a number to choose a place, route 2 to change routes, next to advance, back to review, repeat, or explain my route. If you miss a stop, tell me where you are and whether you are off the train." if s.get("ui_mode")=="chat" else instruction+" You can also type your trip here. After routing, ask me to explain the journey.")
        elif action=='explain' or re.search(r"\b(?:explain|describe|walk me through)\b",low):
            user(s,msg or 'Explain my route')
            if not s['journeys']:say(s,'Tell me your starting place and destination first.')
            else:
                j=s['journeys'][s['selected']]
                lines=[f"Your journey to {j['destination'].get('base_place_name',j['destination']['name'])} · About {j['duration']} minutes on the schedule."]
                for i,l in enumerate(j['legs'],1):
                    if l['transit']:lines.append(f"{i}. TRAIN {l['line']} — Board at {l['from']}. Follow {l.get('direction') or 'the posted direction'} and look for {l['headsign']}. Your first stop is {l.get('next_stop',l['to'])}. Get off at {l['to']} after {len(l['stops'])+1} stops.")
                    else:lines.append(f"{i}. WALK — About {l.get('exterior_walk_minutes') or l['duration']} min on foot to {l['to']}."+('\n'+ '\n'.join('• '+x['text'] for x in l.get('walking',[])) if l.get('walking') else ' Follow the street directions and posted signs.'))
                lines.append('Live predictions are separate from the schedule. Confirm entrance and platform signs; indoor passages are unverified.')
                say(s,'\n\n'.join(lines))
        elif number and (s.get('map_draft') or {}).get('active') and (s.get('map_draft') or {}).get('stage')=='points':
            points=s['map_draft']['points'];index=int(number.group(1))-1
            if not 0<=index<len(points):raise HTTPException(400,'Choose a numbered point shown on the map')
            user(s,msg)
            await finish_map_point(s,points[index]['id'])
        elif body.selection:
            p = s.get("pending")
            sel = body.selection
            valid = (
                p
                and sel.role == p["role"]
                and any(c["id"] == sel.id for c in p["candidates"])
            )
            if not valid:
                raise HTTPException(
                    400, "That choice is no longer current. Search again."
                )
            place = places.get(sel.id)
            if not place:
                raise HTTPException(400, "Place unavailable")
            place.pop("search", None)
            s["pending"] = None
            user(s, f"{'Start at' if sel.role=='origin' else 'Go to'} {place['name']}")
            if planner.needs_points(place) and not s.get('awaiting_platform'):
                planner.prepare(s,place,sel.role,'chat')
                say(s,'Choose a numbered green point on the map for this location. Station points are entrances; park points are nearby street corners.')
            else:
                s[sel.role] = place
                await ready(s)
        elif body.itinerary_index is not None:
            if s.get("ui_mode")=="chat":user(s,msg or f"Choose route {body.itinerary_index+1}")
            if not 0 <= body.itinerary_index < len(s["journeys"]):
                raise HTTPException(400, "Invalid journey")
            s["selected"] = body.itinerary_index
            s["step"] = 0
            say(s, "Switched to that route. " + (s["journeys"][s["selected"]]["steps"][0]["text"] if s.get("ui_mode")=="chat" else "Start with the first step."))
            s["live"] = await live.for_journey(s["journeys"][s["selected"]])
        elif action == "platform":
            if not s["awaiting_platform"] or not s["origin"]:
                raise HTTPException(400, "Confirm a station first")
            user(s, "I’m off the train at " + s["origin"]["name"])
            s["awaiting_platform"] = False
            if s["origin"].get("kind") == "station":
                s["origin"]["inside_station"] = True
            await ready(s)
        elif action == "replan":
            await ready(s)
        elif action == "refresh":
            if s["journeys"]:
                s["live"] = await live.for_journey(s["journeys"][s["selected"]], step_index=s["step"])
                say(
                    s,
                    "Refreshed platform predictions and service notices. The itinerary still uses the downloaded schedule.",
                )
            else:
                say(s, "Tell me your starting point and destination first.")
        elif action == "previous":
            if s["journeys"]:
                s["step"] = max(0, s["step"] - 1)
                if s.get("ui_mode")=="chat":
                    user(s,msg or "Back");say(s,s["journeys"][s["selected"]]["steps"][s["step"]]["text"])
        elif action == "next" or low in [
            "next",
            "next step",
            "done",
            "i'm here",
            "i am here",
            "i've boarded",
            "boarded",
            "i'm at the station",
            "i’m at the station",
            "i am at the station",
            "i've boarded the right train",
            "i’ve boarded the right train",
            "i've finished this walk",
            "i’ve finished this walk",
            "i'm off at this stop",
            "i’m off at this stop",
        ]:
            user(s, msg or "Continue to the next step")
            if s["recovering"] or expired(s):
                say(s, "First, tell me where you are now so I can update your route.")
            elif s["journeys"]:
                j = s["journeys"][s["selected"]]
                s["step"] = min(s["step"] + 1, len(j["steps"]) - 1)
                say(s, j["steps"][s["step"]]["text"])
            else:
                say(s, "Let’s plan your journey first. Where are you now?")
        elif (
            action == "repeat"
            or low == "again"
            or re.search(r"\b(?:repeat|say that again)\b", low)
        ):
            user(s, msg or "Repeat this step")
            if s["recovering"] or expired(s):
                say(
                    s,
                    "Where are you now, and are you still on the train? I’ll keep your destination and plan from your confirmed location.",
                )
            elif s["journeys"]:
                say(s, s["journeys"][s["selected"]]["steps"][s["step"]]["text"])
            else:
                say(s, "Where are you now, and where would you like to go?")
        elif action == "missed" or any(
            x in low
            for x in [
                "missed",
                "wrong direction",
                "wrong train",
                "went past",
                "gone past",
            ]
        ):
            user(s, msg or "Missed it")
            s["recovering"] = True
            s["was_recovery"] = True
            s["origin"] = None
            s["origin_query"] = ""
            s["pending"] = {"role": "origin", "query": "", "candidates": []}
            s["awaiting_platform"] = not bool(
                re.search(r"missed (?:my |the |a )?train", low)
            )
            say(
                s,
                "No problem—we’ll keep your destination. Where are you now? Did you miss the train or go past your stop? Tell me the station or nearest intersection, and whether you’re still on the train.",
            )
        elif not msg:
            raise HTTPException(400, "Enter a message")
        else:
            user(s, msg)
            draft=s.get('map_draft') or {}
            if s.get('ui_mode')=='chat' and draft.get('active') and draft.get('stage')=='points':
                s['pending']={'role':draft['role'],'query':msg,'candidates':[]};draft['active']=False
            # A reply to candidate clarification is a place query, not a new journey.
            pending = s.get("pending")
            if pending and not any(
                x in low for x in ["want to", "go to", "from ", "get to", "going to"]
            ):
                role = pending["role"]
                query = re.sub(
                    r"^(?:i(?:'m| am) (?:now )?(?:at|in)|at)\s+", "", msg, flags=re.I
                )
                query = re.sub(
                    r"[,.;]?\s+(?:and )?i(?:'m| am) (?:off|still on|on) (?:the )?train.*$",
                    "",
                    query,
                    flags=re.I,
                )
                extracted, used = await model.extract(msg)
                s["model_used"] = used
                query = (
                    extracted.get(role)
                    or extracted.get("origin")
                    or extracted.get("destination")
                    or query
                )
                if any(
                    x in low
                    for x in [
                        "off the train",
                        "on the platform",
                        "missed the train",
                        "missed my train",
                    ]
                ):
                    s["awaiting_platform"] = False
                s[role] = None
                s[role + "_query"] = query
                await ready(s)
            else:
                intent, used = await model.extract(msg)
                s["model_used"] = used
                if intent["action"] == "restart":
                    ident = s["id"]
                    s = load(None)
                    s["id"] = ident
                    say(
                        s,
                        "A fresh start. Where are you now, and where would you like to go?",
                    )
                elif intent["action"] == "missed":
                    s.update(
                        recovering=True,
                        was_recovery=True,
                        origin=None,
                        origin_query="",
                        awaiting_platform=True,
                        pending={"role": "origin", "query": "", "candidates": []},
                    )
                    say(
                        s,
                        "Where are you now? Tell me the station or nearest intersection and whether you’re still on the train. I’ll keep your destination.",
                    )
                elif intent["action"] == "repeat":
                    say(
                        s,
                        (
                            s["journeys"][s["selected"]]["steps"][s["step"]]["text"]
                            if s["journeys"] and not s["recovering"] and not expired(s)
                            else "Where are you now, and where would you like to go?"
                        ),
                    )
                elif intent["action"] == "progress" and s["journeys"]:
                    if s["recovering"] or expired(s):
                        say(
                            s,
                            "First, tell me where you are now so I can update your route.",
                        )
                    else:
                        j = s["journeys"][s["selected"]]
                        s["step"] = min(s["step"] + 1, len(j["steps"]) - 1)
                        say(s, j["steps"][s["step"]]["text"])
                else:
                    oq = intent.get("origin", "").strip()
                    dq = intent.get("destination", "").strip()
                    if oq:
                        if (
                            s["journeys"]
                            and s["journeys"][s["selected"]]["steps"][s["step"]]["kind"]
                            == "ride"
                            and "off the train" not in low
                        ):
                            s["awaiting_platform"] = True
                            s["recovering"] = True
                            s["was_recovery"] = True
                        s["origin"] = None
                        s["origin_query"] = oq
                    if dq:
                        s["destination"] = None
                        s["destination_query"] = dq
                    if not oq and not dq and s.get("pending"):
                        role = s["pending"]["role"]
                        s[role + "_query"] = msg
                    if oq or dq:
                        d=s.get('map_draft') or {}
                        if d.get('active'):
                            if not oq and d.get('origin'):s['origin']=d['origin']
                            d['active']=False
                        s["journeys"] = []
                        s["live"] = None
                    if not oq and not dq and intent["action"] == "question":
                        if s["journeys"] and not s["recovering"]:
                            j = s["journeys"][s["selected"]]
                            remaining = j["steps"][s["step"] :]
                            wanted = (
                                "ride"
                                if any(
                                    x in low
                                    for x in [
                                        "get off",
                                        "how many stops",
                                        "stop to",
                                        "which stop",
                                    ]
                                )
                                else "board"
                            )
                            step = next(
                                (x for x in remaining if x["kind"] == wanted), None
                            )
                            if any(x in low for x in ["how long", "how much time"]):
                                say(
                                    s,
                                    f"The planned journey is about {j['duration']} minutes, arriving at {j['end']} on the schedule.",
                                )
                            elif step:
                                say(s, step["text"])
                            else:
                                say(s, j["steps"][s["step"]]["text"])
                        else:
                            say(
                                s,
                                "Tell me where you are and where you’d like to go in NYC.",
                            )
                    else:
                        await ready(s)
        save(s)
        return result(s)
