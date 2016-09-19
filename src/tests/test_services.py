import unittest
import services
import json

class TestDigitransitAPIService(unittest.TestCase):

    def setUp(self):
        self.digitransitAPIService = services.DigitransitAPIService()

    def test_get_stops(self):
        stops = self.digitransitAPIService.get_stops(60.203978, 24.9633573, 160)
        self.assertTrue("stops" in stops)

        all_stops = stops["stops"]
        self.assertEqual(len(all_stops), 1)

        stop = all_stops[0]
        self.assertTrue("stop" in stop)

        stop_data = stop["stop"]
        self.assertTrue("schedule" in stop_data)

        schedule = stop_data["schedule"]
        self.assertNotEqual(len(schedule), 0)


    def test_get_stops_near_coordinates(self):
        self.assertEqual('foo', 'foo')


    def test_get_busses_by_stop_id(self):
        stop = self.digitransitAPIService.get_busses_by_stop_id("HSL:1362141")
        self.assertTrue("stop_name" in stop)
        self.assertEqual(stop["stop_name"], 'Viikki')

        first = stop["schedule"][0]
        self.assertTrue("line" in first)


if __name__ == '__main__':
    unittest.main()