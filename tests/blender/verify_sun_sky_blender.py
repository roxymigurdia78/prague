"""Exercise repeated updates, isolation, JSON restoration and scene preservation."""
import json
import math
from pathlib import Path
import sys

import bpy

root=Path(__file__).resolve().parents[2];sys.path.insert(0,str(root/'src'))
from streetlab.sun_sky import set_sky,get_sky_settings,save_sky_settings,load_sky_settings,SKY_NODE

output=Path(sys.argv[sys.argv.index('--')+1]).resolve();output.mkdir(parents=True,exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=False)
scene=bpy.context.scene
original_world=scene.world
objects={o.name:(o.type,tuple(float(v) for row in o.matrix_world for v in row)) for o in scene.objects}
material_nodes={m.name:len(m.node_tree.nodes) for m in bpy.data.materials if m.use_nodes}
scene.view_settings.exposure=2.0
original_engine=scene.render.engine
settings=set_sky(sun_elevation=20, sun_rotation=45, aerosols=1.5)
assert scene.view_settings.exposure==0.0
original_exposure=-1.25
scene.view_settings.exposure=original_exposure
world=scene.world
assert world!=original_world
assert original_world.node_tree.nodes.get('Background') is not None
sky=world.node_tree.nodes[SKY_NODE]
assert sky.sky_type=='MULTIPLE_SCATTERING'
assert math.isclose(sky.sun_elevation,math.radians(20),abs_tol=1e-6)
for elevation,aerosols in [(5,2),(40,.5),(10,1.3)]:
    updated=set_sky(sun_elevation=elevation,aerosols=aerosols)
    assert scene.world==world
    assert len(world.node_tree.nodes)==3
    assert math.isclose(updated.sun_rotation,45,abs_tol=1e-5)
assert sum(n.type=='TEX_SKY' for n in world.node_tree.nodes)==1
# Manual UI edits are respected by the next partial update.
sky.air_density=.8
set_sky(ozone=.9)
assert math.isclose(get_sky_settings().air,.8,abs_tol=1e-6)
save_sky_settings(output/'settings.json')
saved=get_sky_settings()
set_sky(sun_disc=False,sun_intensity=4)
restored=load_sky_settings(output/'settings.json')
assert restored==saved
before=get_sky_settings()
try:set_sky(sun_elevation=300)
except ValueError:pass
else:raise AssertionError('Invalid settings were accepted')
assert get_sky_settings()==before
# Editing a world shared by two scenes must not alter the other scene.
other=bpy.data.scenes.new('Other scene');other.world=world
set_sky(aerosols=3)
assert scene.world!=other.world
assert math.isclose(get_sky_settings(other).aerosols,before.aerosols,abs_tol=1e-6)
assert objects=={o.name:(o.type,tuple(float(v) for row in o.matrix_world for v in row)) for o in scene.objects}
assert material_nodes=={m.name:len(m.node_tree.nodes) for m in bpy.data.materials if m.use_nodes}
assert scene.view_settings.exposure==original_exposure and scene.render.engine==original_engine
for elevation in (-90,90):
    value=set_sky(sun_elevation=elevation,sun_size=90)
    assert value.sun_elevation==elevation and value.sun_size==90
tiny=set_sky(sun_size=.000001,sun_elevation=.000001)
assert tiny.sun_size>0 and tiny.sun_elevation>0
set_sky(sun_size=.545)
# Compile and render the actual sky shader in this disposable factory scene.
scene.render.engine='CYCLES';scene.cycles.samples=4
scene.render.resolution_x=96;scene.render.resolution_y=96;scene.render.resolution_percentage=100
scene.render.image_settings.file_format='PNG';scene.view_settings.exposure=-3
def render_sky(elevation,name):
    set_sky(sun_elevation=elevation)
    path=output/name;scene.render.filepath=str(path)
    bpy.ops.render.render(write_still=True)
    image=bpy.data.images.load(str(path),check_existing=False)
    pixels=list(image.pixels[:]);bpy.data.images.remove(image)
    return pixels
day=render_sky(50,'test_day.png');low=render_sky(2,'test_low_sun.png')
difference=sum(abs(a-b) for i,(a,b) in enumerate(zip(day,low)) if i%4!=3)/(len(day)*.75)
assert difference>.001,difference
result={'blender':bpy.app.version_string,'repeated_update':True,'single_sky_node':True,
        'degree_conversion':True,'json_restore':True,'invalid_update_is_atomic':True,
        'shared_world_isolation':True,'objects_and_materials_unchanged':True,
        'initial_exposure_zero':True,'updates_preserve_exposure':True,
        'existing_world_unchanged':True,'street_files_opened_or_modified':False,
        'boundary_angles':True,'cycles_shader_rendered':True,'sun_change_pixel_difference':difference,
        'values':get_sky_settings().to_dict()}
(output/'verification.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result))
