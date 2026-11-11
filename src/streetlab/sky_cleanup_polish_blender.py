"""Apply reviewed image-space residual masks to a copy of a cleaned Blender scene."""
import json
from pathlib import Path
import sys

import bmesh
import bpy
import numpy as np
from mathutils.bvhtree import BVHTree

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from streetlab.sky_cleanup_blender import coordinates, visible_sky_faces, remove_floaters, RegionViews, render, save_json
from streetlab.sky_cleanup import refine_cut_boundary, refine_open_boundary
from streetlab.sky_cleanup import polygon_sky_mask
from streetlab.sky_semantics import allowed as allowed_protection


def semantic_artifact_faces(*args,**kwargs):
    """An automated VLM enclosure must respect other visible building views."""
    return visible_sky_faces(*args,allow_surrounded=False,**kwargs)


def project_coverage(xyz,indices,regions):
    weights=np.asarray([[1,0,0],[0,1,0],[0,0,1],[.5,.5,0],[0,.5,.5],[.5,0,.5],[1/3,1/3,1/3]])
    samples=np.einsum('sk,nkd->nsd',weights,xyz[indices])
    maximum=np.zeros(len(indices),dtype=np.float32)
    mixed=np.zeros(len(indices),dtype=bool)
    for camera,projection,mask in regions:
        clip=np.concatenate((samples,np.ones((*samples.shape[:2],1))),axis=2)@projection.T
        ndc=clip[:,:,:2]/np.maximum(clip[:,:,3:4],1e-9)
        h,w=mask.shape
        x=np.clip(((ndc[:,:,0]+1)*.5*w).astype(int),0,w-1)
        y=np.clip(((1-ndc[:,:,1])*.5*h).astype(int),0,h-1)
        inside=(clip[:,:,3]>0)&(np.abs(ndc)<.99).all(axis=2)
        coverage=(mask[y,x]&inside).mean(axis=1)
        maximum=np.maximum(maximum,coverage)
        mixed|=(coverage>0)&(coverage<.99)&inside.all(axis=1)
    return maximum,mixed


def main(output):
    job=json.loads((output/'polish_job.json').read_text())
    source=Path(job['input'])
    bpy.ops.wm.open_mainfile(filepath=str(source/'sky_review.blend'))
    scene=bpy.context.scene
    views=json.loads((source/'views.json').read_text())
    previous=json.loads((source/'report.json').read_text())
    review=json.loads((output/'review.json').read_text()) if (output/'review.json').exists() else {}
    height_gate=previous['minimum_z']
    span=views['height_high']-views['height_low']
    semantic_depth={'default':span*.03}
    for name,fraction in review.get('artifact_depth_fraction',{}).items():
        if name not in {v['id'] for v in views['views']} or not 0<fraction<=1:
            raise ValueError('Invalid reviewed artifact depth')
        semantic_depth[name]=span*fraction
    objects=[o for o in scene.objects if o.type=='MESH' and o.get('sky_source') and not o.get('sky_removed')]
    if not objects:raise ValueError('No cleaned source objects')
    depsgraph=bpy.context.evaluated_depsgraph_get()
    regions=RegionViews()
    semantic_regions=RegionViews()
    protection_path=source/'sky_protection_review.json'
    protection=json.loads(protection_path.read_text(encoding='utf-8')) if protection_path.exists() else {'views':[]}
    polygons={v['id']:v.get('protected_polygons',[]) for v in protection['views']}
    regions.protected={};semantic_regions.protected={}
    for view in views['views']:
        camera=bpy.data.objects[view['id']]
        image=bpy.data.images.load(str(output/(view['id']+'_polish_mask.png')),check_existing=False)
        w,h=image.size
        values=np.empty(w*h*4,dtype=np.float32);image.pixels.foreach_get(values)
        mask=values.reshape(h,w,4)[::-1,:,0]>.5
        bpy.data.images.remove(image)
        protected=(polygon_sky_mask(mask.shape,polygons.get(view['id'],[]))
            & allowed_protection(source,view['id'],mask.shape))
        mask &= ~protected
        regions.protected[camera.name]=protected
        semantic_regions.protected[camera.name]=protected
        projection=np.asarray(camera.calc_matrix_camera(depsgraph,x=w,y=h))@np.asarray(camera.matrix_world.inverted())
        regions.append((camera,projection,mask))
        image=bpy.data.images.load(str(output/(view['id']+'_semantic_mask.png')),check_existing=False)
        values=np.empty(w*h*4,dtype=np.float32);image.pixels.foreach_get(values)
        semantic=values.reshape(h,w,4)[::-1,:,0]>.5
        semantic &= ~protected
        bpy.data.images.remove(image)
        semantic_regions.append((camera,projection,semantic))
    # Refine only triangles that cross the cleanup boundary. Preserve UV interpolation.
    for obj in objects:
        for step in range(2):
            xyz=coordinates(obj)
            indices=np.asarray([p.vertices[:] for p in obj.data.polygons])
            _,mixed=project_coverage(xyz,indices,regions+semantic_regions)
            mixed&=(xyz[indices,:,][...,2].min(axis=1)>=height_gate)
            faceids=set(map(int,np.flatnonzero(mixed)))
            bm=bmesh.new();bm.from_mesh(obj.data);bm.faces.ensure_lookup_table()
            edges={edge for f in bm.faces if f.index in faceids for edge in f.edges
                if ((obj.matrix_world@edge.verts[0].co)-(obj.matrix_world@edge.verts[1].co)).length>span*.003}
            if not edges:bm.free();break
            bmesh.ops.subdivide_edges(bm,edges=list(edges),cuts=1,use_grid_fill=True)
            bmesh.ops.triangulate(bm,faces=list(bm.faces))
            bm.to_mesh(obj.data);bm.free();obj.data.update()
            print('Refined',obj.name,len(obj.data.polygons),flush=True)
    bpy.context.view_layer.update()
    trees=[(o,BVHTree.FromObject(o,bpy.context.evaluated_depsgraph_get())) for o in objects]
    details=[];candidates=[]
    for obj in objects:
        xyz=coordinates(obj)
        indices=np.asarray([p.vertices[:] for p in obj.data.polygons])
        local=np.empty(len(obj.data.vertices)*3,dtype=np.float32)
        obj.data.vertices.foreach_get('co',local);local=local.reshape(-1,3)
        upper=xyz[indices,:,][...,2].min(axis=1)>=height_gate
        removed=np.zeros(len(indices),dtype=bool)
        layers=[];converged=False
        for step in range(40):
            remaining=np.flatnonzero(~removed)
            tree=BVHTree.FromPolygons(local.tolist(),indices[remaining].tolist(),all_triangles=True)
            current=[entry for entry in trees if entry[0]!=obj]+[(obj,tree,remaining)]
            # Both geometric and VLM proposals must respect contrary observations.
            # A single positive view is enough only if no other visible view objects.
            extra=visible_sky_faces(obj,indices,xyz,upper&~removed,regions,current,
                allow_surrounded=False,reference_trees=trees,maximum_depth=span*.03)
            semantic_regions.protected_faces=getattr(regions,'protected_faces',{})
            extra|=semantic_artifact_faces(obj,indices,xyz,upper&~removed,semantic_regions,current,
                reference_trees=trees,maximum_depth=semantic_depth)
            count=int(extra.sum());print('Polish layer',step+1,count,flush=True)
            if not count:converged=True;break
            removed|=extra;layers.append(count)
        # A depth-limited cut may detach the back of a thick scrap. Once detached,
        # remove its complete connected component, without opening the building behind.
        contexts=RegionViews();contexts.contexts={};contexts.protected=regions.protected
        contexts.protected_faces=getattr(regions,'protected_faces',{})
        for (camera,projection,mask),(_,_,semantic) in zip(regions,semantic_regions):
            context=mask|semantic
            contexts.append((camera,projection,context));contexts.contexts[camera.name]=context
        remaining=np.flatnonzero(~removed)
        tree=BVHTree.FromPolygons(local.tolist(),indices[remaining].tolist(),all_triangles=True)
        current=[entry for entry in trees if entry[0]!=obj]+[(obj,tree,remaining)]
        extra,floaters=remove_floaters(obj,indices,xyz,removed,contexts,current,height_gate,span)
        removed|=extra
        print('Detached residual fragments',len(floaters),int(extra.sum()),flush=True)
        if removed.mean()>.15:raise ValueError('Residual trim exceeds safety limit')
        preserve_geometry=any(mask.any() for mask in regions.protected.values())
        new_xyz,adjustment=refine_cut_boundary(xyz,indices,removed,height_gate,span*.0025,iterations=5,
            preserve_geometry=preserve_geometry)
        inverse=np.asarray(obj.matrix_world.inverted())
        obj.data.vertices.foreach_set('co',(new_xyz@inverse[:3,:3].T+inverse[:3,3]).astype(np.float32).ravel())
        attr=obj.data.attributes.new('_polish_selected','INT','FACE')
        attr.data.foreach_set('value',removed.astype(np.int32))
        candidate=obj.copy();candidate.data=obj.data.copy();candidate.name=obj.name+'_POLISH_CANDIDATE'
        scene.collection.objects.link(candidate)
        for target,delete_value in ((obj,True),(candidate,False)):
            bm=bmesh.new();bm.from_mesh(target.data)
            layer=bm.faces.layers.int['_polish_selected']
            bmesh.ops.delete(bm,geom=[f for f in bm.faces if bool(f[layer])==delete_value],context='FACES')
            bm.to_mesh(target.data);bm.free();target.data.update()
            target.data.attributes.remove(target.data.attributes['_polish_selected'])
        candidate['sky_source']=False;candidate['sky_removed']=True
        candidate.hide_render=True;candidate.hide_set(True)
        if hasattr(candidate,'visible_shadow'):candidate.visible_shadow=False
        candidates.append(candidate)
        details.append({'object':obj.name,'faces':int(removed.sum()),'layers':layers,'converged':converged,'boundary_adjustment':adjustment,'detached_fragments':floaters})
        world=coordinates(obj)
        triangles=np.asarray([p.vertices[:] for p in obj.data.polygons])
        world,open_adjustment=refine_open_boundary(world,triangles,height_gate,span*.008,
            preserve_geometry=preserve_geometry)
        obj.data.vertices.foreach_set('co',(world@inverse[:3,:3].T+inverse[:3,3]).astype(np.float32).ravel())
        obj.data.update()
        details[-1]['open_boundary_adjustment']=open_adjustment
        repaired=obj.data.validate(verbose=True,clean_customdata=False)
        obj.data.update()
        # glTF requires coherent edges/loops even on an open surface. Validate again
        # so an incomplete repair fails before exporting the deliverable.
        if obj.data.validate(verbose=True,clean_customdata=False):
            raise ValueError('Mesh validation did not converge')
        details[-1]['mesh_repaired']=bool(repaired)
    bpy.ops.object.select_all(action='DESELECT')
    candidate_names=[candidate.name for candidate in candidates]
    for obj in objects:obj.select_set(True)
    bpy.ops.export_scene.gltf(filepath=str(output/'street_without_sky.glb'),export_format='GLB',use_selection=True,export_animations=False)
    bpy.ops.wm.save_as_mainfile(filepath=str(output/'sky_review.blend'))
    bpy.ops.wm.open_mainfile(filepath=str(output/'sky_review.blend'))
    scene=bpy.context.scene
    objects=[obj for obj in scene.objects if obj.type=='MESH' and obj.get('sky_source') and not obj.get('sky_removed')]
    candidates=[bpy.data.objects[name] for name in candidate_names]
    for view in views['views']:
        render(scene,bpy.data.objects[view['id']],output,view['id']+'_after')
    red=bpy.data.materials.new('Additional artifact cleanup');red.diffuse_color=(1,0,0,1);red.use_nodes=True
    nodes=red.node_tree.nodes;nodes.clear();emission=nodes.new('ShaderNodeEmission');emission.inputs['Color'].default_value=(1,0,0,1)
    result=nodes.new('ShaderNodeOutputMaterial');red.node_tree.links.new(emission.outputs[0],result.inputs['Surface'])
    for candidate in candidates:
        candidate.hide_render=False;candidate.hide_set(False);candidate.data.materials.clear();candidate.data.materials.append(red)
        for polygon in candidate.data.polygons:polygon.material_index=0
    for view in views['views']:
        render(scene,bpy.data.objects[view['id']],output,view['id']+'_overlay')
    save_json(output/'report.json',{'input':str(source),'model':job['model'],'minimum_z':height_gate,
        'kept_faces':sum(len(o.data.polygons) for o in objects),'details':details,
        'multiview_consensus':True,'single_view_override':False,
        'method':'residual_semantic_artifacts_and_silhouette_opening',
        'limitations':['Does not reconstruct missing architecture or deblur original textures.','View-dependent cleanup is restricted to the observed upper region.']})


if __name__=='__main__':main(Path(sys.argv[sys.argv.index('--')+1]).resolve())
