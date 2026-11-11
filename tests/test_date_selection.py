from contextlib import redirect_stdout, redirect_stderr
from datetime import date
import io
import json
from pathlib import Path
import tempfile
import unittest

from streetlab import date_selection as dates


class DateSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "settings.json"
        self.config.write_text(json.dumps({"default_year": 2026, "default_hemisphere": "north",
            "north_season_months": {"spring": [3,4,5], "summer": [6,7,8], "autumn": [9,10,11], "winter": [12,1,2]}}))

    def resolve(self, constraints=None, seed=1):
        return dates.resolve_date(constraints, seed=seed, config_path=self.config)

    def test_season_stays_in_range_and_seed_reproduces_all_metadata(self):
        for season, months in [("春", [3,4,5]), ("summer", [6,7,8]), ("秋", [9,10,11]), ("冬", [1,2,12])]:
            for seed in range(20):
                result = self.resolve({"season": season}, seed=seed)
                self.assertIn(result["month"], months)
                self.assertEqual(result, self.resolve({"season": season}, seed=seed))
                self.assertEqual(date.fromisoformat(result["date"]).year, 2026)

    def test_unspecified_selects_a_season_and_records_assumptions(self):
        results = [self.resolve(seed=seed) for seed in range(64)]
        self.assertEqual({row["season"] for row in results}, set(dates.SEASONS))
        self.assertTrue(all(row["selection"]["season_was_random"] for row in results))
        self.assertTrue(all(row["selection"]["year_source"] == "config_default" for row in results))
        self.assertEqual(self.resolve()["requested"], {})

    def test_month_limits_dates_and_has_all_may_days_available(self):
        observed = {self.resolve({"month": 5}, seed=seed)["day"] for seed in range(400)}
        self.assertEqual(observed, set(range(1,32)))
        result = self.resolve({"month": 5})
        self.assertEqual(result["month"], 5)
        self.assertEqual(result["season"], "spring")
        self.assertEqual(result["selection"]["candidate_days"], 31)
        self.assertFalse(result["selection"]["season_was_random"])

    def test_exact_date_and_leap_year(self):
        self.assertEqual(self.resolve({"date": "2024-02-29"})["date"], "2024-02-29")
        self.assertEqual(self.resolve({"year": 2024, "month": 2, "day": 29})["date"], "2024-02-29")
        for constraints in [{"date": "2026-02-29"}, {"month": 2, "day": 29}, {"month": 4, "day": 31}]:
            with self.assertRaises(ValueError):
                self.resolve(constraints)

    def test_conflicting_and_invalid_conditions_not_silently_randomized(self):
        for constraints in [{"season": "winter", "month": 5}, {"date": "2026-05-01", "year": 2025},
                            {"date": "2026-05-01", "month": 6}, {"month": True}, {"month": 13},
                            {"season": "rainy"}, {"hemisphere": "unknown"}, {"unexpected": 1}]:
            with self.subTest(constraints=constraints), self.assertRaises(ValueError):
                self.resolve(constraints)

    def test_southern_hemisphere_reverses_season_months(self):
        for seed in range(20):
            self.assertIn(self.resolve({"season": "summer", "hemisphere": "south"}, seed)["month"], [12,1,2])

    def test_generated_seed_can_be_used_to_reproduce_a_choice(self):
        result = dates.resolve_date({}, config_path=self.config)
        self.assertEqual(result, self.resolve({}, seed=result["seed"]))

    def test_json_input_and_saved_result_are_ready_for_later_dialogue_connection(self):
        source = self.root / "input.json"
        source.write_text('{"month": 5}', encoding="utf-8")
        target = self.root / "result.json"
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = dates.main(["--input", str(source), "--seed", "42", "--config", str(self.config), "--output", str(target)])
        self.assertEqual(code, 0)
        saved = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(saved["month"], 5)
        self.assertEqual(saved["seed"], 42)
        original = target.read_bytes()
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(dates.main(["--config", str(self.config), "--output", str(target)]), 1)
        self.assertEqual(target.read_bytes(), original)
