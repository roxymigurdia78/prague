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
from streetlab.sky_cleanup_polish_blender import semantic_artifact_faces


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

    def test_one_vlm_artifact_proposal_cannot_overrule_two_building_views(self):
        views=worker.RegionViews(self.views())
        for offset in (-.1,.1):
            camera=self.camera.copy();camera.data=self.camera.data.copy()
            self.scene.collection.objects.link(camera);camera.location.x+=offset
            bpy.context.view_layer.update()
            projection=np.asarray(camera.calc_matrix_camera(bpy.context.evaluated_depsgraph_get(),x=320,y=240)) @ np.asarray(camera.matrix_world.inverted())
            views.append((camera,projection,np.zeros((240,320),dtype=bool)))
        tree=BVHTree.FromPolygons(self.local.tolist(),self.triangles.tolist(),all_triangles=True)
        eligible=np.array([True,True])
        unsafe=worker.visible_sky_faces(self.obj,self.triangles,self.xyz,eligible,views,[(self.obj,tree)],require_consensus=False)
        self.assertTrue(unsafe[1])
        selected=semantic_artifact_faces(self.obj,self.triangles,self.xyz,eligible,views,[(self.obj,tree)])
        self.assertFalse(selected.any())

    def test_face_touching_a_protected_building_patch_is_preserved(self):
        # A narrow wall patch may cover only a corner of a triangle. Removing
        # that whole triangle reopens the same protected wall at every review.
        views=worker.RegionViews(self.views())
        protection=np.zeros((240,320),dtype=bool)
        point=np.append(self.xyz[3],1) @ self.projection.T
        ndc=point[:2]/point[3]
        x=int((ndc[0]+1)*.5*320);y=int((1-ndc[1])*.5*240)
        protection[y-2:y+3,x-2:x+3]=True
        views.protected={self.camera.name:protection}
        tree=BVHTree.FromPolygons(self.local.tolist(),self.triangles.tolist(),all_triangles=True)
        selected=worker.visible_sky_faces(self.obj,self.triangles,self.xyz,np.array([True,True]),
            views,[(self.obj,tree)],allow_surrounded=False)
        self.assertFalse(selected.any())

    def test_small_protected_patch_between_triangle_samples_is_preserved(self):
        views=worker.RegionViews(self.views())
        protection=np.zeros((240,320),dtype=bool)
        # A small wall region can lie inside a face without touching a vertex,
        # midpoint, or centroid; seven point samples must not erase it.
        world=.6*self.xyz[3]+.2*self.xyz[4]+.2*self.xyz[5]
        point=np.append(world,1) @ self.projection.T
        ndc=point[:2]/point[3]
        x=int((ndc[0]+1)*.5*320);y=int((1-ndc[1])*.5*240)
        protection[y-1:y+2,x-1:x+2]=True
        views.protected={self.camera.name:protection}
        tree=BVHTree.FromPolygons(self.local.tolist(),self.triangles.tolist(),all_triangles=True)
        selected=worker.visible_sky_faces(self.obj,self.triangles,self.xyz,np.array([True,True]),
            views,[(self.obj,tree)],allow_surrounded=False)
        self.assertFalse(selected.any())

    def test_visible_corner_is_preserved_when_the_face_center_is_occluded(self):
        mesh=bpy.data.meshes.new('partially occluded building')
        mesh.from_pydata([(-1,-1,3),(1,-1,3),(0,1,3),(-.3,-.5,2),(.3,-.5,2),(0,.2,2)],[],[(0,1,2),(3,4,5)])
        self.obj.data=mesh;bpy.context.view_layer.update()
        xyz=worker.coordinates(self.obj)
        local=np.asarray([v.co[:] for v in mesh.vertices])
        tree=BVHTree.FromPolygons(local.tolist(),self.triangles.tolist(),all_triangles=True)
        second=self.camera.copy();second.data=self.camera.data.copy()
        self.scene.collection.objects.link(second);second.location.x+=.8
        bpy.context.view_layer.update()
        projection=np.asarray(second.calc_matrix_camera(bpy.context.evaluated_depsgraph_get(),x=320,y=240)) @ np.asarray(second.matrix_world.inverted())
        views=worker.RegionViews([(self.camera,self.projection,np.zeros((240,320),dtype=bool)),
            (second,projection,np.ones((240,320),dtype=bool))])
        protected=np.zeros((240,320),dtype=bool)
        point=np.append(np.asarray((1.4,-.6,3)),1) @ self.projection.T
        ndc=point[:2]/point[3];x=int((ndc[0]+1)*.5*320);y=int((1-ndc[1])*.5*240)
        protected[y-1:y+2,x-1:x+2]=True
        views.protected={self.camera.name:protected,second.name:np.zeros_like(protected)}
        result=worker.visible_sky_faces(self.obj,self.triangles,xyz,np.array([True,False]),views,[(self.obj,tree)],allow_surrounded=False)
        self.assertIn(0,views.protected_faces[self.obj.name])
        self.assertFalse(result[0])

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
