"""Shared cache and conservative request budget for the supported free API calls."""
from contextlib import closing
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

from streetlab.paths import ROOT


def validate_single_call(url):
    """Support only one-unit requests: one point, <=10 variables, <=14 days."""
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.username or parts.password or parts.fragment:
        raise ValueError("Unexpected Open-Meteo URL")
    if (parts.netloc, parts.path) == ("geocoding-api.open-meteo.com", "/v1/search"):
        return
    if (parts.netloc, parts.path) != ("archive-api.open-meteo.com", "/v1/archive"):
        raise ValueError("Only the configured free geocoding/archive endpoints are supported")
    params = parse_qs(parts.query)
    for field in ("latitude", "longitude"):
        if len(params.get(field, [])) != 1:
            raise ValueError("Only one weather location per call is supported")
        float(params[field][0])
    variables = params.get("hourly", [""])[0].split(",")
    span = (date.fromisoformat(params["end_date"][0])-date.fromisoformat(params["start_date"][0])).days+1
    if not 1 <= span <= 14 or not 1 <= len(variables) <= 10:
        raise ValueError("Request exceeds this client's one-unit budget rules")


def request_json(url, *, cache_path=None, validate=None, fetch=None):
    """Cache results and log attempts, including failures; never retry automatically.

    The existing geocoding database is retained to preserve its recorded budget.
    Shared-IP calls from other apps/databases cannot be counted here.
    """
    validate_single_call(url)
    fetch = fetch or urlopen
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    database = Path(cache_path) if cache_path else ROOT/"outputs"/"geocoding"/"cache.sqlite3"
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database, timeout=60)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS attempts (at REAL NOT NULL)")
        db.execute("BEGIN IMMEDIATE")
        cached = db.execute("SELECT payload FROM cache WHERE key=?", (key,)).fetchone()
        if cached:
            saved = json.loads(cached[0])
            # Accept caches created before geocoding shared this HTTP client.
            if saved.get("schema") == "streetlab/location-search/v1":
                saved = {"body": {"results": saved["results"]}, "fetched_at": saved["fetched_at"], "request_url": url}
            if validate:
                validate(saved["body"])
            return {**saved, "from_cache": True}
        now = time.time()
        month_start = datetime.fromtimestamp(now, timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
        for cutoff, limit, label in [(now-60,500,"1分"), (now-3600,4500,"1時間"),
                                     (now-86400,9000,"24時間"), (month_start,270000,"今月")]:
            if db.execute("SELECT COUNT(*) FROM attempts WHERE at>=?", (cutoff,)).fetchone()[0] >= limit:
                raise RuntimeError(f"Open-Meteoのローカル上限（{label}）に達しました。")
        last = db.execute("SELECT MAX(at) FROM attempts").fetchone()[0]
        if last is not None:
            time.sleep(max(0, last+2-now))
        db.execute("INSERT INTO attempts VALUES (?)", (time.time(),))
        try:
            request = Request(url, headers={"Accept": "application/json", "User-Agent": "PragueResearch/0.1"})
            with fetch(request, timeout=20) as response:
                body = json.load(response)
            if validate:
                validate(body)
            saved = {"body": body, "request_url": url, "fetched_at": datetime.now(timezone.utc).isoformat()}
            db.execute("INSERT OR REPLACE INTO cache VALUES (?,?)", (key, json.dumps(saved, ensure_ascii=False, allow_nan=False)))
        except BaseException:
            db.commit()
            raise
        db.commit()
    return {**saved, "from_cache": False}
