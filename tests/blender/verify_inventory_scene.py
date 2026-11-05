"""Ensure semantic output is annotations only and source geometry/materials are retained."""
import hashlib
import json
from pathlib import Path
import sys
from array import array
import bpy

output=Path(sys.argv[sys.argv.index('--')+1]).resolve()
job=json.loads((output/'job.json').read_text())
assert hashlib.sha256(Path(job['source']).read_bytes()).hexdigest()==job['sha256']

def snapshot():
    geometry={}
    for o in bpy.context.scene.objects:
        if o.type!='MESH':continue
        coords=array('f',[0])*len(o.data.vertices)*3
        o.data.vertices.foreach_get('co',coords)
        geometry[o.name]=(len(o.data.vertices),len(o.data.polygons),hashlib.sha256(coords.tobytes()).hexdigest(),
                          tuple(float(v) for row in o.matrix_world for v in row))
    mats={}
    for mat in bpy.data.materials:
        if not mat.users or not mat.use_nodes:continue
        mats[mat.name]={'nodes':sorted((n.name,n.type) for n in mat.node_tree.nodes),
            'links':sorted((l.from_node.name,l.from_socket.name,l.to_node.name,l.to_socket.name) for l in mat.node_tree.links)}
        for n in mat.node_tree.nodes:
            if n.type=='BSDF_PRINCIPLED':assert n.inputs['Emission Strength'].default_value==0
    return geometry,mats

bpy.ops.wm.open_mainfile(filepath=job['source']);original=snapshot()
bpy.ops.wm.open_mainfile(filepath=str(output/'street_mapped.blend'));mapped=snapshot()
assert original==mapped,'Source meshes or material nodes changed'
assert not any(o.type in ('LIGHT','CAMERA') for o in bpy.context.scene.objects)
inventory=json.loads((output/'inventory.json').read_text())
markers=[o for o in bpy.context.scene.objects if o.type=='EMPTY' and o.get('role')]
assert len(markers)==len(inventory['objects'])
assert any(o['kind']=='window' for o in markers),'No window regions mapped'
assert all(not o['lighting_assigned'] and not o['material_assigned'] for o in markers)
assert bpy.context.scene.world.node_tree.nodes['Background'].inputs['Strength'].default_value==0
result={'source_unchanged':True,'mesh_and_material_nodes_unchanged':True,'light_count':0,
        'new_emissive_surfaces':0,'camera_count':0,'markers':len(markers),
        'windows':sum(o['kind']=='window' for o in markers),'world_strength':0}
(output/'verification.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result))
