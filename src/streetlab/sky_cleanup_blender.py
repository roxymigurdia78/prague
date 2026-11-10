"""Background Blender worker. No network, no writes to the input GLB."""
import json
import math
from pathlib import Path
import sys

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from streetlab.sky_cleanup import connected_sky, filter_sky_points, fill_small_holes, mesh_components, close_sky_mask, refine_cut_boundary
from streetlab.sky_cleanup import polygon_sky_mask, cleanup_fingerprint
from streetlab.sky_semantics import allowed as allowed_protection

_TRANSFORMS={}


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def render(scene, camera, output, name):
    scene.camera = camera
    scene.render.filepath = str(output / (name + ".png"))
    bpy.context.view_layer.update()
    bpy.ops.render.render(write_still=True)


def coordinates(obj):
    a = np.empty(len(obj.data.vertices) * 3, dtype=np.float32)
    obj.data.vertices.foreach_get("co", a)
    a = a.reshape(-1, 3)
    m = np.asarray(obj.matrix_world)
    return a @ m[:3, :3].T + m[:3, 3]


def nearest_face(origin, direction, trees):
    nearest=None
    for entry in trees:
        obj,tree=entry[:2]
        key=obj.as_pointer()
        if key not in _TRANSFORMS:
            inverse=obj.matrix_world.inverted()
            _TRANSFORMS[key]=(inverse,inverse.to_3x3(),obj.matrix_world.copy())
        inverse,inverse_rotation,world_matrix=_TRANSFORMS[key]
        location,normal,index,distance=tree.ray_cast(inverse @ origin,(inverse_rotation @ direction).normalized())
        if location is not None:
            if len(entry)>2:
                index=int(entry[2][index])
            distance=((world_matrix @ location)-origin).length
            if nearest is None or distance<nearest[0]:
                nearest=distance,obj,index
    return nearest


def protected_face_owners(region_views,trees):
    """Preserve the original surface visible at every protected image pixel.

    A face's centre can be occluded even while one of its corners is visible.
    Pixel ownership supplies that visibility witness without protecting unrelated
    geometry behind a building along the same viewing ray.
    """
    owners={}
    masks=getattr(region_views,'protected',{})
    for camera,projection,_region in region_views:
        mask=masks.get(camera.name)
        if mask is None or not mask.any():continue
        y,x=np.nonzero(mask);h,w=mask.shape
        clip=np.column_stack(((x+.5)/w*2-1,1-(y+.5)/h*2,-np.ones(len(x)),np.ones(len(x))))
        points=clip @ np.linalg.inv(projection).T
        points=points[:,:3]/points[:,3:4]
        origin=camera.matrix_world.translation
        rays=points-np.asarray(origin)
        rays/=np.maximum(np.linalg.norm(rays,axis=1,keepdims=True),1e-12)
        for direction in rays:
            hit=nearest_face(origin,Vector(direction),trees)
            if hit is not None:owners.setdefault(hit[1].name,set()).add(int(hit[2]))
    return owners


def ensure_protected_owners(region_views,trees):
    if getattr(region_views,'protected',None) and not hasattr(region_views,'protected_faces'):
        region_views.protected_faces=protected_face_owners(region_views,trees)
    return getattr(region_views,'protected_faces',{})


def visible_sky_faces(obj,indices,xyz,eligible,region_views,trees,allow_surrounded=True,require_consensus=True,reference_trees=None,maximum_depth=None):
    candidates=np.flatnonzero(eligible)
    selected=np.zeros_like(eligible)
    if not len(candidates):
        return selected
    all_centers=xyz[indices[candidates]].mean(axis=1)
    possible=np.zeros(len(candidates),dtype=bool)
    homogeneous=np.column_stack((all_centers,np.ones(len(all_centers))))
    for camera,projection,region in region_views:
        clip=homogeneous @ projection.T
        ndc=clip[:,:2]/np.maximum(clip[:,3:4],1e-9)
        h,w=region.shape
        px=np.clip(((ndc[:,0]+1)*.5*w).astype(int),0,w-1)
        py=np.clip(((1-ndc[:,1])*.5*h).astype(int),0,h-1)
        possible|=(clip[:,3]>0)&(np.abs(ndc)<.995).all(axis=1)&region[py,px]
    candidates=candidates[possible]
    if not len(candidates):
        return selected
    weights=np.asarray([[1,0,0],[0,1,0],[0,0,1],[.5,.5,0],[0,.5,.5],[.5,0,.5],[1/3,1/3,1/3]])
    centers=xyz[indices[candidates]].mean(axis=1)
    samples=np.einsum("sk,nkd->nsd",weights,xyz[indices[candidates]])
    positive=np.zeros(len(candidates),dtype=int)
    negative=np.zeros(len(candidates),dtype=int)
    surrounded=np.zeros(len(candidates),dtype=int)
    owners=ensure_protected_owners(region_views,trees)
    protected=np.isin(candidates,list(owners.get(obj.name,set())))
    for camera,projection,region in region_views:
        clip=np.column_stack((centers,np.ones(len(centers)))) @ projection.T
        ndc=clip[:,:2]/np.maximum(clip[:,3:4],1e-9)
        on_screen=(clip[:,3]>0)&(np.abs(ndc)<.995).all(axis=1)
        h,w=region.shape
        center_x=np.clip(((ndc[:,0]+1)*.5*w).astype(int),0,w-1)
        center_y=np.clip(((1-ndc[:,1])*.5*h).astype(int),0,h-1)
        sc=np.concatenate((samples,np.ones((*samples.shape[:2],1))),axis=2) @ projection.T
        sn=sc[:,:,:2]/np.maximum(sc[:,:,3:4],1e-9)
        px=np.clip(((sn[:,:,0]+1)*.5*w).astype(int),0,w-1)
        py=np.clip(((1-sn[:,:,1])*.5*h).astype(int),0,h-1)
        inside=(sc[:,:,3]>0)&(np.abs(sn)<1).all(axis=2)&region[py,px]
        coverage=inside.mean(axis=1)
        protection=getattr(region_views,'protected',{}).get(camera.name)
        protected_overlap=np.zeros(len(candidates),dtype=bool)
        if protection is not None and protection.any():
            # A narrow protected region may fall between all seven face samples.
            # Conservatively test its projected bounding rectangle using a summed
            # area table, retaining a face if any protected pixel could intersect it.
            integral=np.pad(protection.cumsum(axis=0,dtype=np.int32).cumsum(axis=1,dtype=np.int32),((1,0),(1,0)))
            left,right=px[:,:3].min(axis=1),px[:,:3].max(axis=1)+1
            top,bottom=py[:,:3].min(axis=1),py[:,:3].max(axis=1)+1
            protected_overlap=(integral[bottom,right]-integral[top,right]
                -integral[bottom,left]+integral[top,left])>0
        origin=camera.matrix_world.translation
        for row in np.flatnonzero(on_screen):
            direction=(Vector(centers[row])-origin).normalized()
            hit=nearest_face(origin,direction,trees)
            if hit is not None and hit[1]==obj and hit[2]==candidates[row]:
                if protected_overlap[row]:
                    protected[row]=True
                if reference_trees is not None:
                    reference=nearest_face(origin,direction,reference_trees)
                    depth_limit=maximum_depth.get(camera.name,maximum_depth['default']) if isinstance(maximum_depth,dict) else maximum_depth
                    if reference is None or hit[0]-reference[0]>depth_limit:
                        continue
                if coverage[row]>=.85:
                    positive[row]+=1
                    # Strong local evidence: a ring well outside the triangle is all sky.
                    radius=max(6,int(max(px[row].max()-px[row].min(),py[row].max()-py[row].min())*1.5))
                    cx,cy=int(center_x[row]),int(center_y[row])
                    ring=[(cx+dx*radius,cy+dy*radius) for dx,dy in
                          ((-1,-1),(0,-1),(1,-1),(-1,0),(1,0),(-1,1),(0,1),(1,1))]
                    if all(0<=x<w and 0<=y<h and region[y,x] for x,y in ring):
                        surrounded[row]+=1
                elif coverage[row]<=.15:
                    negative[row]+=1
    selected[candidates]=(positive>0)&((positive>=2*negative)|((surrounded>0)&allow_surrounded)|(not require_consensus))&~protected
    return selected


def remove_floaters(obj, indices, xyz, sky_mask, region_views, trees, height_gate, span,allow_strong_context=False):
    """Remove only detached, upper fragments with visible sky around their projections."""
    groups=mesh_components(xyz,indices,~sky_mask,precision=max(span*1e-5,1e-6))
    extra=np.zeros_like(sky_mask)
    details=[]
    owners=ensure_protected_owners(region_views,trees).get(obj.name,set())
    for faces in groups:
        if len(faces)>max(2000,len(indices)*.02):
            continue
        vertices=xyz[indices[faces]].reshape(-1,3)
        low,high=vertices.min(axis=0),vertices.max(axis=0)
        if low[2]<height_gate or (high-low).max()>span*.45:
            continue
        sampled=faces[np.linspace(0,len(faces)-1,min(len(faces),32),dtype=int)]
        centers=xyz[indices[sampled]].mean(axis=1)
        face_set=set(map(int,faces))
        support=opposition=strong=0
        protected_component=bool(set(map(int,faces))&owners)
        observations=[]
        for camera,projection,region in region_views:
            context=region_views.contexts[camera.name]
            h,w=region.shape
            clip=np.column_stack((centers,np.ones(len(centers)))) @ projection.T
            ndc=clip[:,:2]/np.maximum(clip[:,3:4],1e-9)
            visible=(clip[:,3]>0)&(np.abs(ndc)<.99).all(axis=1)
            px=np.clip(((ndc[:,0]+1)*.5*w).astype(int),0,w-1)
            py=np.clip(((1-ndc[:,1])*.5*h).astype(int),0,h-1)
            actual=[]
            origin=camera.matrix_world.translation
            for row in np.flatnonzero(visible):
                hit=nearest_face(origin,(Vector(centers[row])-origin).normalized(),trees)
                if hit is not None and hit[1]==obj and hit[2] in face_set:
                    actual.append(row)
            if not actual:
                continue
            protection=getattr(region_views,'protected',{}).get(camera.name)
            if protection is not None and protection[py[actual],px[actual]].any():
                protected_component=True
            # Use the whole component extent so a roof-edge fragment does not look isolated
            # merely because a few chosen face centers project into sky.
            all_centers=xyz[indices[faces]].mean(axis=1)
            all_clip=np.column_stack((all_centers,np.ones(len(all_centers)))) @ projection.T
            all_ndc=all_clip[:,:2]/np.maximum(all_clip[:,3:4],1e-9)
            front=all_clip[:,3]>0
            if not front.all() or (np.abs(all_ndc)>1).any():
                continue
            xs=(all_ndc[:,0]+1)*.5*w
            ys=(1-all_ndc[:,1])*.5*h
            x0,x1=max(0,int(xs.min())-2),min(w,int(xs.max())+3)
            y0,y1=max(0,int(ys.min())-2),min(h,int(ys.max())+3)
            pad=max(4,int(max(x1-x0,y1-y0)*.2))
            ox0,ox1=max(0,x0-pad),min(w,x1+pad)
            oy0,oy1=max(0,y0-pad),min(h,y1+pad)
            ring=region[oy0:oy1,ox0:ox1].copy()
            ring[y0-oy0:y1-oy0,x0-ox0:x1-ox0]=False
            ring_area=ring.size-(y1-y0)*(x1-x0)
            surround=float(ring.sum()/max(1,ring_area))
            coverage=float(context[py[actual],px[actual]].mean())
            if coverage>=.85 and surround>=.65:
                support+=1
                if coverage>=.99 and surround>=.95:
                    strong+=1
            elif coverage<.3 or surround<.2:
                opposition+=1
            observations.append({"view":camera.name,"sky_around":surround,"sky_context":coverage})
        if support>0 and not protected_component and (opposition==0 or (allow_strong_context and strong>0)):
            extra[faces]=True
            details.append({"faces":len(faces),"bounds_min":low.tolist(),"bounds_max":high.tolist(),"views":observations})
    return extra,details


class RegionViews(list):
    pass


def scan_positions(position,objects,span):
    origin=Vector(position)
    graph=bpy.context.evaluated_depsgraph_get()
    trees=[(o,BVHTree.FromObject(o,graph)) for o in objects]
    best=None
    for angle in (0,math.pi/4,math.pi/2,3*math.pi/4):
        direction=Vector((math.cos(angle),math.sin(angle),0))
        distances=[]
        for sign in (1,-1):
            hit=nearest_face(origin,direction*sign,trees)
            distances.append(hit[0] if hit else float(max(span)))
        score=min(distances)
        if best is None or score>best[0]:
            best=score,direction
    positions=[list(origin)]
    eye=min(1.6,float(span[2])*.15)
    step=float(span[2])*.3
    for sign in (-1,1):
        candidate=origin+best[1]*step*sign
        floor=nearest_face(candidate,Vector((0,0,-1)),trees)
        if floor is None or not .01<floor[0]<eye*2.5:
            continue
        candidate.z=candidate.z-floor[0]+eye
        clearance=[]
        for i in range(8):
            angle=i*math.pi/4
            hit=nearest_face(candidate,Vector((math.cos(angle),math.sin(angle),0)),trees)
            clearance.append(hit[0] if hit else float(max(span)))
        ceiling=nearest_face(candidate,Vector((0,0,1)),trees)
        if min(clearance)>float(span[2])*.025 and (ceiling is None or ceiling[0]>eye*.5):
            positions.append(list(candidate))
    return positions


def prepare(output, config):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=config["source"])
    if config.get("source_up", "gltf") == "marble":
        # Marble exports OpenCV coordinates; glTF import alone leaves vertical inverted.
        flip = Matrix.Rotation(math.pi, 4, "X")
        for obj in bpy.context.scene.objects:
            if obj.parent is None:
                obj.matrix_world = flip @ obj.matrix_world
        bpy.context.view_layer.update()
    objects = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not objects:
        raise ValueError("GLBにメッシュがありません。")
    for o in objects:
        o["sky_source"] = True
    xyz = np.concatenate([coordinates(o) for o in objects])
    low, high = np.percentile(xyz, [2, 98], axis=0)
    span = np.maximum(high - low, 0.01)
    if config["camera"]:
        position = config["camera"]
    else:
        # Marble's original viewpoint is generally near the origin. Expose an override.
        xy = [0 if low[i] <= 0 <= high[i] else float((low[i] + high[i]) / 2) for i in (0, 1)]
        position = [*xy, float(low[2] + min(1.6, span[2] * 0.15))]
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    thorough=config.get("quality")=="thorough"
    scene.cycles.samples = 32 if thorough else 8
    scene.cycles.use_denoising = True
    scene.render.resolution_x = 1280 if thorough else 640
    scene.render.resolution_y = 960 if thorough else 480
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0
    scene.view_settings.gamma = 1
    scene.world = bpy.data.worlds.new("Outside geometry - magenta")
    scene.world.use_nodes = True
    scene.world.node_tree.nodes["Background"].inputs["Color"].default_value = (1, 0, 1, 1)
    scene.world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0
    # Magenta visible to the camera, with neutral illumination for non-unlit materials.
    nodes = scene.world.node_tree.nodes
    links = scene.world.node_tree.links
    camera_bg = nodes.new("ShaderNodeBackground")
    camera_bg.inputs["Color"].default_value = (1, 0, 1, 1)
    camera_bg.inputs["Strength"].default_value = 1
    nodes["Background"].inputs["Color"].default_value = (1, 1, 1, 1)
    nodes["Background"].inputs["Strength"].default_value = 0.4
    light_path = nodes.new("ShaderNodeLightPath")
    mix = nodes.new("ShaderNodeMixShader")
    links.new(light_path.outputs["Is Camera Ray"], mix.inputs[0])
    links.new(nodes["Background"].outputs[0], mix.inputs[1])
    links.new(camera_bg.outputs[0], mix.inputs[2])
    links.new(mix.outputs[0], nodes["World Output"].inputs["Surface"])
    views = []
    positions=scan_positions(position,objects,span) if thorough else [position]
    for point_index,point in enumerate(positions):
        for i in range(5):
            data = bpy.data.cameras.new(f"view_{len(views):02d}")
            data.type = "PERSP"
            data.lens = 18
            data.clip_start = 0.01
            data.clip_end = float(max(span) * 20)
            camera = bpy.data.objects.new(data.name, data)
            scene.collection.objects.link(camera)
            camera.location = point
            if i == 4:
                direction = Vector((0, 0.001, 1))
            else:
                azimuth = i * math.pi / 2
                elevation = math.radians(40)
                direction = Vector((math.cos(azimuth) * math.cos(elevation), math.sin(azimuth) * math.cos(elevation), math.sin(elevation)))
            camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
            bpy.context.view_layer.update()
            render(scene, camera, output, camera.name)
            views.append({"id": camera.name,"position_index":point_index,"position":point})
            print(f"Prepared {camera.name} from position {point_index+1}/{len(positions)}",flush=True)
    save_json(output / "views.json", {"views": views, "camera_position": position,"positions":positions,
              "height_low": float(low[2]), "height_high": float(high[2]),
              "coordinate_system": "Blender world, Z up"})
    bpy.ops.wm.save_as_mainfile(filepath=str(output / "prepared.blend"))


def linear_to_srgb(a):
    return np.where(a <= 0.0031308, 12.92 * a, 1.055 * np.maximum(a, 0) ** (1 / 2.4) - 0.055)


class Sampler:
    def __init__(self):
        self.cache = {}
        self.mesh_cache = {}

    def image(self, image):
        if image.name not in self.cache:
            # A downscaled COPY is for classification only; original textures are exported intact.
            copy = image.copy()
            if max(copy.size) > 2048:
                factor = 2048 / max(copy.size)
                copy.scale(max(1, int(copy.size[0] * factor)), max(1, int(copy.size[1] * factor)))
            width, height = copy.size
            pixels = np.empty(width * height * 4, dtype=np.float32)
            copy.pixels.foreach_get(pixels)
            rgb = pixels.reshape(height, width, 4)[:, :, :3]
            # Image.pixels contains stored color values; sRGB images are not linear here.
            self.cache[image.name] = np.clip(rgb, 0, 1)
            bpy.data.images.remove(copy)
        return self.cache[image.name]

    def colors(self, obj, face_ids, weights):
        mesh = obj.data
        face_ids = np.asarray(face_ids)
        if mesh.name not in self.mesh_cache:
            material_all = np.empty(len(mesh.polygons), dtype=np.int32)
            mesh.polygons.foreach_get("material_index", material_all)
            loops_all = np.empty(len(mesh.polygons), dtype=np.int32)
            mesh.polygons.foreach_get("loop_start", loops_all)
            uv_all = None
            if mesh.uv_layers.active:
                uv_all = np.empty(len(mesh.loops) * 2, dtype=np.float32)
                mesh.uv_layers.active.data.foreach_get("uv", uv_all)
                uv_all = uv_all.reshape(-1, 2)
            self.mesh_cache[mesh.name] = material_all, loops_all, uv_all
        material_all, loops_all, uv_all = self.mesh_cache[mesh.name]
        material_ids = material_all[face_ids]
        uv_data = uv_all is not None
        colors = np.zeros((len(face_ids), len(weights), 3), dtype=np.float32)
        loops = loops_all[face_ids]
        if uv_data:
            uv = uv_all[loops[:, None] + np.arange(3)]
            locations = np.einsum("sk,nkd->nsd", weights, uv)
        for material_id in np.unique(material_ids):
            rows = np.flatnonzero(material_ids == material_id)
            material = mesh.materials[int(material_id)] if len(mesh.materials) > int(material_id) else None
            image_nodes = [n for n in material.node_tree.nodes if n.type == "TEX_IMAGE" and n.image] if material and material.use_nodes else []
            if image_nodes and uv_data:
                texture = self.image(image_nodes[0].image)
                h, w = texture.shape[:2]
                selected_uv = locations[rows]
                x = np.minimum((np.mod(selected_uv[:, :, 0], 1) * w).astype(int), w - 1)
                y = np.minimum((np.mod(selected_uv[:, :, 1], 1) * h).astype(int), h - 1)
                colors[rows] = texture[y, x]
            else:
                color = material.diffuse_color[:3] if material else (0.8, 0.8, 0.8)
                colors[rows] = linear_to_srgb(np.asarray(color))
        return colors


def apply(output, config):
    bpy.ops.wm.open_mainfile(filepath=str(output / "prepared.blend"))
    if any(o.get("sky_removed") for o in bpy.context.scene.objects):
        raise ValueError("元の撮影データではありません。")
    objects = [o for o in bpy.context.scene.objects if o.type == "MESH" and o.get("sky_source")]
    views = json.loads((output / "views.json").read_text(encoding="utf-8"))
    height_gate = config["min_z"] if config["min_z"] is not None else views["height_low"] + config["upper_fraction"] * (views["height_high"] - views["height_low"])
    original_faces = sum(len(o.data.polygons) for o in objects)
    thorough=config.get("quality")=="thorough"
    maximum_edge = max((views["height_high"]-views["height_low"]) * (.015 if thorough else .04), .008 if thorough else .02)
    for obj in objects:
        bm=bmesh.new()
        bm.from_mesh(obj.data)
        for iteration in range(6 if thorough else 4):
            edges=[]
            for edge in bm.edges:
                a,b=(obj.matrix_world @ v.co for v in edge.verts)
                if min(a.z,b.z)>=height_gate and (a-b).length>maximum_edge:
                    edges.append(edge)
            if not edges or len(bm.faces)>1500000:
                break
            bmesh.ops.subdivide_edges(bm,edges=edges,cuts=1,use_grid_fill=True)
            bmesh.ops.triangulate(bm,faces=list(bm.faces))
        bm.to_mesh(obj.data)
        bm.free()
        obj.data.update()
    bpy.context.view_layer.update()
    # glTF imports triangles; reject other topology rather than incorrectly mapping UV samples.
    if any(len(p.vertices) != 3 for o in objects for p in o.data.polygons):
        raise ValueError("三角形メッシュのGLBが必要です。")
    analysis = json.loads((output / "analysis.json").read_text(encoding="utf-8"))
    scene = bpy.context.scene
    sampler = Sampler()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    trees = [(o, BVHTree.FromObject(o, depsgraph)) for o in objects]
    rendered = []
    for view in analysis["views"]:
        image = bpy.data.images.load(str(output / (view["id"] + ".png")), check_existing=False)
        width,height=image.size
        pixels=np.empty(width*height*4,dtype=np.float32)
        image.pixels.foreach_get(pixels)
        rendered.append(pixels.reshape(height,width,4)[::-1,:,:3].copy())
        bpy.data.images.remove(image)
    filtered_points=filter_sky_points(rendered,analysis["views"])
    save_json(output / "filtered_points.json",filtered_points)
    palette, seeds = [], []
    height_gate = config["min_z"] if config["min_z"] is not None else views["height_low"] + config["upper_fraction"] * (views["height_high"] - views["height_low"])
    for view in analysis["views"]:
        camera = bpy.data.objects[view["id"]]
        scene.camera = camera
        corners = camera.data.view_frame(scene=scene)
        min_x, max_x = min(v.x for v in corners), max(v.x for v in corners)
        min_y, max_y = min(v.y for v in corners), max(v.y for v in corners)
        for x, y in filtered_points[view["id"]]:
            ray = Vector((min_x + x / 1000 * (max_x - min_x), max_y - y / 1000 * (max_y - min_y), corners[0].z))
            direction = (camera.matrix_world.to_3x3() @ ray).normalized()
            origin = camera.matrix_world.translation
            hits = []
            for obj, tree in trees:
                inverse = obj.matrix_world.inverted()
                local_origin = inverse @ origin
                local_direction = (inverse.to_3x3() @ direction).normalized()
                location, normal, index, distance = tree.ray_cast(local_origin, local_direction)
                if location is not None:
                    world_location = obj.matrix_world @ location
                    hits.append(((world_location - origin).length, obj, index, location, world_location))
            if not hits:
                continue
            _, obj, index, location, world_location = min(hits, key=lambda item: item[0])
            if world_location.z < height_gate:
                continue
            polygon = obj.data.polygons[index]
            a, b, c = [obj.data.vertices[i].co for i in polygon.vertices]
            v0, v1, v2 = b-a, c-a, location-a
            d00, d01, d11 = v0.dot(v0), v0.dot(v1), v1.dot(v1)
            denominator = d00 * d11 - d01 * d01
            if abs(denominator) < 1e-15:
                continue
            v = (d11 * v2.dot(v0) - d01 * v2.dot(v1)) / denominator
            w = (d00 * v2.dot(v1) - d01 * v2.dot(v0)) / denominator
            color = sampler.colors(obj, [index], np.asarray([[1-v-w, v, w]]))[0, 0]
            if color[0] > 0.7 and color[1] < 0.25 and color[2] > 0.7:
                continue
            palette.append(color.tolist())
            seeds.append({"view": view["id"], "xy": [x, y], "z": float(world_location.z), "rgb": color.tolist()})
    save_json(output / "sky_samples.json", {"minimum_z": height_gate, "seeds": seeds})
    if not any(filtered_points.values()):
        raise ValueError("上側の空に対応する点を取得できませんでした。viewsとanalysisを確認してください。")
    # Check vertices, edge midpoints and center against the observed image sky regions.
    weights = np.asarray([[1,0,0],[0,1,0],[0,0,1],[.5,.5,0],[0,.5,.5],[.5,0,.5],[1/3,1/3,1/3]])
    region_views = RegionViews()
    region_views.contexts={}
    region_views.protected={}
    protection_path=output/'sky_protection_review.json'
    protection=json.loads(protection_path.read_text(encoding='utf-8')) if protection_path.exists() else {'views':[]}
    protected_polygons={v['id']:v.get('protected_polygons',[]) for v in protection['views']}
    for view, rgb in zip(analysis["views"],rendered):
        height,width=rgb.shape[:2]
        mask = fill_small_holes(connected_sky(rgb, filtered_points[view["id"]], tolerance=config.get("region_tolerance",.14), adaptive=True))
        # A roofline can enclose a whole upper facade in the sky mask. Only
        # bridge tiny holes; a six-percent image hole can be a real building.
        context = fill_small_holes(close_sky_mask(mask,radius=2),maximum=min(2000,int(width*height*.002)))
        protected=(polygon_sky_mask(context.shape,protected_polygons.get(view['id'],[]))
            & allowed_protection(output,view['id'],context.shape))
        context &= ~protected
        region_views.protected[view['id']]=protected
        # Save the per-view mask so color/region mistakes can be reviewed independently.
        preview = bpy.data.images.new(view["id"] + "_mask", width=width, height=height)
        rgba = np.ones((height,width,4),dtype=np.float32)
        rgba[:,:,:3] = context[::-1,:,None]
        preview.pixels.foreach_set(rgba.ravel())
        preview.filepath_raw = str(output / (view["id"] + "_mask.png"))
        preview.file_format = "PNG"
        preview.save()
        bpy.data.images.remove(preview)
        camera = bpy.data.objects[view["id"]]
        projection = np.asarray(camera.calc_matrix_camera(depsgraph, x=width, y=height)) @ np.asarray(camera.matrix_world.inverted())
        region_views.append((camera, projection, context))
        region_views.contexts[camera.name]=context
    selected, total, cleaned, sky_objects = 0, 0, [], []
    floater_details=[]
    revealed_layers=[]
    visibility_converged=True
    boundary_adjustments=[]
    for obj in objects:
        mesh = obj.data
        face_ids = np.arange(len(mesh.polygons))
        indices = np.asarray([tuple(p.vertices) for p in mesh.polygons])
        world_xyz = coordinates(obj)
        z = world_xyz[indices, 2]
        mask=visible_sky_faces(obj,indices,world_xyz,np.min(z,axis=1)>=height_gate,
                               region_views,trees,allow_surrounded=False)
        # Removing the visible sky can expose another layer. Rebuild visibility from
        # remaining geometry, keeping an exact mapping to original face IDs.
        local=np.empty(len(mesh.vertices)*3,dtype=np.float32)
        mesh.vertices.foreach_get("co",local)
        local=local.reshape(-1,3)
        for iteration in range(12):
            remaining=np.flatnonzero(~mask)
            tree=BVHTree.FromPolygons(local.tolist(),indices[remaining].tolist(),all_triangles=True)
            next_trees=[(o,t) for o,t in trees if o!=obj]+[(obj,tree,remaining)]
            additional=visible_sky_faces(obj,indices,world_xyz,(np.min(z,axis=1)>=height_gate)&~mask,
                                         region_views,next_trees,allow_surrounded=False)
            count=int(additional.sum())
            if not count:
                break
            mask|=additional
            revealed_layers.append({"object":obj.name,"pass":iteration+1,"faces":count})
            if iteration==11:
                visibility_converged=False
        # After the sky is gone, small pieces may become detached even if their color
        # differs completely from the sky. Check topology and the surrounding sky.
        extra,details=remove_floaters(obj,indices,world_xyz,mask,region_views,trees,height_gate,
                                     views["height_high"]-views["height_low"])
        mask|=extra
        floater_details.extend(details)
        if thorough:
            new_xyz,adjustment=refine_cut_boundary(world_xyz,indices,mask,height_gate,
                maximum_move=(views["height_high"]-views["height_low"])*.0015,
                preserve_geometry=any(mask.any() for mask in region_views.protected.values()))
            inverse=np.asarray(obj.matrix_world.inverted())
            local_xyz=new_xyz @ inverse[:3,:3].T+inverse[:3,3]
            mesh.vertices.foreach_set("co",local_xyz.astype(np.float32).ravel())
            mesh.update()
            boundary_adjustments.append({"object":obj.name,**adjustment})
        total += len(mask)
        selected += int(mask.sum())
        obj["sky_candidate_faces"] = int(mask.sum())
        attribute_name = "_sky_cleanup_selected"
        if mesh.attributes.get(attribute_name):
            raise ValueError("判定用の面属性が既に存在します。")
        attribute = mesh.attributes.new(attribute_name, "INT", "FACE")
        attribute.data.foreach_set("value", mask.astype(np.int32))
        # A separate candidate object keeps exact original faces and UVs available for review.
        sky = obj.copy()
        sky.data = mesh.copy()
        sky.name = obj.name + "_SKY_CANDIDATE"
        scene.collection.objects.link(sky)
        for target, remove_selected in ((obj, True), (sky, False)):
            bm = bmesh.new()
            bm.from_mesh(target.data)
            layer = bm.faces.layers.int.get(attribute_name)
            if layer is None:
                raise ValueError("判定用の面属性を引き継げませんでした。")
            # Importing malformed duplicate faces can renumber BMesh faces: use attributes, not indices.
            bmesh.ops.delete(bm, geom=[f for f in bm.faces if bool(f[layer]) == remove_selected], context="FACES")
            bm.to_mesh(target.data)
            bm.free()
            if target.data.attributes.get(attribute_name):
                target.data.attributes.remove(target.data.attributes[attribute_name])
            target.data.update()
        sky["sky_removed"] = True
        sky["sky_source"] = False
        sky.hide_render = True
        sky.hide_set(True)
        if hasattr(sky, "visible_shadow"):
            sky.visible_shadow = False
        cleaned.append(obj)
        sky_objects.append(sky)
    if selected == 0 or selected >= total * 0.6:
        raise ValueError("除去候補が0面、または全体の60%以上です。高さ・色の条件とVLMの判定を確認してください。")
    bpy.ops.object.select_all(action="DESELECT")
    for obj in cleaned:
        obj.select_set(True)
    bpy.ops.export_scene.gltf(filepath=str(output / "street_without_sky.glb"), export_format="GLB", use_selection=True, export_animations=False)
    bpy.ops.wm.save_as_mainfile(filepath=str(output / "sky_review.blend"))
    # Review the serialized geometry. Reloading also clears stale camera/mesh
    # dependency data left after the large mesh edit and image-mask sampling.
    bpy.ops.wm.open_mainfile(filepath=str(output/'sky_review.blend'))
    scene=bpy.context.scene
    cleaned=[obj for obj in scene.objects if obj.type=='MESH' and obj.get('sky_source') and not obj.get('sky_removed')]
    sky_objects=[obj for obj in scene.objects if obj.type=='MESH' and obj.get('sky_removed')]
    red = bpy.data.materials.new("Sky candidate overlay")
    red.use_nodes = True
    red.node_tree.nodes.clear()
    emission = red.node_tree.nodes.new("ShaderNodeEmission")
    emission.inputs["Color"].default_value = (1, 0.02, 0.02, 1)
    result = red.node_tree.nodes.new("ShaderNodeOutputMaterial")
    red.node_tree.links.new(emission.outputs[0], result.inputs["Surface"])
    for view in views["views"]:
        camera = bpy.data.objects[view["id"]]
        render(scene, camera, output, view["id"] + "_after")
    for obj in scene.objects:
        if obj.get("sky_removed"):
            obj.hide_set(False)
            obj.hide_render = False
            obj.data.materials.clear()
            obj.data.materials.append(red)
            for polygon in obj.data.polygons:
                polygon.material_index = 0
    for view in views["views"]:
        render(scene, bpy.data.objects[view["id"]], output, view["id"] + "_overlay")
    save_json(output / "report.json", {"selected_faces": selected, "total_faces": total,
              "cleanup_fingerprint":cleanup_fingerprint(config,analysis,protection),
              "kept_faces": sum(len(o.data.polygons) for o in cleaned),
              "candidate_faces": sum(len(o.data.polygons) for o in sky_objects),
              "original_faces": original_faces, "maximum_upper_edge": maximum_edge,
              "floater_components":len(floater_details),
              "floater_faces":sum(d["faces"] for d in floater_details),
              "floater_details":floater_details,
              "revealed_layers":revealed_layers,
              "visibility_converged":visibility_converged,
              "quality":config.get("quality","normal"),"boundary_adjustments":boundary_adjustments,
              "minimum_z": height_gate, "color_tolerance": config.get("region_tolerance",.14),
              "method": "vlm_seed_regions_multiview",
              "preserved_lower_faces": True, "source_unchanged": config["source"],
              "limitations": ["Upper buildings with matching colors may also be selected; review red overlays.",
              "Original materials and baked illumination are preserved; this does not enable relighting by itself.",
              "Only whole triangles are removed; boundaries may retain sky fragments."]})


if __name__ == "__main__":
    stage, folder = sys.argv[sys.argv.index("--") + 1:]
    output = Path(folder).resolve()
    config = json.loads((output / "job.json").read_text(encoding="utf-8"))
    if stage == "prepare":
        prepare(output, config)
    elif stage == "apply":
        apply(output, config)
    else:
        raise ValueError("Unknown stage")
