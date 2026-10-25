import unittest
from unittest.mock import AsyncMock, patch
from subway import agent, model, app


def place(name):
    return {"id": name, "name": name, "lat":40.75,"lon":-73.98,"kind":"attraction","score":185}


class AgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_location_only_observes_nearby_then_asks_destination(self):
        s=app.load(None);s['origin_query']='Times Square'
        with patch.object(agent,'choose',AsyncMock(side_effect=['search_origin','nearby_stations','respond'])) as choose, patch.object(agent.places,'search',return_value=[place('Times Square')]), patch.object(agent,'nearby',return_value=[{**place('42 St'),'distance_m':100}]):
            text=await agent.run(s)
        self.assertEqual([t['tool'] for t in s['agent_trace']],['search_origin','nearby_stations'])
        self.assertIn('tool_results',choose.await_args_list[1].args[0])
        self.assertEqual(choose.await_args_list[1].args[0]['tool_results'][0]['result']['confirmed'],'Times Square')
        self.assertIsNone(s['destination'])
        self.assertEqual(s['pending']['role'],'destination')
        self.assertIn('Where would you like to go?',text)
        self.assertEqual(s['agent_mode'],'local_agent')

    async def test_model_can_choose_order_and_routes_are_tool_grounded(self):
        s=app.load(None);s.update(origin_query='Times Square',destination_query='Charging Bull')
        def route(walk):
            return {'duration':30,'origin':place('Times Square'),'destination':place('Charging Bull'),'legs':[{'transit':False,'distance':walk,'duration':5},{'transit':True,'line':'R','from':'Times Sq','to':'Rector St','headsign':'Bay Ridge'}]}
        decisions=['search_destination','search_origin','plan_routes','nearby_stations','check_service','respond']
        with patch.object(agent,'choose',AsyncMock(side_effect=decisions)), patch.object(agent.places,'search',side_effect=lambda q,**kw:[place(q)]), patch.object(agent,'nearby',return_value=[]), patch.object(agent.routing,'plan',AsyncMock(return_value=[route(500),route(200)])), patch.object(agent.live,'for_journey',AsyncMock(return_value={'departures':{'status':'live'},'alerts':{'items':[]}})):
            text=await agent.run(s)
        self.assertEqual([t['tool'] for t in s['agent_trace']],decisions[:-1])
        self.assertEqual(s['journeys'][0]['legs'][0]['distance'],200)
        self.assertIn('Rector St',text)
        self.assertNotIn('invented',text)

    async def test_ambiguity_stops_before_planning(self):
        s=app.load(None);s.update(origin_query='Canal St',destination=place('Charging Bull'))
        with patch.object(agent,'choose',AsyncMock(side_effect=['search_origin','respond'])), patch.object(agent.places,'search',return_value=[{**place('Canal St'),'kind':'station','context':'1 train'}]), patch.object(agent.routing,'plan',AsyncMock()) as plan:
            await agent.run(s)
        plan.assert_not_awaited();self.assertEqual(s['pending']['role'],'origin')

    async def test_unavailable_model_is_explicit_fallback(self):
        s=app.load(None);s.update(origin=place('Times Square'))
        with patch.object(agent,'choose',AsyncMock(return_value=None)), patch.object(agent,'nearby',return_value=[]):
            await agent.run(s)
        self.assertEqual(s['agent_mode'],'workflow_fallback')

    async def test_illegal_tool_cannot_skip_missing_origin(self):
        s=app.load(None)
        with patch.object(agent,'choose',AsyncMock(return_value='plan_routes')), patch.object(agent.routing,'plan',AsyncMock()) as plan:
            await agent.run(s)
        plan.assert_not_awaited();self.assertEqual(s['pending']['role'],'origin')

    def test_example_destination_cannot_be_invented(self):
        self.assertFalse(model.stated_place('Charging Bull',"Hey I'm at Times Square"))
        self.assertTrue(model.stated_place('Times Square',"Hey I'm at Times Square"))
        self.assertTrue(model.stated_place('Charging Bull','Take me to the raging bull'))
        self.assertTrue(model.stated_place('Metropolitan Museum of Art','I want to go to the Met'))
