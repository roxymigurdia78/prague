"""Configure a generated Blender file for opening and GPU rendering."""
import json


def configure_gpu(scene):
    import bpy
    addon = bpy.context.preferences.addons.get('cycles')
    failures = []
    if addon:
        prefs = addon.preferences
        for backend in ('OPTIX', 'CUDA', 'HIP', 'METAL', 'ONEAPI'):
            try:
                prefs.compute_device_type = backend
                prefs.refresh_devices()
                devices = [d for d in prefs.devices if d.type == backend]
                if not devices:
                    continue
                for device in prefs.devices:
                    device.use = device in devices
                scene.cycles.device = 'GPU'
                result = {'device': 'GPU', 'backend': backend,
                          'devices': [d.name for d in devices]}
                scene['streetlab_render_device'] = json.dumps(result)
                return result
            except (TypeError, RuntimeError) as exc:
                failures.append(f'{backend}: {exc}')
    scene.cycles.device = 'CPU'
    result = {'device': 'CPU', 'reason': 'No supported GPU detected', 'details': failures}
    scene['streetlab_render_device'] = json.dumps(result)
    return result


def configure_opening_view(scene, position=None):
    import bpy
    from mathutils import Vector
    # Blender deliberately resets rendered shading when loading .blend files.
    # The optional Streetlab add-on restores it only for explicitly tagged files.
    scene['streetlab_open_rendered'] = True
    # Keep semantic Empty markers available in the Outliner without covering the
    # street in labels and boxes on first opening the file.
    for layer in scene.view_layers:
        def hide_markers(collection):
            if collection.collection.name == 'Detected windows and fixtures':
                collection.hide_viewport = True
            for child in collection.children:
                hide_markers(child)
        hide_markers(layer.layer_collection)
    for obj in scene.objects:
        obj.select_set(False)
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        space.shading.type = 'RENDERED'
                        space.shading.use_scene_world_render = True
                        space.shading.use_scene_lights_render = True
                        if position is not None:
                            rotation = Vector((0, 1, .1)).to_track_quat('-Z', 'Y')
                            space.region_3d.view_perspective = 'PERSP'
                            space.region_3d.view_rotation = rotation
                            space.region_3d.view_distance = 2
                            space.region_3d.view_location = Vector(position) + rotation @ Vector((0, 0, -2))
    scene['streetlab_detection_markers_hidden'] = True
