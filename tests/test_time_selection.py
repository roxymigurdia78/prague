from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest

from streetlab.solar_math import solar_day, solar_position
from streetlab import time_selection as times

PRAGUE = {"latitude": 50.08804, "longitude": 14.42076, "timezone": "Europe/Prague"}
TROMSO = {"latitude": 69.6492, "longitude": 18.9553, "timezone": "Europe/Oslo"}


class SolarTimeTests(unittest.TestCase):
    def test_published_nrel_reference_geometry_and_events(self):
        # NREL/TP-560-34302, Table A5.1, pp. A15-A16.
        # Our compact model has no observer parallax or refraction in position.
        when = datetime.fromisoformat("2003-10-17T12:30:30-07:00")
        result = solar_position(39.742476, -105.1786, when)
        self.assertAlmostEqual(result["azimuth_deg"], 194.34024, delta=.03)
        self.assertAlmostEqual(result["elevation_deg"], 90-50.11162, delta=.05)
        day = solar_day("2003-10-17", 39.742476, -105.1786, "Etc/GMT+7")
        for key, expected in [("sunrise", "2003-10-17T13:12:43+00:00"),
                              ("sunset", "2003-10-18T00:20:19+00:00"),
                              ("solar_noon", "2003-10-17T18:46:05+00:00")]:
            actual = datetime.fromisoformat(day["events"][key])
            self.assertLess(abs((actual-datetime.fromisoformat(expected)).total_seconds()), 90)

    def test_geometry_depends_on_instant_not_display_timezone(self):
        when = datetime.fromisoformat("2026-07-15T18:00:00+02:00")
        self.assertEqual(solar_position(50, 14, when), solar_position(50, 14, when.astimezone(timezone.utc)))

    def test_summer_evening_is_later_than_winter(self):
        summer = solar_day("2026-07-15", **dict(latitude=50.08804, longitude=14.42076, timezone_name="Europe/Prague"))
        winter = solar_day("2026-01-15", 50.08804, 14.42076, "Europe/Prague")
        self.assertGreaterEqual(datetime.fromisoformat(summer["events"]["sunset"]).hour, 20)
        self.assertLessEqual(datetime.fromisoformat(winter["events"]["sunset"]).hour, 17)
        for day in (summer, winter):
            rise, noon, setting = [datetime.fromisoformat(day["events"][key]) for key in ["sunrise", "solar_noon", "sunset"]]
            self.assertLess(rise, noon)
            self.assertLess(noon, setting)
            self.assertAlmostEqual(solar_position(50.08804, 14.42076, rise)["elevation_deg"], -.833, delta=.002)

    def test_civil_day_length_tracks_dst(self):
        self.assertEqual(solar_day("2026-03-29", 50, 14, "Europe/Prague")["civil_day_hours"], 23)
        self.assertEqual(solar_day("2026-10-25", 50, 14, "Europe/Prague")["civil_day_hours"], 25)

    def test_all_eight_periods_can_be_selected_and_saved_seed_replays(self):
        for period in times.PERIODS:
            result = times.resolve_time(PRAGUE, "2026-07-15", {"period": period}, seed=42)
            self.assertEqual(result["period"], period)
            self.assertEqual(result["date"], "2026-07-15")
            self.assertEqual(result, times.resolve_time(PRAGUE, "2026-07-15", {"period": period}, seed=42))
        result = times.resolve_time(PRAGUE, "2026-07-15", seed=8)
        self.assertIn(result["period"], result["selection"]["available_periods"])

    def test_evening_is_before_sunset_in_configured_window(self):
        result = times.resolve_time(PRAGUE, "2026-07-15", {"period": "夕方"}, seed=42)
        chosen = datetime.fromisoformat(result["local_datetime"])
        setting = datetime.fromisoformat(result["solar_day"]["events"]["sunset"])
        self.assertGreater(setting-chosen, timedelta(0))
        self.assertLessEqual(setting-chosen, timedelta(hours=1))
        self.assertEqual(chosen.utcoffset(), timedelta(hours=2))

    def test_nonexistent_local_time_rejected(self):
        with self.assertRaisesRegex(ValueError, "存在しません"):
            times.resolve_time(PRAGUE, "2026-03-29", {"time": "02:30"})

    def test_ambiguous_local_time_needs_fold_and_resolves_both_instants(self):
        with self.assertRaisesRegex(ValueError, "2回"):
            times.resolve_time(PRAGUE, "2026-10-25", {"time": "02:30"})
        first = times.resolve_time(PRAGUE, "2026-10-25", {"time": "02:30", "fold": 0}, seed=1)
        second = times.resolve_time(PRAGUE, "2026-10-25", {"time": "02:30", "fold": 1}, seed=1)
        self.assertEqual(datetime.fromisoformat(second["utc_datetime"])-datetime.fromisoformat(first["utc_datetime"]), timedelta(hours=1))

    def test_explicit_clock_time_is_not_randomized(self):
        first = times.resolve_time(PRAGUE, "2026-07-15", {"time": "18:30:45"}, seed=1)
        second = times.resolve_time(PRAGUE, "2026-07-15", {"time": "18:30:45"}, seed=2)
        self.assertEqual(first["local_datetime"], "2026-07-15T18:30:45+02:00")
        self.assertEqual(first["local_datetime"], second["local_datetime"])
        with self.assertRaises(ValueError):
            times.resolve_time(PRAGUE, "2026-07-15", {"time": "12:00", "period": "深夜"})

    def test_polar_day_and_night_do_not_invent_sunrise_or_available_periods(self):
        summer = times.resolve_time(TROMSO, "2026-06-21", seed=1)
        self.assertEqual(summer["solar_day"]["condition"], "polar_day")
        self.assertIsNone(summer["solar_day"]["events"]["sunset"])
        self.assertNotIn("night", summer["selection"]["available_periods"])
        with self.assertRaises(ValueError):
            times.resolve_time(TROMSO, "2026-06-21", {"period": "夕方"})
        winter = times.resolve_time(TROMSO, "2026-12-21", seed=1)
        self.assertEqual(winter["solar_day"]["condition"], "polar_night")
        self.assertIsNone(winter["solar_day"]["events"]["sunrise"])
        with self.assertRaises(ValueError):
            times.resolve_time(TROMSO, "2026-12-21", {"period": "昼"})

    def test_invalid_coordinates_naive_time_and_unknown_timezone_rejected(self):
        with self.assertRaises(ValueError):
            solar_position(50, 14, datetime(2026,1,1))
        with self.assertRaises(ValueError):
            solar_position(float("nan"), 14, datetime.now(timezone.utc))
        with self.assertRaises(ValueError):
            times.resolve_time({**PRAGUE, "timezone": "Unknown/Place"}, "2026-07-15")

    def test_cli_combines_date_selection_and_json_input_without_overwriting(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, target = root/"input.json", root/"result.json"
            source.write_text(json.dumps({"location": PRAGUE, "month": 5, "period": "夕方"}), encoding="utf-8")
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = times.main(["--input", str(source), "--seed", "42", "--output", str(target)])
            self.assertEqual(code, 0)
            saved = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(saved["date_selection"]["month"], 5)
            self.assertEqual(saved["period"], "evening")
            self.assertEqual(saved["location_source"], "input_json")
            self.assertEqual(saved["seed"], 42)
            original = target.read_bytes()
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(times.main(["--date", "2026-07-15", "--output", str(target)]), 1)
            self.assertEqual(target.read_bytes(), original)
