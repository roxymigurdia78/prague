import importlib.util
from pathlib import Path
import unittest


script = Path(__file__).resolve().parents[1] / "scripts/research/lighting_direction_metrics.py"
spec = importlib.util.spec_from_file_location("lighting_direction_metrics", script)
metrics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metrics)


def record(key="a", **changes):
    row = {"image_id": key, "assessable": True, "frame": "blender_world_z_up",
           "toward_light": True, "direction_xyz": [1, 0, 0]}
    row.update(changes)
    return row


class DirectionMetricsTests(unittest.TestCase):
    def test_geometry_and_wraparound(self):
        self.assertAlmostEqual(metrics.angular_error_deg([2, 0, 0], [3, 0, 0]), 0)
        self.assertAlmostEqual(metrics.angular_error_deg([1, 0, 0], [0, 1, 0]), 90)
        self.assertAlmostEqual(metrics.angular_error_deg([1, 0, 0], [-1, 0, 0]), 180)
        self.assertAlmostEqual(metrics.angular_error_deg(metrics.from_angles(359, 0),
                                                         metrics.from_angles(1, 0)), 2)

    def test_abstention_and_missing_do_not_improve_accuracy(self):
        truth = [record("a"), record("b"), record("c"), record("d", assessable=False)]
        predictions = [record("a"), record("b", direction_xyz=None)]
        result = metrics.compare(truth, predictions)
        self.assertEqual(result["summary"]["n_truth_eligible"], 3)
        self.assertAlmostEqual(result["summary"]["coverage"], 1 / 3)
        self.assertAlmostEqual(result["summary"]["fraction_within_15deg_of_all_eligible"], 1 / 3)
        self.assertEqual([row["status"] for row in result["images"]],
                         ["scored", "prediction_abstained", "prediction_missing", "truth_unassessable"])

    def test_coordinate_and_sign_mismatch_are_not_silently_scored(self):
        for change in ({"frame": "camera"}, {"toward_light": False}):
            with self.assertRaises(ValueError):
                metrics.compare([record()], [record(**change)])

    def test_bad_directions_rejected(self):
        for vector in ([0, 0, 0], [float("nan"), 0, 1], [float("inf"), 0, 1], [1, 2], [True, 0, 0]):
            with self.assertRaises(ValueError):
                metrics.unit(vector)
        for angles in ((0, 91), (float("nan"), 0)):
            with self.assertRaises(ValueError):
                metrics.from_angles(*angles)

    def test_duplicate_unknown_and_missing_truth_fail(self):
        for truth, predictions in (([record(), record()], []), ([record()], [record("other")]),
                                   ([record(direction_xyz=None)], [])):
            with self.assertRaises(ValueError):
                metrics.compare(truth, predictions)

    def test_empty_results_have_no_fabricated_score(self):
        summary = metrics.compare([record(assessable=False)], [])["summary"]
        self.assertIsNone(summary["mean_angular_error_deg"])
        self.assertIsNone(summary["coverage"])


if __name__ == "__main__":
    unittest.main()
