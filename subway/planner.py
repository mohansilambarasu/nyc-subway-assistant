"""User-confirmed map locations. Coordinates come from indexed data or an explicit map click."""
import math, sqlite3, uuid
from . import places, entrances


def needs_points(p):
    return p.get('kind') == 'station' or p.get('kind') in {'park','neighbourhood','suburb'} or p['name'].lower().endswith(' park')


def point_options(p):
    if p.get('kind') == 'station':
        entries=entrances.inventory().get(p['id'][1:], [])
        unique=[]
        for e in entries:
            if any(places.distance(e,x)<8 for x in unique):
                continue
            unique.append(e)
        return [{'id':uuid.uuid4().hex,'name':entrances.label(e),'lat':e['lat'],'lon':e['lon'],'kind':'entrance','base_place_name':p['name'],'parent_station':p['id']} for e in unique[:24]]
    if needs_points(p):
        radius=2200 if p['name'].lower()=='central park' else 450
        with sqlite3.connect(places.DB) as db:
            db.row_factory=sqlite3.Row
            rows=[dict(r) for r in db.execute("SELECT id,name,lat,lon,kind FROM places WHERE kind='intersection' AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?", (p['lat']-radius/111195,p['lat']+radius/111195,p['lon']-radius/84000,p['lon']+radius/84000))]
        nearby=sorted((x for x in rows if places.distance(p,x)<=radius),key=lambda x:places.distance(p,x))
        sectors={}
        for x in nearby:
            sector=int((math.atan2(x['lat']-p['lat'],(x['lon']-p['lon'])*.76)+math.pi)/(math.pi/4))%8
            if sector not in sectors:sectors[sector]=x
        return [{**x,'id':uuid.uuid4().hex,'source_id':x['id'],'base_place_name':p['name'],'kind':'intersection'} for x in sectors.values()]
    return [{**p,'id':uuid.uuid4().hex,'source_id':p['id'],'base_place_name':p['name']}]


def new_draft(s, mode='interface'):
    s['map_draft']={'active':True,'role':'origin','stage':'search','origin':None,'destination':None,'base':None,'points':[],'mode':mode}
    return s['map_draft']


def prepare(s,p,role,mode='interface'):
    d=s.get('map_draft')
    if not d or not d.get('active') or d.get('mode')!=mode:
        d=new_draft(s,mode)
        if mode=='chat':d.update(origin=s.get('origin'),destination=s.get('destination'))
    d[role]=None
    d.update(role=role,stage='points',base={k:v for k,v in p.items() if k!='search'},points=point_options(p))
    return d


def select_point(s,ident):
    d=s.get('map_draft') or {}
    p=next((p for p in d.get('points',[]) if p['id']==ident),None)
    if not d.get('active') or d.get('stage')!='points' or not p:
        raise ValueError('That point is no longer current. Select the place again.')
    role=d['role'];d[role]={k:v for k,v in p.items() if k!='search'}
    d[role]['point_confirmed']=True
    d['last_selected_role']=role
    if role=='origin':d.update(role='destination',stage='review' if d.get('destination') else 'search',base=None,points=[])
    else:d.update(role='destination' if d.get('origin') else 'origin',stage='review' if d.get('origin') else 'search',base=None,points=[])
    return role,p,d.get('mode','interface')
