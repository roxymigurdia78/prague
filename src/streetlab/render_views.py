"""Render repeatable camera views and record the settings used for each batch."""
from contextlib import contextmanager
from datetime import datetime, time
import json
import math
from pathlib import Path
import sys
from tempfile import TemporaryDirectory, mkdtemp

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from streetlab.atmosphere_mapping import initial_atmosphere
from streetlab.sun_sky import OWNER, SkySettings, get_sky_settings, set_sky, set_sun


DEFAULT_OUTPUT = Path(r'C:\relight\renders')
DEFAULT_SUN = {
    'latitude': 50.087, 'longitude': 14.421, 'date': '2026-07-15',
    'time': '12:00+02:00', 'north_seed': 0,
}


@contextmanager
def temporary_render_batch(parent=DEFAULT_OUTPUT):
    """Give an entire evaluation loop a disposable directory of its own."""
    parent = Path(parent).resolve()
    parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='evaluation-', dir=parent) as directory:
        yield Path(directory)


def _cameras(scene, camera_names, source):
    """Resolve existing cameras or identify one previously recorded street pose."""
    if camera_names is not None:
        if (not isinstance(camera_names, (list, tuple)) or not camera_names
                or not all(isinstance(name, str) for name in camera_names)
                or len(set(camera_names)) != len(camera_names)):
            raise ValueError('camera_names must be a nonempty sequence of distinct camera names')
        cameras = [scene.objects.get(name) for name in camera_names]
        if any(camera is None or camera.type != 'CAMERA' for camera in cameras):
            raise ValueError('Every camera name must identify a camera in this scene')
        return cameras, None
    if scene.camera and scene.camera.type == 'CAMERA':
        return [scene.camera], None
    cameras = [obj for obj in scene.objects if obj.type == 'CAMERA']
    if len(cameras) == 1:
        return cameras, None
    if not cameras and source and source.name == 'street_mapped.blend':
        record = source.parent / 'round_00' / 'views.json'
        if record.is_file():
            for view in json.loads(record.read_text(encoding='utf-8'))['views']:
                if view['id'] == 'r00_v00' and view['station'] == 0:
                    position = view['position']
                    if (len(position) == 3 and all(type(v) in (int, float) and math.isfinite(v)
                                                   for v in position)):
                        return [], (record, position)
    raise ValueError('Set an active camera or specify camera_names')


def render_views(settings=None, save_dir=DEFAULT_OUTPUT, *, camera_names=None):
    """Render the current Blender scene into a unique folder and return its JSON path.

    The default is one fixed camera. Provide camera_names to reuse a fixed camera
    set across trials. Only the temporary render scene receives setting changes.
    """
    import bpy
    from mathutils import Vector

    options = dict(settings) if settings is not None else {}
    for label, key in [('露出', 'exposure'), ('色温度', 'white_balance_kelvin')]:
        if label in options:
            if key in options:
                raise ValueError(f'Use only one spelling of {key}')
            options[key] = options.pop(label)
    exposure = options.pop('exposure', 0.0)
    kelvin = options.pop('white_balance_kelvin', None)
    sky = options.pop('sky', {})
    default_sun = 'sun' not in options
    sun = options.pop('sun', {})
    reference = options.pop('atmosphere_reference', None)
    width = options.pop('width', 768)
    height = options.pop('height', 576)
    samples = options.pop('samples', 16)
    if options:
        raise ValueError(f'Unknown render settings: {sorted(options)}')
    if not isinstance(sky, dict) or (sun is not None and not isinstance(sun, dict)):
        raise ValueError('sky and sun must be dictionaries; sun may also be None')
    if sun is not None and {'sun_elevation', 'sun_rotation'} & sky.keys():
        raise ValueError('Set sun=None to control solar angles directly through sky')
    for name, value in [('width', width), ('height', height), ('samples', samples)]:
        if type(value) is not int or value < 1:
            raise ValueError(f'{name} must be a positive integer')

    source = Path(bpy.data.filepath) if bpy.data.filepath else None
    original = bpy.context.scene
    atmosphere = None
    if reference is not None:
        reference_file = None
        if isinstance(reference, (str, Path)):
            reference_file = Path(reference).resolve()
            reference = json.loads(reference_file.read_text(encoding='utf-8-sig'))
        location = (reference.get('location') or {}) if isinstance(reference, dict) else {}
        if not isinstance(location, dict):
            raise ValueError('CAMS EAC4 reference location must be a dictionary')
        if 'altitude' in sky:
            altitude = sky['altitude']
        elif sun is not None and sun.get('altitude') is not None:
            altitude = sun['altitude']
        elif type(location.get('elevation')) in (int, float) and math.isfinite(location['elevation']):
            altitude = max(0.0, location['elevation'])
        else:
            current = get_sky_settings(original) if original.world and original.world.get(OWNER) else SkySettings()
            altitude = current.altitude
        atmosphere = initial_atmosphere(reference, altitude)
        if reference_file is not None:
            atmosphere['reference_file'] = str(reference_file)
        sky = {'altitude': altitude, **atmosphere['initial'], **sky}
        if default_sun and reference.get('render_local_datetime') and {'latitude', 'longitude'} <= location.keys():
            when = datetime.fromisoformat(reference['render_local_datetime'])
            sun = {'latitude': location['latitude'], 'longitude': location['longitude'],
                   'date': when.date().isoformat(), 'time': when.timetz().isoformat()}
            if when.utcoffset() is None and location.get('timezone'):
                sun['timezone_name'] = location['timezone']
    cameras, recorded_pose = _cameras(original, camera_names, source)
    scene = original.copy()
    temporary_camera = None
    temporary_camera_data = None
    try:
        solar = None
        if sun is not None:
            solar_options = DEFAULT_SUN | sun
            if 'time' not in sun and ('date' in sun or 'timezone_name' in sun):
                solar_options['time'] = '12:00'
            if time.fromisoformat(solar_options['time']).tzinfo is None:
                solar_options.setdefault('timezone_name', 'Europe/Prague')
            solar = set_sun(scene=scene, **solar_options)
            # Record sky controls once, after the caller's overrides are applied.
            solar.pop('sky_settings')
        if sky:
            set_sky(scene, **sky)

        view_settings = scene.view_settings
        for key, value in [('exposure', exposure), ('white_balance_temperature', kelvin)]:
            if key == 'white_balance_temperature' and value is None:
                continue
            prop = view_settings.bl_rna.properties[key]
            if type(value) not in (int, float) or not prop.hard_min <= value <= prop.hard_max:
                raise ValueError(f'{key} must be between {prop.hard_min} and {prop.hard_max}')
            setattr(view_settings, key, value)
        view_settings.use_white_balance = kelvin is not None

        render = scene.render
        render.engine = 'CYCLES'
        scene.cycles.samples = samples
        render.resolution_x = width
        render.resolution_y = height
        render.resolution_percentage = 100
        render.use_border = False
        render.use_compositing = False
        render.use_sequencer = False
        render.image_settings.file_format = 'PNG'
        render.image_settings.color_management = 'FOLLOW_SCENE'
        render.use_file_extension = True

        if recorded_pose:
            record, position = recorded_pose
            temporary_camera_data = bpy.data.cameras.new('Recorded street view')
            temporary_camera_data.lens = 22
            temporary_camera_data.clip_start = .01
            temporary_camera_data.clip_end = 500
            temporary_camera = bpy.data.objects.new('r00_v00', temporary_camera_data)
            scene.collection.objects.link(temporary_camera)
            temporary_camera.location = position
            # The saved scan's first view points along +X with a slight upward tilt.
            temporary_camera.rotation_euler = Vector((1, 0, .20)).to_track_quat('-Z', 'Y').to_euler()
            cameras = [temporary_camera]

        parent = Path(save_dir).resolve()
        parent.mkdir(parents=True, exist_ok=True)
        output = Path(mkdtemp(prefix='shot-', dir=parent))
        views = []
        for index, camera in enumerate(cameras, start=1):
            name = f'view_{index:02d}.png'
            scene.camera = camera
            with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
                graph = bpy.context.evaluated_depsgraph_get()
                evaluated = camera.evaluated_get(graph)
                matrix = [list(row) for row in evaluated.matrix_world]
                optics = evaluated.data
                view = {'image': name, 'camera': camera.name, 'type': optics.type,
                        'matrix_world': matrix, 'lens_mm': optics.lens, 'ortho_scale': optics.ortho_scale,
                        'sensor_fit': optics.sensor_fit, 'sensor_width_mm': optics.sensor_width,
                        'sensor_height_mm': optics.sensor_height, 'shift': [optics.shift_x, optics.shift_y],
                        'clip': [optics.clip_start, optics.clip_end]}
                if optics.type == 'PANO':
                    view['panorama_type'] = optics.panorama_type
                    if optics.panorama_type == 'EQUIRECTANGULAR':
                        view['coverage_degrees'] = [math.degrees(optics.longitude_max - optics.longitude_min),
                                                    math.degrees(optics.latitude_max - optics.latitude_min)]
            render.filepath = str(output / name)
            status = bpy.ops.render.render(scene=scene.name, write_still=True)
            if 'FINISHED' not in status or not (output / name).is_file():
                raise RuntimeError(f'Render did not produce {name}')
            views.append(view)

        manifest = {
            'schema': 'streetlab/render-views/v1', 'source_blend': str(source) if source else None,
            'source_scene': original.name, 'blender_version': bpy.app.version_string,
            'frame': scene.frame_current, 'sun': solar,
            'sky': get_sky_settings(scene).to_dict() if scene.world and scene.world.get(OWNER) else None,
            'capture': {'exposure': view_settings.exposure,
                        'white_balance_kelvin': view_settings.white_balance_temperature if kelvin is not None else None,
                        'white_balance_tint': view_settings.white_balance_tint if kelvin is not None else None,
                        'view_transform': view_settings.view_transform, 'look': view_settings.look,
                        'gamma': view_settings.gamma, 'engine': render.engine,
                        'width': render.resolution_x, 'height': render.resolution_y,
                        'pixel_aspect': [render.pixel_aspect_x, render.pixel_aspect_y],
                        'samples': scene.cycles.samples},
            'camera_record': str(record) if recorded_pose else None, 'views': views,
        }
        if atmosphere is not None:
            atmosphere['applied'] = {name: manifest['sky'][name] for name in ('altitude', 'aerosols', 'ozone')}
            manifest['atmosphere_reference'] = atmosphere
        path = output / 'settings.json'
        path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False) + '\n',
                        encoding='utf-8')
        return path
    finally:
        world = scene.world
        bpy.data.scenes.remove(scene)
        if world is not None and world != original.world and world.users == 0 and world.get(OWNER):
            bpy.data.worlds.remove(world)
        if temporary_camera is not None:
            bpy.data.objects.remove(temporary_camera, do_unlink=True)
        if temporary_camera_data is not None:
            bpy.data.cameras.remove(temporary_camera_data)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Render fixed views from the loaded Blender scene.')
    parser.add_argument('--settings', type=Path, help='JSON object of render settings')
    parser.add_argument('--atmosphere-reference', type=Path, help='Saved EAC4 atmosphere reference JSON')
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--camera', action='append', help='Camera name; repeat for multiple views')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    settings = json.loads(args.settings.read_text(encoding='utf-8-sig')) if args.settings else None
    if args.atmosphere_reference is not None:
        settings = {} if settings is None else settings
        if 'atmosphere_reference' in settings:
            parser.error('Specify atmosphere_reference in settings or on the command line, not both')
        settings['atmosphere_reference'] = args.atmosphere_reference
    print(render_views(settings, args.output, camera_names=args.camera))
