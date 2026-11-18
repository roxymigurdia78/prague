import json
import unittest
from streetlab.scene_inventory import parse_inventory, merge_inventory

class InventoryTests(unittest.TestCase):
    def candidate(self,**kw):
        c={'kind':'window','appearance':'dark','confidence':.95,'position':[0,0,1],
           'normal':[0,1,0],'width':1,'height':1.5,'camera_position':[0,2,1]}
        c.update(kw);return c

    def test_dark_and_reflective_windows_both_retained(self):
        groups=merge_inventory([self.candidate(),self.candidate(position=[2,0,1],appearance='reflective')],1)
        self.assertEqual(len(groups),2)
        self.assertTrue(all(g['role']=='reflective_surface_candidate' and not g['lighting_assigned'] for g in groups))

    def test_other_views_merge_but_adjacent_windows_and_fixture_stay_separate(self):
        a=self.candidate();b=self.candidate(position=[.1,0,1],camera_position=[1,2,1])
        groups=merge_inventory([a,b,self.candidate(position=[1.2,0,1]),self.candidate(kind='lantern')],1)
        self.assertEqual(len(groups),3)
        self.assertEqual(groups[0]['support_positions'],2)

    def test_parser_needs_no_emission_field(self):
        obj={'kind':'window','appearance':'dark','center':[500,500],'bbox':[400,400,600,600],'confidence':.9}
        data={'views':[{'id':'x','objects':[obj]}]}
        self.assertEqual(parse_inventory(json.dumps(data),['x']),data)
        obj['confidence']=float('nan')
        parsed=parse_inventory(json.dumps(data),['x'])
        self.assertEqual(parsed['views'][0]['objects'],[])
        self.assertEqual(parsed['views'][0]['dropped_invalid_objects'],1)

    def test_malformed_object_is_dropped_and_bare_list_is_accepted(self):
        good={'kind':'window','appearance':'dark','center':[500,500],'bbox':[400,400,600,600],'confidence':.9}
        bad={'kind':[211,660],'bbox':[190,620,230,700],'confidence':.95,'appearance':'reflective'}
        parsed=parse_inventory(json.dumps([{'id':'x','objects':[good,bad,'junk']}]),['x'])
        self.assertEqual(parsed['views'][0]['objects'],[good])
        self.assertEqual(parsed['views'][0]['dropped_invalid_objects'],2)
        with self.assertRaises(ValueError):parse_inventory(json.dumps({'views':[{'id':'y','objects':[]}]}),['x'])

    def test_more_than_35_objects_keeps_most_confident(self):
        objs=[{'kind':'window','appearance':'dark','center':[500,500],'bbox':[400,400,600,600],'confidence':round(.5+i/100,2)} for i in range(40)]
        parsed=parse_inventory(json.dumps({'views':[{'id':'x','objects':objs}]}),['x'])
        v=parsed['views'][0]
        self.assertEqual(len(v['objects']),35);self.assertEqual(v['truncated_objects'],5)
        self.assertEqual(min(o['confidence'] for o in v['objects']),.55)

    def test_close_adjacent_windows_need_region_overlap(self):
        def region(x):
            return self.candidate(position=[x,0,1],width=.05,height=.1,
                corners=[[x-.025,0,1.05],[x+.025,0,1.05],[x+.025,0,.95],[x-.025,0,.95]])
        groups=merge_inventory([region(0),region(.1),region(.01)],1)
        self.assertEqual(len(groups),2)

    def test_inconsistent_center_is_recorded_and_confidence_not_invented_as_high(self):
        obj={'kind':'window','appearance':'reflective','center':[50,50],'bbox':[400,400,600,600]}
        data=parse_inventory(json.dumps({'views':[{'id':'x','objects':[obj]}]}),['x'])
        result=data['views'][0]['objects'][0]
        self.assertEqual(result['center'],[500,500])
        self.assertEqual(result['reported_center'],[50,50])
        self.assertEqual(result['confidence'],.7)
        self.assertIn('not_model_score',result['confidence_source'])

if __name__=='__main__':unittest.main()
