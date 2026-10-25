import tempfile,unittest
from pathlib import Path
from unittest.mock import patch,AsyncMock
from fastapi.testclient import TestClient
from subway import app as api,planner,places,agent

class MapPlannerTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.db=patch.object(api,'DB',Path(self.tmp.name)/'sessions.sqlite');self.db.start();self.client=TestClient(api.app);self.client.__enter__()
 def tearDown(self):
  self.client.__exit__(None,None,None);self.db.stop();self.tmp.cleanup()
 def post(self,s=None,**data):
  return self.client.post('/api/planner',json={'session_id':s and s['session_id'],**data})
 def test_confirmed_points_required_and_unoffered_point_rejected(self):
  s=self.post(action='begin').json()
  self.assertEqual(self.post(s,action='submit').status_code,400)
  p={'id':'p1','name':'Cafe','kind':'cafe','lat':40.75,'lon':-73.98}
  with patch.object(places,'get',return_value=p):s=self.post(s,action='place',role='origin',id='p1').json()
  self.assertEqual(self.post(s,action='point',id='fake').status_code,400)
  self.assertEqual(s['map_draft']['points'],[])
  self.assertTrue(s['map_draft']['origin']['point_confirmed']);self.assertEqual(s['map_draft']['stage'],'search')
  with patch.object(places,'get',return_value={**p,'id':'p2','name':'Museum'}):s=self.post(s,action='place',role='destination',id='p2').json()
  self.assertEqual(s['map_draft']['stage'],'review')
  s=self.post(s,action='edit',role='origin').json()
  self.assertIsNone(s['map_draft']['origin']);self.assertEqual(self.post(s,action='submit').status_code,400)
 def test_station_points_are_recorded_entrances_and_deduplicated(self):
  with patch.object(planner.entrances,'inventory',return_value={'123':[{'lat':40.75,'lon':-73.98},{'lat':40.75,'lon':-73.98},{'lat':40.751,'lon':-73.98}]}),patch.object(planner.entrances,'label',return_value='Recorded stair entrance'):
   points=planner.point_options({'id':'s123','name':'Station','kind':'station'})
  self.assertEqual(len(points),2);self.assertTrue(all(p['kind']=='entrance' for p in points))
 def test_manual_pin_validation(self):
  s=self.post(action='begin').json()
  self.assertEqual(self.post(s,action='pin',lat=40.75,lon=-73.98).status_code,400)
  p={'id':'s123','name':'Station','kind':'station','lat':40.75,'lon':-73.98}
  with patch.object(places,'get',return_value=p),patch.object(planner,'point_options',return_value=[]):s=self.post(s,action='place',role='origin',id='s123').json()
  self.assertEqual(self.post(s,action='pin',lat=0,lon=0).status_code,400)
  s=self.post(s,action='pin',lat=40.7501,lon=-73.9801).json()
  self.assertEqual(s['map_draft']['points'][0]['kind'],'user_pin')
 def test_greeting_preserves_active_picker_without_model(self):
  s=self.post(action='begin').json()
  with patch.object(api.model,'extract',AsyncMock()) as model:
   r=self.client.post('/api/chat',json={'session_id':s['session_id'],'message':'Hi!'}).json()
  model.assert_not_awaited();self.assertTrue(r['map_draft']['active']);self.assertIn('Start journey',r['messages'][-1]['text'])
 def test_broad_park_requires_point_before_route(self):
  s=api.load(None);s['origin_query']='Bryant Park'
  p={'id':'park','name':'Bryant Park','kind':'park','score':185,'lat':40.7536,'lon':-73.9832}
  import asyncio
  with patch.object(agent,'choose',AsyncMock(side_effect=['search_origin','respond'])),patch.object(places,'search',return_value=[p]),patch.object(planner,'point_options',return_value=[{**p,'id':'corner','kind':'intersection'}]),patch.object(agent.routing,'plan',AsyncMock()) as route:
   asyncio.run(agent.run(s))
  route.assert_not_awaited();self.assertIsNone(s['origin']);self.assertEqual(s['map_draft']['stage'],'points')
 def test_destination_first_prompts_for_origin(self):
  s=self.post(action='begin').json()
  p={'id':'dest','name':'Museum','kind':'museum','lat':40.75,'lon':-73.98}
  with patch.object(places,'get',return_value=p):s=self.post(s,action='place',role='destination',id='dest').json()
  self.assertEqual(s['map_draft']['role'],'origin');self.assertEqual(s['map_draft']['stage'],'search')
  self.assertEqual(self.post(s,action='submit').status_code,400)
 def test_cancel_preserves_previous_journey(self):
  s=api.load(None);s.update(ui_mode='map',journeys=[{'steps':[{'kind':'arrive'}],'legs':[]}],step=0,selected=0);api.save(s)
  r=self.post({'session_id':s['id']},action='begin').json();r=self.post(r,action='cancel').json()
  self.assertEqual(len(r['journeys']),1);self.assertEqual(r['step'],0);self.assertIsNone(r['map_draft'])
