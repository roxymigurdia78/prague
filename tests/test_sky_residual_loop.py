import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from streetlab import sky_residual_loop as loop
from streetlab.cleanup_convergence import read,save
from streetlab.paths import ROOT


class SkyResidualTests(unittest.TestCase):
    def setUp(self):
        parent=ROOT/'scratch/tests/sky_residuals';parent.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=parent);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'source';self.source.mkdir()
        root_patch=patch.object(loop,'ROOT',self.root);root_patch.start();self.addCleanup(root_patch.stop)
        (self.source/'sky_review.blend').write_bytes(b'immutable source')
        save(self.source/'views.json',{'views':[{'id':'v0'},{'id':'v1'}]})
        self.rgb=np.full((200,240,3),(255,0,255),dtype=np.uint8)
        self.rgb[140:]=[100,100,100]
        self.rgb[35:38,40:200]=[110,100,90]
        for name in ('v0','v1'):Image.fromarray(self.rgb).save(self.source/(name+'_after.png'))
        self.proposals={'views':[{'id':name,'polygons':[[[100,100],[900,100],[900,350],[100,350]]]}
                                 for name in ('v0','v1')]}
        self.metrics={'thin_pixels':960,'proposal_pixels':960,
                      'polygons':{v['id']:v['polygons'] for v in self.proposals['views']},'excessive':False}

    def test_thin_band_surrounded_by_sky_is_detected_but_roof_edge_is_not(self):
        mask=loop.thin_residual_mask(self.rgb)
        self.assertGreater(int(mask[35:38].sum()),300)
        self.assertFalse(mask[139:145].any())

    def test_malformed_audit_is_retried_with_feedback_then_cached(self):
        class Stream:
            def __init__(self,text):self.text=text
            def __iter__(self):
                yield SimpleNamespace(choices=[SimpleNamespace(
                    delta=SimpleNamespace(content=self.text),finish_reason='stop')])
            def close(self):pass
        from unittest.mock import MagicMock
        client=MagicMock()
        client.chat.completions.create.side_effect=[Stream('{'),Stream('{'),Stream(json.dumps(self.proposals))]
        with patch('openai.OpenAI',return_value=client),patch.object(loop,'hpc_settings',return_value=('test','https://example.invalid','test')):
            result=loop.audit(self.source,self.root/'cache')
            self.assertEqual(result,self.proposals)
            self.assertEqual(client.chat.completions.create.call_count,3)
            self.assertIn('failed validation',client.chat.completions.create.call_args.kwargs['messages'][0]['content'])
            self.assertEqual(loop.audit(self.source,self.root/'cache'),result)
            self.assertEqual(client.chat.completions.create.call_count,3)

    def test_building_interior_and_empty_sky_polygons_are_not_removal_candidates(self):
        proposals={'views':[{'id':'v0','polygons':[
            [[0,750],[1000,750],[1000,1000],[0,1000]],
            [[0,400],[1000,400],[1000,600],[0,600]]]}]}
        measured=loop.measure(self.source,proposals)
        self.assertEqual(measured['proposal_pixels'],0)
        self.assertGreater(measured['thin_pixels'],0)

    def apply(self,source,output,polygons,guard=None,*,removed=100):
        output.mkdir(exist_ok=True)
        save(output/'report.json',{'kept_faces':100000-removed,'details':[{'removed_faces':removed}]})

    def execute_loop(self,metrics,*,removed=100,loss=0,policy=None):
        with patch.object(loop,'audit',return_value=self.proposals) as audit, \
             patch.object(loop,'measure',side_effect=metrics) as measure, \
             patch.object(loop,'apply_candidate',side_effect=lambda *a,**kw:self.apply(*a,**kw,removed=removed)) as apply, \
             patch.object(loop,'building_loss',return_value=loss) as guard:
            result=loop.run(self.source,policy=policy)
        return result,loop.summary(result) or loop.summary(self.source),audit,measure,apply,guard

    def test_clear_requires_audit_after_the_actual_removal(self):
        clear=self.metrics|{'thin_pixels':0,'proposal_pixels':0}
        result,state,audit,measure,apply,guard=self.execute_loop([self.metrics,clear,clear])
        self.assertEqual(audit.call_count,2);self.assertEqual(apply.call_count,1)
        self.assertNotEqual(result,self.source)
        self.assertEqual(state['termination_reason'],'clear');self.assertFalse(state['needs_review'])
        self.assertEqual((self.source/'sky_review.blend').read_bytes(),b'immutable source')

    def test_persistent_relaunch_does_not_repeat_hpc_or_removal(self):
        clear=self.metrics|{'thin_pixels':0,'proposal_pixels':0}
        result,*_=self.execute_loop([self.metrics,clear,clear])
        with patch.object(loop,'audit',side_effect=AssertionError('No repeated HPC')):
            self.assertEqual(loop.run(self.source),result)
            self.assertEqual(loop.run(result),result)

    def test_detected_lines_cannot_be_declared_clear_when_hpc_returns_no_polygon(self):
        result,state,*_=self.execute_loop([self.metrics|{'proposal_pixels':0}])
        self.assertEqual(result,self.source)
        self.assertEqual(state['termination_reason'],'unresolved_detection');self.assertTrue(state['needs_review'])

    def test_building_loss_rolls_back_entire_candidate(self):
        def guarded(*args):
            guard=args[2];guard.mkdir(parents=True,exist_ok=True)
            save(guard/'sky_protection_review.json',{'views':[{'id':'v0',
                'protected_polygons':[[[10,10],[20,10],[20,20],[10,20]]]}]})
            return 20
        with patch.object(loop,'audit',return_value=self.proposals), \
             patch.object(loop,'measure',return_value=self.metrics), \
             patch.object(loop,'apply_candidate',side_effect=self.apply), \
             patch.object(loop,'building_loss',side_effect=guarded):
            result=loop.run(self.source,policy={'max_rounds':1})
        state=loop.summary(self.source)
        self.assertEqual(result,self.source)
        self.assertEqual(state['termination_reason'],'building_guard')
        self.assertFalse(state['history'][0]['accepted'])

    def test_rejected_building_patch_is_protected_before_the_next_attempt(self):
        guards=[]
        def guarded(before,after,guard,*_args):
            guard.mkdir(parents=True,exist_ok=True)
            save(guard/'sky_protection_review.json',{'views':[{'id':'v0',
                'protected_polygons':[[[10,10],[20,10],[20,20],[10,20]]]}]})
            return 20 if len(guards)==1 else 0
        def apply(source,output,polygons,guard=None):
            guards.append(guard);self.apply(source,output,polygons,guard)
        clear=self.metrics|{'thin_pixels':0,'proposal_pixels':0}
        with patch.object(loop,'audit',return_value=self.proposals), \
             patch.object(loop,'measure',side_effect=[self.metrics,self.metrics,clear,clear]), \
             patch.object(loop,'apply_candidate',side_effect=apply), \
             patch.object(loop,'building_loss',side_effect=guarded):
            result=loop.run(self.source)
        self.assertIsNone(guards[0]);self.assertTrue(Path(guards[1]).is_dir())
        self.assertFalse(loop.summary(result)['history'][0]['accepted'])
        self.assertTrue(loop.summary(result)['history'][1]['accepted'])
        self.assertEqual(loop.summary(result)['termination_reason'],'clear')

    def test_unchanged_geometry_stops_after_two_attempts_with_explicit_remaining_lines(self):
        result,state,audit,measure,apply,guard=self.execute_loop([self.metrics]*4,removed=0)
        self.assertEqual(apply.call_count,2);guard.assert_not_called()
        self.assertEqual(state['termination_reason'],'stalled');self.assertTrue(state['needs_review'])

    def test_maximum_rounds_still_runs_final_residual_audit(self):
        result,state,audit,measure,apply,guard=self.execute_loop([self.metrics]*3,
            policy={'max_rounds':1})
        self.assertEqual(apply.call_count,1);self.assertEqual(audit.call_count,2)
        self.assertEqual(state['termination_reason'],'max_rounds');self.assertTrue(state['needs_review'])

    def test_two_small_changes_do_not_masquerade_as_zero_residuals(self):
        result,state,*_=self.execute_loop([self.metrics]*4,removed=10)
        self.assertEqual(state['termination_reason'],'small_change');self.assertTrue(state['needs_review'])

    def test_verified_local_restoration_keeps_other_safe_sky_deletions(self):
        clear=self.metrics|{'thin_pixels':0,'proposal_pixels':0}
        def repair(source,candidate,guard):
            guard.mkdir(exist_ok=True)
            save(guard/'sky_protection_review.json',{'views':[{'id':'v0','protected_polygons':[]}]})
            output=candidate.with_name(candidate.name+'_protected')
            self.apply(source,output,{},removed=90)
            return output
        with patch.object(loop,'protected_repair',side_effect=repair):
            result,state,audit,measure,apply,guard=self.execute_loop([self.metrics,clear,clear],loss=20)
        self.assertEqual(apply.call_count,1);self.assertEqual(guard.call_count,1)
        self.assertEqual(state['history'][0]['initial_building_loss_pixels'],20)
        self.assertEqual(state['history'][0]['building_loss_pixels'],0)
        self.assertTrue(state['history'][0]['accepted']);self.assertEqual(state['history'][0]['removed_faces'],90)

    def test_fresh_semantic_region_is_tried_instead_of_using_old_stagnation(self):
        clear=self.metrics|{'thin_pixels':0,'proposal_pixels':0}
        attempts=[]
        def apply(*args,**kwargs):
            attempts.append(True);self.apply(*args,**kwargs,removed=100 if len(attempts)==3 else 0)
        with patch.object(loop,'audit',return_value=self.proposals) as audit, \
             patch.object(loop,'measure',side_effect=[self.metrics,self.metrics,self.metrics,self.metrics,clear,clear]), \
             patch.object(loop,'selection_signature',side_effect=['a','a','b','c']), \
             patch.object(loop,'apply_candidate',side_effect=apply), \
             patch.object(loop,'building_loss',return_value=0):
            result=loop.run(self.source)
        self.assertEqual(len(attempts),3)
        self.assertEqual(loop.summary(result)['termination_reason'],'clear')


if __name__=='__main__':unittest.main()
