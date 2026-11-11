"""Persistent fictional north: +Z up, angle counterclockwise from world +X.

One Blender scene represents one generated street. Randomise once, then reuse the
scene property and optional per-world JSON. Geometry is never rotated.
"""
import json
import math
from pathlib import Path
import random
import secrets

PROPERTY = '_research_north_orientation'
SCHEMA = 'streetlab/north-orientation/v1'
CONVENTION = 'degrees_counterclockwise_from_world_+X; world_+Z_up'


def _validate(data):
    if not isinstance(data, dict) or set(data) != {'schema', 'north_angle_deg', 'seed', 'method', 'convention'}:
        raise ValueError('Invalid north orientation record')
    angle, seed = data['north_angle_deg'], data['seed']
    if data['schema'] != SCHEMA or data['convention'] != CONVENTION:
        raise ValueError('Unknown north orientation schema or convention')
    if type(angle) not in (int, float) or not math.isfinite(angle) or not 0 <= angle < 360:
        raise ValueError('North angle must be finite and in [0, 360) degrees')
    if data['method'] == 'random':
        if type(seed) is not int or not 0 <= seed < 2**64:
            raise ValueError('North seed must be an unsigned 64-bit integer')
    elif data['method'] != 'manual' or seed is not None:
        raise ValueError('Invalid north orientation method')
    return dict(data)


def random_north(*, seed=None):
    seed = secrets.randbits(64) if seed is None else seed
    if type(seed) is not int or not 0 <= seed < 2**64:
        raise ValueError('North seed must be an unsigned 64-bit integer')
    return _validate({'schema': SCHEMA, 'north_angle_deg': random.Random(seed).random() * 360,
                      'seed': seed, 'method': 'random', 'convention': CONVENTION})


def _scene(scene):
    if scene is None:
        import bpy
        return bpy.context.scene
    return scene


def get_north(scene=None):
    """Read without creating; malformed existing state is never silently replaced."""
    raw = _scene(scene).get(PROPERTY)
    return None if raw is None else _validate(json.loads(raw))


def read_north(path):
    return _validate(json.loads(Path(path).read_text(encoding='utf-8-sig')))


def save_north(path, orientation):
    """Create a per-world record, or verify an existing one; never overwrite it."""
    orientation = _validate(orientation)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('x', encoding='utf-8') as handle:
            handle.write(json.dumps(orientation, ensure_ascii=False, indent=2) + '\n')
    except FileExistsError:
        if read_north(path) != orientation:
            raise ValueError('Existing north JSON differs from this scene; refusing overwrite') from None
    return path


def ensure_north(scene=None, *, seed=None, path=None):
    """Reuse stored north; seed is used only on the very first assignment.

    An optional world-folder JSON survives re-running preparation from an earlier
    .blend checkpoint. Save the .blend normally to persist the scene property.
    """
    scene = _scene(scene)
    current = get_north(scene)
    saved = read_north(path) if path is not None and Path(path).exists() else None
    if current is not None and saved is not None and current != saved:
        raise ValueError('Scene north and world-folder north disagree; resolve explicitly')
    selected = current if current is not None else saved if saved is not None else random_north(seed=seed)
    if path is not None:
        save_north(path, selected)
    if current is None:
        scene[PROPERTY] = json.dumps(selected, ensure_ascii=False, allow_nan=False)
    return selected


def set_north(north_angle_deg, scene=None):
    """Explicit manual override for later alignment; call set_sun again afterwards."""
    if type(north_angle_deg) not in (int, float) or not math.isfinite(north_angle_deg):
        raise ValueError('North angle must be a finite number in degrees')
    result = _validate({'schema': SCHEMA, 'north_angle_deg': north_angle_deg % 360,
                        'seed': None, 'method': 'manual', 'convention': CONVENTION})
    _scene(scene)[PROPERTY] = json.dumps(result, ensure_ascii=False, allow_nan=False)
    return result
