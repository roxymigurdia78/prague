"""Resolve coarse calendar constraints to a reproducible sampled local date.

No conversational model, network calls, or Blender changes are involved.
"""
import argparse
import calendar
from datetime import date as Date, datetime, timezone
import json
from pathlib import Path
import random
import secrets
import sys

from streetlab.paths import ROOT

SEASONS = ("spring", "summer", "autumn", "winter")
ALIASES = {"春": "spring", "夏": "summer", "秋": "autumn", "冬": "winter",
           "fall": "autumn", **{value: value for value in SEASONS}}
FIELDS = {"season", "month", "day", "year", "date", "hemisphere"}


def _integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer in {low}..{high}")
    return value


def resolve_date(constraints=None, *, seed=None, config_path=None):
    """Return a sampled date and assumptions, without writing files.

    With no season/month/date, sample one season uniformly, then one allowed day
    uniformly. A month restricts sampling to that month. Conflicting explicit
    constraints are rejected. Dates always belong to the selected calendar year.
    """
    supplied = {} if constraints is None else constraints
    if not isinstance(supplied, dict) or set(supplied) - FIELDS:
        raise ValueError("Date constraints must be an object containing only " + ", ".join(sorted(FIELDS)))
    supplied = {key: value for key, value in supplied.items() if value is not None}
    config = json.loads(Path(config_path or ROOT / "config" / "date_selection.json").read_text(encoding="utf-8-sig"))
    months_by_season = config["north_season_months"]
    if not isinstance(months_by_season, dict) or set(months_by_season) != set(SEASONS):
        raise ValueError("Configuration must define four seasons")
    months = []
    for season in SEASONS:
        values = months_by_season[season]
        if not isinstance(values, list) or not values:
            raise ValueError("Each season must have at least one month")
        months.extend(_integer(value, "season month", 1, 12) for value in values)
    if sorted(months) != list(range(1, 13)):
        raise ValueError("Configured seasons must partition the twelve months")
    hemisphere = supplied.get("hemisphere", config["default_hemisphere"])
    if hemisphere not in ("north", "south"):
        raise ValueError("hemisphere must be north or south")
    if hemisphere == "south":
        months_by_season = {key: [((month + 5) % 12) + 1 for month in value]
                            for key, value in months_by_season.items()}
    season = supplied.get("season")
    if season is not None:
        if not isinstance(season, str) or season.strip().lower() not in ALIASES:
            raise ValueError("season must be 春/夏/秋/冬 or spring/summer/autumn/winter")
        season = ALIASES[season.strip().lower()]
    exact = None
    if "date" in supplied:
        value = supplied["date"]
        if not isinstance(value, str) or len(value) != 10:
            raise ValueError("date must use YYYY-MM-DD")
        exact = Date.fromisoformat(value)
        if exact.isoformat() != value:
            raise ValueError("date must use YYYY-MM-DD")
    year = _integer(supplied.get("year", exact.year if exact else config["default_year"]), "year", 1, 9999)
    month = _integer(supplied["month"], "month", 1, 12) if "month" in supplied else None
    day = _integer(supplied["day"], "day", 1, 31) if "day" in supplied else None
    if exact and (year != exact.year or (month is not None and month != exact.month) or
                  (day is not None and day != exact.day)):
        raise ValueError("Explicit date conflicts with year/month/day")
    if exact:
        month, day = exact.month, exact.day
    if month is not None:
        month_season = next(key for key in SEASONS if month in months_by_season[key])
        if season is not None and season != month_season:
            raise ValueError("Explicit month/date conflicts with the requested season")
        season = month_season
    if seed is None:
        seed = secrets.randbits(64)
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    rng = random.Random(seed)
    random_season = season is None
    if random_season:
        season = rng.choice(SEASONS)
    allowed_months = [month] if month is not None else months_by_season[season]
    candidates = [Date(year, m, d) for m in sorted(allowed_months)
                  for d in range(1, calendar.monthrange(year, m)[1] + 1)
                  if day is None or day == d]
    if not candidates:
        raise ValueError("No valid date satisfies the specified conditions")
    selected = candidates[0] if len(candidates) == 1 else rng.choice(candidates)
    method = "exact_date" if exact or (month is not None and day is not None) else (
        "random_day_in_month" if month is not None else (
            "random_season_then_day" if random_season else "random_day_in_season"))
    return {"schema": "streetlab/date-selection/v1", "date": selected.isoformat(),
            "year": selected.year, "month": selected.month, "day": selected.day,
            "season": season, "hemisphere": hemisphere, "seed": seed,
            "requested": supplied,
            "selection": {"method": method, "candidate_days": len(candidates),
                          "season_was_random": random_season,
                          "year_source": "explicit" if "year" in supplied or exact else "config_default",
                          "hemisphere_source": "explicit" if "hemisphere" in supplied else "config_default"},
            "season_months_used": months_by_season[season]}


def main(argv=None):
    parser = argparse.ArgumentParser(description="季節・月・日付の指定から日付を選び、未指定なら季節からランダムに選択します。")
    parser.add_argument("--season", help="春・夏・秋・冬、または英語名")
    parser.add_argument("--month", type=int, help="指定月の中から日を選択（1〜12）")
    parser.add_argument("--day", type=int, help="日を限定（1〜31）")
    parser.add_argument("--year", type=int, help="指定がなければ設定ファイルの年")
    parser.add_argument("--date", help="具体的な日付 YYYY-MM-DD")
    parser.add_argument("--hemisphere", choices=["north", "south"], help="半球。初期値は北半球")
    parser.add_argument("--seed", type=int, help="同じ入力から同じ日付を再現する乱数seed")
    parser.add_argument("--input", type=Path, help="将来の会話抽出結果と同形式のJSON。CLI指定が優先")
    parser.add_argument("--config", type=Path, help="年・季節区分の設定JSON")
    parser.add_argument("--output", type=Path, help="結果JSONの保存先。既存ファイルは上書きしません")
    args = parser.parse_args(argv)
    try:
        constraints = json.loads(args.input.read_text(encoding="utf-8-sig")) if args.input else {}
        if not isinstance(constraints, dict):
            raise ValueError("Input JSON must be an object")
        constraints.update({key: getattr(args, key) for key in FIELDS if getattr(args, key) is not None})
        result = resolve_date(constraints, seed=args.seed, config_path=args.config)
        path = args.output or ROOT / "outputs" / "date_choices" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ") + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
        with path.open("x", encoding="utf-8") as output:
            output.write(text + "\n")
        print(text)
        print(f"保存先: {path.resolve()}", file=sys.stderr)
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"日付選択を停止しました: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
