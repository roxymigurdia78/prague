import json
import unittest

from streetlab.light_placement import detections, audit, merge_candidates, infer_sun


class LightPlacementTests(unittest.TestCase):
    def test_sun_requires_independent_consistent_views(self):
        a={'confidence':.99,'direction':[0,.8,.6],'camera_position':[0,0,0]}
        self.assertIsNone(infer_sun([a,a]))
        b={**a,'camera_position':[1,0,0]}
        self.assertAlmostEqual(infer_sun([a,b])[0],90)
        self.assertIsNone(infer_sun([a,{**b,'direction':[.8,0,.6]}]))
    def sample(self, **changes):
        value={'kind':'lantern','emits':True,'confidence':.85,'position':[0,0,1],
               'normal':[0,1,0],'camera_position':[0,2,1]}
        value.update(changes)
        return value

    def test_same_station_does_not_create_multiview_confirmation(self):
        lights=merge_candidates([self.sample(),self.sample(position=[.03,0,1])],.1)
        self.assertEqual(len(lights),1)
        self.assertFalse(lights[0]['enabled'])
        lights=merge_candidates([self.sample(),self.sample(camera_position=[1,2,1])],.1)
        self.assertTrue(lights[0]['enabled'])

    def test_reflection_and_opposite_walls_not_merged_as_emitters(self):
        lights=merge_candidates([self.sample(kind='window'),self.sample(kind='window',normal=[0,-1,0]),self.sample(emits=False)],.1)
        self.assertEqual(len(lights),2)

    def test_same_fixture_with_two_names_is_one_light(self):
        lights=merge_candidates([self.sample(),self.sample(kind='streetlight',camera_position=[1,2,1])],.1)
        self.assertEqual(len(lights),1)
        self.assertTrue(lights[0]['enabled'])

    def test_rejects_bad_coordinates_and_unknown_audit_ids(self):
        obj={'kind':'window','emits':True,'confidence':.9,'center':[800,500],'bbox':[100,100,200,200]}
        with self.assertRaises(ValueError):
            detections(json.dumps({'views':[{'id':'a','objects':[obj]}]}),['a'])
        with self.assertRaises(ValueError):
            audit('{"adjustments":[{"id":"alien","enabled":true,"factor":1}],"issues":[]}',{'L000'})
        with self.assertRaises(ValueError):
            audit('{"adjustments":[{"id":"L000","enabled":true,"factor":NaN}],"issues":[]}',{'L000'})


if __name__=='__main__':unittest.main()
