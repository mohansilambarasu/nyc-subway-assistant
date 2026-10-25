"""Regression tests for directions, freshness and conversation state."""

import asyncio, json, tempfile, time, unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from google.transit import gtfs_realtime_pb2 as pb
from subway import app as api, places, routing, live, walking, entrances, agent


def example_journey():
    return {
        "duration": 20,
        "transfers": 0,
        "origin": {"name": "Bryant Park"},
        "destination": {"name": "Charging Bull"},
        "legs": [],
        "steps": [
            {"kind": "walk", "text": "Walk to the station."},
            {"kind": "board", "text": "Take the train toward South Ferry."},
            {"kind": "ride", "text": "Get off at Rector St."},
        ],
    }


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.agentpatch = patch.object(agent, "choose", AsyncMock(return_value=None))
        self.agentpatch.start()
        self.tmp = tempfile.TemporaryDirectory()
        self.dbpatch = patch.object(api, "DB", Path(self.tmp.name) / "sessions.sqlite")
        self.dbpatch.start()
        self.client = TestClient(api.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.dbpatch.stop()
        self.tmp.cleanup()
        self.agentpatch.stop()

    def post(self, **kw):
        r = self.client.post("/api/chat", json=kw)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def seeded(self):
        s = api.load(None)
        s.update(
            origin={"id": "o", "name": "Bryant Park", "lat": 40.75, "lon": -73.98},
            destination={
                "id": "d",
                "name": "Charging Bull",
                "lat": 40.70,
                "lon": -74.01,
            },
            journeys=[example_journey()],
        )
        api.save(s)
        return s

    def test_number_reply_selects_current_station(self):
        s = self.seeded()
        s["origin"] = None
        station = {"id": "s123", "name": "Canal St", "kind": "station"}
        s["pending"] = {"role": "origin", "candidates": [station]}
        s["awaiting_platform"] = True
        api.save(s)
        with patch.object(places, "get", return_value=station):
            r = self.post(session_id=s["id"], message="1")
        self.assertEqual(r["origin"]["id"], "s123")
        self.assertTrue(r["awaiting_platform"])

    def test_exact_landmarks_need_no_extra_selection(self):
        s = api.load(None)
        s.update(origin_query="Times Square", destination_query="Charging Bull")
        api.save(s)
        def search(query, **kw):
            return [{"id": query, "name": query, "kind": "attraction", "score": 185}]
        with patch.object(places, "search", side_effect=search), patch.object(routing, "plan", new=AsyncMock(return_value=[example_journey()])), patch.object(live, "for_journey", new=AsyncMock(return_value=None)):
            asyncio.run(api.ready(s))
        self.assertIsNone(s["pending"])
        self.assertEqual(s["destination"]["name"], "Charging Bull")
        self.assertTrue(s["journeys"])

    def test_repeat_does_not_advance(self):
        s = self.seeded()
        r = self.post(session_id=s["id"], message="Could you repeat that please?")
        self.assertEqual(r["step"], 0)
        self.assertEqual(r["messages"][-1]["text"], "Walk to the station.")

    def test_missed_keeps_destination_and_pauses(self):
        s = self.seeded()
        r = self.post(session_id=s["id"], message="I missed my stop")
        self.assertEqual(r["destination"]["name"], "Charging Bull")
        self.assertIsNone(r["origin"])
        self.assertTrue(r["recovering"])
        self.assertTrue(r["awaiting_platform"])
        self.assertIn("still on the train", r["messages"][-1]["text"])

    def test_selection_cannot_supply_arbitrary_coordinates(self):
        s = self.seeded()
        r = self.client.post(
            "/api/chat",
            json={
                "session_id": s["id"],
                "selection": {"role": "origin", "id": "n373069793"},
            },
        )
        self.assertEqual(r.status_code, 400)

    def test_failed_replan_does_not_leave_old_route_active(self):
        s = self.seeded()
        with patch.object(routing, "plan", AsyncMock(side_effect=routing.RoutingError("No route available"))):
            asyncio.run(api.ready(s))
        self.assertEqual(s["journeys"], [])
        self.assertEqual(s["routing_error"], "No route available")
        self.assertIsNone(s["live"])

    def test_reset_clears_destination(self):
        s = self.seeded()
        r = self.post(session_id=s["id"], action="reset")
        self.assertIsNone(r["destination"])
        self.assertEqual(r["journeys"], [])

    def test_progress_and_previous(self):
        s = self.seeded()
        r = self.post(session_id=s["id"], action="next")
        self.assertEqual(r["step"], 1)
        r = self.post(session_id=s["id"], action="previous")
        self.assertEqual(r["step"], 0)

    def test_expired_route_cannot_advance(self):
        s = self.seeded()
        s["journeys"][0]["legs"] = [{"end_ms": (time.time() - 3600) * 1000}]
        api.save(s)
        r = self.post(session_id=s["id"], action="next")
        self.assertEqual(r["step"], 0)
        self.assertTrue(r["journey_expired"])
        self.assertIn("where you are now", r["messages"][-1]["text"])

    def test_model_progress_cannot_advance_expired_journey(self):
        s = self.seeded()
        s["journeys"][0]["legs"] = [{"end_ms": (time.time()-3600)*1000}]
        api.save(s)
        with patch.object(api.model, "extract", AsyncMock(return_value=({"action": "progress"}, True))):
            r = self.post(session_id=s["id"], message="Ready to move along")
        self.assertEqual(r["step"], 0)

    def test_local_street_map_has_real_segments_and_bounded_requests(self):
        result = self.client.get('/api/map/streets?south=40.75&north=40.755&west=-73.99&east=-73.98')
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json()['segments'])
        self.assertEqual(self.client.get('/api/map/streets?south=0&north=90&west=-180&east=180').status_code, 400)

    def test_cross_origin_rejected(self):
        r = self.client.post(
            "/api/chat",
            json={"message": "hi"},
            headers={"Origin": "https://example.com"},
        )
        self.assertEqual(r.status_code, 403)

    def test_recovery_requires_platform_confirmation(self):
        s = self.seeded()
        s.update(awaiting_platform=True, recovering=True)
        api.save(s)
        with patch.object(
            routing, "plan", AsyncMock(return_value=[example_journey()])
        ) as planner, patch.object(live, "for_journey", AsyncMock(return_value={})):
            r = self.post(session_id=s["id"], message="I'm at Canal St")
            self.assertIsNotNone(r["pending"])
            candidate = r["pending"]["candidates"][0]
            r = self.post(
                session_id=s["id"], selection={"role": "origin", "id": candidate["id"]}
            )
            self.assertTrue(r["awaiting_platform"])
            planner.assert_not_called()
            r = self.post(session_id=s["id"], action="platform")
            planner.assert_awaited_once()
            self.assertTrue(planner.await_args.args[0]["inside_station"])
            self.assertFalse(r["recovering"])


class DataTests(unittest.TestCase):
    def test_same_named_stations_have_distinct_street_context(self):
        candidates = places.search("Canal St")
        context = {p["id"]: p["context"] for p in candidates}
        self.assertNotEqual(context["s639"], context["sR23"])
        self.assertIn("Near", context["sR23"])

    def test_bull_alias_is_landmark_not_station(self):
        self.assertEqual(
            places.search("Wall Street to the raging bull")[0]["name"], "Charging Bull"
        )

    def test_unknown_shop_not_invented(self):
        self.assertEqual(places.search("waffles and dingles"), [])

    def test_museum_search(self):
        self.assertIn(
            "Metropolitan Museum",
            places.search("Metropolitan Museum of Art")[0]["name"],
        )

    def test_directional_stop_is_preserved(self):
        it = {
            "duration": 600,
            "startTime": 1792608000000,
            "endTime": 1792608600000,
            "walkDistance": 0,
            "numberOfTransfers": 0,
            "legs": [
                {
                    "mode": "SUBWAY",
                    "duration": 600,
                    "distance": 1000,
                    "startTime": 1792608000000,
                    "endTime": 1792608600000,
                    "transitLeg": True,
                    "route": {"gtfsId": "MTA:1", "shortName": "1"},
                    "trip": {"gtfsId": "MTA:x", "tripHeadsign": "South Ferry"},
                    "from": {
                        "name": "Times Square",
                        "lat": 40.75,
                        "lon": -73.98,
                        "stop": {"gtfsId": "MTA:127S"},
                    },
                    "to": {"name": "Rector St", "lat": 40.70, "lon": -74.01},
                    "intermediateStops": [
                        {"name": "34 St", "lat": 40.74, "lon": -73.99}
                    ],
                }
            ],
        }
        r = routing.normalize(it, {"name": "Start"}, {"name": "Finish"})
        self.assertEqual(r["legs"][0]["stop_id"], "127S")
        self.assertIn("South Ferry", r["steps"][0]["title"])
        self.assertIn("34 St", r["steps"][0]["text"])

    def test_polyline_decoder(self):
        self.assertEqual(
            walking.decode("_p~iF~ps|U_ulLnnqC_mqNvxq`@"),
            [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)],
        )


class DirectionTests(unittest.TestCase):
    def test_manhattan_direction_uses_trip_stops_and_terminus(self):
        leg = {
            "from": {"lat": 40.755, "lon": -73.987, "stop": {"gtfsId": "MTA:R16S"}},
            "to": {"lat": 40.71, "lon": -74.01},
            "intermediateStops": [{"lat": 40.75, "lon": -73.988}],
            "trip": {"stops": [{"gtfsId": "MTA:R45S"}]},
        }
        with patch.object(entrances, "boroughs", return_value={"R16": "M", "R45": "B"}):
            self.assertEqual(entrances.direction(leg), "Downtown · Brooklyn-bound")

    def test_borough_is_not_inferred_from_a_direction_number(self):
        leg = {
            "from": {"lat": 40.755, "lon": -73.987, "stop": {"gtfsId": "MTA:R16S"}},
            "to": {"lat": 40.71, "lon": -74.01},
            "trip": {"directionId": "0", "stops": []},
        }
        with patch.object(entrances, "boroughs", return_value={}):
            self.assertEqual(entrances.direction(leg), "")


class LiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_transfer_predictions_use_current_boarding_leg(self):
        first = {"transit": True, "stop_id": "R16S", "route_id": "R", "from": "Times Sq"}
        second = {"transit": True, "stop_id": "A32S", "route_id": "A", "from": "Transfer station"}
        j = {"legs": [first, {"transit": False}, second], "steps": [{"leg": 0}, {"leg": 2}]}
        with patch.object(live, "departures", AsyncMock(return_value={"status": "live"})) as dep, patch.object(live, "alerts", AsyncMock(return_value={"items": []})):
            result = await live.for_journey(j, step_index=1)
        dep.assert_awaited_once_with(second)
        self.assertEqual(result["boarding"]["stop_id"], "A32S")

    async def test_expired_schedule_is_rejected(self):
        from datetime import datetime

        with patch.object(
            routing,
            "feed_info",
            return_value={"feed_start_date": "20200101", "feed_end_date": "20200131"},
        ):
            with self.assertRaises(routing.RoutingError):
                await routing.plan(
                    {"name": "a", "lat": 40.75, "lon": -73.98},
                    {"name": "b", "lat": 40.70, "lon": -74.01},
                    datetime(2026, 10, 21, tzinfo=routing.TZ),
                )

    async def test_stale_predictions_are_hidden(self):
        f = pb.FeedMessage()
        f.header.gtfs_realtime_version = "2.0"
        f.header.timestamp = int(time.time() - 600)
        with patch.object(live, "fetch", AsyncMock(return_value=f)):
            r = await live.departures({"route_id": "1", "stop_id": "127S"})
            self.assertEqual(r["status"], "stale")
            self.assertEqual(r["arrivals"], [])

    async def test_cancelled_and_wrong_direction_not_shown(self):
        f = pb.FeedMessage()
        f.header.gtfs_realtime_version = "2.0"
        f.header.timestamp = int(time.time())
        for i, (stop, cancel) in enumerate(
            [("127N", False), ("127S", True), ("127S", False)]
        ):
            e = f.entity.add()
            e.id = str(i)
            t = e.trip_update
            t.trip.route_id = "1"
            t.trip.trip_id = str(i)
            if cancel:
                t.trip.schedule_relationship = pb.TripDescriptor.CANCELED
            s = t.stop_time_update.add()
            s.stop_id = stop
            s.arrival.time = int(time.time() + 300)
        with patch.object(live, "fetch", AsyncMock(return_value=f)):
            r = await live.departures({"route_id": "1", "stop_id": "127S"})
            self.assertEqual(len(r["arrivals"]), 1)
            self.assertEqual(r["arrivals"][0]["trip_id"], "2")


if __name__ == "__main__":
    unittest.main()
