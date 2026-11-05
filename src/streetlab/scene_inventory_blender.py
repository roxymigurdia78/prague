"""Geometry inventory worker: no lights, no emissive patches, no glass assignment."""
import json
import math
from pathlib import Path
import sys

import bpy
from mathutils import Vector
from bpy_extras.object_utils import world_to_camera_view

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from streetlab.light_placement import save
from streetlab.light_placement_blender import setup,cameras,neutral,render,ray,ray_direction


def project(scene,folder,unit):
    data=json.loads((folder/'detections.json').read_text());candidates=[];rejected=[];suns=[]
    for view in data['views']:
        cam=bpy.data.objects[view['id']]
        for obj in view['objects']:
            if obj['confidence']<.7:continue
            x,y=obj['center'];direction=ray_direction(scene,cam,x,y)
            if obj['kind']=='sun':
                suns.append({**obj,'view':view['id'],'direction':list(direction)});continue
            hit=ray(scene,cam,x,y)
            if not hit:rejected.append({**obj,'view':view['id'],'rejection':'no visible surface'});continue
            position=Vector(hit['position']);normal=Vector(hit['normal'])
            a,b,c,d=obj['bbox'];corners=[];faces=set();distances=[]
            for sx,sy in [(a,b),(c,b),(c,d),(a,d)]:
                dr=ray_direction(scene,cam,sx,sy);den=dr.dot(normal)
                if abs(den)<.08:break
                t=(position-cam.location).dot(normal)/den
                if t<=0 or t>hit['distance']*3:break
                corners.append(list(cam.location+dr*t))
            if len(corners)!=4:
                rejected.append({**obj,'view':view['id'],'rejection':'grazing or invalid window plane'});continue
            width=(Vector(corners[1])-Vector(corners[0])).length
            height=(Vector(corners[3])-Vector(corners[0])).length
            if min(width,height)<unit*.015 or max(width,height)>unit*(5 if obj['kind']=='window' else 3):
                rejected.append({**obj,'view':view['id'],'rejection':'implausible extent'});continue
            for i in range(5):
                for j in range(5):
                    h=ray(scene,cam,a+(c-a)*(.1+.8*i/4),b+(d-b)*(.1+.8*j/4))
                    if h and h['mesh']==hit['mesh'] and abs((Vector(h['position'])-position).dot(normal))<unit*.35:
                        faces.add(h['face']);distances.append(h['distance'])
            if len(distances)<5:
                rejected.append({**obj,'view':view['id'],'rejection':'insufficient surface support'});continue
            candidates.append({**obj,**hit,'corners':corners,'width':width,'height':height,
                'face_ids':sorted(faces),'face_ids_note':'sampled supporting faces, may include frames or wall; not a glass segmentation',
                'view':view['id'],'camera_position':list(cam.location)})
    save(folder/'projected.json',{'candidates':candidates,'rejected':rejected,'sun_evidence':suns})


def mark(scene,folder,output,views):
    inventory=json.loads((folder/'inventory.json').read_text())
    collection=bpy.data.collections.new('SEMANTIC_PLACEMENT');scene.collection.children.link(collection)
    for g in inventory['objects']:
        marker=bpy.data.objects.new(g['id'],None);collection.objects.link(marker)
        marker.empty_display_type='CUBE';marker.empty_display_size=1;marker.show_name=True;marker.show_in_front=True
        marker.location=g['position']
        normal=Vector(g['normal']);right=(Vector(g['corners'][1])-Vector(g['corners'][0])).normalized()
        up=normal.cross(right).normalized()
        from mathutils import Matrix
        marker.rotation_euler=Matrix((right,up,normal)).transposed().to_euler()
        marker.scale=(g['width']/2,g['height']/2,max(g['width'],g['height'])*.01)
        for key in ('kind','status','role','confidence','lighting_assigned','material_assigned'):marker[key]=g[key]
        marker['surface_mesh']=g['mesh'];marker['surface_normal']=g['normal'];marker['inventory_file']=str(output/'inventory.json')
        marker.color=(0,1,1,1) if g['kind']=='window' else (1,.6,0,1)
    labels={}
    for view in views:
        cam=bpy.data.objects[view['id']];items=[]
        for g in inventory['objects']:
            point=Vector(g['position']);dr=point-cam.location
            hit,p,*_=scene.ray_cast(bpy.context.evaluated_depsgraph_get(),cam.location,dr.normalized(),distance=dr.length+.01)
            projected=[world_to_camera_view(scene,cam,Vector(v)) for v in g['corners']]
            if (not hit or (p-point).length<.1) and all(v.z>0 for v in projected) and any(0<v.x<1 and 0<v.y<1 for v in projected):
                items.append({'id':g['id'],'polygon':[[float(v.x),float(1-v.y)] for v in projected]})
        labels[view['id']]=items
    save(folder/'labels.json',labels)
    # Saved scene keeps source materials and black world; scan emission never reaches it.
    for obj in list(bpy.data.objects):
        if obj.type=='CAMERA':bpy.data.objects.remove(obj,do_unlink=True)
    scene.camera=None
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type=='VIEW_3D':
                shading=area.spaces.active.shading
                shading.type='MATERIAL';shading.use_scene_world=False;shading.use_scene_lights=False
    assert not any(o.type=='LIGHT' for o in scene.objects)
    bpy.ops.wm.save_as_mainfile(filepath=str(output/'street_mapped.blend'),compress=True)


def main():
    output,stage,r=sys.argv[sys.argv.index('--')+1:];output=Path(output);r=int(r)
    folder=output/f'round_{r:02d}';job=json.loads((output/'job.json').read_text())
    scene,positions,unit=setup(job);views=cameras(scene,positions,job,r)
    if stage=='scan':
        neutral(scene)
        for v in views:render(scene,bpy.data.objects[v['id']],folder/(v['id']+'.png'))
        save(folder/'views.json',{'views':views,'unit':unit})
    elif stage=='project':project(scene,folder,unit)
    elif stage=='mark':mark(scene,folder,output,views)
    else:raise ValueError(stage)


if __name__=='__main__':main()
