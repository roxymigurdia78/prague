"""Real Blender verification: camera ray roundtrip, saved source, emitted illumination."""
import hashlib
import json
from pathlib import Path
import sys

import bpy
import numpy as np
from bpy_extras.object_utils import world_to_camera_view

root=Path(__file__).resolve().parents[2];sys.path.insert(0,str(root/'src'))
from streetlab.light_placement_blender import ray, place

output=Path(sys.argv[sys.argv.index('--')+1]).resolve()
bpy.ops.wm.read_factory_settings(use_empty=True)
scene=bpy.context.scene
camera=bpy.data.objects.new('test_camera',bpy.data.cameras.new('test_camera'))
scene.collection.objects.link(camera);scene.camera=camera
scene.render.resolution_x=768;scene.render.resolution_y=576
bpy.ops.mesh.primitive_plane_add(size=20,location=(0,0,-3))
bpy.context.view_layer.update()
for x,y in [(500,500),(250,250),(750,650)]:
    h=ray(scene,camera,x,y);assert h is not None
    from mathutils import Vector
    projected=world_to_camera_view(scene,camera,Vector(h['position']))
    assert abs(projected.x-x/1000)<1e-5 and abs(projected.y-(1-y/1000))<1e-5,(x,y,list(projected))

scene.world=bpy.data.worlds.new('test_world');scene.world.use_nodes=True
place(scene,{'unit':1,'profile':'night','sun':None,'lights':[{
    'id':'TEST_WINDOW','kind':'window','enabled':True,'normal':[0,0,1],
    'position':[0,0,-3],'size':.5,'energy_factor':1,'mesh':'Plane','confidence':1}]})
assert bpy.data.objects['TEST_WINDOW'].data.type=='AREA'
assert bpy.data.objects['TEST_WINDOW'].location.z>-3
assert bpy.data.objects['TEST_WINDOW_emitter'].type=='MESH'

job=json.loads((output/'job.json').read_text())
assert hashlib.sha256(Path(job['source']).read_bytes()).hexdigest()==job['sha256']
bpy.ops.wm.open_mainfile(filepath=str(output/'street_lit.blend'))
scene=bpy.context.scene
lights=[o for o in scene.objects if o.type=='LIGHT']
assert lights,'No actual lights placed'
assert all(o.data.energy>0 for o in lights)
assert scene.render.engine=='CYCLES' and scene.cycles.diffuse_bounces>=2
scene.render.resolution_x=384;scene.render.resolution_y=288;scene.cycles.samples=8
scene.cycles.use_denoising=False
def measure(name):
    path=output/(name+'.png');scene.render.filepath=str(path)
    bpy.ops.render.render(write_still=True)
    img=bpy.data.images.load(str(path),check_existing=False)
    pixels=np.array(img.pixels[:]).reshape(-1,4)[:,:3]
    bpy.data.images.remove(img)
    return float(pixels.mean())
on=measure('verification_lights_on')
for o in lights:o.hide_render=True
for o in scene.objects:
    if o.name.endswith('_emitter'):o.hide_render=True
off=measure('verification_lights_off')
assert on>off+.001,(on,off)
result={'ray_roundtrip':True,'window_area_fixture':True,'source_unchanged':True,'physical_lights':len(lights),
        'cycles_indirect':True,'mean_lights_on':on,'mean_lights_off':off,'saved_scene_unmodified_by_test':True}
(output/'verification.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result))
