"""Verify fixed random north, .blend persistence and actual Cycles sun direction."""
import json
import math
from pathlib import Path
import sys

import bpy
from mathutils import Vector

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'src'))
from streetlab.north_orientation import ensure_north, get_north, PROPERTY
from streetlab.sun_sky import get_sky_settings, set_sky, set_sun

output = Path(sys.argv[sys.argv.index('--') + 1]).resolve()
output.mkdir(parents=True, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=False)
scene = bpy.context.scene
original_objects = {obj.name: (obj.type, tuple(v for row in obj.matrix_world for v in row)) for obj in scene.objects}
exposure, engine = scene.view_settings.exposure, scene.render.engine
set_sky(air=.9, aerosols=.7, ozone=1.1, sun_intensity=1.2)
initial = ensure_north(scene, seed=42, path=output / 'north.json')
morning = set_sun(50.08804, 14.42076, '2026-07-15', '08:00+02:00', scene=scene, altitude=202)
evening = set_sun(50.08804, 14.42076, '2026-07-15', '20:00+02:00', scene=scene)
assert get_north(scene) == initial
assert morning['sky_settings']['sun_rotation'] != evening['sky_settings']['sun_rotation']
assert scene.view_settings.exposure == exposure and scene.render.engine == engine
assert original_objects == {obj.name: (obj.type, tuple(v for row in obj.matrix_world for v in row)) for obj in scene.objects}
assert math.isclose(get_sky_settings(scene).aerosols, .7, abs_tol=1e-6)
assert not [obj for obj in scene.objects if obj.type == 'LIGHT' and obj.data.type == 'SUN']
other = bpy.data.scenes.new('Independent generated world')
ensure_north(other, seed=0)
assert get_north(other) != initial

blend = output / 'sun_position_test.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(blend))
bpy.ops.wm.open_mainfile(filepath=str(blend))
scene = bpy.context.scene
assert get_north(scene) == initial
restored = set_sun(50.08804, 14.42076, '2026-07-15', '08:00+02:00', scene=scene)
assert restored['north'] == morning['north']
assert restored['solar_position'] == morning['solar_position']
# JSON also preserves the same north when preparation restarts from a scene
# checkpoint that predates orientation assignment.
del scene[PROPERTY]
assert ensure_north(scene, path=output / 'north.json') == initial

# Render the sky towards the calculated world vector and away from it. An offset
# or reversed azimuth conversion would put the sun disc outside the first view.
for obj in list(scene.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
camera = bpy.data.cameras.new('Sun direction verification')
obj = bpy.data.objects.new('Sun direction verification', camera)
scene.collection.objects.link(obj)
scene.camera = obj
camera.lens = 1000
scene.render.engine = 'CYCLES'
scene.cycles.device = 'CPU'
scene.cycles.samples = 1
scene.render.resolution_x = 16
scene.render.resolution_y = 16
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = 'OPEN_EXR'
scene.render.image_settings.color_depth = '32'
scene.render.film_transparent = False
scene.view_settings.exposure = 0

def brightness(direction, filename):
    obj.rotation_euler = Vector(direction).to_track_quat('-Z', 'Y').to_euler()
    scene.render.filepath = str(output / filename)
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(scene.render.filepath, check_existing=False)
    pixels = list(image.pixels[:])
    bpy.data.images.remove(image)
    return max(sum(pixels[i:i+3]) for i in range(0, len(pixels), 4))

direction = restored['sun_direction_world']
towards = brightness(direction, 'towards_sun.exr')
away = brightness([-direction[0], -direction[1], direction[2]], 'away_from_sun.exr')
assert towards > away * 10, (towards, away, direction)
# Exercise the actual preparation entry point twice from fresh checkpoints.
from streetlab.relight_world_blender import save_relight_scene
preparation = output / 'preparation'
preparation.mkdir(exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=False)
first_prepared = save_relight_scene(preparation)
bpy.ops.wm.read_factory_settings(use_empty=False)
second_prepared = save_relight_scene(preparation)
assert first_prepared['north'] == second_prepared['north']
assert get_north(bpy.context.scene) == first_prepared['north']
assert not bpy.data.cameras and not bpy.data.lights
assert bpy.context.scene.world.node_tree.nodes.get('Background').inputs['Strength'].default_value == 0
result = {'random_north_reused': True, 'world_json_reused': True, 'blend_reload_preserved_north': True,
          'date_time_applied_to_sky': True, 'scene_geometry_preserved': True, 'exposure_preserved': True,
          'no_extra_sun_light': True, 'cycles_sun_direction_verified': True,
          'preparation_rerun_kept_north_and_dark_scene': True,
          'towards_sun_brightness': towards, 'away_brightness': away, 'north': initial,
          'blender': bpy.app.version_string}
(output / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False))
