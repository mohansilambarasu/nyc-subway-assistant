"""Isolated, real-service Map workflow QA. Does not reuse browser/user sessions."""
import json,time
from pathlib import Path
import httpx
c=httpx.Client(base_url='http://127.0.0.1:8765',timeout=180)
checks=[]
def check(name,condition):
 checks.append({'test':name,'status':'pass' if condition else 'FAIL'});assert condition,name
 print('PASS',name,flush=True)
def call(path,s=None,**kw):
 r=c.post(path,json={'session_id':s and s['session_id'],**kw});r.raise_for_status();return r.json()
def picker(s,role,p):
 s=call('/api/planner',s,action='place',role=role,id=p['id'])
 return call('/api/planner',s,action='point',id=s['map_draft']['points'][0]['id']) if s['map_draft']['stage']=='points' else s
def find(q):return c.get('/api/map/search',params={'q':q}).json()['places']
try:
 h=c.get('/api/health').json();check('Local model and routing ready',h['model'] and h['router'])
 joe=find("Joe's Pizza");check('Multiple cafe branches distinguishable',len(joe)>1 and len({p.get('address') for p in joe})>1)
 check('Unknown place returns empty results',not find('zzzxqvnonexistentcafeyyy'))
 check('One-character query waits for more input',not find('J'))
 origin=next(p for p in joe if p.get('address')=='1435 Broadway');dest=next(p for p in find('Charging Bull') if p['name']=='Charging Bull')
 check('Street address search resolves',bool(find('1435 Broadway')))
 chat=call('/api/chat',ui_mode='chat',message='Hi');chat_before=c.get('/api/session/'+chat['session_id']).json()
 s=call('/api/planner',action='begin');check('Chat and Map session IDs differ',s['session_id']!=chat['session_id'])
 check('Incomplete submit rejected',c.post('/api/planner',json={'session_id':s['session_id'],'action':'submit'}).status_code==400)
 s=picker(s,'destination',dest);check('Destination-first asks for start',s['map_draft']['role']=='origin' and s['map_draft']['stage']=='search')
 station=next(p for p in find('Times Sq-42 St') if p['kind']=='station')
 s=call('/api/planner',s,action='place',role='origin',id=station['id']);check('Station offers multiple recorded entrances',len(s['map_draft']['points'])>1 and all(p['kind']=='entrance' for p in s['map_draft']['points']))
 park=next(p for p in find('Bryant Park') if p['name']=='Bryant Park')
 oldpoint=s['map_draft']['points'][0]['id'];s=call('/api/planner',s,action='place',role='origin',id=park['id'])
 check('Park offers real street corners',len(s['map_draft']['points'])>1 and all(p['kind']=='intersection' for p in s['map_draft']['points']))
 check('Stale point rejected',c.post('/api/planner',json={'session_id':s['session_id'],'action':'point','id':oldpoint}).status_code==400)
 check('Out-of-NYC pin rejected',c.post('/api/planner',json={'session_id':s['session_id'],'action':'pin','lat':0,'lon':0}).status_code==400)
 s=call('/api/planner',s,action='pin',lat=park['lat'],lon=park['lon']);check('Explicit pin awaits confirmation',s['map_draft']['points'][0]['kind']=='user_pin' and s['map_draft']['origin'] is None)
 s=picker(s,'origin',origin);check('Both confirmed points enable review',s['map_draft']['stage']=='review' and bool(s['map_draft']['origin']) and bool(s['map_draft']['destination']))
 s=call('/api/planner',s,action='edit',role='origin');check('Editing invalidates only selected endpoint',s['map_draft']['origin'] is None and s['map_draft']['destination'] is not None)
 s=picker(s,'origin',origin);s=call('/api/planner',s,action='submit');check('Real agent produces a subway journey',bool(s['journeys']) and s['agent_mode']=='local_agent' and any(l['transit'] for l in s['journeys'][0]['legs']))
 check('Route has walking and train geometry',all(l.get('geometry') for l in s['journeys'][0]['legs']))
 check('Boarding directions and first stop supplied',all(l.get('headsign') and l.get('direction') and l.get('next_stop') for l in s['journeys'][0]['legs'] if l['transit']))
 if len(s['journeys'])>1:
  s=call('/api/chat',s,ui_mode='map',itinerary_index=1);check('Alternate route resets progress',s['selected']==1 and s['step']==0)
 previous_step=s['step'];s=call('/api/chat',s,ui_mode='map',action='repeat');check('Repeat does not advance',s['step']==previous_step)
 s=call('/api/chat',s,ui_mode='map',action='next');check('Next advances one step',s['step']==1)
 s=call('/api/chat',s,ui_mode='map',action='previous');check('Back returns to previous step',s['step']==0)
 s=call('/api/chat',s,ui_mode='map',action='refresh');check('Live data belongs to selected boarding stop',not s.get('live',{}).get('boarding') or s['live']['boarding']['stop_id']==next(l['stop_id'] for l in s['journeys'][s['selected']]['legs'] if l['transit']))
 count=len(s['journeys'][s['selected']]['steps'])
 for _ in range(count+1):s=call('/api/chat',s,ui_mode='map',action='next')
 check('Full journey reaches arrival without overflowing',s['step']==count-1 and s['journeys'][s['selected']]['steps'][s['step']]['kind']=='arrive')
 restored=c.get('/api/session/'+s['session_id']).json();check('Refresh restores route and progress',restored['selected']==s['selected'] and restored['step']==s['step'])
 previous=s['journeys'];s=call('/api/planner',s,action='begin');s=call('/api/planner',s,action='cancel');check('Cancel preserves completed journey',s['journeys']==previous)
 s=call('/api/planner',s,action='recover');check('Recovery keeps destination',s['map_draft']['destination']['name']=='Charging Bull')
 canal=next(p for p in find('Canal St') if p['kind']=='station');s=picker(s,'origin',canal)
 check('Recovery waits for off-train confirmation',s['awaiting_platform'] and s['recovering'] and not s['map_draft']['active'])
 s=call('/api/chat',s,ui_mode='map',action='platform');check('Confirmed recovery replans',bool(s['journeys']) and not s['awaiting_platform'] and not s['recovering'])
 check('Map workflow never changes Chat',c.get('/api/session/'+chat['session_id']).json()==chat_before)
 before_map=c.get('/api/session/'+s['session_id']).json();chat=call('/api/chat',chat,ui_mode='chat',message='Help');check('Chat workflow never changes Map',c.get('/api/session/'+s['session_id']).json()==before_map)
except Exception as e:
 checks.append({'test':'Workflow execution','status':'FAIL','detail':str(e)});raise
finally:
 report={'tested_at_unix':time.time(),'checks':checks,'passed':sum(x['status']=='pass' for x in checks),'failed':sum(x['status']=='FAIL' for x in checks)}
 (Path(__file__).resolve().parents[1]/'docs/map-qa-results.json').write_text(json.dumps(report,indent=2))
 print(json.dumps({k:v for k,v in report.items() if k!='checks'}),flush=True)
