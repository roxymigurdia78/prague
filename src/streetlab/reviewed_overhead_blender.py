"""Apply explicit, reviewed sky-opening polygons to a separate cleanup copy."""
import json
import shutil
from pathlib import Path
import sys

import bmesh
import bpy
import numpy as np
from mathutils.bvhtree import BVHTree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from streetlab.sky_cleanup import polygon_sky_mask
from streetlab.sky_cleanup_blender import coordinates, visible_sky_faces, RegionViews, render
from streetlab.blender_presentation import configure_gpu


def main(request_path):
    job = json.loads(Path(request_path).read_text(encoding='utf-8'))
    source, output = Path(job['source']), Path(job['output'])
    output.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source/'views.json',output/'views.json')
    if (source/'north.json').exists():
        shutil.copy2(source/'north.json',output/'north.json')
    bpy.ops.wm.open_mainfile(filepath=str(source/'sky_review.blend'))
    scene = bpy.context.scene
    configure_gpu(scene)
    scene.cycles.samples = 4 if job.get('paired_baseline') else 8
    if job.get('paired_baseline'):scene.cycles.use_denoising=False
    scene.cycles.seed=0
    scene.render.use_persistent_data=True
    views = json.loads((source/'views.json').read_text())
    gate = float(json.loads((source/'report.json').read_text())['minimum_z'])
    objects = [o for o in scene.objects if o.type=='MESH' and o.get('sky_source') and not o.get('sky_removed')]
    regions = RegionViews()
    if job.get('negative_mask_files'):regions.negative_masks={}
    if job.get('protection_folder'):
        protection=json.loads((Path(job['protection_folder'])/'sky_protection_review.json').read_text(encoding='utf-8'))
        protected={row['id']:row['protected_polygons'] for row in protection['views']}
        regions.protected={}
    else:
        protected={}
    graph = bpy.context.evaluated_depsgraph_get()
    names=list(job.get('sky_mask_files') or job['sky_openings'])
    for name in names:
        camera = bpy.data.objects[name]
        if job.get('sky_mask_files'):
            picture=bpy.data.images.load(job['sky_mask_files'][name],check_existing=False)
            width,height=picture.size
            values=np.empty(width*height*4,dtype=np.float32);picture.pixels.foreach_get(values)
            mask=values.reshape(height,width,4)[::-1,:,0]>.5
            bpy.data.images.remove(picture)
        else:
            mask = polygon_sky_mask((960,1280),job['sky_openings'][name])
        if protected:
            keep=polygon_sky_mask(mask.shape,protected.get(name,[]))
            regions.protected[name]=keep
            mask &= ~keep
        height,width=mask.shape
        if job.get('negative_mask_files') and name in job['negative_mask_files']:
            picture=bpy.data.images.load(job['negative_mask_files'][name],check_existing=False)
            nw,nh=picture.size
            if (nw,nh)!=(width,height):raise ValueError('Architecture evidence dimensions do not match sky mask')
            values=np.empty(nw*nh*4,dtype=np.float32);picture.pixels.foreach_get(values)
            regions.negative_masks[name]=values.reshape(nh,nw,4)[::-1,:,0]>.5
            bpy.data.images.remove(picture)
        projection = np.asarray(camera.calc_matrix_camera(graph,x=width,y=height)) @ np.asarray(camera.matrix_world.inverted())
        # An image with no semantic opening is an abstention, not independent
        # evidence that every projected point in the image belongs to a building.
        explicit_negative=getattr(regions,'negative_masks',{}).get(name)
        if (not job.get('automatic') or mask.any()
                or explicit_negative is not None and explicit_negative.any()):
            regions.append((camera,projection,mask))
    render_names=job.get('render_view_ids') or names
    signature={'engine':scene.render.engine,'samples':scene.cycles.samples,'seed':scene.cycles.seed,
        'denoise':scene.cycles.use_denoising,'device':scene.cycles.device,
        'resolution':[scene.render.resolution_x,scene.render.resolution_y,scene.render.resolution_percentage]}
    baseline_done=False
    details = []
    minimum_votes=int(job.get('minimum_reviewed_views',2))
    if job.get('automatic') and minimum_votes<2:
        raise ValueError('Automatic cleanup requires multiple reviewed views')
    if minimum_votes<1 or minimum_votes>len(regions):
        raise ValueError('Invalid number of reviewed views')
    trees = [(o,BVHTree.FromObject(o,graph)) for o in objects]
    selections={}
    restricted=None
    if job.get('restrict_to_selection'):
        with np.load(job['restrict_to_selection'],allow_pickle=False) as data:
            restricted={name:data[name].astype(bool) for name in data.files}
    for obj in objects:
        xyz = coordinates(obj)
        faces = np.asarray([p.vertices[:] for p in obj.data.polygons])
        upper = xyz[faces][...,2].min(axis=1)>=gate
        # Candidate vertices must overlap several reviewed openings; the precise
        # seven-point coverage and actual visibility are checked below. Manual
        # one-view requests retain the stricter all-vertices-in-opening gate.
        votes = np.zeros(len(faces),dtype=np.uint8)
        for camera,projection,mask in regions:
            points = xyz[faces]
            clip = np.concatenate((points,np.ones((*points.shape[:2],1))),axis=2) @ projection.T
            ndc = clip[:,:,:2]/np.maximum(clip[:,:,3:4],1e-9)
            height,width=mask.shape
            x = np.clip(((ndc[:,:,0]+1)*.5*width).astype(int),0,width-1)
            y = np.clip(((1-ndc[:,:,1])*.5*height).astype(int),0,height-1)
            inside=(clip[:,:,3]>0)&(np.abs(ndc)<.995).all(axis=2)&mask[y,x]
            votes += inside.sum(axis=1)>= (2 if job.get('automatic') else 3)
        eligible = upper & (votes>=minimum_votes)
        if restricted is not None:
            if obj.name not in restricted or restricted[obj.name].shape!=eligible.shape:
                raise ValueError('Restricted cleanup selection does not match source faces')
            eligible &= restricted[obj.name]
            # A source triangle can cover an anti-aliasing sample without owning
            # a pixel centre. During a protected replay, exclude every original
            # candidate whose projected bounds could touch any protected pixel.
            # This is deliberately conservative and cannot introduce a new cut.
            for name,polygons in protected.items():
                if not polygons:continue
                keep=polygon_sky_mask((960,1280),polygons)
                integral=np.pad(keep.cumsum(axis=0,dtype=np.int32).cumsum(axis=1,dtype=np.int32),((1,0),(1,0)))
                camera=bpy.data.objects[name]
                matrix=np.asarray(camera.calc_matrix_camera(graph,x=1280,y=960)) @ np.asarray(camera.matrix_world.inverted())
                ids=np.flatnonzero(eligible)
                if not len(ids):break
                points=xyz[faces[ids]]
                clip=np.concatenate((points,np.ones((*points.shape[:2],1))),axis=2) @ matrix.T
                ndc=clip[:,:,:2]/np.maximum(clip[:,:,3:4],1e-9)
                valid=(clip[:,:,3]>0).all(axis=1)&(ndc.max(axis=1)>=-1).all(axis=1)&(ndc.min(axis=1)<=1).all(axis=1)
                px=(np.clip(ndc[:,:,0],-2,2)+1)*.5*1280
                py=(1-np.clip(ndc[:,:,1],-2,2))*.5*960
                left=np.clip(np.floor(px.min(axis=1)).astype(int)-1,0,1280)
                right=np.clip(np.ceil(px.max(axis=1)).astype(int)+2,0,1280)
                top=np.clip(np.floor(py.min(axis=1)).astype(int)-1,0,960)
                bottom=np.clip(np.ceil(py.max(axis=1)).astype(int)+2,0,960)
                overlap=(integral[bottom,right]-integral[top,right]-integral[bottom,left]+integral[top,left])>0
                eligible[ids[valid&overlap]]=False
        removed = np.zeros(len(faces),dtype=bool)
        local = np.empty(len(obj.data.vertices)*3,dtype=np.float32)
        obj.data.vertices.foreach_get('co',local)
        local=local.reshape(-1,3)
        counts=[]
        batch_limit=max(1,int(len(faces)*.08)) if job.get('automatic') else len(faces)
        deferred=0
        for step in range(6):
            remaining=np.flatnonzero(~removed)
            tree=BVHTree.FromPolygons(local.tolist(),faces[remaining].tolist(),all_triangles=True)
            current=[entry for entry in trees if entry[0]!=obj]+[(obj,tree,remaining)]
            extra=visible_sky_faces(obj,faces,xyz,eligible&~removed,regions,current,allow_surrounded=False,
                minimum_positive_views=2 if job.get('automatic') else 1,
                reference_trees=trees if job.get('automatic') else None,
                maximum_depth=(views['height_high']-views['height_low'])*.03 if job.get('automatic') else None)
            counts.append(int(extra.sum()))
            print('Reviewed overhead pass',step+1,counts[-1],flush=True)
            if not extra.any():break
            remaining_budget=batch_limit-int(removed.sum())
            if int(extra.sum())>remaining_budget:
                ids=np.flatnonzero(extra)
                order=np.lexsort((ids,-xyz[faces[ids]][...,2].mean(axis=1)))
                deferred=int(extra.sum())-remaining_budget
                extra[:]=False;extra[ids[order[:remaining_budget]]]=True
            removed|=extra
            if int(removed.sum())>=batch_limit:
                print('Bounded cleanup batch complete; remaining candidates deferred',deferred,flush=True)
                break
        if removed.mean()>.1:
            raise ValueError('Reviewed overhead selection exceeds 10% limit')
        assert not (removed & ~upper).any()
        selections[obj.name]=removed.copy()
        if removed.any() and job.get('paired_baseline') and not job.get('selection_only') and not baseline_done:
            baseline=output/'baseline';baseline.mkdir(exist_ok=True)
            previous=json.loads((source/'report.json').read_text())
            for name in render_names:
                destination=baseline/(name+'_after.png')
                original=source/(name+'_after.png')
                if not destination.exists() and previous.get('render_signature')==signature and original.exists():
                    shutil.copy2(original,destination)
                if not destination.exists():render(scene,bpy.data.objects[name],baseline,name+'_after')
            baseline_done=True
        original_lower=int((~upper).sum())
        bm=bmesh.new();bm.from_mesh(obj.data);bm.faces.ensure_lookup_table()
        bmesh.ops.delete(bm,geom=[bm.faces[int(i)] for i in np.flatnonzero(removed)],context='FACES')
        bm.to_mesh(obj.data);bm.free();obj.data.update()
        details.append({'object':obj.name,'removed_faces':int(removed.sum()),'passes':counts,
                        'lower_faces_preserved':original_lower,'moved_vertices':0,
                        'deferred_faces':deferred,'batch_face_limit':batch_limit})
    np.savez_compressed(output/'removed_faces.npz',**selections)
    if job.get('selection_only'):return
    if not any(mask.any() for mask in selections.values()):
        (output/'report.json').write_text(json.dumps({'source':str(source),'minimum_z':gate,'details':details,
            'kept_faces':sum(len(o.data.polygons) for o in objects),'render_signature':signature,
            'no_geometry_change':True},indent=2),encoding='utf-8')
        (output/'noop.json').write_text('{"no_geometry_change":true}',encoding='utf-8')
        print('No removable geometry; re-render/export skipped',flush=True)
        return
    kept_faces=sum(len(o.data.polygons) for o in objects)
    bpy.ops.wm.save_as_mainfile(filepath=str(output/'sky_review.blend'),compress=True)
    # Reset the render dependency graph after topology changes. Reuse data only
    # across cameras of the same geometry, never the pre-cut acceleration data.
    bpy.ops.wm.open_mainfile(filepath=str(output/'sky_review.blend'))
    scene=bpy.context.scene
    for name in render_names:
        render(scene,bpy.data.objects[name],output,name+'_after')
    (output/'report.json').write_text(json.dumps({'method':'explicit_reviewed_sky_openings',
        'source':str(source),'minimum_z':gate,'details':details,
        'kept_faces':kept_faces,
        'maximum_passes':6,'minimum_reviewed_view_votes':minimum_votes},indent=2),encoding='utf-8')
    report=json.loads((output/'report.json').read_text())
    report['render_signature']=signature
    (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    from streetlab.finalize_world_blender import main as finalize
    finalize(output)


if __name__=='__main__':
    main(sys.argv[sys.argv.index('--')+1])
