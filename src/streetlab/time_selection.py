"""Sample time periods relative to local solar events; never modify Blender."""
import argparse
from datetime import date as Date, datetime, time as Time, timedelta, timezone
import json
from pathlib import Path
import random
import re
import secrets
import sys

from streetlab.date_selection import resolve_date
from streetlab.paths import ROOT
from streetlab.solar_math import solar_day, solar_position, validate_coordinates, get_zone, SUNRISE_ELEVATION, CIVIL_ELEVATION

PERIODS = ("dawn", "early_morning", "morning", "noon", "afternoon", "evening", "night", "midnight")
ALIASES = {"明け方": "dawn", "早朝": "early_morning", "朝": "morning", "昼": "noon",
           "午後": "afternoon", "夕方": "evening", "夜": "night", "深夜": "midnight",
           **{key: key for key in PERIODS}}


def _period(local, sun, events, config):
    elevation = sun["elevation_deg"]
    if elevation < SUNRISE_ELEVATION:
        if sun["hour_angle_deg"] < 0 and elevation >= CIVIL_ELEVATION:
            return "dawn"
        if elevation < CIVIL_ELEVATION and (local.hour >= config["deep_night_start_hour"] or
                                            local.hour < config["deep_night_end_hour"]):
            return "midnight"
        return "night"
    moment = local.astimezone(timezone.utc)
    noon = datetime.fromisoformat(events["solar_noon"]).astimezone(timezone.utc)
    sunrise = datetime.fromisoformat(events["sunrise"]).astimezone(timezone.utc) if events["sunrise"] else None
    sunset = datetime.fromisoformat(events["sunset"]).astimezone(timezone.utc) if events["sunset"] else None
    before = (noon-sunrise).total_seconds() if sunrise and sunrise < noon else None
    after = (sunset-noon).total_seconds() if sunset and sunset > noon else None
    noon_before = min(config["noon_half_width_minutes"]*60, before/3) if before else config["noon_half_width_minutes"]*60
    noon_after = min(config["noon_half_width_minutes"]*60, after/3) if after else config["noon_half_width_minutes"]*60
    if -noon_before <= (moment-noon).total_seconds() < noon_after:
        return "noon"
    if moment < noon:
        early = min(config["early_morning_minutes"]*60, before/3) if before else None
        return "early_morning" if early and 0 <= (moment-sunrise).total_seconds() < early else "morning"
    late = min(config["evening_minutes"]*60, after/3) if after else None
    return "evening" if late and 0 < (sunset-moment).total_seconds() <= late else "afternoon"


def _local_time(day, clock, zone, fold):
    if not isinstance(clock, str) or not re.fullmatch(r"\d{2}:\d{2}(:\d{2})?", clock):
        raise ValueError("time must use HH:MM or HH:MM:SS")
    if fold is not None and (type(fold) is not int or fold not in (0, 1)):
        raise ValueError("fold must be 0 or 1")
    naive = datetime.combine(day, Time.fromisoformat(clock))
    candidates = {}
    for option in (0, 1):
        value = naive.replace(tzinfo=zone, fold=option)
        instant = value.astimezone(timezone.utc)
        back = instant.astimezone(zone)
        if back.replace(tzinfo=None) == naive:
            candidates[instant] = back
    if not candidates:
        raise ValueError("指定時刻は夏時間への切り替えで存在しません。")
    if len(candidates) > 1:
        if fold is None:
            raise ValueError("指定時刻は夏時間終了で2回あります。fold=0（前）または1（後）を指定してください。")
        return next(value for value in candidates.values() if value.fold == fold)
    return next(iter(candidates.values()))


def resolve_time(location, selected_date, constraints=None, *, seed=None, config_path=None):
    """Return sampled local/UTC timestamps, solar events/angles, seed and provenance.

    location is one selected geocoding candidate, not a list of search results.
    selected_date is YYYY-MM-DD or an output of resolve_date(). No API calls occur.
    """
    if not isinstance(location, dict):
        raise ValueError("location must be a selected location object")
    latitude, longitude, tz_name = location["latitude"], location["longitude"], location["timezone"]
    validate_coordinates(latitude, longitude)
    zone = get_zone(tz_name)
    date_text = selected_date.get("date") if isinstance(selected_date, dict) else selected_date
    day = Date.fromisoformat(date_text)
    requested = {} if constraints is None else constraints
    if not isinstance(requested, dict) or set(requested)-{"period", "time", "fold"}:
        raise ValueError("Time constraints accept only period, time, fold")
    requested = {key: value for key, value in requested.items() if value is not None}
    config = json.loads(Path(config_path or ROOT/"config"/"time_selection.json").read_text(encoding="utf-8-sig"))
    for key in ("early_morning_minutes", "noon_half_width_minutes", "evening_minutes"):
        if type(config[key]) not in (int, float) or not 0 < config[key] <= 180:
            raise ValueError(f"Invalid time window configuration: {key}")
    for key in ("deep_night_start_hour", "deep_night_end_hour"):
        if type(config[key]) is not int or not 0 <= config[key] <= 23:
            raise ValueError(f"Invalid clock-hour configuration: {key}")
    if config["deep_night_start_hour"] <= config["deep_night_end_hour"]:
        raise ValueError("Deep-night interval must cross midnight")
    period = requested.get("period")
    if period is not None:
        if not isinstance(period, str) or period.strip().lower() not in ALIASES:
            raise ValueError("Unknown time period")
        period = ALIASES[period.strip().lower()]
    if seed is None:
        seed = secrets.randbits(64)
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    rng = random.Random(seed)
    solar = solar_day(day, latitude, longitude, tz_name)
    groups = {key: [] for key in PERIODS}
    start = datetime.combine(day, Time(), zone).astimezone(timezone.utc)
    end = datetime.combine(day+timedelta(days=1), Time(), zone).astimezone(timezone.utc)
    for offset in range(0, int((end-start).total_seconds()), 60):
        instant = start+timedelta(seconds=offset)
        local = instant.astimezone(zone)
        if local.date() != day:
            continue
        sun = solar_position(latitude, longitude, local)
        groups[_period(local, sun, solar["events"], config)].append(local)
    available = [key for key in PERIODS if groups[key]]
    if "time" in requested:
        chosen = _local_time(day, requested["time"], zone, requested.get("fold"))
        actual_period = _period(chosen, solar_position(latitude, longitude, chosen), solar["events"], config)
        if period is not None and period != actual_period:
            raise ValueError("Specified clock time conflicts with requested time period")
        period = actual_period
        method = "explicit_clock_time"
        count = 1
    else:
        if "fold" in requested:
            raise ValueError("fold is only used with an explicit clock time")
        if period is None:
            period = rng.choice(available)
            method = "random_available_period_then_minute"
        else:
            method = "random_minute_in_period"
        if not groups[period]:
            raise ValueError(f"この場所・日付に指定の時間帯はありません: {period}; available={available}")
        chosen = rng.choice(groups[period])
        count = len(groups[period])
    return {"schema": "streetlab/time-selection/v1", "date": day.isoformat(),
            "local_datetime": chosen.isoformat(timespec="seconds"),
            "utc_datetime": chosen.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "timezone": tz_name, "fold": chosen.fold, "period": period, "seed": seed,
            "location": dict(location), "requested": requested,
            "solar_position": solar_position(latitude, longitude, chosen), "solar_day": solar,
            "selection": {"method": method, "candidate_instants": count,
                          "available_periods": available,
                          "unavailable_periods": [key for key in PERIODS if key not in available],
                          "sampling_resolution_seconds": 60 if "time" not in requested else None},
            "time_window_config": {key: value for key, value in config.items() if key != "default_location"}}


def main(argv=None):
    parser = argparse.ArgumentParser(description="場所・日付の日の出／日没に合わせて時間帯から時刻を選択。太陽高度・方位も保存します。")
    parser.add_argument("--period", help="明け方・早朝・朝・昼・午後・夕方・夜・深夜")
    parser.add_argument("--time", help="現地時刻 HH:MM または HH:MM:SS")
    parser.add_argument("--fold", type=int, choices=[0,1], help="夏時間終了で重複する時刻の前後")
    parser.add_argument("--date", help="YYYY-MM-DD")
    parser.add_argument("--season", help="日付を季節内から選択")
    parser.add_argument("--month", type=int, help="日付を指定月内から選択")
    parser.add_argument("--day", type=int)
    parser.add_argument("--year", type=int)
    parser.add_argument("--hemisphere", choices=["north", "south"])
    parser.add_argument("--latitude", type=float)
    parser.add_argument("--longitude", type=float)
    parser.add_argument("--timezone", help="Europe/PragueなどのIANA名")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--input", type=Path, help="場所・日付条件・時間帯の入力JSON")
    parser.add_argument("--config", type=Path, help="時間帯・既定地点の設定JSON")
    parser.add_argument("--output", type=Path, help="保存先。既存ファイルは上書きしません")
    args = parser.parse_args(argv)
    try:
        config_path = args.config or ROOT/"config"/"time_selection.json"
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        payload = json.loads(args.input.read_text(encoding="utf-8-sig")) if args.input else {}
        allowed = {"location", "date", "season", "month", "day", "year", "hemisphere", "period", "time", "fold"}
        if not isinstance(payload, dict) or set(payload)-allowed:
            raise ValueError("Unexpected time-selection input fields")
        location = dict(payload.get("location", config["default_location"]))
        overrides = {key: getattr(args, key) for key in ("latitude", "longitude", "timezone") if getattr(args, key) is not None}
        if overrides and len(overrides) != 3:
            raise ValueError("地点を変更するときはlatitude・longitude・timezoneをまとめて指定してください。")
        if overrides:
            location = overrides
        for key in allowed-{"location"}:
            if getattr(args, key) is not None:
                payload[key] = getattr(args, key)
        seed = args.seed if args.seed is not None else secrets.randbits(64)
        date_constraints = {key: payload[key] for key in ("date", "season", "month", "day", "year", "hemisphere") if key in payload}
        if not date_constraints.get("hemisphere"):
            date_constraints["hemisphere"] = "north" if location["latitude"] >= 0 else "south"
        date_choice = resolve_date(date_constraints, seed=seed)
        result = resolve_time(location, date_choice, {key: payload[key] for key in ("period", "time", "fold") if key in payload}, seed=seed, config_path=config_path)
        result["date_selection"] = date_choice
        result["location_source"] = "cli" if overrides else "input_json" if "location" in payload else "config_default"
        path = args.output or ROOT/"outputs"/"time_choices"/(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")+".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
        with path.open("x", encoding="utf-8") as output:
            output.write(text+"\n")
        print(text)
        print(f"保存先: {path.resolve()}", file=sys.stderr)
        return 0
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"時刻選択を停止しました: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
