"""Render the prepared lighting-capture plan inside Blender."""

from datetime import datetime
import json
import math
from pathlib import Path, PureWindowsPath
import subprocess
import sys

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from streetlab.blender_presentation import configure_gpu
from streetlab.north_orientation import set_north
from streetlab.sun_sky import BACKGROUND_NODE, get_sky_settings, set_sky, set_sun


def install_camera(scene, pose):
    camera = bpy.data.cameras.new('Lighting comparison')
    camera.lens = pose['lens_mm']
    camera.clip_start = 0.01
    obj = bpy.data.objects.new(camera.name, camera)
    scene.collection.objects.link(obj)
    obj.location = pose['position']
    yaw, pitch = map(math.radians, (pose['yaw_deg'], pose['pitch_deg']))
    direction = Vector((math.cos(yaw), math.sin(yaw), math.tan(pitch)))
    obj.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
    scene.camera = obj
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                area.spaces.active.region_3d.view_perspective = 'CAMERA'


def render_world(world, plan, output):
    source_name = world['source']
    if sys.platform == 'linux' and PureWindowsPath(source_name).drive:
        source_name = subprocess.check_output(['wslpath', '-u', source_name], encoding='utf-8').strip()
    source = Path(source_name)
    if (source.stat().st_mtime_ns, source.stat().st_size) != (
            world['source_mtime_ns'], world['source_bytes']):
        raise RuntimeError(f'初期Blenderが撮影計画から変更されています: {source}')
    bpy.ops.wm.open_mainfile(filepath=str(source), use_scripts=False)
    scene = bpy.context.scene
    if any(obj.type == 'LIGHT' and not obj.hide_render for obj in scene.objects):
        raise ValueError(f'{source}: 昼光比較には点灯前の初期Blenderを指定してください。')
    install_camera(scene, world['camera'])

    when = datetime.fromisoformat(world['time_selection']['local_datetime'])
    location = world['time_selection']['location']
    set_north(world['north_deg'], scene=scene)
    sun = set_sun(location['latitude'], location['longitude'], when.date(),
                  when.timetz().isoformat(), scene=scene)
    sky = get_sky_settings(scene)
    background = scene.world.node_tree.nodes[BACKGROUND_NODE].inputs['Strength']
    base_world = background.default_value
    view = scene.view_settings
    base_exposure, base_look = view.exposure, view.look
    base_white_balance = view.use_white_balance
    base_temperature = (view.white_balance_temperature if base_white_balance
                        else view.bl_rna.properties['white_balance_temperature'].default)
    base_tint = (view.white_balance_tint if base_white_balance
                 else view.bl_rna.properties['white_balance_tint'].default)

    scene.render.engine = 'CYCLES'
    scene.render.resolution_x, scene.render.resolution_y = plan['resolution']
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    scene.cycles.samples = plan['samples']
    scene.cycles.use_adaptive_sampling = False
    scene.cycles.use_denoising = True
    scene.cycles.seed = 17
    device = configure_gpu(scene)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.context.preferences.filepaths.file_preview_type = 'NONE'

    for case in plan['cases']:
        directory = output / world['id'] / case['id']
        image, blend, settings = (directory / name for name in ('view.png', 'scene.blend', 'settings.json'))
        changes = case['changes']
        applied = set_sky(scene, sky, sun_disc=True, sun_size=0.545,
                          sun_intensity=sky.sun_intensity * changes.get('sun_multiplier', 1),
                          aerosols=sky.aerosols * changes.get('aerosols_multiplier', 1))
        scene.world.node_tree.nodes[BACKGROUND_NODE].inputs['Strength'].default_value = (
            base_world * changes.get('world_multiplier', 1))
        view.exposure = base_exposure + changes.get('exposure_delta', 0)
        view.look = changes.get('look', base_look)
        view.use_white_balance = base_white_balance or 'temperature_k' in changes or 'tint_delta' in changes
        view.white_balance_temperature = changes.get('temperature_k', base_temperature)
        view.white_balance_tint = base_tint + changes.get('tint_delta', 0)
        record = {
            'world': world['label'], 'case': case['id'], 'changes': changes,
            'source': world['source'], 'local_datetime': when.isoformat(), 'north_deg': world['north_deg'],
            'camera': world['camera'], 'solar_position': sun['solar_position'], 'sky': applied.to_dict(),
            'exposure_ev': view.exposure, 'look': view.look,
            'white_balance_enabled': view.use_white_balance,
            'temperature_k': view.white_balance_temperature, 'tint': view.white_balance_tint,
            'world_strength': scene.world.node_tree.nodes[BACKGROUND_NODE].inputs['Strength'].default_value,
            'resolution': plan['resolution'], 'samples': plan['samples'], 'render_device': device,
            'inventory_file': world['inventory_file'], 'inventory_counts': world['inventory_counts'],
        }
        if all(path.is_file() and path.stat().st_size for path in (settings, image, blend)):
            saved = json.loads(settings.read_text(encoding='utf-8'))
            comparable = record | {'render_device': saved['render_device']}
            if not comparable['white_balance_enabled']:
                comparable.update(temperature_k=saved['temperature_k'], tint=saved['tint'])
            if saved == comparable:
                continue
        settings.unlink(missing_ok=True)
        directory.mkdir(parents=True, exist_ok=True)
        data = json.dumps(record, ensure_ascii=False, indent=2)
        scene['streetlab_lighting_capture'] = data
        scene.render.filepath = '//view.png'
        saved = bpy.ops.wm.save_as_mainfile(filepath=str(blend), compress=True, copy=True)
        if 'FINISHED' not in saved or not blend.is_file() or not blend.stat().st_size:
            raise RuntimeError(f'Blenderファイルを保存できません: {blend}')
        scene.render.filepath = str(image)
        image.unlink(missing_ok=True)
        rendered = bpy.ops.render.render(write_still=True)
        if 'FINISHED' not in rendered or not image.is_file() or not image.stat().st_size:
            raise RuntimeError(f'画像を撮影できません: {image}')
        temporary = settings.with_suffix('.tmp')
        temporary.write_text(data, encoding='utf-8')
        temporary.replace(settings)
        print(f'CAPTURE DONE {world["id"]} {case["id"]}', flush=True)


if __name__ == '__main__':
    plan_path = Path(sys.argv[sys.argv.index('--') + 1])
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    for world in plan['worlds']:
        render_world(world, plan, plan_path.parent)
