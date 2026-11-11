import unittest

from streetlab.roads.explore_world import reachable_grid, validate_vlm
from streetlab.roads.walk_panorama import plan


class RoadTests(unittest.TestCase):
    def road(self):
        cells=[[0,0,0],[1,0,0],[2,0,0],[2,1,0],[2,-1,0],[9,9,0]]
        links=[[[0,0],[1,0]],[[1,0],[2,0]],[[2,0],[2,1]],[[2,0],[2,-1]]]
        return {'cells':cells,'links':links,'step':1,'eye':1.6}

    def test_only_connected_road_is_reachable(self):
        data=self.road()
        world,parents,depth=reachable_grid(data,[0,0,0],1)
        self.assertNotIn((9,9),parents)
        self.assertEqual(depth[(2,1)],3)

    def test_panorama_plan_stays_on_verified_links_and_respects_limit(self):
        data=self.road()
        world,parents,_=reachable_grid(data,[0,0,0],1)
        data['reachable']=[list(cell) for cell in parents]
        data['routes']=[[[0,0,0],[1,0,0],[2,0,0],[2,1,0]],
                        [[0,0,0],[1,0,0],[2,0,0],[2,-1,0]]]
        stations,routes=plan(data,3,1)
        self.assertLessEqual(len(stations),3)
        for station in stations:
            self.assertIn(tuple(station['cell']),parents)
            self.assertEqual(station['eye'][2],1.6)
        edges={frozenset(map(tuple,edge)) for edge in data['links']}
        for route in routes:
            for a,b in zip(route,route[1:]):
                self.assertIn(frozenset((tuple(a[:2]),tuple(b[:2]))),edges)

    def test_vlm_passages_require_matching_views_and_normalized_points(self):
        value='{"views":[{"id":"v00","passages":[{"point":[500,700],"state":"visible","description":"alley"}]}]}'
        self.assertEqual(validate_vlm(value,['v00'])['v00'][0]['point'],[500,700])
        with self.assertRaises(ValueError):validate_vlm(value,['other'])
        with self.assertRaises(ValueError):validate_vlm(value.replace('[500,700]','[500,1001]'),['v00'])


if __name__=='__main__':unittest.main()
