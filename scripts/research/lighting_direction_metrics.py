"""Compare explicit light-source directions; does not infer lighting from RGB.

Run: python lighting_direction_metrics.py --truth truth.jsonl \
    --predictions predictions.jsonl --output direction_metrics.json
See docs/research/lightcity_pilot_2026-11-10.md for the data contract.
"""

import argparse
import json
import math
from pathlib import Path
import statistics


def unit(vector):
    if not isinstance(vector, (list, tuple)) or len(vector) != 3:
        raise ValueError("direction_xyz must contain three numbers")
    if any(isinstance(value, bool) for value in vector):
        raise ValueError("Direction components cannot be booleans")
    values = tuple(float(value) for value in vector)
    length = math.hypot(*values)
    if not all(math.isfinite(value) for value in values) or not math.isfinite(length) or length == 0:
        raise ValueError("Direction must be finite and nonzero")
    return tuple(value / length for value in values)


def from_angles(azimuth_deg, elevation_deg):
    """Azimuth: +X toward +Y; elevation: XY plane toward +Z, in degrees."""
    azimuth, elevation = float(azimuth_deg), float(elevation_deg)
    if not math.isfinite(azimuth) or not math.isfinite(elevation) or not -90 <= elevation <= 90:
        raise ValueError("Angles must be finite, with elevation between -90 and 90 degrees")
    a, e = math.radians(azimuth % 360), math.radians(elevation)
    return (math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e))


def angular_error_deg(first, second):
    a, b = unit(first), unit(second)
    dot = max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b))))
    return math.degrees(math.acos(dot))


def direction(record):
    if record.get("toward_light") is not True:
        raise ValueError("Explicit toward_light=true is required (surface toward source)")
    if not isinstance(record.get("frame"), str) or not record["frame"].strip():
        raise ValueError("An explicit coordinate frame is required")
    vector = record.get("direction_xyz")
    has_angles = record.get("azimuth_deg") is not None or record.get("elevation_deg") is not None
    if vector is not None and has_angles:
        raise ValueError("Use either direction_xyz or angles, not both")
    if vector is not None:
        return unit(vector)
    if has_angles:
        if record.get("azimuth_deg") is None or record.get("elevation_deg") is None:
            raise ValueError("Both azimuth_deg and elevation_deg are required")
        return unit(from_angles(record["azimuth_deg"], record["elevation_deg"]))
    return None  # unknown / abstention, not an invented direction


def by_id(records):
    result = {}
    for row in records:
        key = row.get("image_id")
        if not isinstance(key, str) or not key.strip() or key in result:
            raise ValueError(f"Missing or duplicate image_id: {key!r}")
        result[key] = row
    return result


def compare(truth_rows, prediction_rows):
    truth, predictions = by_id(truth_rows), by_id(prediction_rows)
    extra = set(predictions) - set(truth)
    if extra:
        raise ValueError(f"Predictions have unknown image IDs: {sorted(extra)}")
    details, errors = [], []
    eligible = 0
    for key, reference in truth.items():
        item = {"image_id": key, "angular_error_deg": None}
        if reference.get("assessable") is not True:
            item["status"] = "truth_unassessable"
        else:
            expected = direction(reference)
            if expected is None:
                raise ValueError(f"{key}: assessable truth must have a direction")
            eligible += 1
            if key not in predictions:
                item["status"] = "prediction_missing"
            else:
                prediction = predictions[key]
                estimated = direction(prediction)
                if estimated is None:
                    item["status"] = "prediction_abstained"
                else:
                    if reference["frame"] != prediction["frame"]:
                        raise ValueError(f"{key}: coordinate frames do not match")
                    error = angular_error_deg(expected, estimated)
                    errors.append(error)
                    item.update(status="scored", angular_error_deg=error)
        details.append(item)
    summary = {
        "n_images": len(truth),
        "n_truth_eligible": eligible,
        "n_scored": len(errors),
        "coverage": len(errors) / eligible if eligible else None,
        "mean_angular_error_deg": statistics.mean(errors) if errors else None,
        "median_angular_error_deg": statistics.median(errors) if errors else None,
        "fraction_within_15deg_of_all_eligible": sum(x <= 15 for x in errors) / eligible if eligible else None,
        "fraction_within_30deg_of_all_eligible": sum(x <= 30 for x in errors) / eligible if eligible else None,
    }
    return {"summary": summary, "images": details}


def read_jsonl(path):
    with Path(path).open(encoding="utf-8-sig") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--truth", required=True, type=Path)
    parser.add_argument("--predictions", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = compare(read_jsonl(args.truth), read_jsonl(args.predictions))
    # Exclusive creation avoids silently replacing a previous experiment.
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
