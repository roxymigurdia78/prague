"""Behavioral verification on the actual saved scene; temporary lights/camera are not saved."""
import json,math,sys
from pathlib import Path
import bpy
import numpy as np

folder=Path(sys.argv[sys.argv.index('--')+1]).resolve()
bpy.ops.wm.open_mainfile(filepath=str(folder/'street_relight.blend'))
assert not bpy.data.cameras and not bpy.data.lights
faces=sum(len(o.data.polygons) for o in bpy.data.objects if o.type=='MESH')
assert faces==json.loads((folder/'final_scene.json').read_text())['faces']
textures=[]
for material in bpy.data.materials:
    if not material.users:continue
    output=next(n for n in material.node_tree.nodes if n.type=='OUTPUT_MATERIAL' and n.is_active_output)
    shader=output.inputs['Surface'].links[0].from_node
    assert shader.type=='BSDF_PRINCIPLED'
    assert shader.inputs['Emission Strength'].default_value==0
    textures.append(shader.inputs['Base Color'].links[0].from_node.image.name)
with bpy.data.libraries.load(str(folder/'sky_review.blend'),link=False) as (source,target):
    target.objects=['view_00']
camera=target.objects[0];bpy.context.scene.collection.objects.link(camera)
scene=bpy.context.scene;scene.camera=camera
scene.render.resolution_x=512;scene.render.resolution_y=384;scene.render.resolution_percentage=100
scene.cycles.samples=16;scene.cycles.use_denoising=False
def render(name):
    scene.render.filepath=str(folder/(name+'.png'))
    bpy.ops.render.render(write_still=True)
    pixels=np.asarray(bpy.data.images['Render Result'].pixels[:])
    # Some background-mode builds do not expose Render Result pixel buffers.
    image=bpy.data.images.load(str(folder/(name+'.png')),check_existing=False)
    pixels=np.asarray(image.pixels[:]).reshape(-1,4)[:,:3]
    stats={'mean':float(pixels.mean()),'maximum':float(pixels.max())}
    bpy.data.images.remove(image)
    return stats
dark=render('lighting_off')
sun=bpy.data.objects.new('Temporary verification sun',bpy.data.lights.new('Temporary verification sun','SUN'))
scene.collection.objects.link(sun)
sun.data.energy=4
sun.rotation_euler=(math.radians(25),math.radians(-20),math.radians(-25))
lit=render('lighting_test_sun')
assert dark['maximum']<.005,dark
assert lit['mean']>dark['mean']+.01,lit
# Check the exported GLB in a fresh scene as well.
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=str(folder/'street_relight.glb'))
assert not bpy.data.cameras and not bpy.data.lights
for material in bpy.data.materials:
    if not material.users:continue
    outputs=[n for n in material.node_tree.nodes if n.type=='OUTPUT_MATERIAL' and n.is_active_output]
    assert outputs[0].inputs['Surface'].links[0].from_node.type=='BSDF_PRINCIPLED'
result={'dark':dark,'with_test_sun':lit,'textures':textures,'faces':faces,
        'glb_reimport_principled':True,'temporary_helpers_saved':False}
(folder/'lighting_verification.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result))
