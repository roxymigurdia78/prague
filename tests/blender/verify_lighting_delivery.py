"""Save/reopen a tiny fixture; no street render or model/API inference."""
from pathlib import Path
import sys
import tempfile

import bpy

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'src'))
from streetlab.blender_presentation import configure_lighting_delivery

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
bpy.ops.mesh.primitive_cube_add()
mesh = bpy.context.object
mesh.name = 'visible street'
camera = bpy.data.objects.new('road camera', bpy.data.cameras.new('road camera'))
scene.collection.objects.link(camera)
scene.camera = camera
lamp = bpy.data.objects.new('working lamp', bpy.data.lights.new('working lamp', 'POINT'))
lamp.data.energy = 234
scene.collection.objects.link(lamp)
marker = bpy.data.objects.new('guide box', None)
marker.empty_display_type = 'CUBE'
scene.collection.objects.link(marker)
scene.view_layers.new('Second layer')
configure_lighting_delivery(scene)
folder = root / 'scratch/tests'
folder.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(dir=folder, prefix='lighting-delivery-') as directory:
    path = Path(directory) / 'a_all_20261117_150203_test.blend'
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    bpy.ops.wm.open_mainfile(filepath=str(path), use_scripts=False)
    scene = bpy.context.scene
    assert scene.camera.name == 'road camera'
    for layer in scene.view_layers:
        assert scene.objects['road camera'].hide_get(view_layer=layer)
        assert scene.objects['guide box'].hide_get(view_layer=layer)
        assert not scene.objects['working lamp'].hide_get(view_layer=layer)
        assert not scene.objects['visible street'].hide_get(view_layer=layer)
    assert not scene.objects['working lamp'].hide_render
    assert scene.objects['working lamp'].data.energy == 234
    assert not scene.objects['visible street'].hide_render
    spaces = [space for screen in bpy.data.screens for area in screen.areas
              for space in area.spaces if space.type == 'VIEW_3D']
    assert spaces and all(not space.overlay.show_extras for space in spaces)
    assert all(space.region_3d.view_perspective == 'PERSP' for space in spaces)
print('PASS: hidden camera/marker guides persisted; geometry, camera and illumination preserved')
