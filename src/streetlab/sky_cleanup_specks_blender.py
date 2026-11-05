"""Final render feedback: remove small upper pieces visibly isolated in empty sky."""
import json,sys
from pathlib import Path
import bpy,bmesh,numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from streetlab.sky_cleanup_blender import nearest_face,coordinates,render


def main(folder):
    bpy.ops.wm.open_mainfile(filepath=str(folder/'sky_review.blend'))
    scene=bpy.context.scene
    report=json.loads((folder/'report.json').read_text())
    manifest=json.loads((folder/'views.json').read_text())
    objects=[o for o in scene.objects if o.type=='MESH' and o.get('sky_source') and not o.get('sky_removed')]
    trees=[(o,BVHTree.FromObject(o,bpy.context.evaluated_depsgraph_get())) for o in objects]
    xyz={o:coordinates(o) for o in objects}
    selected={o:set() for o in objects}
    rays=[]
    for view in manifest['views']:
        path=folder/(view['id']+'_isolated_mask.png')
        if not path.exists():continue
        camera=bpy.data.objects[view['id']]
        image=bpy.data.images.load(str(path),check_existing=False)
        w,h=image.size;values=np.empty(w*h*4,dtype=np.float32);image.pixels.foreach_get(values)
        mask=values.reshape(h,w,4)[::-1,:,0]>.5;bpy.data.images.remove(image)
        corners=camera.data.view_frame(scene=scene)
        minx,maxx=min(v.x for v in corners),max(v.x for v in corners)
        miny,maxy=min(v.y for v in corners),max(v.y for v in corners)
        for y,x in np.argwhere(mask):
            for dx,dy in ((.5,.5),(.2,.2),(.2,.5),(.2,.8),(.5,.2),(.5,.8),(.8,.2),(.8,.5),(.8,.8)):
                ray=Vector((minx+(x+dx)/w*(maxx-minx),maxy-(y+dy)/h*(maxy-miny),corners[0].z))
                direction=(camera.matrix_world.to_3x3()@ray).normalized()
                hit=nearest_face(camera.matrix_world.translation,direction,trees)
                if hit is None:continue
                distance,obj,index=hit
                vertices=xyz[obj][list(obj.data.polygons[index].vertices)]
                if vertices[:,2].min()>=report['minimum_z']:
                    selected[obj].add(index)
                    rays.append((camera.matrix_world.translation.copy(),direction,distance))
    # Resolve the back layers of each tiny island without rendering every layer.
    local={o:np.asarray([v.co[:] for v in o.data.vertices]) for o in objects}
    triangles={o:np.asarray([p.vertices[:] for p in o.data.polygons]) for o in objects}
    span=manifest['height_high']-manifest['height_low']
    for iteration in range(16):
        current=[]
        for obj in objects:
            keep=np.ones(len(triangles[obj]),dtype=bool);keep[list(selected[obj])]=False
            ids=np.flatnonzero(keep)
            current.append((obj,BVHTree.FromPolygons(local[obj].tolist(),triangles[obj][ids].tolist(),all_triangles=True),ids))
        additional=0
        for origin,direction,initial_distance in rays:
            hit=nearest_face(origin,direction,current)
            if hit is None:continue
            distance,obj,index=hit
            if distance-initial_distance>span*.3:continue
            if xyz[obj][triangles[obj][index],2].min()>=report['minimum_z'] and index not in selected[obj]:
                selected[obj].add(index);additional+=1
        if not additional:break
    degenerate_total=0
    for obj in objects:
        if len(selected[obj])>len(obj.data.polygons)*.005:raise ValueError('Final speck removal exceeds limit')
        corners=xyz[obj][triangles[obj]]
        area=np.linalg.norm(np.cross(corners[:,1]-corners[:,0],corners[:,2]-corners[:,0]),axis=1)
        f32=corners.astype(np.float32)
        export_area=np.linalg.norm(np.cross(f32[:,1]-f32[:,0],f32[:,2]-f32[:,0]),axis=1)
        zero=np.flatnonzero((area<1e-9)|(export_area<1e-10))
        selected[obj].update(map(int,zero));degenerate_total+=len(zero)
    total=0
    for obj,ids in selected.items():
        if not ids:continue
        attr=obj.data.attributes.new('_isolated_selected','INT','FACE')
        values=np.zeros(len(obj.data.polygons),dtype=np.int32);values[list(ids)]=1
        attr.data.foreach_set('value',values)
        candidate=obj.copy();candidate.data=obj.data.copy();candidate.name=obj.name+'_ISOLATED_CANDIDATE'
        scene.collection.objects.link(candidate)
        for target,flag in ((obj,True),(candidate,False)):
            bm=bmesh.new();bm.from_mesh(target.data);layer=bm.faces.layers.int['_isolated_selected']
            bmesh.ops.delete(bm,geom=[f for f in bm.faces if bool(f[layer])==flag],context='FACES')
            bm.to_mesh(target.data);bm.free();target.data.attributes.remove(target.data.attributes['_isolated_selected']);target.data.update()
        candidate['sky_source']=False;candidate['sky_removed']=True;candidate.hide_render=True;candidate.hide_set(True)
        if hasattr(candidate,'visible_shadow'):candidate.visible_shadow=False
        obj.data.validate(clean_customdata=False);obj.data.update()
        assert not obj.data.validate(clean_customdata=False)
        total+=len(ids)
    if total:
        bpy.ops.object.select_all(action='DESELECT')
        for obj in objects:obj.select_set(True)
        bpy.ops.export_scene.gltf(filepath=str(folder/'street_without_sky.glb'),export_format='GLB',use_selection=True,export_animations=False)
        bpy.ops.wm.save_as_mainfile(filepath=str(folder/'sky_review.blend'))
        for view in manifest['views']:render(scene,bpy.data.objects[view['id']],folder,view['id']+'_after')
    report['kept_faces']=sum(len(o.data.polygons) for o in objects)
    report.setdefault('final_speck_passes',[]).append({'faces':total,'degenerate_faces':degenerate_total})
    (folder/'report.json').write_text(json.dumps(report,indent=2))
    print('Final isolated faces:',total,flush=True)


if __name__=='__main__':main(Path(sys.argv[sys.argv.index('--')+1]).resolve())
