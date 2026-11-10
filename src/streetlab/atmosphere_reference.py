"""Small CAMS EAC4 subsets, with provenance and resumable, sequential acquisition.

Planning, setup checks and cached reads are offline. This module does not set sky
parameters: optical depth / total column ozone are not Blender density multipliers.
"""
import argparse
from contextlib import closing
from datetime import date as Date, datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import secrets
import sqlite3
import sys
import time
import zipfile

from streetlab.paths import ROOT
from streetlab.solar_math import get_zone, validate_coordinates
from streetlab.time_selection import _local_time

ENDPOINT = "https://ads.atmosphere.copernicus.eu/api"
DATASET = "cams-global-reanalysis-eac4"
VARIABLES = ("total_aerosol_optical_depth_550nm", "total_column_ozone")
UTC = timezone.utc
# Mass of an O3 column of one Dobson unit, kg/m² (NASA/ECMWF convention).
KG_M2_PER_DU = 2.1415e-5
DATA_URL = "https://ads.atmosphere.copernicus.eu/datasets/" + DATASET
ATTRIBUTION = {
    "source": "Copernicus Atmosphere Monitoring Service (CAMS), ECMWF, EAC4",
    "url": DATA_URL + "?tab=overview", "doi": "10.24381/d58bbf47",
    "license": "CC BY 4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
    "data_type": "reanalysis; observations assimilated into a numerical model",
    "processing": "nearest 0.75 degree grid point and nearest 3-hour UTC analysis; ozone mass column converted to DU",
}


class AtmosphereError(ValueError):
    """A user-facing, locally generated error safe to display without secrets."""


def _settings(path=None):
    config = json.loads(Path(path or ROOT / "config/atmosphere_reference.json").read_text(encoding="utf-8-sig"))
    if config.get("endpoint") != ENDPOINT or config.get("dataset") != DATASET:
        raise AtmosphereError("Only the official ADS endpoint and CAMS EAC4 dataset are supported")
    start, end = (Date.fromisoformat(config[key]) for key in ("available_start_date", "available_end_date"))
    if start < Date(2003, 1, 1) or end < start or end >= Date.today():
        raise AtmosphereError("Invalid published EAC4 availability dates")
    return config, start, end


def _grid_point(latitude, longitude):
    # Longitude is canonicalised so +180 and -180 reuse the same subset.
    lat = max(-90.0, min(90.0, math.floor(latitude / .75 + .5) * .75))
    lon = (math.floor(longitude / .75 + .5) * .75 + 180) % 360 - 180
    return lat, lon


def _request_key(request):
    payload = json.dumps({"dataset": DATASET, "request": request}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def plan_atmosphere(location, selected_datetime, *, reference_year=None, past_years=1, config_path=None):
    """Return a serialisable acquisition plan; no imports of SDKs, writes or network."""
    config, start, end = _settings(config_path)
    latitude, longitude = location["latitude"], location["longitude"]
    validate_coordinates(latitude, longitude)
    zone = get_zone(location["timezone"])
    when = datetime.fromisoformat(selected_datetime.replace("Z", "+00:00")) if isinstance(selected_datetime, str) else selected_datetime
    if not isinstance(when, datetime) or when.utcoffset() is None:
        raise AtmosphereError("A timezone-aware render datetime is required")
    local = when.astimezone(zone)
    year = config["default_reference_year"] if reference_year is None else reference_year
    if type(year) is not int or not start.year <= year <= end.year:
        raise AtmosphereError(f"Reference year must be in published EAC4 coverage: {start.year}..{end.year}")
    if type(past_years) is not int or not 1 <= past_years <= 20 or year - past_years + 1 < start.year:
        raise AtmosphereError("past_years must be 1..20 within published EAC4 coverage")
    lat, lon = _grid_point(latitude, longitude)
    # A narrow box enclosing a native grid point; no regridding is requested.
    area = [round(min(90, lat + .05), 6), round(max(-180, lon - .05), 6),
            round(max(-90, lat - .05), 6), round(min(180, lon + .05), 6)]
    items, skipped = [], []
    for source_year in range(year - past_years + 1, year + 1):
        try:
            day = Date(source_year, local.month, local.day)
            reference = _local_time(day, local.strftime("%H:%M:%S"), zone, local.fold) + timedelta(microseconds=local.microsecond)
            utc = reference.astimezone(UTC)
            base = utc.replace(hour=utc.hour // 3 * 3, minute=0, second=0, microsecond=0)
            source = base + timedelta(hours=3) if utc - base > timedelta(minutes=90) else base
            if not start <= source.date() <= end:
                raise AtmosphereError("Nearest UTC analysis falls outside published EAC4 coverage")
        except ValueError as error:
            if past_years == 1:
                raise AtmosphereError(f"Invalid reference date/time in {source_year}: {error}") from error
            skipped.append({"year": source_year, "reason": str(error)})
            continue
        request = {"variable": list(VARIABLES), "date": [source.date().isoformat()],
                   "time": [source.strftime("%H:%M")], "area": area, "data_format": "netcdf_zip"}
        items.append({"reference_year": source_year, "reference_local_datetime": reference.isoformat(),
                      "source_utc_datetime": source.isoformat(), "source_local_datetime": source.astimezone(zone).isoformat(),
                      "sample_offset_seconds": (source - utc).total_seconds(),
                      "expected_grid": {"latitude": lat, "longitude": lon},
                      "request": request, "cache_key": _request_key(request)})
    if not items:
        raise AtmosphereError("No valid reference dates in the requested years")
    return {"schema": "streetlab/atmosphere-plan/v1", "dataset": DATASET, "endpoint": ENDPOINT,
            "render_local_datetime": local.isoformat(), "location": dict(location),
            "reference_year": year, "past_years": past_years, "requests": items, "skipped_years": skipped,
            "maximum_new_data_requests": len(items), "availability": {"start": str(start), "end": str(end)},
            "sampling": "nearest native grid; nearest 3-hour UTC analysis, earlier at a time tie; no interpolation"}


def _env_values(path):
    values = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            name, separator, value = line.strip().partition("=")
            if separator and name.strip() in {"CDSAPI_KEY", "CAMS_EAC4_LICENSE_ACCEPTED"}:
                values[name.strip()] = value.strip().strip("\"'")
    return values


def _credentials():
    values = _env_values(ROOT / ".env")
    key = os.environ.get("CDSAPI_KEY", "").strip() or values.get("CDSAPI_KEY", "").strip()
    if not key:
        path = Path(os.environ.get("CDSAPI_RC", str(Path.home() / ".cdsapirc")))
        if path.is_file():
            rc = {}
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                name, sep, value = line.partition(":")
                if sep:
                    rc[name.strip()] = value.strip().strip("\"'")
            if rc.get("url", "").rstrip("/") == ENDPOINT:
                key = rc.get("key", "")
    accepted = (os.environ.get("CAMS_EAC4_LICENSE_ACCEPTED") or values.get("CAMS_EAC4_LICENSE_ACCEPTED", "")).lower() == "true"
    return key, accepted


def setup_status():
    """Report booleans only, never tokens; do not call any remote endpoint."""
    import importlib.util
    key, accepted = _credentials()
    return {"api_key_present": bool(key), "manual_license_confirmation_present": accepted,
            "cdsapi_installed": importlib.util.find_spec("cdsapi") is not None,
            "netcdf4_installed": importlib.util.find_spec("netCDF4") is not None,
            "license_page": DATA_URL + "?tab=download",
            "note": "Local confirmation only; the service verifies actual account acceptance when retrieving data"}


def _new_client():
    key, accepted = _credentials()
    if not key or not accepted:
        raise AtmosphereError("ADSでEAC4の利用条件に手動同意し、.envにCDSAPI_KEYとCAMS_EAC4_LICENSE_ACCEPTED=trueを設定してください")
    if ":" in key:
        raise AtmosphereError("Use the current ADS personal access token, not a legacy uid:key")
    try:
        import cdsapi
        import netCDF4  # noqa: F401 -- fail before submission if the reader is absent
    except ImportError as error:
        raise AtmosphereError('Install the atmosphere extra: python -m pip install -e ".[atmosphere]"') from error
    # One HTTP attempt, sequential jobs, no automatic HTTP-error retry.
    # delete=False keeps server jobs available for resuming after interruption.
    return cdsapi.Client(url=ENDPOINT, key=key, quiet=True, debug=False, progress=False,
                         retry_max=1, sleep_max=30, timeout=60, delete=False, wait_until_complete=False,
                         info_callback=lambda *a, **k: None, warning_callback=lambda *a, **k: None,
                         error_callback=lambda *a, **k: None, debug_callback=lambda *a, **k: None)


def _netcdf_rows(payload, expected):
    """Read classic/NetCDF4 bytes; validate units, coordinates and exact UTC time."""
    import netCDF4
    import numpy as np
    out = {}
    with netCDF4.Dataset("eac4-memory.nc", memory=payload) as dataset:
        variables = dataset.variables
        def coordinate(names):
            matches = [variables[name] for name in names if name in variables]
            if not matches:
                raise AtmosphereError(f"Missing NetCDF coordinate: {names[0]}")
            result = matches[0]
            if result.ndim != 1 or result.size == 0 or np.ma.getmaskarray(result[:]).any():
                raise AtmosphereError("Invalid NetCDF coordinate")
            return result
        latitude = coordinate(("latitude", "lat"))
        longitude = coordinate(("longitude", "lon"))
        clock = coordinate(("valid_time", "time"))
        if getattr(clock, "calendar", "standard") not in {"standard", "gregorian", "proleptic_gregorian"}:
            raise AtmosphereError("Unsupported NetCDF calendar")
        dates = netCDF4.num2date(clock[:], units=clock.units, calendar=getattr(clock, "calendar", "standard"),
                                only_use_cftime_datetimes=False, only_use_python_datetimes=True)
        target = datetime.fromisoformat(expected["source_utc_datetime"])
        time_indices = [index for index, value in enumerate(dates) if value.replace(tzinfo=UTC) == target]
        if len(time_indices) != 1:
            raise AtmosphereError("NetCDF does not contain exactly one requested UTC analysis")
        latitudes = np.asarray(latitude[:], dtype=float)
        longitudes = (np.asarray(longitude[:], dtype=float) + 180) % 360 - 180
        if not np.isfinite(latitudes).all() or not np.isfinite(longitudes).all():
            raise AtmosphereError("Non-finite NetCDF coordinates")
        grid = expected["expected_grid"]
        ilat = int(np.argmin(abs(latitudes - grid["latitude"])))
        ilon = int(np.argmin(abs((longitudes - grid["longitude"] + 180) % 360 - 180)))
        if abs(latitudes[ilat] - grid["latitude"]) > 1e-4 or abs((longitudes[ilon] - grid["longitude"] + 180) % 360 - 180) > 1e-4:
            raise AtmosphereError("Returned coordinates do not match the requested native EAC4 grid point")
        axes = {latitude.dimensions[0]: ilat, longitude.dimensions[0]: ilon, clock.dimensions[0]: time_indices[0]}
        for name, aliases in {"aod550": ("aod550", VARIABLES[0]), "total_column_ozone": ("gtco3", "tco3", VARIABLES[1])}.items():
            present = [alias for alias in aliases if alias in variables]
            if not present:
                continue  # ZIP can separate the two variables into different files.
            var = variables[present[0]]
            if not set(axes) <= set(var.dimensions):
                raise AtmosphereError("Atmospheric variable is missing time/latitude/longitude dimensions")
            indices = []
            for dimension in var.dimensions:
                if dimension in axes:
                    indices.append(axes[dimension])
                elif len(dataset.dimensions[dimension]) == 1:
                    indices.append(0)
                else:
                    raise AtmosphereError("Unexpected non-singleton dimension in EAC4 response")
            raw = var[tuple(indices)]
            if np.ma.is_masked(raw) or not math.isfinite(float(raw)) or float(raw) < 0:
                raise AtmosphereError(f"Missing or invalid EAC4 value: {name}")
            value = float(raw)
            units = getattr(var, "units", "").lower().replace(" ", "").replace("**", "^")
            if name == "aod550":
                if units not in {"1", "dimensionless", "~", "(0-1)"}:
                    raise AtmosphereError("Unexpected AOD units")
                out["aod550"] = value
            else:
                if units not in {"kgm^-2", "kgm-2", "kg/m^2", "kg/m2"}:
                    raise AtmosphereError("Expected ozone total-column mass in kg/m²")
                out["total_column_ozone_kg_m2"] = value
                out["total_column_ozone_du"] = value / KG_M2_PER_DU
        out["api_grid"] = {"latitude": float(latitudes[ilat]), "longitude": float(longitudes[ilon])}
    return out


def read_subset(path, expected):
    """Handle ADS ZIP with split variables, or a direct NetCDF, without extracting paths."""
    path = Path(path)
    if path.stat().st_size > 32 * 1024 * 1024:
        raise AtmosphereError("Subset exceeds this application's 32 MiB download bound")
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            members = [member for member in archive.infolist() if not member.is_dir()]
            if not members or len(members) > 8 or sum(member.file_size for member in members) > 32 * 1024 * 1024:
                raise AtmosphereError("Unexpected or oversized EAC4 archive")
            if any(not member.filename.lower().endswith((".nc", ".nc4", ".netcdf")) for member in members):
                raise AtmosphereError("Expected only NetCDF members in the EAC4 archive")
            rows = [_netcdf_rows(archive.read(member), expected) for member in members]
    else:
        rows = [_netcdf_rows(path.read_bytes(), expected)]
    merged = {}
    for row in rows:
        for key, value in row.items():
            if key in merged and merged[key] != value:
                raise AtmosphereError("Conflicting EAC4 values or coordinates across archive members")
            merged[key] = value
    if not {"aod550", "total_column_ozone_kg_m2", "total_column_ozone_du", "api_grid"} <= merged.keys():
        raise AtmosphereError("Both AOD550 and total column ozone are required")
    return {"source_utc_datetime": expected["source_utc_datetime"], **merged,
            "units": {"aod550": "1", "total_column_ozone_kg_m2": "kg/m²", "total_column_ozone_du": "DU"}}


def _atomic_json(path, data):
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _fetch_subset(item, cache_dir):
    """Serialize cache access and persist remote IDs before waiting/downloading.

    The SQLite write transaction also prevents two local processes from submitting
    the same request. A row with no remote ID after interruption requires review;
    do not resubmit an uncertain POST automatically.
    """
    folder = Path(cache_dir)
    folder.mkdir(parents=True, exist_ok=True)
    key = item["cache_key"]
    path = folder / (key + ".zip")
    manifest = folder / (key + ".json")
    with closing(sqlite3.connect(folder / "requests.sqlite3", timeout=5)) as db:
        db.execute("CREATE TABLE IF NOT EXISTS jobs (key TEXT PRIMARY KEY, submitted REAL NOT NULL, remote_id TEXT)")
        db.execute("BEGIN IMMEDIATE")
        try:
            if path.is_file() and manifest.is_file():
                meta = json.loads(manifest.read_text(encoding="utf-8"))
                if meta["request"] != item["request"] or hashlib.sha256(path.read_bytes()).hexdigest() != meta["sha256"]:
                    raise AtmosphereError("EAC4 cache integrity check failed; original files retained for inspection")
                data = read_subset(path, item)
                return {"from_cache": True, "raw_file": str(path.resolve()), "fetched_at": meta["fetched_at"],
                        "remote_id": meta["remote_id"], "selected_sample": data}
            # Client construction verifies local credentials/dependencies before a submission.
            client = _new_client()
            stored = db.execute("SELECT remote_id FROM jobs WHERE key=?", (key,)).fetchone()
            if stored:
                if not stored[0]:
                    raise AtmosphereError("Previous submission outcome is uncertain. Check ADS Your requests and the local job ledger before resubmitting")
                remote = client.client.get_remote(stored[0])
            else:
                now = time.time()
                if db.execute("SELECT COUNT(*) FROM jobs WHERE submitted>?", (now - 86400,)).fetchone()[0] >= 20:
                    raise AtmosphereError("Local conservative limit: 20 new EAC4 submissions in 24 hours; use existing cache or wait")
                # Reserve durably before POST, so an interruption cannot silently duplicate it.
                db.execute("INSERT INTO jobs VALUES (?,?,NULL)", (key, now))
                db.commit()
                db.execute("BEGIN IMMEDIATE")
                remote = client.retrieve(DATASET, item["request"])
                db.execute("UPDATE jobs SET remote_id=? WHERE key=?", (remote.request_id, key))
                db.commit()
                db.execute("BEGIN IMMEDIATE")
            temporary = folder / (key + ".download.part")
            print(f"EAC4: {item['source_utc_datetime']}（処理待ち・ダウンロード）", file=sys.stderr)
            remote.download(str(temporary))
            data = read_subset(temporary, item)
            fetched = datetime.now(UTC).isoformat()
            temporary.replace(path)
            _atomic_json(manifest, {"dataset": DATASET, "request": item["request"], "remote_id": remote.request_id,
                                   "fetched_at": fetched, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                   "attribution": ATTRIBUTION})
            return {"from_cache": False, "raw_file": str(path.resolve()), "fetched_at": fetched,
                    "remote_id": remote.request_id, "selected_sample": data}
        finally:
            db.rollback()


def fetch_atmosphere(location, selected_datetime, *, reference_year=None, past_years=1,
                     sample_year=False, seed=None, config_path=None, cache_dir=None):
    """Fetch one matched sample per year; retain both fields from the same analysis."""
    plan = plan_atmosphere(location, selected_datetime, reference_year=reference_year, past_years=past_years, config_path=config_path)
    if type(sample_year) is not bool or (seed is not None and type(seed) is not int):
        raise AtmosphereError("sample_year must be boolean and seed must be integer")
    seed = seed if seed is not None else secrets.randbits(64) if sample_year else None
    records = [{**item, **_fetch_subset(item, cache_dir or ROOT / "outputs/atmosphere_reference/cache")} for item in plan["requests"]]
    selected = random.Random(seed).choice(records) if sample_year else next((row for row in records if row["reference_year"] == plan["reference_year"]), None)
    return {"schema": "streetlab/atmosphere-reference/v1", "dataset": DATASET, "location": dict(location),
            "render_local_datetime": plan["render_local_datetime"], "reference_year": plan["reference_year"],
            "past_years": past_years, "seed": seed,
            "selection_method": "random_reference_year" if sample_year else "fixed_reference_year",
            "selected_reference_year": selected["reference_year"] if selected else None,
            "selected_sample": selected["selected_sample"] if selected else None,
            "selection_note": None if selected else "Configured reference year has no valid matching analysis; choose another year explicitly",
            "records": records, "skipped_years": plan["skipped_years"], "attribution": ATTRIBUTION,
            "blender_mapping": "not_calibrated; do not assign AOD or DU directly to sky density multipliers"}


def main(argv=None):
    parser = argparse.ArgumentParser(description="CAMS EAC4のAOD550・オゾン全量を取得。既定2025年。")
    parser.add_argument("--date", help="描画日付 YYYY-MM-DD")
    parser.add_argument("--time", help="描画の現地時刻 HH:MM[:SS]")
    parser.add_argument("--fold", type=int, choices=(0, 1))
    parser.add_argument("--from-time", type=Path, help="保存済みtime結果JSON")
    parser.add_argument("--from-weather", type=Path, help="weather結果JSONの採用年・日時・地点を引き継ぐ")
    parser.add_argument("--reference-year", type=int)
    parser.add_argument("--past-years", type=int, default=1, help="同じ月日の1〜20年")
    parser.add_argument("--sample-year", action="store_true")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--latitude", type=float)
    parser.add_argument("--longitude", type=float)
    parser.add_argument("--timezone")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="計画のみ。API呼び出し・ファイル作成なし")
    parser.add_argument("--check-setup", action="store_true", help="認証・依存関係の有無のみ確認。通信なし")
    args = parser.parse_args(argv)
    try:
        if args.check_setup:
            print(json.dumps(setup_status(), ensure_ascii=False, indent=2))
            return 0
        if args.from_time and args.from_weather:
            raise AtmosphereError("Choose either --from-time or --from-weather")
        if args.from_time or args.from_weather:
            if any(value is not None for value in (args.date, args.time, args.latitude, args.longitude, args.timezone, args.fold)):
                raise AtmosphereError("Saved input cannot be combined with date/time/location overrides")
            saved = json.loads((args.from_time or args.from_weather).read_text(encoding="utf-8-sig"))
            location = saved["location"]
            when = datetime.fromisoformat(saved["local_datetime"] if args.from_time else saved["render_local_datetime"])
            if args.from_weather:
                if args.reference_year is not None or args.past_years != 1 or args.sample_year or args.seed is not None:
                    raise AtmosphereError("--from-weather uses the weather result's selected year; do not override year selection")
                if type(saved.get("selected_reference_year")) is not int:
                    raise AtmosphereError("Weather result has no selected reference year")
                args.reference_year = saved["selected_reference_year"]
        else:
            if not args.date or not args.time:
                raise AtmosphereError("Specify --date and --time, --from-time, or --from-weather")
            overrides = {key: getattr(args, key) for key in ("latitude", "longitude", "timezone") if getattr(args, key) is not None}
            if overrides and len(overrides) != 3:
                raise AtmosphereError("Specify latitude, longitude and timezone together")
            location = overrides or json.loads((ROOT / "config/time_selection.json").read_text(encoding="utf-8-sig"))["default_location"]
            when = _local_time(Date.fromisoformat(args.date), args.time, get_zone(location["timezone"]), args.fold)
        options = {"reference_year": args.reference_year, "past_years": args.past_years, "config_path": args.config}
        if args.dry_run:
            print(json.dumps(plan_atmosphere(location, when, **options), ensure_ascii=False, indent=2))
            return 0
        output = args.output or ROOT / "outputs/atmosphere_reference" / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S_%fZ") + ".json")
        if output.exists():
            raise AtmosphereError("Output already exists; choose a new filename")
        result = fetch_atmosphere(location, when, sample_year=args.sample_year, seed=args.seed, cache_dir=args.cache_dir, **options)
        output.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
        with output.open("x", encoding="utf-8") as handle:
            handle.write(text + "\n")
        print(text)
        print(f"保存先: {output.resolve()}", file=sys.stderr)
        return 0
    except Exception as error:
        # SDK exceptions may contain a token or signed download URL. Report no raw SDK errors.
        if isinstance(error, AtmosphereError):
            message = str(error)
        else:
            message = "入出力・データ形式・接続を確認してください。認証情報は表示しません。"
        print(f"大気データ取得を停止しました: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
