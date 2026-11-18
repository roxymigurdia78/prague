"""Open-Meteo place search for non-commercial research; no Blender mutations."""
import argparse
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from streetlab.paths import ROOT
from streetlab.open_meteo_http import request_json

ENDPOINT = "https://geocoding-api.open-meteo.com/v1/search"
ATTRIBUTION = {
    "service": "Open-Meteo Geocoding API",
    "service_url": "https://open-meteo.com/en/docs/geocoding-api",
    "data_source": "GeoNames",
    "data_source_url": "https://www.geonames.org/",
    "license": "CC BY 4.0",
    "license_url": "https://creativecommons.org/licenses/by/4.0/",
}


def _validate_results(body):
    if not isinstance(body, dict) or body.get("error"):
        raise ValueError("地名検索APIからエラーが返りました。")
    results = body.get("results", [])
    if not isinstance(results, list):
        raise ValueError("地名検索APIの結果形式が不正です。")
    for row in results:
        if not isinstance(row, dict):
            raise ValueError("地名検索APIの候補形式が不正です。")
        for key, low, high in [("latitude", -90, 90), ("longitude", -180, 180)]:
            value = row.get(key)
            if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError("地名検索APIの座標が不正です。")
        elevation = row.get("elevation")
        if elevation is not None and (type(elevation) not in (int, float) or not math.isfinite(elevation)):
            raise ValueError("地名検索APIの標高が不正です。")
    return results


def search_locations(name, *, country=None, language=None, count=None,
                     config_path=None, cache_path=None):
    """Return candidates with provenance; elevation is metres above mean sea level.

    No candidate is chosen automatically. Missing elevation/timezone stay missing.
    Cache and request budget persist across CLI runs sharing the same database.
    """
    config_path = Path(config_path) if config_path else ROOT / "config" / "geocoding.json"
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    if config.get("endpoint") != ENDPOINT or config.get("usage") != "non-commercial":
        raise ValueError("この入口はOpen-Meteoの無料・非商用エンドポイント用です。")
    name = name.strip()
    country = country.strip().upper() if country else None
    language = language or config.get("language", "ja")
    count = count if count is not None else config.get("count", 5)
    if len(name) < 2:
        raise ValueError("地名は2文字以上で指定してください。")
    if country and not re.fullmatch(r"[A-Z]{2}", country):
        raise ValueError("国はCZ、DE、JPなど2文字の国コードで指定してください。")
    if not isinstance(language, str) or not re.fullmatch(r"[a-z]{2,3}", language):
        raise ValueError("言語はja、enなどの言語コードで指定してください。")
    if type(count) is not int or not 1 <= count <= 100:
        raise ValueError("候補数は1〜100で指定してください。")
    params = {"name": name, "count": count, "language": language, "format": "json"}
    if country:
        params["countryCode"] = country
    request_url = ENDPOINT + "?" + urlencode(params)
    response = request_json(request_url,
                            cache_path=cache_path or ROOT / "outputs" / "geocoding" / "cache.sqlite3",
                            validate=_validate_results, fetch=urlopen)
    return {"schema": "streetlab/location-search/v1", "query": params,
            "request_url": request_url, "fetched_at": response["fetched_at"],
            "attribution": ATTRIBUTION, "results": _validate_results(response["body"]),
            "from_cache": response["from_cache"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description="地名から緯度・経度・標高・タイムゾーンの候補を取得。空は変更しません。")
    parser.add_argument("name", help="都市名または郵便番号")
    parser.add_argument("--country", help="CZ、DE、JPなどの国コード")
    parser.add_argument("--language", help="返却言語（初期値ja）")
    parser.add_argument("--count", type=int, help="候補数（初期値5）")
    parser.add_argument("--config", type=Path, help="設定JSON")
    parser.add_argument("--cache", type=Path, help="キャッシュと呼び出し回数を保存するSQLiteファイル")
    args = parser.parse_args(argv)
    try:
        result = search_locations(args.name, country=args.country, language=args.language,
                                  count=args.count, config_path=args.config, cache_path=args.cache)
    except HTTPError as error:
        print(f"地名検索に失敗しました（HTTP {error.code}）。自動再送はしません。", file=sys.stderr)
        return 1
    except (OSError, URLError, TimeoutError, ValueError, RuntimeError, sqlite3.Error) as error:
        print(f"地名検索を停止しました: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
