"""Local Blender worker: neutral scans, visible surface rays, Cycles lights and previews."""
import json
import math
from pathlib import Path
import sys

import bpy
import numpy as np
from mathutils import Vector
from bpy_extras.object_utils import world_to_camera_view

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from streetlab.light_placement import save
from streetlab.sky_cleanup_blender import coordinates, scan_positions


def setup(job):
    bpy.ops.wm.open_mainfile(filepath=job['source'])
    scene=bpy.context.scene
    for o in list(bpy.data.objects):
        if o.type in ('LIGHT','CAMERA'):bpy.data.objects.remove(o,do_unlink=True)
    scene.render.engine='CYCLES';scene.cycles.samples=16;scene.cycles.use_denoising=True
    scene.cycles.max_bounces=6;scene.cycles.diffuse_bounces=3;scene.cycles.glossy_bounces=3
    scene.render.resolution_x=768;scene.render.resolution_y=576;scene.render.resolution_percentage=100
    scene.render.image_settings.file_format='PNG';scene.view_settings.view_transform='AgX'
    scene.view_settings.exposure=0;scene.render.film_transparent=False
    xyz=np.concatenate([coordinates(o) for o in scene.objects if o.type=='MESH'])
    low,high=np.percentile(xyz,[2,98],axis=0);span=np.maximum(high-low,.01)
    # Existing camera positions are in exactly the same Blender frame as the prepared mesh.
    path=Path(job['source']).parent/'views.json'
    if path.exists():positions=json.loads(path.read_text())['positions']
    else:
        xy=[0 if low[i]<=0<=high[i] else float((low[i]+high[i])/2) for i in (0,1)]
        positions=scan_positions([*xy,float(low[2]+min(1.6,span[2]*.15))],
                                [o for o in scene.objects if o.type=='MESH'],span)
    unit=float(span[2]/8) # heuristic street height of 8m; expose in plan, not a measured scale
    scene.world=bpy.data.worlds.new('AUTO lighting world');scene.world.use_nodes=True
    scene.world.node_tree.nodes['Background'].inputs['Color'].default_value=(.12,.18,.3,1)
    scene.world.node_tree.nodes['Background'].inputs['Strength'].default_value=0
    return scene,positions,unit


def cameras(scene,positions,job,r):
    result=[];count=job['views_per_position']
    for station,point in enumerate(positions):
        for i in range(count):
            name=f'r{r:02d}_v{len(result):02d}'
            data=bpy.data.cameras.new(name);data.lens=22;data.clip_start=.01;data.clip_end=500
            obj=bpy.data.objects.new(name,data);scene.collection.objects.link(obj);obj.location=point
            angle=(i+r/job['rounds'])*2*math.pi/count
            obj.rotation_euler=Vector((math.cos(angle),math.sin(angle),.20)).to_track_quat('-Z','Y').to_euler()
            result.append({'id':name,'station':station,'position':list(point)})
    bpy.context.view_layer.update()
    return result


def render(scene,camera,path):
    scene.camera=camera;scene.render.filepath=str(path)
    bpy.context.view_layer.update()
    bpy.ops.render.render(write_still=True)


def neutral(scene):
    for mat in bpy.data.materials:
        if not mat.use_nodes:continue
        outputs=[n for n in mat.node_tree.nodes if n.type=='OUTPUT_MATERIAL' and n.is_active_output]
        if not outputs or not outputs[0].inputs['Surface'].is_linked:continue
        shader=outputs[0].inputs['Surface'].links[0].from_node
        if shader.type!='BSDF_PRINCIPLED':continue
        color=shader.inputs['Base Color'];emission=mat.node_tree.nodes.new('ShaderNodeEmission')
        emission.inputs['Color'].default_value=color.default_value
        if color.is_linked:mat.node_tree.links.new(color.links[0].from_socket,emission.inputs['Color'])
        mat.node_tree.links.new(emission.outputs[0],outputs[0].inputs['Surface'])
    scene.view_settings.view_transform='Standard'


def ray_direction(scene,camera,x,y):
    corners=camera.data.view_frame(scene=scene)
    # view_frame order is top-right, bottom-right, bottom-left, top-left
    local=corners[3].lerp(corners[0],x/1000).lerp(corners[2].lerp(corners[1],x/1000),y/1000)
    return (camera.matrix_world.to_3x3()@local).normalized()


def ray(scene,camera,x,y):
    direction=ray_direction(scene,camera,x,y)
    hit,point,normal,face,obj,_=scene.ray_cast(bpy.context.evaluated_depsgraph_get(),camera.location,direction)
    if not hit:return None
    if normal.dot(camera.location-point)<0:normal=-normal
    return {'position':list(point),'normal':list(normal),'mesh':obj.name,'face':face,'distance':(point-camera.location).length}


def project(scene,folder,unit):
    detection=json.loads((folder/'detections.json').read_text());candidates=[];rejected=[];suns=[]
    for view in detection['views']:
        camera=bpy.data.objects[view['id']]
        for obj in view['objects']:
            if not obj['emits'] or obj['confidence']<.75:continue
            x,y=obj['center']
            if obj['kind']=='sun':
                # Evidence only: a solar disk in a baked texture does not set today's sun.
                suns.append({'view':view['id'],**obj,'direction':list(ray_direction(scene,camera,x,y)),
                             'camera_position':list(camera.location)});continue
            center=ray(scene,camera,x,y)
            if not center:
                rejected.append({'view':view['id'],'reason':'no mesh at emitter center',**obj});continue
            a,b,c,d=obj['bbox'];hits=[]
            for sx,sy in [(x,y),(x-(c-a)*.18,y),(x+(c-a)*.18,y),(x,y-(d-b)*.18),(x,y+(d-b)*.18)]:
                h=ray(scene,camera,sx,sy)
                if h and h['mesh']==center['mesh'] and math.dist(h['position'],center['position'])<unit*1.5 and sum(i*j for i,j in zip(h['normal'],center['normal']))>.7:hits.append(h)
            if len(hits)<3:
                rejected.append({'view':view['id'],'reason':'inconsistent surface depth',**obj});continue
            points=np.array([h['position'] for h in hits]);size=float(np.linalg.norm(points.max(axis=0)-points.min(axis=0))*2)
            if size>unit*2.5 or size<unit*.015:
                rejected.append({'view':view['id'],'reason':'implausible emitter extent',**obj});continue
            candidates.append({**obj,**center,'size':size,'view':view['id'],'camera_position':list(camera.location)})
    save(folder/'projected.json',{'candidates':candidates,'rejected':rejected,'sun_evidence':suns})


def place(scene,plan):
    unit=plan['unit'];profile=plan['profile']
    scene.world.node_tree.nodes['Background'].inputs['Strength'].default_value={'night':.025,'dusk':.12,'day':.5}[profile]
    for g in plan['lights']:
        if not g['enabled']:continue
        window=g['kind']=='window';normal=Vector(g['normal']);position=Vector(g['position'])
        kind='AREA' if window else 'POINT';data=bpy.data.lights.new(g['id'],kind)
        data.color=(1,.57,.27) if window else (1,.64,.32)
        # Blender power follows scene-length squared; source scale is explicitly heuristic.
        data.energy=(60 if window else 90)*unit**2*g['energy_factor']*(.15 if profile=='day' else 1)
        if window:
            data.shape='DISK';data.size=max(unit*.12,min(unit*1.2,g['size']*.7))
        else:data.shadow_soft_size=max(unit*.04,min(unit*.2,g['size']*.25))
        obj=bpy.data.objects.new(g['id'],data);scene.collection.objects.link(obj)
        obj.location=position+normal*max(unit*.08,g['size']*.12)
        obj.rotation_euler=normal.to_track_quat('-Z','Y').to_euler()
        obj['auto_emitter_kind']=g['kind'];obj['auto_surface']=g['mesh'];obj['auto_confidence']=g['confidence']
        # Small surface patch makes the emitter itself visible and contributes indirect light.
        radius=max(unit*.025,min(unit*.5,g['size']*.28))
        bpy.ops.mesh.primitive_plane_add(size=radius*2,location=position+normal*unit*.015)
        patch=bpy.context.object;patch.name=g['id']+'_emitter';patch.rotation_euler=normal.to_track_quat('Z','Y').to_euler()
        mat=bpy.data.materials.new(patch.name);mat.use_nodes=True
        shader=mat.node_tree.nodes.get('Principled BSDF');shader.inputs['Base Color'].default_value=(*data.color,1)
        shader.inputs['Emission Color'].default_value=(*data.color,1);shader.inputs['Emission Strength'].default_value=3*g['energy_factor']
        patch.data.materials.append(mat)
        # Auxiliary emitting patch should not obstruct the physical lamp light behind it.
        patch.visible_shadow=False
    if plan['sun']:
        az,el=map(math.radians,plan['sun']);toward=Vector((math.cos(el)*math.cos(az),math.cos(el)*math.sin(az),math.sin(el)))
        data=bpy.data.lights.new('AUTO_SUN','SUN');data.energy=2 if profile=='day' else .3
        obj=bpy.data.objects.new(data.name,data);scene.collection.objects.link(obj);obj.rotation_euler=(-toward).to_track_quat('-Z','Y').to_euler()


def overlays(scene,folder,views,plan):
    # Pillow is only in the controller Python; overlays generated by direct PNG helper below.
    positions={}
    for v in views:
        camera=bpy.data.objects[v]
        visible=[]
        for g in plan['lights']:
            if not g['enabled']:continue
            point=Vector(g['position']);ndc=world_to_camera_view(scene,camera,point)
            direction=point-camera.location
            hit,p,*_=scene.ray_cast(bpy.context.evaluated_depsgraph_get(),camera.location,direction.normalized(),distance=direction.length+plan['unit']*.01)
            if ndc.z>0 and 0<ndc.x<1 and 0<ndc.y<1 and (not hit or (p-point).length<plan['unit']*.15):
                visible.append({'id':g['id'],'xy':[float(ndc.x),float(1-ndc.y)]})
        positions[v]=visible
    save(folder/'overlay_labels.json',positions)


def main():
    output,stage,index=sys.argv[sys.argv.index('--')+1:];output=Path(output);r=int(index)
    folder=output/f'round_{r:02d}';job=json.loads((output/'job.json').read_text())
    scene,positions,unit=setup(job);views=cameras(scene,positions,job,r)
    if stage=='scan':
        neutral(scene)
        for v in views:render(scene,bpy.data.objects[v['id']],folder/(v['id']+'.png'))
        save(folder/'views.json',{'views':views,'unit':unit,'unit_note':'scene height/8; heuristic, not measured meters'})
    elif stage=='project':project(scene,folder,unit)
    else:
        plan=json.loads((folder/'plan.json').read_text());place(scene,plan)
        center_views=[v['id'] for v in views if v['station']==0]
        preview=[center_views[i*len(center_views)//4] for i in range(4)]
        for v in preview:render(scene,bpy.data.objects[v],folder/(v+'_lit.png'))
        overlays(scene,folder,preview,plan);save(folder/'preview.json',{'views':preview})
        scene.camera=bpy.data.objects[preview[0]]
        bpy.ops.wm.save_as_mainfile(filepath=str(output/('street_lit.blend' if stage=='final' else 'proposal.blend')),compress=True)
        save(folder/'placed.json',{'enabled':sum(g['enabled'] for g in plan['lights']),
             'light_objects':[o.name for o in scene.objects if o.type=='LIGHT'],
             'cycles_indirect':True,'source':job['source']})


if __name__=='__main__':main()
