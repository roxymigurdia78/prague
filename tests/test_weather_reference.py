from contextlib import closing, redirect_stdout, redirect_stderr
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from streetlab import weather_reference as weather, open_meteo_http as http, geocoding as geo

LOCATION = {"latitude": 50.08804, "longitude": 14.42076, "timezone": "Europe/Prague"}
WHEN = "2026-07-15T18:30:00+02:00"


class WeatherReferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.cache = self.folder/'cache.sqlite3'

    def fake_fetch(self, request, **kwargs):
        query = parse_qs(urlsplit(request.full_url).query)
        start = datetime.fromisoformat(query['start_date'][0]).replace(tzinfo=timezone.utc)
        times = [int((start+timedelta(hours=hour)).timestamp()) for hour in range(72)]
        year = start.year
        # Correlate all core fields with the source year to detect mixed-year sampling.
        hourly = {'time': times, **{key: [None]*72 for key in weather.VARIABLES}}
        for index in range(72):
            hourly['temperature_2m'][index] = year-2000
            hourly['surface_pressure'][index] = 900+(year-2000)
            hourly['relative_humidity_2m'][index] = year-1970
            hourly['cloud_cover'][index] = 40
            hourly['precipitation'][index] = 0
            hourly['weather_code'][index] = 2
        body = {'hourly': hourly, 'hourly_units': weather.UNITS,
                'latitude':50.1, 'longitude':14.4, 'elevation':200}
        return io.BytesIO(json.dumps(body).encode('utf-8'))

    def fetch(self, **kwargs):
        return weather.fetch_weather(LOCATION, WHEN, cache_path=self.cache, **kwargs)

    def test_default_2025_preserves_render_year_and_hourly_units_and_cache(self):
        with patch.object(http, 'urlopen', side_effect=self.fake_fetch) as get:
            result = self.fetch()
            cached = self.fetch()
        self.assertEqual(get.call_count, 1)
        self.assertTrue(result['render_local_datetime'].startswith('2026-'))
        self.assertEqual(result['selected_reference_year'], 2025)
        self.assertEqual(result['selected_sample']['surface_pressure'],925)
        self.assertEqual(result['selected_sample']['temperature_2m'],25)
        self.assertTrue(cached['records'][0]['from_cache'])
        self.assertEqual(len(result['records'][0]['hours']),24)
        self.assertEqual(result['records'][0]['units']['surface_pressure'],'hPa')

    def test_twenty_year_plan_is_20_small_requests_without_network(self):
        with patch.object(http,'urlopen') as get:
            plan = weather.plan_weather(LOCATION,WHEN,past_years=20)
        get.assert_not_called()
        self.assertEqual([row['reference_year'] for row in plan['requests']],list(range(2006,2026)))
        self.assertEqual(plan['maximum_api_call_units'],20)
        for row in plan['requests']:
            query=parse_qs(urlsplit(row['request_url']).query)
            self.assertEqual(query['models'],['era5'])
            self.assertEqual(len(query['hourly'][0].split(',')),6)
            self.assertEqual((datetime.fromisoformat(query['end_date'][0])-datetime.fromisoformat(query['start_date'][0])).days,2)

    def test_history_and_random_sample_keep_all_fields_in_same_year(self):
        with patch.object(http,'urlopen',side_effect=self.fake_fetch), patch.object(http.time,'sleep'):
            result=self.fetch(past_years=20,sample_year=True,seed=42)
            repeated=self.fetch(past_years=20,sample_year=True,seed=42)
            fixed=self.fetch(past_years=20)
        year=result['selected_reference_year']
        sample=result['selected_sample']
        self.assertEqual(sample['temperature_2m'],year-2000)
        self.assertEqual(sample['surface_pressure'],year-2000+900)
        self.assertEqual(sample['relative_humidity_2m'],year-1970)
        self.assertEqual(year,repeated['selected_reference_year'])
        self.assertEqual(fixed['selected_reference_year'],2025)
        self.assertEqual(len(result['records']),20)

    def test_leap_day_is_not_silently_changed(self):
        with self.assertRaises(ValueError):
            weather.plan_weather(LOCATION,'2024-02-29T12:00:00+01:00')
        plan=weather.plan_weather(LOCATION,'2024-02-29T12:00:00+01:00',past_years=20)
        self.assertEqual(len(plan['requests']),5)
        self.assertEqual(len(plan['skipped']),15)
        self.assertTrue(all(row['reference_date'].endswith('02-29') for row in plan['requests']))

    def test_failed_call_is_counted_and_never_retried(self):
        with patch.object(http,'urlopen',side_effect=HTTPError(weather.ENDPOINT,429,'limit',{},None)) as get:
            with self.assertRaises(HTTPError):
                self.fetch()
        self.assertEqual(get.call_count,1)
        with closing(sqlite3.connect(self.cache)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cache').fetchone()[0],0)

    def test_geocoding_and_weather_share_budget_and_old_cache_is_compatible(self):
        params={'name':'Prague','count':5,'language':'ja','format':'json','countryCode':'CZ'}
        from urllib.parse import urlencode
        import hashlib
        url=geo.ENDPOINT+'?'+urlencode(params)
        key=hashlib.sha256(url.encode()).hexdigest()
        with closing(sqlite3.connect(self.cache)) as db,db:
            db.execute('CREATE TABLE cache (key TEXT PRIMARY KEY,payload TEXT NOT NULL)')
            legacy={'schema':'streetlab/location-search/v1','results':[{'latitude':50,'longitude':14}], 'fetched_at':'2025-01-01T00:00:00+00:00'}
            db.execute('INSERT INTO cache VALUES (?,?)',(key,json.dumps(legacy)))
        with patch.object(geo,'urlopen') as get:
            result=geo.search_locations('Prague',country='CZ',cache_path=self.cache)
            get.assert_not_called()
        self.assertTrue(result['from_cache'])
        with patch.object(http,'urlopen',side_effect=self.fake_fetch):
            self.fetch()
        with closing(sqlite3.connect(self.cache)) as db,db:
            db.executemany('INSERT INTO attempts VALUES (?)',[(http.time.time(),)]*499)
        with patch.object(geo,'urlopen') as get:
            with self.assertRaises(RuntimeError):
                geo.search_locations('Berlin',country='DE',cache_path=self.cache)
            get.assert_not_called()

    def test_single_call_budget_refuses_long_range_before_network(self):
        url=weather.ENDPOINT+'?latitude=50&longitude=14&hourly=temperature_2m&start_date=2006-01-01&end_date=2025-12-31'
        with patch.object(http,'urlopen') as get:
            with self.assertRaises(ValueError):
                http.request_json(url,cache_path=self.cache)
            get.assert_not_called()

    def test_wrong_units_and_null_values_not_fabricated(self):
        with patch.object(http,'urlopen',side_effect=self.fake_fetch):
            result=self.fetch()
        self.assertEqual(result['records'][0]['sample_method'],'nearest_complete_hour_no_interpolation')
        row={'hourly':{'time':[0],**{key:[None] for key in weather.VARIABLES}},'hourly_units':{**weather.UNITS,'surface_pressure':'Pa'}}
        with self.assertRaises(ValueError):
            weather._validate_weather(row)

    def test_cli_dry_run_and_existing_output_send_no_api_calls(self):
        with patch.object(http,'urlopen') as get,redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            self.assertEqual(weather.main(['--date','2026-07-15','--time','18:30','--past-years','20','--dry-run']),0)
            target=self.folder/'result.json'
            target.write_text('keep')
            self.assertEqual(weather.main(['--date','2026-07-15','--time','18:30','--output',str(target)]),1)
            get.assert_not_called()
        self.assertEqual(target.read_text(),'keep')
