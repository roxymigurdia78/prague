from contextlib import closing, redirect_stdout, redirect_stderr
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, PropertyMock
import zipfile

from streetlab import atmosphere_reference as atmosphere

LOCATION = {"latitude": 50.08804, "longitude": 14.42076, "timezone": "Europe/Prague"}
WHEN = "2026-07-15T18:30:00+02:00"
try:
    import netCDF4
    import numpy as np
except ImportError:
    netCDF4 = None


class PlanningTests(unittest.TestCase):
    def test_default_prague_utc_and_nearest_grid(self):
        plan = atmosphere.plan_atmosphere(LOCATION, WHEN)
        item = plan["requests"][0]
        self.assertEqual(plan["reference_year"], 2025)
        self.assertEqual(plan["render_local_datetime"], WHEN)
        self.assertEqual(item["source_utc_datetime"], "2025-07-15T15:00:00+00:00")
        self.assertEqual(item["sample_offset_seconds"], -5400)
        self.assertEqual(item["expected_grid"], {"latitude": 50.25, "longitude": 14.25})
        self.assertEqual(set(item["request"]), {"date", "time", "area", "variable", "data_format"})
        self.assertEqual(len(item["request"]["variable"]), 2)

    def test_twenty_years_is_twenty_matching_days_not_full_history(self):
        plan = atmosphere.plan_atmosphere(LOCATION, WHEN, past_years=20)
        self.assertEqual([row["reference_year"] for row in plan["requests"]], list(range(2006, 2026)))
        self.assertTrue(all(len(row["request"]["date"]) == len(row["request"]["time"]) == 1 for row in plan["requests"]))

    def test_midnight_rounding_utc_date_and_longitude_wrap(self):
        loc = {"latitude": 90, "longitude": 180, "timezone": "Pacific/Kiritimati"}
        item = atmosphere.plan_atmosphere(loc, "2026-07-15T13:20:00+14:00")["requests"][0]
        self.assertEqual(item["source_utc_datetime"], "2025-07-15T00:00:00+00:00")
        self.assertEqual(item["expected_grid"], {"latitude": 90, "longitude": -180})
        self.assertTrue(-90 <= item["request"]["area"][2] <= item["request"]["area"][0] <= 90)
        same = atmosphere.plan_atmosphere({**loc, "longitude": -180}, "2026-07-15T13:20:00+14:00")["requests"][0]
        self.assertEqual(item["cache_key"], same["cache_key"])

    def test_invalid_leap_day_and_dst_are_never_silently_changed(self):
        for when in ("2024-02-29T12:00:00+01:00", "2026-03-30T02:30:00+02:00"):
            with self.assertRaises(ValueError):
                atmosphere.plan_atmosphere(LOCATION, when)
        plan = atmosphere.plan_atmosphere(LOCATION, "2024-02-29T12:00:00+01:00", past_years=20)
        self.assertEqual(len(plan["requests"]), 5)
        self.assertEqual(len(plan["skipped_years"]), 15)

    def test_invalid_inputs_and_out_of_coverage_analysis(self):
        for options in ({"past_years": 21}, {"past_years": True}, {"reference_year": 2026},
                        {"reference_year": 2003, "past_years": 2}):
            with self.assertRaises(ValueError):
                atmosphere.plan_atmosphere(LOCATION, WHEN, **options)
        with self.assertRaises(ValueError):
            atmosphere.plan_atmosphere(LOCATION, "2026-07-15T12:00:00")
        with self.assertRaises(ValueError):
            atmosphere.plan_atmosphere({**LOCATION, "latitude": float("nan")}, WHEN)
        with self.assertRaises(ValueError):
            atmosphere.plan_atmosphere({**LOCATION, "timezone": "America/Los_Angeles"}, "2026-12-31T23:00:00-08:00")

    def test_cli_dry_run_has_no_sdk_call_or_cache_or_result_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(atmosphere, "_new_client") as client, redirect_stdout(io.StringIO()) as stdout:
                code = atmosphere.main(["--date", "2026-07-15", "--time", "18:30", "--past-years", "20",
                                        "--cache-dir", str(root / "cache"), "--output", str(root / "result.json"), "--dry-run"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout.getvalue())["maximum_new_data_requests"], 20)
            self.assertFalse(list(root.iterdir()))
            client.assert_not_called()

    def test_from_weather_reuses_selected_year_and_rejects_conflicting_sampling(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "weather.json"
            source.write_text(json.dumps({"location": LOCATION, "render_local_datetime": WHEN, "selected_reference_year": 2014}))
            with redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(atmosphere.main(["--from-weather", str(source), "--dry-run"]), 0)
            self.assertEqual(json.loads(stdout.getvalue())["requests"][0]["reference_year"], 2014)
            with redirect_stderr(io.StringIO()):
                self.assertEqual(atmosphere.main(["--from-weather", str(source), "--sample-year", "--dry-run"]), 1)


class SetupTests(unittest.TestCase):
    def test_no_network_without_key_and_explicit_local_license_confirmation(self):
        for key, accepted in (("", False), ("placeholder", False), ("", True)):
            with patch.object(atmosphere, "_credentials", return_value=(key, accepted)):
                with self.assertRaises(ValueError):
                    atmosphere._new_client()

    def test_env_file_precedence_and_no_secret_in_setup_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text('CDSAPI_KEY="file-secret"\nCAMS_EAC4_LICENSE_ACCEPTED=true\nOTHER=ignored\n')
            with patch.object(atmosphere, "ROOT", root), patch.dict("os.environ", {"CDSAPI_KEY": "environment-secret", "CAMS_EAC4_LICENSE_ACCEPTED": "false"}):
                self.assertEqual(atmosphere._credentials(), ("environment-secret", False))
                with redirect_stdout(io.StringIO()) as stdout:
                    self.assertEqual(atmosphere.main(["--check-setup"]), 0)
                self.assertNotIn("secret", stdout.getvalue())
                self.assertTrue(json.loads(stdout.getvalue())["api_key_present"])

    def test_cli_redacts_sdk_exceptions(self):
        with patch.object(atmosphere, "fetch_atmosphere", side_effect=ValueError("token=private-signed-url")), redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(atmosphere.main(["--date", "2026-07-15", "--time", "18:30"]), 1)
        self.assertNotIn("private", stderr.getvalue())


@unittest.skipIf(netCDF4 is None, "Install .[atmosphere] for real binary NetCDF and cache tests")
class AcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cache = self.root / "cache"
        self.item = atmosphere.plan_atmosphere(LOCATION, WHEN)["requests"][0]
        self.submissions = []
        self.resumes = []
        self.downloads = 0
        self.fail_download = False

    def fixture(self, item=None, *, fields=("aod550", "tco3"), units=None, masked=False, wrong_time=False,
                longitude=None, extra_dimension=False):
        item = item or self.item
        p = self.root / ("fixture" + str(len(list(self.root.glob("fixture*.nc")))) + ".nc")
        with netCDF4.Dataset(p, "w") as ds:
            ds.createDimension("valid_time", 1)
            ds.createDimension("latitude", 1)
            ds.createDimension("longitude", 1)
            if extra_dimension:
                ds.createDimension("number", 2)
            ds.createVariable("latitude", "f8", ("latitude",))[:] = [item["expected_grid"]["latitude"]]
            ds.createVariable("longitude", "f8", ("longitude",))[:] = [item["expected_grid"]["longitude"] if longitude is None else longitude]
            t = ds.createVariable("valid_time", "f8", ("valid_time",))
            t.units = "seconds since 1970-01-01 00:00:00"
            t[:] = [datetime.fromisoformat(item["source_utc_datetime"]).timestamp() + (3600 if wrong_time else 0)]
            for field in fields:
                dimensions = ("longitude", "valid_time", "latitude") + (("number",) if extra_dimension else ())
                var = ds.createVariable(field, "f8", dimensions, fill_value=-999)
                var.units = units or ("1" if field == "aod550" else "kg m**-2")
                if not masked:
                    var[:] = .12 if field == "aod550" else 300 * atmosphere.KG_M2_PER_DU
        return p.read_bytes()

    def fake_client(self):
        owner = self
        class Remote:
            request_id = "offline-job-id"
            def __init__(self, item):
                self.item = item
            def download(self, target):
                owner.downloads += 1
                if owner.fail_download:
                    raise OSError("simulated interruption")
                with zipfile.ZipFile(target, "w") as archive:
                    archive.writestr("aod.nc", owner.fixture(self.item, fields=("aod550",)))
                    archive.writestr("ozone.nc", owner.fixture(self.item, fields=("tco3",)))
        def submit(dataset, request):
            self.assertEqual(dataset, atmosphere.DATASET)
            self.submissions.append(request)
            item = {**self.item, "source_utc_datetime": request["date"][0] + "T" + request["time"][0] + ":00+00:00"}
            return Remote(item)
        def resume(request_id):
            owner.resumes.append(request_id)
            return Remote(owner.item)
        return SimpleNamespace(retrieve=submit, client=SimpleNamespace(get_remote=resume))

    def test_split_zip_netCDF_units_conversion_and_cache_without_auth(self):
        with patch.object(atmosphere, "_new_client", side_effect=self.fake_client):
            first = atmosphere.fetch_atmosphere(LOCATION, WHEN, cache_dir=self.cache)
        with patch.object(atmosphere, "_new_client", side_effect=AssertionError("cache must be offline")):
            second = atmosphere.fetch_atmosphere(LOCATION, WHEN, cache_dir=self.cache)
        self.assertEqual(len(self.submissions), 1)
        self.assertFalse(first["records"][0]["from_cache"])
        self.assertTrue(second["records"][0]["from_cache"])
        self.assertAlmostEqual(first["selected_sample"]["aod550"], .12)
        self.assertAlmostEqual(first["selected_sample"]["total_column_ozone_du"], 300)
        self.assertEqual(first["selected_reference_year"], 2025)
        self.assertEqual(first["selected_sample"]["api_grid"], self.item["expected_grid"])

    def test_interrupted_download_resumes_existing_job_without_new_submission(self):
        self.fail_download = True
        with patch.object(atmosphere, "_new_client", side_effect=self.fake_client):
            with self.assertRaises(OSError):
                atmosphere._fetch_subset(self.item, self.cache)
            self.fail_download = False
            atmosphere._fetch_subset(self.item, self.cache)
        self.assertEqual(len(self.submissions), 1)
        self.assertEqual(self.resumes, ["offline-job-id"])

    def test_submission_failure_not_silently_repeated(self):
        client = self.fake_client()
        with patch.object(atmosphere, "_new_client", return_value=client), patch.object(client, "retrieve", side_effect=OSError("lost response")) as submit:
            with self.assertRaises(OSError):
                atmosphere._fetch_subset(self.item, self.cache)
            with self.assertRaisesRegex(ValueError, "uncertain"):
                atmosphere._fetch_subset(self.item, self.cache)
            self.assertEqual(submit.call_count, 1)

    def test_cache_tampering_stops_before_network(self):
        with patch.object(atmosphere, "_new_client", side_effect=self.fake_client):
            atmosphere._fetch_subset(self.item, self.cache)
        raw = self.cache / (self.item["cache_key"] + ".zip")
        raw.write_bytes(raw.read_bytes() + b"tampered")
        with patch.object(atmosphere, "_new_client") as client:
            with self.assertRaisesRegex(ValueError, "integrity"):
                atmosphere._fetch_subset(self.item, self.cache)
            client.assert_not_called()

    def test_reject_units_missing_value_wrong_time_and_extra_ensemble(self):
        for kwargs in ({"units": "ug m-3"}, {"masked": True}, {"wrong_time": True}, {"extra_dimension": True}):
            p = self.root / "bad.nc"
            p.write_bytes(self.fixture(**kwargs))
            with self.assertRaises(ValueError):
                atmosphere.read_subset(p, self.item)

    def test_longitude_0_to_360_and_wrong_grid(self):
        item = atmosphere.plan_atmosphere({**LOCATION, "longitude": -100}, WHEN)["requests"][0]
        p = self.root / "west.nc"
        p.write_bytes(self.fixture(item, longitude=item["expected_grid"]["longitude"] + 360))
        self.assertEqual(atmosphere.read_subset(p, item)["api_grid"], item["expected_grid"])
        p.write_bytes(self.fixture(item, longitude=0))
        with self.assertRaisesRegex(ValueError, "grid point"):
            atmosphere.read_subset(p, item)

    def test_archive_path_is_never_extracted_and_missing_field_rejected(self):
        p = self.root / "archive.zip"
        with zipfile.ZipFile(p, "w") as archive:
            archive.writestr("../outside.nc", self.fixture(fields=("aod550", "gtco3")))
        self.assertAlmostEqual(atmosphere.read_subset(p, self.item)["aod550"], .12)
        self.assertFalse((self.root.parent / "outside.nc").exists())
        with zipfile.ZipFile(p, "w") as archive:
            archive.writestr("only-aod.nc", self.fixture(fields=("aod550",)))
        with self.assertRaisesRegex(ValueError, "Both AOD550"):
            atmosphere.read_subset(p, self.item)

    def test_20_year_budget_and_seeded_selection_keep_both_fields_together(self):
        with patch.object(atmosphere, "_new_client", side_effect=self.fake_client):
            result = atmosphere.fetch_atmosphere(LOCATION, WHEN, past_years=20, sample_year=True, seed=42, cache_dir=self.cache)
            repeated = atmosphere.fetch_atmosphere(LOCATION, WHEN, past_years=20, sample_year=True, seed=42, cache_dir=self.cache)
            self.assertEqual(len(self.submissions), 20)
            self.assertEqual(result["selected_reference_year"], repeated["selected_reference_year"])
            with self.assertRaisesRegex(ValueError, "20 new EAC4"):
                atmosphere.fetch_atmosphere(LOCATION, "2026-07-16T18:30:00+02:00", cache_dir=self.cache)
        self.assertEqual(len(self.submissions), 20)
        selected = next(row for row in result["records"] if row["reference_year"] == result["selected_reference_year"])
        self.assertEqual(result["selected_sample"], selected["selected_sample"])
        with closing(sqlite3.connect(self.cache / "requests.sqlite3")) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 20)

    def test_official_sdk_adapter_is_offline_and_disables_retries(self):
        from ecmwf.datastores import Client
        catalogue = SimpleNamespace(messages=SimpleNamespace(log_messages=lambda: None))
        # The SDK reads service announcements in its constructor. Stub those too.
        with patch.object(atmosphere, "_credentials", return_value=("test-placeholder", True)), \
             patch.object(Client, "_catalogue_api", new_callable=PropertyMock, return_value=catalogue), \
             patch("requests.Session.send", side_effect=AssertionError("offline test")) as send:
            client = atmosphere._new_client()
        send.assert_not_called()
        self.assertEqual(client.url, atmosphere.ENDPOINT)
        self.assertEqual(client.client.maximum_tries, 1)
        self.assertFalse(client.wait_until_complete)
        self.assertFalse(client.delete)


if __name__ == "__main__":
    unittest.main()
