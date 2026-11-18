"""Recreate the cameras of a finished road capture and render one lighting candidate."""
import json
from pathlib import Path
import sys

import bpy
from mathutils import Matrix

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from streetlab.blender_presentation import configure_gpu, configure_lighting_delivery
from streetlab.lighting_placement import place_lights
from streetlab.render_views import apply_capture_settings, render_views
from streetlab.sun_sky import BACKGROUND_NODE, get_sky_settings, set_sky


def install_cameras(scene, views):
    names = []
    for record in views:
        data = bpy.data.cameras.new(record['camera'])
        data.type = 'PANO'
        data.panorama_type = 'EQUIRECTANGULAR'
        horizontal, vertical = record['coverage_degrees']
        if abs(horizontal - 360) > 0.01 or abs(vertical - 180) > 0.01:
            raise ValueError('照明探索には360度の道路パノラマが必要です。')
        data.lens = record['lens_mm']
        data.clip_start, data.clip_end = record['clip']
        camera = bpy.data.objects.new(data.name, data)
        scene.collection.objects.link(camera)
        camera.matrix_world = Matrix(record['matrix_world'])
        names.append(camera.name)
    scene.camera = scene.objects[names[0]]
    return names


def main(request):
    spec = json.loads(request.read_text(encoding='utf-8'))
    output = request.parent
    bpy.ops.wm.open_mainfile(filepath=spec['blend'], use_scripts=False)
    scene = bpy.context.scene
    capture = spec['capture']
    scene.frame_set(spec['frame'])
    scene.view_settings.view_transform = capture['view_transform']
    scene.view_settings.gamma = capture['gamma']
    scene.render.pixel_aspect_x, scene.render.pixel_aspect_y = capture['pixel_aspect']
    configure_gpu(scene)
    names = install_cameras(scene, spec['views'])
    sky = spec['sky'].copy()
    knobs = spec['knobs']
    original = spec.get('original_lighting', False)
    if not original:
        sky['sun_intensity'] *= knobs['sun_multiplier']
        sky['aerosols'] *= knobs['aerosols_multiplier']
        maximum = bpy.types.ShaderNodeTexSky.bl_rna.properties['aerosol_density'].hard_max
        sky['aerosols'] = min(sky['aerosols'], maximum)
    strength = spec.get('world_strength')
    if strength is None:
        strength = scene.world.node_tree.nodes[BACKGROUND_NODE].inputs['Strength'].default_value
    if not original:
        strength *= knobs['world_multiplier']
    if spec.get('night') and not original:
        # Below the horizon the daytime world shader can evaluate to black.
        # Keep a dim, scene-wide ambient source for surfaces beyond the lamps.
        background = scene.world.node_tree.nodes[BACKGROUND_NODE]
        sky_output = background.inputs['Color']
        for link in list(sky_output.links):
            scene.world.node_tree.links.remove(link)
        sky_output.default_value = (.016, .024, .045, 1)
        strength = .35 * knobs['world_multiplier']
    placements = []
    if 'placement' in spec and not original:
        layout = spec['placement']
        placements = place_lights(scene, layout['anchors'], layout['unit'], knobs)
    exposure = capture['exposure'] + knobs['exposure_delta']
    kelvin = knobs['temperature_k']
    tint = (capture['white_balance_tint'] or 0) + knobs['tint_delta'] if kelvin is not None else None
    look = knobs['look']
    size = {key: capture[key] for key in ('width', 'height', 'samples')}
    if spec['save_blend']:
        set_sky(scene, **sky)
        scene.world.node_tree.nodes[BACKGROUND_NODE].inputs['Strength'].default_value = strength
        apply_capture_settings(scene, exposure=exposure, kelvin=kelvin, tint=tint, look=look)
        scene.render.resolution_x = size['width']
        scene.render.resolution_y = size['height']
        scene.render.resolution_percentage = 100
        scene.render.filepath = '//view.png'
        scene.cycles.samples = size['samples']
        presentation = configure_lighting_delivery(scene)
        bpy.context.preferences.filepaths.save_version = 0
        filename = spec.get('blend_filename', 'scene.blend')
        if Path(filename).name != filename or not filename.endswith('.blend'):
            raise ValueError('最終案のBlenderファイル名が無効です。')
        # Remap any relative texture paths to the exported scene's location.
        saved = bpy.ops.wm.save_as_mainfile(filepath=str(output / filename), compress=True, copy=True)
        if 'FINISHED' not in saved:
            raise RuntimeError('最終案のBlenderを保存できませんでした。')
        record = {'sky': get_sky_settings(scene).to_dict(), 'world_strength': strength,
                  'capture': capture | {'exposure': exposure, 'white_balance_kelvin': kelvin,
                                        'white_balance_tint': tint, 'look': look}, 'views': spec['views'],
                  'presentation': presentation}
    else:
        manifest = render_views({'sun': None, 'sky': sky, 'world_strength': strength, 'exposure': exposure,
                                 'white_balance_kelvin': kelvin, 'white_balance_tint': tint,
                                 'look': look, **size}, output, camera_names=names)
        record = json.loads(manifest.read_text(encoding='utf-8'))
        for view, source in zip(record['views'], spec['views']):
            (manifest.parent / view['image']).replace(output / source['image'])
            view['image'] = source['image']
        manifest.unlink()
        manifest.parent.rmdir()
    record['sun'] = spec['sun']
    record['knobs'] = knobs
    if 'placement' in spec:
        record['placement'] = {'inventory': spec['placement']['inventory'], 'lights': placements}
    if spec.get('night') and not original:
        record['night_ambient_linear_rgb'] = [.016, .024, .045]
    previous = output / 'settings.json'
    if previous.is_file() and not spec['save_blend']:
        existing = json.loads(previous.read_text(encoding='utf-8'))
        combined = {view['image']: view for view in existing['views']}
        combined.update({view['image']: view for view in record['views']})
        record['views'] = [combined[name] for name in sorted(combined)]
    temporary = output / 'settings.tmp'
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(previous)


if __name__ == '__main__':
    main(Path(sys.argv[sys.argv.index('--') + 1]))
