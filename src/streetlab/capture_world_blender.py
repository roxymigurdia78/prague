"""Place saved road cameras on a prepared scene and call the settings-aware renderer."""

import argparse
import json
from pathlib import Path
import sys

import bpy
from mathutils import Matrix

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from streetlab.render_views import render_views


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def open_scene(path):
    bpy.ops.wm.open_mainfile(filepath=str(path))
    return bpy.context.scene


def mesh_frame(scene):
    meshes = [obj for obj in scene.objects if obj.type == 'MESH']
    if len(meshes) != 1:
        raise ValueError('Transferring a plan between files requires the same single street mesh')
    return meshes[0].name, meshes[0].matrix_world.copy()


def ground(scene, graph, position):
    hit, point, normal, *_ = scene.ray_cast(graph, position, (0, 0, -1), distance=3)
    if not hit or normal.z < 0.5 or position.z - point.z < 0.15:
        raise ValueError(f'No plausible ground below viewpoint at {tuple(position)}')
    return point


def install_cameras(target, source, views):
    matrices = [Matrix(view['matrix_world']) for view in views]
    transform = Matrix.Identity(4)
    source_floor = None
    if source != target:
        scene = open_scene(source)
        name, frame = mesh_frame(scene)
        graph = bpy.context.evaluated_depsgraph_get()
        source_floor = [ground(scene, graph, pose.translation) for pose in matrices]

    scene = open_scene(target)
    if any(obj.type == 'LIGHT' for obj in scene.objects):
        raise ValueError('Use the prepared street_relight.blend with the original lights removed')
    materials = {slot.material for obj in scene.objects if obj.type == 'MESH'
                 for slot in obj.material_slots if slot.material}
    if not materials or any(not mat.get('relight_prepared') for mat in materials):
        raise ValueError('Use the prepared street_relight.blend with relightable materials')

    if source_floor is not None:
        target_name, target_frame = mesh_frame(scene)
        if name != target_name:
            raise ValueError('Capture plan and prepared street have different mesh names')
        transform = target_frame @ frame.inverted()
    graph = bpy.context.evaluated_depsgraph_get()
    poses, offsets = [], []
    for i, matrix in enumerate(matrices):
        pose = transform @ matrix
        floor = ground(scene, graph, pose.translation)
        if source_floor is not None:
            expected = transform @ source_floor[i]
            if (floor - expected).length > 0.15:
                raise ValueError(f'Capture viewpoint {i + 1} does not align with the prepared road')
        poses.append(pose)
        offsets.append(float(pose.translation.z - floor.z))

    names = []
    for view, pose in zip(views, poses):
        name = Path(view['image']).stem
        data = bpy.data.cameras.new(name)
        data.type = 'PANO'
        data.panorama_type = 'EQUIRECTANGULAR'
        camera = bpy.data.objects.new(name, data)
        scene.collection.objects.link(camera)
        camera.matrix_world = pose
        names.append(camera.name)
    bpy.context.view_layer.update()
    return names, offsets, [list(row) for row in transform]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('target', type=Path)
    parser.add_argument('--capture-plan', type=Path, required=True)
    parser.add_argument('--capture-source', type=Path)
    parser.add_argument('--atmosphere-reference', type=Path, required=True)
    parser.add_argument('--settings', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    plan = read_json(args.capture_plan)
    source = args.capture_source or plan.get('source')
    if source is None:
        source = read_json(args.capture_plan.with_name('views.json'))['source']
    source = Path(source).resolve(strict=True)
    views = plan['views']
    if not views or any(view.get('projection') != 'equirectangular' for view in views):
        raise ValueError('Capture plan must contain equirectangular road viewpoints')
    settings = read_json(args.settings) if args.settings else {}
    if 'atmosphere_reference' in settings:
        raise ValueError('Pass the atmosphere reference only with --atmosphere-reference')
    settings.setdefault('width', 2048)
    settings.setdefault('height', settings['width'] // 2)
    settings['atmosphere_reference'] = args.atmosphere_reference

    cameras, heights, transform = install_cameras(args.target.resolve(), source, views)
    manifest = render_views(settings, args.output, camera_names=cameras)
    report = read_json(manifest)
    report['capture_plan'] = {'file': str(args.capture_plan), 'source_blend': str(source),
                              'survey_to_render_matrix': transform}
    for rendered, planned, height in zip(report['views'], views, heights):
        rendered.update(plan_image=planned['image'], height_above_road=height)
    temporary = manifest.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(manifest)
    print(manifest, flush=True)


if __name__ == '__main__':
    main()
