"""Fetch reference meteorology for 2025, or matching calendar days in up to 20 years.

This does not infer Air/Aerosols/Ozone or change Blender or the requested weather.
"""
import argparse
from datetime import date as Date, datetime, time as Time, timedelta, timezone
import json
import math
from pathlib import Path
import random
import secrets
import sqlite3
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

from streetlab.open_meteo_http import request_json, validate_single_call
from streetlab.paths import ROOT
from streetlab.solar_math import get_zone, validate_coordinates
from streetlab.time_selection import _local_time

ENDPOINT = "https://archive-api.open-meteo.com/v1/archive"
VARIABLES = ("temperature_2m", "surface_pressure", "relative_humidity_2m", "cloud_cover", "precipitation", "weather_code")
CORE = VARIABLES[:3]
UNITS = {"temperature_2m": "°C", "surface_pressure": "hPa", "relative_humidity_2m": "%",
         "cloud_cover": "%", "precipitation": "mm", "weather_code": "wmo code"}
ATTRIBUTION = {"service": "Open-Meteo Historical Weather API", "data_source": "ECMWF ERA5 reanalysis",
               "url": "https://open-meteo.com/en/docs/historical-weather-api",
               "license": "CC BY 4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
               "processing": "Matching local month/day and clock time in a reference year; nearest available complete hour"}


def _settings(path):
    config = json.loads(Path(path or ROOT/"config"/"weather_reference.json").read_text(encoding="utf-8-sig"))
    if config.get("endpoint") != ENDPOINT or config.get("usage") != "non-commercial" or config.get("model") != "era5":
        raise ValueError("This entry supports only non-commercial Open-Meteo ERA5 archive requests")
    return config


def plan_weather(location, selected_datetime, *, reference_year=None, past_years=1, config_path=None):
    """Build requests without network or filesystem writes. Twenty years means 20 matching dates."""
    config = _settings(config_path)
    latitude, longitude = location["latitude"], location["longitude"]
    validate_coordinates(latitude, longitude)
    zone = get_zone(location["timezone"])
    when = datetime.fromisoformat(selected_datetime.replace("Z", "+00:00")) if isinstance(selected_datetime, str) else selected_datetime
    if not isinstance(when, datetime) or when.utcoffset() is None:
        raise ValueError("A timezone-aware render datetime is required")
    local = when.astimezone(zone)
    year = reference_year if reference_year is not None else config["default_reference_year"]
    if type(year) is not int or not 1941 <= year <= Date.today().year:
        raise ValueError("Reference year must be between 1941 and the current year")
    if type(past_years) is not int or not 1 <= past_years <= 20 or year-past_years+1 < 1941:
        raise ValueError("past_years must be 1..20 within the supported archive years")
    requests, skipped = [], []
    for source_year in range(year-past_years+1, year+1):
        try:
            source_day = Date(source_year, local.month, local.day)
            clock = local.strftime("%H:%M:%S")
            reference = _local_time(source_day, clock, zone, local.fold)
        except ValueError as error:
            if past_years == 1:
                raise ValueError(f"Reference date/time is invalid in {source_year}; select a valid --reference-year: {error}") from error
            skipped.append({"year": source_year, "reason": str(error)})
            continue
        if source_day >= Date.today():
            raise ValueError("Reference date must be in the past; no forecast fallback is used")
        # UTC plus surrounding days avoids ambiguity and missing hours at DST/date-line boundaries.
        utc_day = reference.astimezone(timezone.utc).date()
        params = {"latitude": latitude, "longitude": longitude,
                  "start_date": (utc_day-timedelta(days=1)).isoformat(),
                  "end_date": (utc_day+timedelta(days=1)).isoformat(),
                  "hourly": ",".join(VARIABLES), "models": "era5", "timezone": "UTC", "timeformat": "unixtime"}
        url = ENDPOINT+"?"+urlencode(params)
        validate_single_call(url)
        requests.append({"reference_year": source_year, "reference_date": source_day.isoformat(),
                         "reference_local_datetime": reference.isoformat(), "request_url": url})
    if not requests:
        raise ValueError("No valid reference dates in the requested years")
    return {"schema": "streetlab/weather-plan/v1", "render_local_datetime": local.isoformat(),
            "location": dict(location), "reference_year": year, "past_years": past_years,
            "requests": requests, "skipped": skipped, "maximum_api_call_units": len(requests),
            "request_scope": "one location; six variables; three UTC dates per valid reference year"}


def _validate_weather(body):
    if not isinstance(body, dict) or body.get("error") or not isinstance(body.get("hourly"), dict):
        raise ValueError("Unexpected historical-weather response")
    hourly, units = body["hourly"], body.get("hourly_units", {})
    times = hourly.get("time")
    if not isinstance(times, list) or not times:
        raise ValueError("No hourly weather timestamps returned")
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in times):
        raise ValueError("Weather timestamps must be UTC epoch seconds")
    if any(a >= b for a,b in zip(times,times[1:])):
        raise ValueError("Weather timestamps must be strictly increasing")
    for key in VARIABLES:
        values = hourly.get(key)
        if not isinstance(values, list) or len(values) != len(times) or units.get(key) != UNITS[key]:
            raise ValueError(f"Missing weather values or unexpected units for {key}")
        for value in values:
            if value is None:
                continue
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f"Invalid numeric weather value: {key}")
            if key in ("relative_humidity_2m", "cloud_cover") and not 0 <= value <= 100:
                raise ValueError(f"Invalid percentage: {key}")
            if key == "surface_pressure" and value <= 0:
                raise ValueError("Invalid surface pressure")


def fetch_weather(location, selected_datetime, *, reference_year=None, past_years=1,
                  sample_year=False, seed=None, config_path=None, cache_path=None):
    """Return all reference-day hours and one complete, internally consistent sample per year."""
    plan = plan_weather(location, selected_datetime, reference_year=reference_year,
                        past_years=past_years, config_path=config_path)
    if type(sample_year) is not bool:
        raise ValueError("sample_year must be a boolean")
    if seed is not None and type(seed) is not int:
        raise ValueError("seed must be an integer")
    seed = seed if seed is not None else secrets.randbits(64) if sample_year else None
    zone = get_zone(location["timezone"])
    records = []
    for item in plan["requests"]:
        response = request_json(item["request_url"], cache_path=cache_path, validate=_validate_weather)
        body = response["body"]
        rows = []
        for index, stamp in enumerate(body["hourly"]["time"]):
            instant = datetime.fromtimestamp(stamp, timezone.utc)
            local = instant.astimezone(zone)
            if local.date().isoformat() != item["reference_date"]:
                continue
            rows.append({"local_datetime": local.isoformat(), "utc_datetime": instant.isoformat(),
                         **{key: body["hourly"][key][index] for key in VARIABLES}})
        complete = [row for row in rows if all(row[key] is not None for key in CORE)]
        if not complete:
            raise ValueError(f"No complete pressure/temperature/humidity data for {item['reference_date']}")
        requested = datetime.fromisoformat(item["reference_local_datetime"])
        selected = min(complete, key=lambda row: abs((datetime.fromisoformat(row["utc_datetime"])-requested).total_seconds()))
        delta = (datetime.fromisoformat(selected["utc_datetime"])-requested).total_seconds()
        if abs(delta) > 3600:
            raise ValueError(f"No complete weather sample within one hour for {item['reference_date']}")
        records.append({**item, "fetched_at": response["fetched_at"], "from_cache": response["from_cache"],
                        "api_grid": {key: body.get(key) for key in ("latitude", "longitude", "elevation")},
                        "units": body["hourly_units"], "hours": rows, "selected_sample": selected,
                        "sample_offset_seconds": delta, "sample_method": "nearest_complete_hour_no_interpolation"})
    if sample_year:
        selected_record = random.Random(seed).choice(records)
    else:
        selected_record = next((row for row in records if row["reference_year"] == plan["reference_year"]), None)
    return {"schema": "streetlab/weather-reference/v1", "render_local_datetime": plan["render_local_datetime"],
            "location": dict(location), "model": "era5", "reference_year": plan["reference_year"],
            "past_years": past_years, "selection_method": "random_reference_year" if sample_year else "fixed_reference_year",
            "seed": seed, "selected_reference_year": selected_record["reference_year"] if selected_record else None,
            "selected_sample": selected_record["selected_sample"] if selected_record else None,
            "selection_note": None if selected_record else "Configured reference year has no valid matching date/time; select another year explicitly",
            "skipped_years": plan["skipped"], "records": records, "attribution": ATTRIBUTION,
            "weather_policy": "historical values retained; requested clear/cloudy appearance is not applied here"}


def main(argv=None):
    parser = argparse.ArgumentParser(description="既定2025年の同じ月日・現地時刻の気象を取得。指定時だけ過去20年にも対応。")
    parser.add_argument("--date", help="描画する日付 YYYY-MM-DD")
    parser.add_argument("--time", help="描画する現地時刻 HH:MM[:SS]")
    parser.add_argument("--fold", type=int, choices=[0,1], help="描画時刻が重複する場合の前後")
    parser.add_argument("--from-time", type=Path, help="timeコマンドが保存した結果JSON")
    parser.add_argument("--reference-year", type=int, help="参照年。初期値2025")
    parser.add_argument("--past-years", type=int, default=1, help="参照年までの1〜20年。同じ月日だけ取得")
    parser.add_argument("--sample-year", action="store_true", help="取得した年から1年をランダムに選ぶ")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--latitude", type=float)
    parser.add_argument("--longitude", type=float)
    parser.add_argument("--timezone")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="取得計画を表示。API呼び出し・ファイル作成なし")
    args = parser.parse_args(argv)
    try:
        if args.from_time:
            if any(value is not None for value in (args.date,args.time,args.latitude,args.longitude,args.timezone,args.fold)):
                raise ValueError("--from-time cannot be combined with date/time/location overrides")
            saved = json.loads(args.from_time.read_text(encoding="utf-8-sig"))
            location, when = saved["location"], datetime.fromisoformat(saved["local_datetime"])
        else:
            if not args.date or not args.time:
                raise ValueError("Specify --date and --time, or --from-time")
            overrides = {key: getattr(args,key) for key in ("latitude","longitude","timezone") if getattr(args,key) is not None}
            if overrides and len(overrides) != 3:
                raise ValueError("Specify latitude, longitude and timezone together")
            location = overrides or json.loads((ROOT/"config"/"time_selection.json").read_text(encoding="utf-8-sig"))["default_location"]
            when = _local_time(Date.fromisoformat(args.date), args.time, get_zone(location["timezone"]), args.fold)
        options = {"reference_year": args.reference_year, "past_years": args.past_years, "config_path": args.config}
        if args.dry_run:
            print(json.dumps(plan_weather(location, when, **options), ensure_ascii=False, indent=2))
            return 0
        path = args.output or ROOT/"outputs"/"weather_reference"/(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")+".json")
        if path.exists():
            raise ValueError("Output already exists; choose a new filename")
        result = fetch_weather(location, when, sample_year=args.sample_year, seed=args.seed, cache_path=args.cache, **options)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
        with path.open("x", encoding="utf-8") as output:
            output.write(text+"\n")
        print(text)
        print(f"保存先: {path.resolve()}", file=sys.stderr)
        return 0
    except HTTPError as error:
        print(f"気象取得に失敗しました（HTTP {error.code}）。自動再試行はしません。", file=sys.stderr)
        return 1
    except (OSError, URLError, TimeoutError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
        print(f"気象取得を停止しました: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
