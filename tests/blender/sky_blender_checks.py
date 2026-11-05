"""Run with Blender --background --python tests/blender/sky_blender_checks.py."""
import sys
from pathlib import Path
import unittest

import bpy
import numpy as np
from mathutils import Vector,Matrix
from mathutils.bvhtree import BVHTree

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from streetlab import sky_cleanup_blender as worker


class BlenderSkyChecks(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        worker._TRANSFORMS.clear()
        self.scene=bpy.context.scene
        self.scene.render.resolution_x=320
        self.scene.render.resolution_y=240
        self.scene.render.resolution_percentage=100
        mesh=bpy.data.meshes.new('occluded sky')
        mesh.from_pydata([(-.5,-.5,3),(.5,-.5,3),(0,.5,3),
                          (-.6,-.6,2),(.6,-.6,2),(0,.6,2)],[],[(0,1,2),(3,4,5)])
        self.obj=bpy.data.objects.new('test source',mesh)
        self.scene.collection.objects.link(self.obj)
        self.obj.matrix_world=Matrix.Translation((2,0,0))
        camera=bpy.data.objects.new('test camera',bpy.data.cameras.new('test camera'))
        self.scene.collection.objects.link(camera)
        camera.location=(2,0,0)
        camera.rotation_euler=Vector((0,0,1)).to_track_quat('-Z','Y').to_euler()
        camera.data.lens=18
        self.scene.camera=camera
        bpy.context.view_layer.update()
        self.camera=camera
        self.xyz=worker.coordinates(self.obj)
        self.triangles=np.array([[0,1,2],[3,4,5]])
        self.local=np.array([v.co[:] for v in mesh.vertices])
        self.projection=np.asarray(camera.calc_matrix_camera(bpy.context.evaluated_depsgraph_get(),x=320,y=240)) @ np.asarray(camera.matrix_world.inverted())

    def views(self,sky=True):
        return [(self.camera,self.projection,np.full((240,320),sky,dtype=bool))]

    def test_visible_layer_then_hidden_layer_are_selected_in_order(self):
        tree=BVHTree.FromPolygons(self.local.tolist(),self.triangles.tolist(),all_triangles=True)
        first=worker.visible_sky_faces(self.obj,self.triangles,self.xyz,np.array([True,True]),
                                     self.views(),[(self.obj,tree)],allow_surrounded=False)
        self.assertEqual(first.tolist(),[False,True])
        remaining=np.array([0])
        next_tree=BVHTree.FromPolygons(self.local.tolist(),self.triangles[remaining].tolist(),all_triangles=True)
        second=worker.visible_sky_faces(self.obj,self.triangles,self.xyz,np.array([True,False]),
                                      self.views(),[(self.obj,next_tree,remaining)])
        self.assertEqual(second.tolist(),[True,False])

    def test_building_region_is_not_selected(self):
        tree=BVHTree.FromPolygons(self.local.tolist(),self.triangles.tolist(),all_triangles=True)
        selected=worker.visible_sky_faces(self.obj,self.triangles,self.xyz,np.array([True,True]),
                                        self.views(False),[(self.obj,tree)])
        self.assertFalse(selected.any())

    def test_deeper_building_behind_artifact_is_preserved(self):
        original=BVHTree.FromPolygons(self.local.tolist(),self.triangles.tolist(),all_triangles=True)
        remaining=np.array([0])
        revealed=BVHTree.FromPolygons(self.local.tolist(),self.triangles[remaining].tolist(),all_triangles=True)
        selected=worker.visible_sky_faces(self.obj,self.triangles,self.xyz,np.array([True,False]),
            self.views(),[(self.obj,revealed,remaining)],require_consensus=False,
            reference_trees=[(self.obj,original)],maximum_depth={'default':.3,'test camera':.2})
        self.assertFalse(selected.any())


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(BlenderSkyChecks))
    if not result.wasSuccessful():
        raise RuntimeError('Blender sky checks failed')
