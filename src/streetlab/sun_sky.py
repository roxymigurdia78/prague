"""Nine editable Multiple Scattering controls. No work is performed on import.

Public angles are degrees; altitude is meters. The sky's sun disc supplies direct
sunlight, so this module never adds a second SUN light. Run scene functions inside
Blender Python; settings validation and JSON I/O also work in ordinary Python.
"""
from dataclasses import asdict, dataclass, fields, replace
import json
import math
from pathlib import Path


SCHEMA = 'blender_multiple_scattering/v1'
OWNER = '_research_multiple_scattering'
SKY_NODE = 'Research Sky'
BACKGROUND_NODE = 'Research Sky Background'
OUTPUT_NODE = 'Research Sky Output'


@dataclass(frozen=True)
class SkySettings:
    sun_disc: bool = True
    sun_size: float = .545
    sun_intensity: float = 1.0
    sun_elevation: float = 15.0
    sun_rotation: float = 0.0
    altitude: float = 100.0
    air: float = 1.0
    aerosols: float = 1.0
    ozone: float = 1.0

    def __post_init__(self):
        if type(self.sun_disc) is not bool:
            raise ValueError('sun_disc must be True or False')
        limits = {'sun_size': (0, 90), 'sun_intensity': (0, 1000),
                  'sun_elevation': (-90, 90), 'altitude': (0, 100000),
                  'air': (0, 1000), 'aerosols': (0, 1000), 'ozone': (0, 1000)}
        for field in fields(self):
            if field.name == 'sun_disc':
                continue
            value = getattr(self, field.name)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f'{field.name} must be a finite number')
            if field.name in limits:
                low, high = limits[field.name]
                if not low <= value <= high or (field.name == 'sun_size' and value == 0):
                    raise ValueError(f'{field.name} is out of range: {value}')
            object.__setattr__(self, field.name, float(value) % 360 if field.name == 'sun_rotation' else float(value))

    def updated(self, **changes):
        """Make a validated copy, retaining all unspecified controls."""
        return replace(self, **changes)

    def to_dict(self):
        return asdict(self)


PROPERTIES = {'sun_disc': 'sun_disc', 'sun_size': 'sun_size',
              'sun_intensity': 'sun_intensity', 'sun_elevation': 'sun_elevation',
              'sun_rotation': 'sun_rotation', 'altitude': 'altitude',
              'air': 'air_density', 'aerosols': 'aerosol_density', 'ozone': 'ozone_density'}
ANGLE_FIELDS = {'sun_size', 'sun_elevation', 'sun_rotation'}


def _scene(scene):
    if scene is not None:
        return scene
    import bpy
    return bpy.context.scene


def _sky(world):
    if not world or not world.get(OWNER) or not world.use_nodes:
        raise ValueError('This scene has no sky managed by sun_sky.set_sky()')
    node = world.node_tree.nodes.get(SKY_NODE)
    if not node or node.bl_idname != 'ShaderNodeTexSky':
        raise ValueError('Managed sky node is missing or has been replaced')
    return node


def get_sky_settings(scene=None):
    """Read actual node values, including edits made in Blender's node editor."""
    node = _sky(_scene(scene).world)
    if node.sky_type != 'MULTIPLE_SCATTERING':
        raise ValueError('Managed sky has been switched to another sky type')
    values = {}
    for name, prop in PROPERTIES.items():
        value = getattr(node, prop)
        value = math.degrees(value) if name in ANGLE_FIELDS else value
        # RNA stores angles as float32; exact +/-90 can read back a few ulps outside.
        if name in ('sun_size', 'sun_elevation'):
            low = 0 if name == 'sun_size' else -90
            for boundary in (low, 90):
                outside = value < low if boundary == low else value > 90
                if outside and math.isclose(value, boundary, abs_tol=1e-5, rel_tol=0):
                    value = float(boundary)
        values[name] = value
    return SkySettings(**values)


def set_sky(scene=None, settings=None, **changes):
    """Create or update one managed world. Return the actual applied SkySettings.

    Unspecified values retain their current state. Passing settings replaces all
    nine controls; keyword changes then override those settings. Geometry,
    materials, cameras, light objects, renderer and exposure are not modified.
    A shared world is copied before updating, isolating the selected scene.
    """
    import bpy
    scene = _scene(scene)
    if settings is None:
        base = get_sky_settings(scene) if scene.world and scene.world.get(OWNER) else SkySettings()
    elif isinstance(settings, SkySettings):
        base = settings
    elif isinstance(settings, dict):
        base = SkySettings(**settings)
    else:
        raise TypeError('settings must be SkySettings or a dictionary')
    proposed = base.updated(**changes)  # Validate before changing any Blender data.
    available = {e.identifier for e in bpy.types.ShaderNodeTexSky.bl_rna.properties['sky_type'].enum_items}
    if 'MULTIPLE_SCATTERING' not in available:
        raise RuntimeError('This Blender version does not support Multiple Scattering')
    # Check the installed API bounds before mutation; do not silently clamp values.
    for name, prop in PROPERTIES.items():
        rna = bpy.types.ShaderNodeTexSky.bl_rna.properties[prop]
        if name == 'sun_disc':
            continue
        value = getattr(proposed, name)
        if name in ANGLE_FIELDS:
            value = math.radians(value)
        if not rna.hard_min <= value <= rna.hard_max:
            # UI bounds stored as float32 may differ by a few ulps at the boundary.
            if not math.isclose(value, min(max(value, rna.hard_min), rna.hard_max), abs_tol=1e-6):
                raise ValueError(f'{name} exceeds the installed Blender API bounds')

    world = scene.world
    if world and world.get(OWNER):
        _sky(world)  # Reject damaged managed data before making a shared copy.
        if world.users > 1:
            world = world.copy()
            scene.world = world
    else:
        world = bpy.data.worlds.new('Research Multiple Scattering')
        world.use_nodes = True
        world[OWNER] = True
        nodes = world.node_tree.nodes
        nodes.clear()
        sky = nodes.new('ShaderNodeTexSky'); sky.name = SKY_NODE
        background = nodes.new('ShaderNodeBackground'); background.name = BACKGROUND_NODE
        output = nodes.new('ShaderNodeOutputWorld'); output.name = OUTPUT_NODE
        sky.location = (-500, 0); background.location = (-150, 0); output.location = (130, 0)
        world.node_tree.links.new(sky.outputs['Color'], background.inputs['Color'])
        world.node_tree.links.new(background.outputs['Background'], output.inputs['Surface'])
        scene.world = world
    sky = _sky(world)
    sky.sky_type = 'MULTIPLE_SCATTERING'
    for name, prop in PROPERTIES.items():
        value = getattr(proposed, name)
        setattr(sky, prop, math.radians(value) if name in ANGLE_FIELDS else value)
    return get_sky_settings(scene)


def write_settings(path, settings):
    """Save explicit renderer settings, not inferred photographic ground truth."""
    if not isinstance(settings, SkySettings):
        settings = SkySettings(**settings)
    path = Path(path)
    payload = {'schema': SCHEMA, 'angle_unit': 'degrees', 'altitude_unit': 'meters',
               'parameters': settings.to_dict()}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return path


def read_settings(path):
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if data.get('schema') != SCHEMA or data.get('angle_unit') != 'degrees' or data.get('altitude_unit') != 'meters':
        raise ValueError('Unknown sky settings schema or units')
    values = data.get('parameters')
    if not isinstance(values, dict) or set(values) != set(PROPERTIES):
        raise ValueError('Settings must contain exactly the nine sky controls')
    return SkySettings(**values)


def save_sky_settings(path, scene=None):
    return write_settings(path, get_sky_settings(scene))


def load_sky_settings(path, scene=None):
    return set_sky(scene, settings=read_settings(path))
