"""Verify that initialization includes detection markers but adds no light/camera or photo."""
import json,sys
from pathlib import Path
import bpy

root=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(root/'src'))
from streetlab.build_world_blender import main

fixture=root/'scratch/tests/build_blender'
output=fixture/'inventory_connection'
output.mkdir(parents=True,exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
marker=bpy.data.objects.new('WINDOW_001',None)
bpy.context.scene.collection.objects.link(marker)
marker.location=(0,1,1)
marker['kind']='window'
marker['role']='reflective_surface_candidate'
marker['lighting_assigned']=False
marker['material_assigned']=False
mapped=output/'mapped.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(mapped))
inventory=output/'inventory.json'
inventory.write_text(json.dumps({'objects':[{'id':'WINDOW_001','kind':'window'}],
    'lighting_assigned':False,'materials_assigned':False}),encoding='utf-8')
request=json.loads((fixture/'initialize_request.json').read_text())
request.update(output=str(output),capture_enabled=False,inventory_file=str(inventory),inventory_blend=str(mapped))
request_file=output/'request.json'
request_file.write_text(json.dumps(request),encoding='utf-8')
main(request_file)
result=json.loads((output/'result.json').read_text())
assert result['inventory_summary']['windows']==1
assert result['render_settings'] is None and result['views']==[]
assert not (output/'renders').exists()
assert json.loads((output/'camera.json').read_text())['semantic_inventory']==str(inventory)
bpy.ops.wm.open_mainfile(filepath=str(output/'street_initial.blend'))
assert not bpy.data.cameras and not bpy.data.lights
assert bpy.context.scene['streetlab_inventory_file']==str(inventory)
assert bpy.data.objects['WINDOW_001'].type=='EMPTY'
assert not bpy.data.objects['WINDOW_001']['lighting_assigned']
print('PASS: initial sky + semantic marker + camera plan; zero saved cameras/lights and no photos')
