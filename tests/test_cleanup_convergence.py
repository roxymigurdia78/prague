"""Stopping is based on measured change or a persistent budget, never endless retries."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from streetlab import cleanup_convergence as convergence,prepare_world
from streetlab.paths import ROOT


class ConvergenceTests(unittest.TestCase):
    def setUp(self):
        parent=ROOT/'scratch/tests/convergence';parent.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=parent);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.folder=self.root/'cleaned';self.folder.mkdir()
        self.source=self.root/'source.glb'
        self.policy=dict(convergence.DEFAULTS)

    def progress(self):return convergence.Progress(self.folder,self.source,self.policy)

    def observe(self,progress,*,pixels=5,total=1000000):
        convergence.save(self.folder/'review_metrics.json',{'new_regions':1,
            'new_protected_pixels':pixels,'reviewed_pixels':total,'new_protected_fraction':pixels/total,
            'after_folder':str(self.folder.resolve())})
        progress.record_review('base',1,self.folder)

    def snapshot(self,name,count):return {'sha256':name,'kept_faces':count,'total_faces':1000000}

    def test_two_small_repairs_converge_without_requiring_zero_proposals(self):
        progress=self.progress()
        self.observe(progress)
        self.assertIsNone(progress.record_rebuild(self.snapshot('a',10000),self.snapshot('b',10001)))
        self.observe(progress)
        self.assertEqual(progress.record_rebuild(self.snapshot('b',10001),self.snapshot('c',10002)),'converged')

    def test_a_large_repair_resets_the_small_change_streak(self):
        progress=self.progress();self.observe(progress)
        progress.record_rebuild(self.snapshot('a',10000),self.snapshot('b',10001))
        self.observe(progress,pixels=50000)
        self.assertIsNone(progress.record_rebuild(self.snapshot('b',10001),self.snapshot('c',12000)))
        self.assertEqual(progress.data['stable_count'],0)

    def test_unchanged_geometry_has_its_own_stopping_reason(self):
        progress=self.progress()
        for expected in (None,'unchanged_geometry'):
            self.observe(progress,pixels=50000)
            self.assertEqual(progress.record_rebuild(self.snapshot('a',10000),self.snapshot('a',10000)),expected)
        self.assertEqual(progress.data['stable_count'],0)

    def test_review_budget_survives_relaunch(self):
        progress=self.progress()
        for _ in range(self.policy['max_reviews']):self.observe(progress)
        reopened=self.progress()
        self.assertTrue(reopened.exhausted)
        self.assertEqual(reopened.count,6)

    def run_prepare(self,review,*,legacy=0,policy=None):
        if legacy:
            self.source=self.root/'old_build/world/source.glb'
            self.source.parent.mkdir(parents=True)
            (self.source.parent.parent/'build_connected.log').write_text(
                f'HPCによる清掃確認ループ {legacy}\n',encoding='utf-8')
        calls=[];finalized=[]
        def clean(_args):
            calls.append(True)
            (self.folder/'street_without_sky.glb').write_bytes(('geometry'+str(len(calls))).encode())
            convergence.save(self.folder/'report.json',{'kept_faces':10000+len(calls),'total_faces':1000000})
            return self.folder
        def finish(folder):finalized.append((folder,(folder/'street_without_sky.glb').read_bytes()))
        with patch.object(prepare_world.sky_cleanup,'main',side_effect=clean), \
             patch('streetlab.sky_cleanup_review.review',side_effect=review) as inspection, \
             patch.object(prepare_world.sky_cleanup_polish,'main') as polish, \
             patch.object(prepare_world.sky_cleanup_polish,'save_clean_scene',side_effect=finish), \
             patch.object(prepare_world,'load_policy',return_value=policy or self.policy),redirect_stdout(io.StringIO()):
            with patch.object(prepare_world.sky_residual_loop,'run',side_effect=lambda folder,**_kwargs:folder):
                result=prepare_world.main([str(self.source)])
        return result,inspection,polish,calls,finalized

    def test_budget_exit_applies_last_repairs_and_preserves_the_base(self):
        policy=self.policy|{'max_reviews':2}
        result,review,polish,calls,finalized=self.run_prepare(lambda *_args,**_kw:1,policy=policy)
        self.assertEqual(result,self.folder);self.assertEqual(review.call_count,2)
        self.assertEqual(len(calls),3);self.assertEqual(finalized[0][1],b'geometry3')
        polish.assert_not_called()
        state=convergence.read(self.folder/'preparation.json')
        self.assertEqual(state['termination_reason'],'max_reviews')
        self.assertIsNone(state['final_review_added_regions'])
        self.assertEqual(state['output_mode'],'bounded_base')

    def test_existing_long_run_does_not_receive_a_fresh_review_budget(self):
        def forbidden(*_args,**_kwargs):raise AssertionError('No more HPC reviews after the inherited cap')
        result,review,polish,calls,finalized=self.run_prepare(forbidden,legacy=12)
        review.assert_not_called();polish.assert_not_called()
        self.assertEqual(len(calls),1);self.assertEqual(result,self.folder)
        state=convergence.read(self.folder/'preparation.json')
        self.assertEqual(state['reviews_completed'],12)
        self.assertEqual(state['termination_reason'],'max_reviews')

    def test_measured_convergence_finalizes_after_applying_the_second_small_repair(self):
        def review(*_args,**_kw):
            convergence.save(self.folder/'review_metrics.json',{'new_regions':1,'new_protected_pixels':5,
                'reviewed_pixels':1000000,'new_protected_fraction':.000005,
                'after_folder':str(self.folder.resolve())})
            return 1
        result,inspection,polish,calls,finalized=self.run_prepare(review)
        self.assertEqual(inspection.call_count,2);self.assertEqual(len(calls),3)
        self.assertEqual(finalized[0][1],b'geometry3');polish.assert_not_called()
        self.assertEqual(convergence.read(self.folder/'preparation.json')['termination_reason'],'converged')


if __name__=='__main__':unittest.main()
