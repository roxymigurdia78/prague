"""Save the deliverable as street geometry only, retaining the inspection checkpoint."""
import json
from pathlib import Path
import sys

import bpy
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from streetlab.relight_world_blender import save_relight_scene


def main(folder):
    bpy.ops.wm.open_mainfile(filepath=str(folder/'sky_review.blend'))
    kept=[o for o in bpy.context.scene.objects if o.type=='MESH' and o.get('sky_source') and not o.get('sky_removed')]
    if not kept:
        raise ValueError('No cleaned street mesh found')
    before_faces=sum(len(o.data.polygons) for o in kept)
    removed={}
    # Only this generated scene is loaded; no interaction with the user's open Blender.
    for obj in list(bpy.data.objects):
        if obj not in kept:
            removed[obj.type]=removed.get(obj.type,0)+1
            bpy.data.objects.remove(obj,do_unlink=True)
    for scene in bpy.data.scenes:
        scene.camera=None
    for obj in kept:
        obj.hide_set(False)
        obj.hide_render=False
    bpy.data.orphans_purge(do_recursive=True)
    bpy.context.scene.render.use_persistent_data=False
    target=folder/'street_clean.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(target),compress=True)
    # Read the saved file back to verify the actual deliverable.
    bpy.ops.wm.open_mainfile(filepath=str(target))
    assert all(o.type=='MESH' for o in bpy.data.objects)
    assert not bpy.data.cameras and not bpy.data.lights
    assert sum(len(o.data.polygons) for o in bpy.data.objects)==before_faces
    result={'removed_objects':removed,'objects':len(bpy.data.objects),'faces':before_faces,
            'cameras':len(bpy.data.cameras),'lights':len(bpy.data.lights),'file':str(target),
            'bytes':target.stat().st_size}
    (folder/'final_scene.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
    save_relight_scene(folder)


if __name__=='__main__':
    main(Path(sys.argv[sys.argv.index('--')+1]).resolve())
