"""Initialize a cleaned street with existing sky tools and render nearby floor viewpoints."""
from datetime import datetime
import json
import math
from pathlib import Path
import sys

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from streetlab.sun_sky import set_sun, set_sky, get_sky_settings, save_sky_settings
from streetlab.atmosphere_mapping import initial_atmosphere
from streetlab.render_views import render_views


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def main(request_file):
    request = read(request_file)
    folder = Path(request['output'])
    prepared = Path(request['prepared_dir'])
    location = request['location']
    atmosphere_file = Path(request['atmosphere_file'])
    reference = read(atmosphere_file)
    when = datetime.fromisoformat(request['local_datetime'])
    altitude = max(0.0, float(location['elevation']))
    source = prepared / 'street_relight.blend'
    bpy.ops.wm.open_mainfile(filepath=str(source))
    scene = bpy.context.scene
    assert not bpy.data.lights and not bpy.data.cameras
    assert scene.world.node_tree.nodes['Background'].inputs['Strength'].default_value == 0
    for material in bpy.data.materials:
        if material.users:
            output = next(n for n in material.node_tree.nodes if n.type == 'OUTPUT_MATERIAL' and n.is_active_output)
            shader = output.inputs['Surface'].links[0].from_node
            assert shader.type == 'BSDF_PRINCIPLED'
            assert shader.inputs['Emission Strength'].default_value == 0
    faces = sum(len(o.data.polygons) for o in scene.objects if o.type == 'MESH')
    initial = folder / 'street_initial.blend'
    scene['streetlab_location'] = json.dumps(location, ensure_ascii=False)
    scene['streetlab_atmosphere_reference_file'] = str(atmosphere_file)
    scene['streetlab_camera_plan_file'] = str(folder/'camera.json')
    inventory_file=request.get('inventory_file')
    inventory=read(inventory_file) if inventory_file else None
    if inventory_file:
        scene['streetlab_inventory_file']=inventory_file
    if request.get('capture_plan'):
        scene['streetlab_road_capture_plan_file'] = request['capture_plan']
    # Keep the zero-light file usable with the same per-world controls.
    bpy.ops.wm.save_as_mainfile(filepath=str(source),compress=True)
    sun_options = {'latitude': location['latitude'], 'longitude': location['longitude'],
                   'date': when.date().isoformat(), 'time': when.timetz().isoformat(), 'altitude': altitude}
    sun = set_sun(scene=scene, **sun_options)
    mapping = initial_atmosphere(reference, altitude)
    sun_disc = sun['solar_position']['elevation_deg'] > -0.833
    set_sky(scene, **mapping['initial'], sun_disc=sun_disc)
    scene.view_settings.view_transform=request.get('view_transform','AgX')
    scene.view_settings.look='None'
    scene.view_settings.exposure=float(request.get('exposure',-2.0))
    if inventory:
        ids={obj['id'] for obj in inventory['objects']}
        with bpy.data.libraries.load(request['inventory_blend'],link=False) as (available,target):
            target.objects=[name for name in available.objects if name in ids]
        collection=bpy.data.collections.new('Detected windows and fixtures')
        scene.collection.children.link(collection)
        for marker in target.objects:
            if marker is None or marker.type!='EMPTY':
                raise ValueError('検出マーカーがEmpty以外になっています。')
            collection.objects.link(marker)
        if len(target.objects)!=len(ids):
            raise ValueError('検出結果のマーカーがそろっていません。')
    scene.render.engine = 'CYCLES'
    scene['streetlab_location'] = json.dumps(location, ensure_ascii=False)
    scene['streetlab_initial_conditions'] = json.dumps({'sun': sun, 'atmosphere': mapping}, ensure_ascii=False)
    save_sky_settings(folder / 'initial_sky.json', scene)
    bpy.ops.wm.save_as_mainfile(filepath=str(initial), compress=True)
    bpy.ops.wm.open_mainfile(filepath=str(initial))
    scene = bpy.context.scene
    applied = get_sky_settings(scene).to_dict()
    assert applied['altitude'] == altitude
    assert abs(applied['aerosols'] - mapping['initial']['aerosols']) < 1e-4
    assert abs(applied['ozone'] - mapping['initial']['ozone']) < 1e-5
    assert not bpy.data.lights and not bpy.data.cameras
    assert scene.view_settings.view_transform==request.get('view_transform','AgX')
    assert scene.view_settings.exposure==float(request.get('exposure',-2.0))
    if inventory:
        assert sum(obj.type=='EMPTY' and obj.name in ids for obj in scene.objects)==len(ids)
    assert sum(len(o.data.polygons) for o in scene.objects if o.type == 'MESH') == faces
    manifest = read(prepared / 'views.json')
    positions = manifest.get('positions') or [manifest['camera_position']]
    graph = bpy.context.evaluated_depsgraph_get()
    meshes = [o for o in scene.objects if o.type == 'MESH']
    corners = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
    span = max(max(p[i] for p in corners) - min(p[i] for p in corners) for i in range(3))
    names, records = [], []
    if request.get('capture_plan'):
        from streetlab.capture_world_blender import install_cameras
        capture_plan=read(request['capture_plan'])
        names,heights,transform=install_cameras(initial,Path(request['capture_source']),capture_plan['views'])
        scene=bpy.context.scene
        records=[{'camera':name,'position':[view['matrix_world'][i][3] for i in range(3)],
                  'height_above_floor':height,'plan_image':view['image'],'projection':'equirectangular'}
                 for name,height,view in zip(names,heights,capture_plan['views'])]
        positions=[]
    for position in positions[:3]:
        origin = Vector(position)
        hit, point, normal, *_ = scene.ray_cast(graph, origin, Vector((0, 0, -1)), distance=max(span, 3))
        if not hit or normal.z < 0.5 or origin.z <= point.z:
            continue
        for index in range(4):
            name = f'initial_{len(names):02d}'
            data = bpy.data.cameras.new(name)
            data.lens = 24
            data.clip_start = max(0.001, span * 0.0001)
            data.clip_end = max(100.0, span * 20)
            camera = bpy.data.objects.new(name, data)
            scene.collection.objects.link(camera)
            camera.location = origin
            angle = index * math.pi / 2
            camera.rotation_euler = Vector((math.cos(angle), math.sin(angle), 0)).to_track_quat('-Z', 'Y').to_euler()
            names.append(name)
            records.append({'camera': name, 'position': list(origin), 'floor': list(point),
                            'height_above_floor': origin.z - point.z})
    if not names:
        raise ValueError('清掃後の街に床のある撮影位置を確認できませんでした。')
    bpy.context.view_layer.update()
    camera=bpy.context.scene.objects[names[0]]
    (folder/'camera.json').write_text(json.dumps({'source_blend':str(initial),
        'compatible_blends':[str(source),str(initial)],'matrix_world':[list(row) for row in camera.matrix_world],
        'altitude_m':altitude,'fixture_inventory':None},indent=2),encoding='utf-8')
    if inventory_file:
        camera_record=read(folder/'camera.json')
        camera_record['semantic_inventory']=inventory_file
        (folder/'camera.json').write_text(json.dumps(camera_record,indent=2),encoding='utf-8')
    settings = None
    if request.get('capture_enabled',True):
        settings = render_views({'sun': sun_options, 'sky': {'sun_disc': sun_disc, 'altitude': altitude},
        'atmosphere_reference': atmosphere_file, 'width': request['width'], 'height': request['height'],
        'samples': request['samples'],'exposure':request.get('exposure',-2.0)}, folder / 'renders', camera_names=names)
    # Helpers only exist in this background process, after the deliverable was saved.
    result = {'dark_blend': str(source), 'dark_glb': str(prepared / 'street_relight.glb'),
        'initial_blend': str(initial), 'render_settings': str(settings) if settings else None,
        'views': records if settings else [],'camera_plan_views':records,
        'camera_plan_file':str(folder/'camera.json'),'capture_enabled':request.get('capture_enabled',True),
        'location': location, 'sun': sun, 'sky': applied, 'atmosphere_reference': mapping,
        'reference_file': str(atmosphere_file), 'faces': faces,
        'initial_scene_verified_after_reload': True, 'cameras_saved': 0, 'lights_saved': 0,
        'capture_plan':request.get('capture_plan'),
        'inventory_file':inventory_file,'inventory_blend':request.get('inventory_blend'),
        'inventory_summary':{'windows':sum(obj['kind']=='window' for obj in inventory['objects']),
            'fixtures':sum(obj['kind']!='window' for obj in inventory['objects']),
            'lighting_assigned':False,'materials_assigned':False} if inventory else None,
        'capture_color_management':{'view_transform':scene.view_settings.view_transform,
            'look':scene.view_settings.look,'exposure':scene.view_settings.exposure},
        'time_selection':str(folder/'time_selection.json') if (folder/'time_selection.json').exists() else None,
        'weather_reference':str(folder/'weather.json') if (folder/'weather.json').exists() else None,
        'viewpoint_method': 'HPC Mask2Former road masks + HPC VLM branch exploration + verified road camera plan' if request.get('capture_plan') else 'cleanup survey positions with floor raycast; four horizontal directions each'}
    (folder / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main(Path(sys.argv[sys.argv.index('--') + 1]).resolve())
