"""NOAA/Meeus approximate solar geometry; angles in degrees, east longitude positive.

Astronomy uses Python's math library. zoneinfo/tzdata supply civil-time rules only.
Reference: https://gml.noaa.gov/grad/solcalc/calcdetails.html
"""
from datetime import date as Date, datetime, timedelta, timezone, tzinfo
import math
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
SUNRISE_ELEVATION = -0.833
CIVIL_ELEVATION = -6.0


def validate_coordinates(latitude, longitude):
    for value, bound, name in [(latitude, 90, "latitude"), (longitude, 180, "longitude")]:
        if type(value) not in (int, float) or not math.isfinite(value) or not -bound <= value <= bound:
            raise ValueError(f"Invalid {name}")


def get_zone(name):
    if not isinstance(name, str):
        raise ValueError("An IANA timezone name is required")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as error:
        raise ValueError("Unknown timezone or missing tzdata; install project dependencies") from error


def solar_position(latitude, longitude, when):
    """Geometric sun centre, no atmospheric refraction or terrain/parallax correction.

    Azimuth is clockwise from true north, and None at the zenith/nadir singularity.
    This is a compact NOAA/Meeus approximation, not the full NREL SPA algorithm.
    """
    validate_coordinates(latitude, longitude)
    if not isinstance(when, datetime) or when.utcoffset() is None:
        raise ValueError("Solar position requires a timezone-aware datetime")
    utc = when.astimezone(UTC)
    if not 1800 <= utc.year <= 2100:
        raise ValueError("Supported solar calculation years: 1800..2100")
    jd = 2440587.5 + (utc - EPOCH).total_seconds() / 86400
    t = (jd - 2451545.0) / 36525
    rad, deg = math.radians, math.degrees
    mean_long = (280.46646 + t * (36000.76983 + .0003032*t)) % 360
    anomaly = 357.52911 + t * (35999.05029 - .0001537*t)
    eccentricity = .016708634 - t * (.000042037 + .0000001267*t)
    centre = (math.sin(rad(anomaly)) * (1.914602 - t*(.004817 + .000014*t))
              + math.sin(rad(2*anomaly)) * (.019993 - .000101*t)
              + math.sin(rad(3*anomaly)) * .000289)
    omega = 125.04 - 1934.136*t
    apparent_long = mean_long + centre - .00569 - .00478*math.sin(rad(omega))
    seconds = 21.448 - t*(46.815 + t*(.00059 - .001813*t))
    obliquity = 23 + (26 + seconds/60)/60 + .00256*math.cos(rad(omega))
    declination = math.asin(math.sin(rad(obliquity))*math.sin(rad(apparent_long)))
    y = math.tan(rad(obliquity)/2)**2
    equation = 4 * deg(y*math.sin(rad(2*mean_long)) - 2*eccentricity*math.sin(rad(anomaly))
        + 4*eccentricity*y*math.sin(rad(anomaly))*math.cos(rad(2*mean_long))
        - .5*y*y*math.sin(rad(4*mean_long)) - 1.25*eccentricity**2*math.sin(rad(2*anomaly)))
    minutes = utc.hour*60 + utc.minute + (utc.second + utc.microsecond/1e6)/60
    hour_angle = ((minutes + equation + 4*longitude) % 1440)/4 - 180
    lat, ha = rad(latitude), rad(hour_angle)
    sine = math.sin(lat)*math.sin(declination) + math.cos(lat)*math.cos(declination)*math.cos(ha)
    elevation = deg(math.asin(max(-1, min(1, sine))))
    azimuth = None if abs(abs(elevation)-90) < 1e-8 else (
        deg(math.atan2(math.sin(ha), math.cos(ha)*math.sin(lat)-math.tan(declination)*math.cos(lat))) + 180) % 360
    return {"elevation_deg": elevation, "azimuth_deg": azimuth, "hour_angle_deg": hour_angle,
            "elevation_kind": "geometric_sun_centre", "azimuth_convention": "true_north_clockwise"}


def _root(function, left, right):
    first = function(left)
    if abs(first) < 1e-12:
        return left
    if abs(function(right)) < 1e-12:
        return right
    while right-left > .1:
        middle = (left+right)/2
        value = function(middle)
        if (value >= 0) == (first >= 0):
            left, first = middle, value
        else:
            right = middle
    return (left+right)/2


def solar_day(local_date, latitude, longitude, timezone_name):
    """Find standard-horizon events within one local civil date (23/24/25h).

    Sunrise/set use sun-centre elevation -0.833 degrees, including a conventional
    combined solar-radius/refraction allowance. Actual air, terrain and observer
    altitude are not modelled. No event is invented for polar day/night.
    """
    validate_coordinates(latitude, longitude)
    day = Date.fromisoformat(local_date) if isinstance(local_date, str) else local_date
    if type(day) is not Date:
        raise ValueError("A calendar date is required")
    zone = timezone_name if isinstance(timezone_name, tzinfo) else get_zone(timezone_name)
    start = datetime.combine(day, datetime.min.time(), zone).astimezone(UTC)
    end = datetime.combine(day+timedelta(days=1), datetime.min.time(), zone).astimezone(UTC)
    duration = (end-start).total_seconds()
    if duration <= 0:
        raise ValueError("This local date does not exist in the selected timezone")
    def position(offset):
        return solar_position(latitude, longitude, start+timedelta(seconds=offset))
    offsets = list(range(0, int(duration), 300)) + [duration]
    positions = [position(offset) for offset in offsets]
    events = {key: None for key in ("civil_dawn", "sunrise", "solar_noon", "sunset", "civil_dusk")}
    for threshold, rising_name, setting_name in [(SUNRISE_ELEVATION, "sunrise", "sunset"),
                                                (CIVIL_ELEVATION, "civil_dawn", "civil_dusk")]:
        for left, right, a, b in zip(offsets, offsets[1:], positions, positions[1:]):
            x, y = a["elevation_deg"]-threshold, b["elevation_deg"]-threshold
            if (x <= 0 < y) or (x >= 0 > y):
                root = _root(lambda value: position(value)["elevation_deg"]-threshold, left, right)
                key = rising_name if y > x else setting_name
                events[key] = (start+timedelta(seconds=root)).astimezone(zone).isoformat(timespec="seconds")
    for left, right, a, b in zip(offsets, offsets[1:], positions, positions[1:]):
        if a["hour_angle_deg"] <= 0 < b["hour_angle_deg"] and b["hour_angle_deg"]-a["hour_angle_deg"] < 180:
            root = _root(lambda value: position(value)["hour_angle_deg"], left, right)
            events["solar_noon"] = (start+timedelta(seconds=root)).astimezone(zone).isoformat(timespec="seconds")
    if events["solar_noon"] is None:
        raise ValueError("No solar transit on this local date; check that timezone matches location")
    elevations = [row["elevation_deg"] for row in positions]
    condition = "normal"
    if events["sunrise"] is None and events["sunset"] is None:
        condition = "polar_day" if min(elevations) > SUNRISE_ELEVATION else (
            "polar_night" if max(elevations) < SUNRISE_ELEVATION else "transition")
    return {"date": day.isoformat(), "timezone": str(timezone_name), "events": events,
            "condition": condition, "civil_day_hours": duration/3600,
            "sunrise_threshold_deg": SUNRISE_ELEVATION, "civil_twilight_threshold_deg": CIVIL_ELEVATION,
            "model": "NOAA_Meeus_approximation/v1", "reference": "https://gml.noaa.gov/grad/solcalc/calcdetails.html",
            "limitations": "standard horizon; terrain, actual refraction and observer altitude not modelled"}
