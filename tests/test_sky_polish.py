import unittest
import numpy as np
from streetlab.sky_cleanup_polish import residual_mask, parse_audit
from streetlab.sky_cleanup import refine_open_boundary


class PolishTests(unittest.TestCase):
    def scene(self):
        a=np.empty((200,240,3),dtype=np.uint8);a[:]=[255,0,255]
        a[100:]=[120,110,90]
        a[75:100,80:120]=[80,70,60]  # real dormer silhouette
        return a

    def test_island_removed_and_dormer_preserved(self):
        a=self.scene();a[30:34,50:54]=[90,90,90]
        mask,_=residual_mask(a,[])
        self.assertTrue(mask[31,51])
        self.assertFalse(mask[80,100])
        self.assertFalse(mask[110,100])

    def test_wall_interior_semantic_polygon_rejected(self):
        a=self.scene()
        polygon=[[600,600],[800,600],[800,800],[600,800]]
        mask,_=residual_mask(a,[polygon])
        self.assertFalse(mask[140,170])

    def test_flat_polygon_normalized_without_changing_points(self):
        data=parse_audit('{"views":[{"id":"v","polygons":[[10,20,30,40,50,60]]}]}',['v'])
        self.assertEqual(data['views'][0]['polygons'],[[[10,20],[30,40],[50,60]]])

    def test_exposed_boundary_moves_but_interior_and_lower_vertices_stay(self):
        xyz=np.array([[0,0,2],[1,0,2],[1,1,2],[0,1,2],[.5,.5,2],[0,0,-1]],dtype=float)
        triangles=np.array([[0,1,4],[1,2,4],[2,3,4],[3,0,4]])
        result,info=refine_open_boundary(xyz,triangles,1,.05)
        np.testing.assert_array_equal(result[[4,5]],xyz[[4,5]])
        self.assertGreater(info['vertices'],0)
        self.assertLessEqual(info['maximum_displacement'],.0500001)


if __name__=='__main__':unittest.main()
