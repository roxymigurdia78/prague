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
        policy_patch=patch.dict(loop.DEFAULT_POLICY,{'require_clear':False,'max_rounds':12})
        policy_patch.start();self.addCleanup(policy_patch.stop)
        focus_patch=patch.object(loop,'focused_audit',return_value=None)
        focus_patch.start();self.addCleanup(focus_patch.stop)
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

    def test_small_isolated_fragments_are_detected_without_treating_roofs_as_scraps(self):
        rgb=self.rgb.copy();rgb[60:63,90:93]=[100,100,100]
        mask=loop.floating_residual_mask(rgb)
        self.assertTrue(mask[60:63,90:93].all())
        self.assertFalse(mask[139:].any())

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
        (output/'sky_review.blend').write_bytes(b'changed geometry '+str(output).encode())
        save(output/'views.json',read(source/'views.json'))
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

    def strict_run(self,measured,*,removed=100,policy=None):
        strict={'require_clear':True,'max_rounds':None}|(policy or {})
        with patch.object(loop,'audit',return_value=self.proposals) as audit, \
             patch.object(loop,'measure',side_effect=measured), \
             patch.object(loop,'review_fragments',side_effect=lambda folder,metrics,*a,**kw:metrics|{'fragment_review_complete':True}), \
             patch.object(loop,'apply_candidate',side_effect=lambda *a,**kw:self.apply(*a,**kw,removed=removed)) as apply, \
             patch.object(loop,'building_loss',return_value=0):
            result=loop.run(self.source,policy=strict)
        return result,audit,apply

    def test_strict_mode_keeps_cleaning_beyond_twelve_rounds_and_verifies_twice(self):
        calls=[0]
        def measured(*args,**kwargs):
            return self.metrics|{'thin_pixels':0,'proposal_pixels':0} if calls[0]>=14 else self.metrics
        def apply(*args,**kwargs):
            calls[0]+=1;self.apply(*args,**kwargs,removed=10)
        with patch.object(loop,'apply_candidate',side_effect=apply), \
             patch.object(loop,'audit',return_value=self.proposals) as audit, \
             patch.object(loop,'measure',side_effect=measured), \
             patch.object(loop,'review_fragments',side_effect=lambda f,m,*a,**kw:m|{'fragment_review_complete':True}), \
             patch.object(loop,'building_loss',return_value=0):
            result=loop.run(self.source,policy={'require_clear':True,'max_rounds':None})
        state=loop.summary(result)
        self.assertEqual(state['rounds'],14)
        self.assertEqual(state['clear_checks'],2)
        self.assertTrue(state['finished'])
        self.assertTrue(audit.call_args.kwargs['verification'])

    def test_final_detail_check_can_reopen_cleaning_instead_of_accepting_an_early_clear(self):
        clear=self.metrics|{'thin_pixels':0,'proposal_pixels':0}
        result,audit,apply=self.strict_run([clear,self.metrics,clear,clear,clear])
        self.assertEqual(apply.call_count,1)
        self.assertEqual(loop.summary(result)['clear_checks'],2)
        self.assertEqual(sum(call.kwargs.get('verification',False) for call in audit.call_args_list),2)

    def test_ambiguous_scraps_remain_unfinished_and_raise_instead_of_publishing(self):
        metrics=self.metrics|{'proposal_pixels':0,'uncertain_fragment_pixels':12}
        with self.assertRaises(loop.SkyCleanupIncomplete):
            self.strict_run([metrics,metrics],policy={'max_stalled_rechecks':1})
        state=loop.summary(self.source)
        self.assertFalse(state['finished'])
        self.assertTrue(state['needs_review'])
        self.assertEqual(state['remaining_uncertain_pixels'],12)

    def test_an_explicit_round_limit_does_not_become_success_in_strict_mode(self):
        with self.assertRaises(loop.SkyCleanupIncomplete):
            self.strict_run([self.metrics]*3,policy={'max_rounds':1})
        state=loop.summary(self.source)
        self.assertFalse(state['finished'])
        self.assertEqual(state['termination_reason'],'max_rounds')

    def test_persistent_relaunch_does_not_repeat_hpc_or_removal(self):
        clear=self.metrics|{'thin_pixels':0,'proposal_pixels':0}
        result,*_=self.execute_loop([self.metrics,clear,clear])
        with patch.object(loop,'audit',side_effect=AssertionError('No repeated HPC')):
            self.assertEqual(loop.run(self.source),result)
            self.assertEqual(loop.run(result),result)

    def test_explicit_recheck_uses_last_accepted_geometry_in_a_new_workspace(self):
        clear=self.metrics|{'thin_pixels':0,'proposal_pixels':0}
        result,state,*_=self.execute_loop([self.metrics,clear,clear])
        previous_workspace=Path(state['workspace'])
        previous_state=(previous_workspace/'state.json').read_bytes()
        geometry=(result/'sky_review.blend').read_bytes()
        guard=self.root/'existing-building-protection';guard.mkdir()
        save(guard/'sky_protection_review.json',{'views':[]})
        state['protection_folder']=str(guard)
        save(self.source/'sky_residual_status.json',state)
        with patch.object(loop,'audit',return_value=self.proposals) as audit, \
             patch.object(loop,'measure',return_value=clear), \
             patch.object(loop,'apply_candidate',side_effect=AssertionError('No cut needed')):
            same=loop.run(self.source,recheck=True)
        self.assertEqual(same,result)
        self.assertEqual(audit.call_args.args[0],result)
        current=loop.summary(self.source)
        self.assertNotEqual(current['workspace'],str(previous_workspace))
        self.assertEqual(current['starting_output'],str(result))
        self.assertEqual(current['protection_folder'],str(guard))
        self.assertEqual((previous_workspace/'state.json').read_bytes(),previous_state)
        self.assertEqual((result/'sky_review.blend').read_bytes(),geometry)

    def test_interrupted_recheck_reuses_its_pending_workspace(self):
        with patch.object(loop,'audit',side_effect=RuntimeError('interrupted')):
            with self.assertRaisesRegex(RuntimeError,'interrupted'):
                loop.run(self.source,recheck=True)
        pending=read(self.source/'residual_loop/recheck_active.json')['job']
        clear=self.metrics|{'thin_pixels':0,'proposal_pixels':0}
        with patch.object(loop,'audit',return_value=self.proposals),patch.object(loop,'measure',return_value=clear):
            result=loop.run(self.source,recheck=True)
        self.assertEqual(Path(loop.summary(result)['workspace']).name,pending)

    def test_changed_source_geometry_invalidates_the_finished_cache(self):
        clear=self.metrics|{'thin_pixels':0,'proposal_pixels':0}
        self.execute_loop([self.metrics,clear,clear])
        (self.source/'sky_review.blend').write_bytes(b'a different input scene')
        with patch.object(loop,'audit',return_value=self.proposals) as audit,patch.object(
                loop,'measure',return_value=clear):
            result=loop.run(self.source)
        audit.assert_called_once()
        self.assertEqual(result,self.source)

    def test_detected_lines_cannot_be_declared_clear_when_hpc_returns_no_polygon(self):
        result,state,*_=self.execute_loop([self.metrics|{'proposal_pixels':0}])
        self.assertEqual(result,self.source)
        self.assertEqual(state['termination_reason'],'unresolved_detection');self.assertTrue(state['needs_review'])

    def test_closeup_can_resolve_a_full_view_miss_before_the_loop_stops(self):
        missed=self.metrics|{'proposal_pixels':0}
        clear=self.metrics|{'thin_pixels':0,'proposal_pixels':0}
        with patch.object(loop,'focused_audit',return_value=self.proposals) as focus:
            result,state,audit,measure,apply,guard=self.execute_loop([missed,self.metrics,clear,clear])
        focus.assert_called_once()
        self.assertEqual(apply.call_count,1)
        self.assertEqual(state['termination_reason'],'clear')

    def test_small_fragments_alone_cannot_be_declared_clear(self):
        metrics=self.metrics|{'thin_pixels':0,'proposal_pixels':0,'fragment_pixels':9}
        result,state,*_=self.execute_loop([metrics])
        self.assertEqual(state['termination_reason'],'unresolved_detection')
        self.assertEqual(state['remaining_fragment_pixels'],9)

    def test_no_positive_masks_are_needed_when_all_residual_detectors_are_clear(self):
        mask=self.root/'empty-mask.png';Image.new('L',(240,200),0).save(mask)
        clear=self.metrics|{'thin_pixels':0,'proposal_pixels':0,'fragment_pixels':0,
                            'mask_files':{'v0':str(mask)},'supporting_views':0}
        result,state,*_=self.execute_loop([clear])
        self.assertEqual(state['termination_reason'],'clear')

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

    def test_small_face_changes_continue_until_actual_residuals_are_clear(self):
        clear=self.metrics|{'thin_pixels':0,'proposal_pixels':0}
        result,state,audit,measure,apply,guard=self.execute_loop(
            [self.metrics,self.metrics,self.metrics,self.metrics,clear,clear],removed=10)
        self.assertEqual(apply.call_count,3)
        self.assertEqual(state['termination_reason'],'clear');self.assertFalse(state['needs_review'])

    def test_small_changes_with_remaining_residuals_only_stop_at_the_explicit_budget(self):
        result,state,*_=self.execute_loop([self.metrics]*6,removed=10,policy={'max_rounds':3})
        self.assertEqual(state['rounds'],3)
        self.assertEqual(state['termination_reason'],'max_rounds');self.assertTrue(state['needs_review'])

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


class FocusedSkyTests(unittest.TestCase):
    def test_closeup_coordinates_return_to_full_frame_without_changing_original_openings(self):
        parent=ROOT/'scratch/tests/sky_residuals';parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            folder=Path(directory)
            rgb=np.full((400,600,3),(255,0,255),dtype=np.uint8)
            rgb[330:]=[100,70,40]
            rgb[100:104,180:420]=[100,100,100]
            Image.fromarray(rgb).save(folder/'v0_after.png')
            original={'views':[{'id':'v0','polygons':[]}]}
            reply={'views':[{'id':'v0','polygons':[[[0,0],[1000,0],[1000,1000],[0,1000]]]}]}
            with patch.object(loop,'audit',return_value=reply) as audit:
                result=loop.focused_audit(folder,original,{},folder/'work','model',None,0)
            self.assertEqual(original,{'views':[{'id':'v0','polygons':[]}]})
            box=read(folder/'work/focus_00/crop_boxes.json')[0]['box']
            polygon=result['views'][0]['focused_polygons'][0]
            self.assertEqual(polygon[0],[round(box[0]/600*1000),round(box[1]/400*1000)])
            self.assertEqual(polygon[2],[round(box[2]/600*1000),round(box[3]/400*1000)])
            with Image.open(audit.call_args.args[0]/'v0_after.png') as image:
                self.assertLess(image.width,600)
                self.assertLess(image.height,400)

    def test_empty_sky_without_candidates_does_not_request_closeups(self):
        parent=ROOT/'scratch/tests/sky_residuals';parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            folder=Path(directory)
            Image.new('RGB',(200,200),(255,0,255)).save(folder/'v0_after.png')
            with patch.object(loop,'audit',side_effect=AssertionError('No residual to inspect')):
                result=loop.focused_audit(folder,{'views':[{'id':'v0','polygons':[]}]},{},folder/'work','model',None,0)
            self.assertIsNone(result)


if __name__=='__main__':unittest.main()
