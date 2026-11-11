"""Switch a prepared Blender world between daylight presets using a saved EAC4 sample."""

from datetime import datetime, time, timedelta, timezone
import json
from pathlib import Path

from streetlab.atmosphere_mapping import initial_atmosphere
from streetlab.solar_math import get_zone, solar_day
from streetlab.sun_sky import OWNER, SkySettings, get_sky_settings, set_sky, set_sun


ALIASES = {'明け方': 'dawn', '朝': 'morning', '昼': 'noon', '午後': 'afternoon',
           '夕方': 'sunset', '夕焼け': 'sunset', 'evening': 'sunset', '夜': 'night', '深夜': 'midnight'}
NIGHT_PRESETS = {'night', 'midnight'}


def daylight_times(reference):
    """Select local instants relative to twilight, sunrise, transit and sunset."""
    when = datetime.fromisoformat(reference['render_local_datetime'])
    location = reference['location']
    name = location.get('timezone')
    try:
        zone = get_zone(name)
    except ValueError:
        # Windows Blender can use the date's recorded offset even without tzdata.
        zone = when.tzinfo
        if zone is None:
            raise
    events = solar_day(when.date(), location['latitude'], location['longitude'], zone)['events']
    names = ('civil_dawn', 'sunrise', 'solar_noon', 'sunset')
    if not all(events[name] for name in names):
        raise ValueError('This location/date does not have all five daylight presets')
    dawn, rise, noon, setting = [datetime.fromisoformat(events[name]).astimezone(timezone.utc) for name in names]
    presets = {'dawn': dawn + (rise - dawn) * 0.5, 'morning': rise + (noon - rise) * 0.4,
               'noon': noon, 'afternoon': noon + (setting - noon) * 0.48,
               'sunset': noon + (setting - noon) * 0.94}
    midnight = datetime.combine(when.date() + timedelta(days=1), time.min, zone).astimezone(timezone.utc)
    dusk = datetime.fromisoformat(events['civil_dusk']).astimezone(timezone.utc) if events['civil_dusk'] else None
    if dusk is not None and setting < dusk < midnight:
        presets['night'] = dusk + min(timedelta(minutes=35), (midnight - dusk) / 2)
    presets['midnight'] = midnight
    return {key: value.astimezone(zone).replace(microsecond=0) for key, value in presets.items()}


def set_world_time(period, reference, *, altitude=None, scene=None):
    """Apply a preset to the live scene; keep the same atmosphere sample between presets."""
    import bpy

    scene = bpy.context.scene if scene is None else scene
    if isinstance(reference, (str, Path)):
        reference = json.loads(Path(reference).read_text(encoding='utf-8-sig'))
    period = ALIASES.get(period, period)
    presets = daylight_times(reference)
    if period not in presets:
        raise ValueError(f'Unknown daylight preset: {period}')

    materials = {slot.material for obj in scene.objects if obj.type == 'MESH'
                 for slot in obj.material_slots if slot.material}
    if not materials or any(not material.get('relight_prepared') for material in materials):
        raise ValueError('Open a prepared street_relight.blend first')
    if any(obj.type == 'LIGHT' and obj.data.type == 'SUN' and obj.data.energy > 0
           and not obj.hide_render for obj in scene.objects):
        raise ValueError('Disable the existing SUN light to use the sky Sun Disc')

    location = reference['location']
    current = get_sky_settings(scene) if scene.world and scene.world.get(OWNER) else SkySettings()
    if altitude is None:
        elevation = location.get('elevation')
        altitude = current.altitude if elevation is None else max(0, elevation)
    atmosphere = initial_atmosphere(reference, altitude)
    changes = {'altitude': altitude, 'sun_disc': period not in NIGHT_PRESETS, **atmosphere['initial']}
    current.updated(**changes)
    when = presets[period]
    sun = set_sun(location['latitude'], location['longitude'], when.date(), when.timetz().isoformat(),
                  scene=scene, north_seed=0)
    sky = set_sky(scene, **changes).to_dict()
    scene.render.engine = 'CYCLES'
    atmosphere['applied'] = {name: sky[name] for name in ('altitude', 'aerosols', 'ozone')}
    result = {'period': period, 'sun': sun, 'sky': sky, 'atmosphere_reference': atmosphere}
    scene['_research_daylight_preset'] = json.dumps(result, ensure_ascii=False, allow_nan=False)
    return result
