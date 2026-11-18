import bpy,sys,json,importlib.util
from pathlib import Path
root=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(root/'src'))
from streetlab.blender_presentation import configure_gpu,configure_opening_view
bpy.ops.wm.read_factory_settings(use_empty=True)
scene=bpy.context.scene
scene.render.engine='CYCLES'
collection=bpy.data.collections.new('Detected windows and fixtures')
scene.collection.children.link(collection)
marker=bpy.data.objects.new('WINDOW_TEST',None)
collection.objects.link(marker)
device=configure_gpu(scene)
configure_opening_view(scene,(0,0,1))
assert scene.cycles.device==device['device']
assert scene.view_layers[0].layer_collection.children[collection.name].hide_viewport
assert scene['streetlab_open_rendered']
spec=importlib.util.spec_from_file_location('opening_test',root/'scripts/blender/streetlab_opening.py')
addon=importlib.util.module_from_spec(spec);spec.loader.exec_module(addon)
for screen in bpy.data.screens:
    for area in screen.areas:
        if area.type=='VIEW_3D':area.spaces.active.shading.type='SOLID'
addon.apply_opening_view()
assert all(a.spaces.active.shading.type=='RENDERED' for s in bpy.data.screens for a in s.areas if a.type=='VIEW_3D')
scene['streetlab_open_rendered']=False
for screen in bpy.data.screens:
    for area in screen.areas:
        if area.type=='VIEW_3D':area.spaces.active.shading.type='SOLID'
addon.apply_opening_view()
assert all(a.spaces.active.shading.type=='SOLID' for s in bpy.data.screens for a in s.areas if a.type=='VIEW_3D')
assert not bpy.data.lights and not bpy.data.cameras
print('PASS: GPU selection, hidden markers, rendered opening restricted to tagged scenes',json.dumps(device))
