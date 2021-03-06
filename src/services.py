import datetime
import requests
import json
import math
import paho.mqtt.publish as publish
from itertools import groupby

import thread_helper

import csv
import io

class DigitransitAPIService:
    def __init__(self, db, push_notification_service, hsl_api_url):
        self.url = hsl_api_url
        self.headers = {'Content-Type': 'application/graphql'}
        self.db = db
        self.MQTT_host = "epsilon.fixme.fi"
        self.push_notification_service = push_notification_service

    def get_stops(self, lat, lon, radius):
        """
        Gets info from all stops within given radius of the point specified by lat and lon, including all the busses
        going to pass those stops.

        See: get_stops_near_coordinates, get_busses_by_stop_id

        :param lat: latitude
        :param lon: longitude
        :param radius: radius
        :return: dict containing info of all the stops within radius and busses scheduled to pass those stops
        """
        data = {}
        stops = []
        stop_ids = self.get_stops_near_coordinates(lat, lon, radius)

        for stop_id in stop_ids:
            stops.append({"stop": self.get_busses_by_stop_id(stop_id['stop_id'], stop_id['distance'])})

        data["stops"] = stops
        return data

    def get_stops_with_beacon(self, major, minor):
        """
        Gets stop info with iBeacons identifying major and minor values, including busses that are going to pass given
        stop. Uses csv file provided in 'https://dev.hsl.fi/tmp/stop_beacons.csv' to match major and minor to stop code.

        See: get_stops_by_code

        :param major: identifies iBeacon together with minor
        :param minor: identifies iBeacon together with major
        :return: dict containing info of the stop including busses that are scheduled to pass it
        """
        beacons = {}
        beacon_csv = requests.get("https://dev.hsl.fi/tmp/stop_beacons.csv").text
        reader = csv.DictReader(io.StringIO(beacon_csv))
        for beacon in reader:
            beacons[(int(beacon['Major']), int(beacon['Minor']))] = beacon

        beacon = beacons.get((major, minor))
        if not beacon: # XXX unknown beacon, fake a location for now
            beacon_coords = {'lat': 60.203978, 'lon': 24.9633573}
            return self.get_stops(beacon_coords.get('lat'), beacon_coords.get('lon'), 160)
        else:
            stops = self.get_stops_by_code(beacon['Stop'])
            stop = stops['stops'][0] # XXX calculate average if multiple stops?
            return self.get_stops(stop['lat'], stop['lon'], 1)


    def get_busses_with_beacon(self, major_minor):
        """
        Gets info of all the busses related to given list of majors and minors. Uses csv file provided in
        'https://dev.hsl.fi/tmp/bus_beacons.csv' to match major and minors to bus code. Then gets that busses route,
        direction and time data from 'https://dev.hsl.fi/hfp/journey/bus/{bus_code}/'. Finally fetches trip info with
        fetch_single_fuzzy_trip using that data.

        See: fetch_single_fuzzy_trip

        :param major_minor: List of the following form: [ { "major":"X", "minor":"Y"},... ]
        :return: dict containing bus info including major, minor and EITHER trip_id, direction, line OR error
        """
        result = dict()
        result['vehicles'] = []

        beacons = dict()

        csvdata = requests.get('http://dev.hsl.fi/tmp/bus_beacons.csv').text
        reader = csv.DictReader(io.StringIO(csvdata))

        for row in reader:
            beacons[(row['Major'], row['Minor'])] = row

        for mm in major_minor:
            if mm.get('major') == 12345 and mm.get('minor') == 12345:
                result['vehicles'].append(json.loads('''{"major":12345,
                                   "minor":12345,
                                   "trip_id":"1055_20161031_Ma_2_1359",
                                   "destination":"Rautatientori via Kalasatama(M)",
                                   "line":"55",
                                   "vehicle_type":3}'''))
                continue

            row = beacons.get((mm['major'], mm['minor']))

            if not row:
                result['vehicles'].append(json.loads(('''{"error":"Invalid major and/or minor", "major":%d, "minor":%d}''') % (mm['major'], mm['minor'])))
            else:
                if not row['Vehicle']:
                    continue
                json_data = json.loads(requests.get(('https://dev.hsl.fi/hfp/journey/bus/%s/') % (row['Vehicle'])).text)

                # The above API returns empty json object if there is not available realtime data of the bus
                if json_data == json.loads("{}"):
                    result['vehicles'].append(json.loads(('''{"error":"No realtime data from the bus", "major":%d, "minor":%d}''') % (mm['major'], mm['minor'])))
                    continue

                bus = json_data[list(json_data)[0]]['VP']

                route = "HSL:" + bus['line']
                direction = int(bus['dir'])
                date = datetime.datetime.fromtimestamp(float(bus['tsi'])).strftime("%Y%m%d")
                time = math.floor( (int(bus['start'])/100) * 60) + (int(bus['start']) % 60) * 60

                data = self.fetch_single_fuzzy_trip(route, direction, date, time)

                data['major'] = mm['major']
                data['minor'] = mm['minor']
                data['vehicle_type'] = 3            # For now always assumes vehicle is a bus
                result['vehicles'].append(data)

        return result


    def fetch_single_fuzzy_trip(self, route, direction, date, time):
        """
        Gets trip info from Digitransit API. See: get_query

        :param route: route number
        :param direction: direction
        :param date: date
        :param time: time bus has started
        :return: dict containing EITHER trip_id, direction, line OR error
        """
        query = ('''{fuzzyTrip(route:"%s", date:"%s", time:%d, direction:%d){
                        gtfsId
                        tripHeadsign
                        route{
                            shortName
                        }
                    }
                }''') % (route, date, time, direction)

        data = json.loads(self.get_query(query))['data']['fuzzyTrip']

        if data is None:
            return json.loads('{"error":"No trip found matching route, direction, date and time"}')

        return json.loads( ('{"trip_id":"%s", "destination":"%s", "line":"%s"}') % (data['gtfsId'], data['tripHeadsign'], data['route']['shortName']) )


    def get_stops_near_coordinates(self, lat, lon, radius):
        """
        Gets stops within specified radius of a point defined by lat and lon from Digitransit API. See: get_query

        :param lat: latitude
        :param lon: longitude
        :param radius: radius
        :return: list of stops including ids and their distance to point defined by lat and lon
        """
        radius = min(radius, 1000)
        query = ("{stopsByRadius(lat:%f, lon:%f, radius:%d) {"
                 "  edges {"
                 "      node {"
                 "          distance"
                 "          stop {"
                 "    	        gtfsId"
                 "              name"
                 "              vehicleType"
                 "          }"
                 "      }"
                 "    }"
                 "  }"
                 "}") % (lat, lon, radius)
        data = json.loads(self.get_query(query))
        data = data['data']['stopsByRadius']['edges']
        stoplist = []
        for n in data:
            if n['node']['stop']['vehicleType'] == 0 or n['node']['stop']['vehicleType'] == 3:      #vehicle_type: 0 - tram, 1 - metro, 3 - bus, 4 - ferry
                stoplist.append({'stop_id': n['node']['stop']['gtfsId'], 'distance': n['node']['distance']})
        sorted(stoplist, key=lambda k: k['distance'])
        return stoplist[:3]

    def get_busses_by_stop_id(self, stop_id, distance):
        """
        Gets info from busses passing stop identified by stop_id from Digitransit API. See: get_query

        :param stop_id: stop id
        :param distance: distance appended to the result
        :return: dict containing info from both the stop and the busses passing it
        """
        query = ("{stop(id: \"%s\") {"
                 "  name"
                 "  code"
                 "  vehicleType"
                 "  stoptimesForServiceDate(date: \"%s\"){"
                 "     pattern {"
                 "         code"
                 "         name"
                 "         directionId"
                 "         route {"
                 "             gtfsId"
                 "             longName"
                 "             shortName"
                 "         }"
                 "     }"
                 "     stoptimes {"
                 "         trip{"
                 "             gtfsId"
                 "         }"
                 "         stopHeadsign"
                 "         serviceDay"
                 "    	    realtimeArrival"
                 "      }"
                 "    }"
                 "  }"
                 "}") % (stop_id, datetime.datetime.now().strftime("%Y%m%d"))

        data = json.loads(self.get_query(query))["data"]["stop"]

        if data is None:
            return json.loads('{ "error":"Invalid stop id" }')

        lines = data["stoptimesForServiceDate"]

        current_time = datetime.datetime.now()

        stop = {'stop_name': data["name"], 'stop_code': data["code"], 'stop_id': stop_id, 'distance': distance, 'schedule': []}
        schedule = []
        active_vehicles = self.db.get_vehicles()

        for line in lines:
            stoptimes = line["stoptimes"]

            for time in stoptimes:
                if not "serviceDay" in time: continue

                arrival_time = datetime.datetime.fromtimestamp(time["serviceDay"] + time["realtimeArrival"])
                arrival = math.floor((arrival_time - current_time).total_seconds() / 60.0)  # Arrival in minutes
                if current_time < arrival_time and arrival < 61:
                    if time.get("trip").get("gtfsId") in active_vehicles:
                        supports_stop_requests = True
                    else:
                        supports_stop_requests = False
                    schedule.append({'trip_id': time["trip"]["gtfsId"],
                                     'line': line["pattern"]["route"]["shortName"],
                                     'destination': time.get("stopHeadsign", ""),
                                     'arrival': arrival,
                                     'route_id': line["pattern"]["route"]["gtfsId"],
                                     'vehicle_type': data["vehicleType"],
                                     'supportsStopRequests': supports_stop_requests
                                     })

        sorted_by_route = sorted(schedule, key=lambda k: k['route_id'])
        bus_list = []
        for key, group in groupby(sorted_by_route, lambda k: k['route_id']):
            group = list(group)
            group = sorted(group, key=lambda k: k['arrival'])
            group = group[:2]
            if len(group) == 2 and group[1]['arrival'] > 30:
                group.pop()
            for g in group:
                bus_list.append(g)

        bus_list = sorted(bus_list, key=lambda k: k['arrival'])
        stop["schedule"] = bus_list[:10]

        return stop

    def get_query(self, query):
        """
        Gets given graphQL-query from Digitransit API.

        :param query: graphQL-query
        :return: JSON string response from API
        """
        response = requests.post(self.url, data=query, headers=self.headers)

        # Force encoding as auto-detection sometimes fails
        response.encoding = 'utf-8'
        if response.text.find('"errors"') != -1:
            print("ERROR:", response.text)
        return response.text

    def make_request(self, trip_id, stop_id, device_id, push_notification):
        """
        Saves stop request to database. If push notification is wanted tries to start running notify-method in 30 second
        intervals. (Does not start if it's already running.)

        See: notify

        :param trip_id: trip id
        :param stop_id: stop id
        :param device_id: device id, used to send push notifications via FCM (Firebase Cloud Messaging)
        :param push_notification:
        :return: returns dict containing request_id
        """
        request_id = self.db.store_request(trip_id, stop_id, device_id, push_notification)

        data = self.get_requests(trip_id)
        publish.single(topic="stoprequests/" + trip_id, payload=json.dumps(data), hostname=self.MQTT_host, port=1883)

        result = {"request_id": request_id}
        if push_notification:
            thread_helper.start_do_every("PUSH", 30, self.notify)
        return result

    def get_request_info(self, request_id):
        """
        Gets info of the trip related to given request id from Digitransit API. See: get_query

        :param request_id: id of the stoprequest
        :return: dict containing stop_name, stop_code, stop_id, arrives_in, delay
        """
        request_data = self.db.get_request_info(request_id)

        query = ("{"
                 "  trip(id: \"%s\"){"
                 "      stoptimesForDate(serviceDay: \"%s\"){"
                 "          stop{"
                 "              gtfsId"
                 "              code"
                 "              name"
                 "          }"
                 "          serviceDay"
                 "          realtimeArrival"
                 "          arrivalDelay"
                 "      }"
                 "  }"
                 "}") % (request_data[0], datetime.datetime.now().strftime("%Y%m%d"))

        stop_data = json.loads(self.get_query(query))['data']['trip']['stoptimesForDate']
        result = {}
        for stop in stop_data:
            if request_data[1] == stop['stop']['gtfsId']:
                current_time = datetime.datetime.now()
                real_time = datetime.datetime.fromtimestamp(stop["serviceDay"] + stop["realtimeArrival"])
                arrival = math.floor((real_time - current_time).total_seconds() / 60.0)
                result = {'stop_name': stop['stop']['name'], 'stop_code': stop['stop']['code'],
                          'stop_id': stop['stop']['gtfsId'], 'arrives_in': arrival, 'delay': stop['arrivalDelay']}

        return result

    def cancel_request(self, request_id):
        """
        Cancels stoprequest with the given id. See: get_requests

        :param request_id:
        :return: empty string
        """
        trip_id = self.db.cancel_request(request_id)
        data = self.get_requests(trip_id)
        publish.single(topic="stoprequests/" + trip_id, payload=json.dumps(data), hostname=self.MQTT_host, port=1883)

        return ''

    def store_report(self, trip_id, stop_id):
        """
        Saves report (notification that no one got one at the stop where stoprequest was made) to database.

        :param trip_id:
        :param stop_id:
        :return: empty string
        """
        self.db.store_report(trip_id, stop_id)

        return ''

    def get_requests(self, trip_id):
        """
        Gets all requests related to trip id from the database.

        :param trip_id:
        :return: dict containing stop_ids of all stoprequests related to trip_id
        """
        requests = self.db.get_requests(trip_id)
        stop_dict = {}

        for stop_id in requests:
            i = stop_dict.get(stop_id[0], 0)
            stop_dict[stop_id[0]] = i + 1
        stop_list = []

        for key in stop_dict.keys():
            stop_list.append({"id": key, "passengers": stop_dict[key]})

        return {"stop_ids": stop_list}

    def get_stops_by_trip_id(self, trip_id):
        """
        Gets stops on the route of trip identified by trip_id from Digitransit API. See: get_query

        :param trip_id:
        :return: dict containing list of stops which include stop_name, stop_code, stop_id, arrives_in
        """
        query = ("{trip(id: \"%s\") {"
                 " stoptimesForDate(serviceDay: \"%s\") {"
                 "      stop{"
                 "          gtfsId"
                 "          name"
                 "          code"
                 " }"
                 "      serviceDay"
                 "      realtimeArrival"
                 "        }"
                 "       }"
                 "      }"
                 "}") % (trip_id, datetime.datetime.now().strftime("%Y%m%d"))

        current_time = datetime.datetime.now()
        result = {}
        stops = []
        data = json.loads(self.get_query(query))['data']['trip']

        if data is None:
            return json.loads('{ "error":"Invalid trip id" }')

        for stop in data['stoptimesForDate']:
            real_time = datetime.datetime.fromtimestamp(stop["serviceDay"] + stop["realtimeArrival"])
            arrival = math.floor((real_time - current_time).total_seconds() / 60.0)
            stops.append({'stop_name': stop['stop']['name'], 'stop_code': stop['stop']['code'],
                          'stop_id': stop['stop']['gtfsId'], 'arrives_in': arrival})
        result["stops"] = stops

        return result

    def get_single_stop_by_trip_id(self, trip_id, stop_id):
        """
        Gets info of a single stop on route of trip identified by trip_id from Digitransit API. See: get_query

        :param trip_id:
        :param stop_id:
        :return: dict containing list with single stop with stop_name, stop_code, stop_id, arrives_in
        """
        query = ("{trip(id: \"%s\") {"
                 " stoptimesForDate(serviceDay: \"%s\") {"
                 "      stop{"
                 "          gtfsId"
                 "          name"
                 "          code"
                 " }"
                 "      serviceDay"
                 "      realtimeArrival"
                 "        }"
                 "       }"
                 "      }"
                 "}") % (trip_id, datetime.datetime.now().strftime("%Y%m%d"))

        current_time = datetime.datetime.now()
        result = {}
        stops = []
        data = json.loads(self.get_query(query))['data']['trip']

        if data is None:
            return json.loads('{ "error":"Invalid trip id" }')

        for stop in data['stoptimesForDate']:
            if stop_id == stop['stop']['gtfsId']:
                real_time = datetime.datetime.fromtimestamp(stop["serviceDay"] + stop["realtimeArrival"])
                arrival = math.floor((real_time - current_time).total_seconds() / 60.0)
                stops.append({'stop_name': stop['stop']['name'], 'stop_code': stop['stop']['code'],
                                  'stop_id': stop['stop']['gtfsId'], 'arrives_in': arrival})
        result["stops"] = stops

        return result

    def get_stops_by_code(self, stop_code):
        """
        Gets stops with given stop_code from Digitransit API. See: get_query

        :param stop_code:
        :return: dict containing list containing stop info
        """
        query = '''{ stops(name:"%s") { gtfsId code name platformCode lat lon } }''' % stop_code
        data = json.loads(self.get_query(query))
        return data['data']

    def fetch_single_trip(self, trip_id):
        """
        Get info of single trip identified by trip_id from Digitransit API. See: get_query

        :param trip_id:
        :return:
        """
        query = ('''{ trip(id:"%s"){
                        gtfsId
                        stoptimesForDate(serviceDay:"%s"){
                            serviceDay
                            realtimeArrival
                            stop{
                                gtfsId
                            }
                            }
                        }
                    }''') % (trip_id, datetime.datetime.now().strftime("%Y%m%d"))

        data = json.loads(self.get_query(query))

        return data['data']

    def notify(self):
        """
        (Called from make_request, after which will run every 30 seconds until environment variable PUSH is set to STOP.)
        Fetches all stoprequests, where push notification is not yet sent and calls
        fetch_trips_and_send_push_notifications. If all stoprequests have been served, sets environment variable
        PUSH to STOP which stops running this function.

        See: fetch_pushable_requests, fetch_trips_and_send_push_notifications, thread_helper.py
        """
        pushable_requests = self.fetch_pushable_requests()
        if not pushable_requests:
            thread_helper.stop_do_every("PUSH")
            return
        pushed_requests = self.fetch_trips_and_send_push_notifications(pushable_requests)
        # still need some kind of evaluation wether the notifications were sent
        if pushed_requests:
            self.db.set_pushed(pushed_requests)


    def fetch_trips_and_send_push_notifications(self, stoprequests):
        """
        Fetches trips related to stoprequests given to it as a list as parameter, gets trip info related to those
        stoprequests from Digitransit API and sends push notifications to users whose bus is estimated to arrive in
        under two minutes. In case of invalid requests (due to invalid trip_id or stop_id), sends push notification
        notifying about it.

        See: fetch_single_trip, PushNotificationService (in push_notification_service.py)

        :param stoprequests: dict where stoprequests[trip_id] = [ (request_id_1, stop_id_1, device_id_1), ... ]
        :return: dict containing info on the sent notifications
        """
        current_time = datetime.datetime.now()
        to_send = [] # List of push notifications to be sent
        pushed_requests = [] # List of ids of pushed requests

        # stoprequests is dict where:
        # stoprequests[trip_id] = [ (request_id_1, stop_id_1, device_id_1), ... ]
        for trip_id in stoprequests.keys():
            data = self.fetch_single_trip(trip_id)

            # In case trip_id is invalid (cancels invalid requests and send push_notifications of error)
            if data['trip'] is None:
                error_notifications = []
                for sr in stoprequests[trip_id]:
                    self.cancel_request(sr[0])
                    error_notifications.append(sr[2])
                self.push_notification_service.send_error_push_notifications(error_notifications, 'Invalid trip_id!')
                continue

            for sr in stoprequests[trip_id]:
                found = False # Whether wanted stop_id is on the route of the trip
                # sr[0] = request_id, sr[1] = stop_id, sr[2] = device_id
                for stoptime in data['trip']['stoptimesForDate']:
                    if stoptime['stop']['gtfsId'] == sr[1]:
                        found = True
                        arrival_time = datetime.datetime.fromtimestamp(stoptime['serviceDay'] + stoptime['realtimeArrival'])
                        arrival = math.floor((arrival_time - current_time).total_seconds())
                        if arrival <= 120:
                            to_send.append(sr[2])
                            pushed_requests.append(sr[0])

                # In case stop_id was invalid (cancels invalid request and send push_notification of error)
                if not found:
                    self.cancel_request(sr[0])
                    self.push_notification_service.send_error_push_notifications([sr[2]], 'Invalid stop_id!')

        if len(to_send) != 0:
            result = self.push_notification_service.send_push_notifications(to_send)
            if result[0].get('success') == 0:
                pushed_requests = []

        return pushed_requests

    def fetch_pushable_requests(self):
        """
        Fetches uncancelled and unpushed stoprequests from the database.

        :return: dict where dict where stoprequests[trip_id] = [ (request_id_1, stop_id_1, device_id_1), ... ]
        """
        pushable_requests = self.db.get_unpushed_requests()
        requests_by_trip_id = {}

        for request in pushable_requests:
            if requests_by_trip_id.get(request[0]):
                requests_by_trip_id.get(request[0]).append((request[1], request[2], request[3]))
            else:
                requests_by_trip_id[request[0]] = [(request[1], request[2], request[3])]

        return requests_by_trip_id
