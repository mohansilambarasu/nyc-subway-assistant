import tempfile,unittest
from pathlib import Path
from unittest.mock import patch,AsyncMock
from fastapi.testclient import TestClient
from subway import app as api,places

class IndependentModesTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.db=patch.object(api,'DB',Path(self.tmp.name)/'sessions.sqlite');self.db.start();self.client=TestClient(api.app);self.client.__enter__()
 def tearDown(self):
  self.client.__exit__(None,None,None);self.db.stop();self.tmp.cleanup()
 def chat(self,s=None,**kw):return self.client.post('/api/chat',json={'ui_mode':'chat','session_id':s and s['session_id'],**kw})
 def map(self,s=None,**kw):return self.client.post('/api/planner',json={'session_id':s and s['session_id'],**kw})
 def test_separate_sessions_and_mode_guard(self):
  chat=self.chat(message='Hi').json();before=api.load(chat['session_id'])
  m=self.map(action='begin').json()
  self.assertNotEqual(chat['session_id'],m['session_id'])
  self.assertEqual(api.load(chat['session_id']),before)
  self.assertEqual(self.map(chat,action='begin').status_code,409)
  self.assertEqual(self.chat(m,message='Hi').status_code,409)
  self.assertNotIn('click',chat['messages'][-1]['text'].lower())
 def test_all_place_choices_are_text_with_numbered_reply(self):
  s=api.load(None);s['ui_mode']='chat';s['map_draft']={'active':True,'mode':'chat','role':'origin','stage':'points','base':{'name':'Bryant Park'},'points':[{'id':'point1','name':'6th Avenue & West 42nd Street','lat':40.75,'lon':-73.98}],'origin':None,'destination':None}
  api.say(s,'Choose a green point on the map');api.save(s)
  self.assertIn('1. 6th Avenue',s['messages'][-1]['text']);self.assertNotIn('on the map',s['messages'][-1]['text'])
  r=self.chat({'session_id':s['id']},message='1').json()
  self.assertEqual(r['origin']['name'],'6th Avenue & West 42nd Street');self.assertIn('Where would you like',r['messages'][-1]['text'])
 def test_map_recovery_uses_picker_and_off_train_confirmation(self):
  s=api.load(None);s.update(ui_mode='map',destination={'id':'dest','name':'Charging Bull','lat':40.70,'lon':-74.01});api.save(s);ref={'session_id':s['id']}
  m=self.map(ref,action='recover').json();self.assertEqual(m['map_draft']['destination']['name'],'Charging Bull')
  p={'id':'cafe','name':'Cafe','kind':'cafe','lat':40.75,'lon':-73.98}
  with patch.object(places,'get',return_value=p):m=self.map(m,action='place',role='origin',id='cafe').json()
  self.assertFalse(m['map_draft']['active']);self.assertTrue(m['awaiting_platform']);self.assertTrue(m['recovering'])
 def test_chat_reset_retains_chat_binding(self):
  c=self.chat(message='Hi').json();c=self.chat(c,action='reset').json();self.assertEqual(c['ui_mode'],'chat')
 def test_typed_route_choice_and_back_reply_without_buttons(self):
  s=api.load(None);s['ui_mode']='chat'
  j={'steps':[{'kind':'walk','text':'Walk along Broadway.'},{'kind':'board','text':'Board the downtown train.'}],'legs':[]}
  s.update(journeys=[j,j],selected=0,step=1);api.save(s);ref={'session_id':s['id']}
  with patch.object(api.live,'for_journey',AsyncMock(return_value=None)):
   c=self.chat(ref,message='route 2').json()
  self.assertEqual(c['selected'],1);self.assertEqual(c['step'],0)
  self.assertEqual(c['messages'][-2]['text'],'route 2')
  c=self.chat(c,message='next').json();self.assertEqual(c['step'],1)
  c=self.chat(c,message='back').json();self.assertEqual(c['step'],0);self.assertEqual(c['messages'][-1]['text'],'Walk along Broadway.')
