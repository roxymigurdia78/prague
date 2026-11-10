import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock
from types import SimpleNamespace

from streetlab.sky_cleanup_review import validate, validate_or_clip_border
from streetlab import prepare_world
from streetlab import sky_cleanup_review
from streetlab.paths import ROOT


class ReviewTests(unittest.TestCase):
    def test_proposal_must_cover_the_actual_native_loss_not_just_its_small_cell(self):
        import hashlib
        import numpy as np
        from PIL import Image
        folder=self.request_fixture()
        before=np.full((960,1280,3),180,dtype=np.uint8)
        after=before.copy();after[400,400]=[255,0,255]
        Image.fromarray(before).save(folder/'view_00.png');Image.fromarray(after).save(folder/'view_00_after.png')
        (folder/'views.json').write_text(json.dumps({'views':[{'id':'view_00'}]}),encoding='utf-8')
        digest=hashlib.sha256(b'test-model');digest.update(b'view_00')
        for picture in (before,after):digest.update(str((1280,960)).encode());digest.update(picture.tobytes())
        cache=folder/'protection_review_cache';cache.mkdir()
        proposal={'views':[{'id':'view_00','protected_polygons':[[[313,417],[314,417],[314,419],[313,419]]]}]}
        (cache/(digest.hexdigest()+'.json')).write_text(json.dumps(proposal),encoding='utf-8')
        with patch('streetlab.sky_semantics.ensure'),patch('streetlab.sky_semantics.allowed',side_effect=lambda folder,name,shape:np.ones(shape,dtype=bool)):
            self.assertEqual(sky_cleanup_review.review(folder,model='test-model'),0)

    def request_fixture(self):
        from PIL import Image
        parent=ROOT/'scratch/tests/review_loop';parent.mkdir(parents=True,exist_ok=True)
        temporary=tempfile.TemporaryDirectory(dir=parent);self.addCleanup(temporary.cleanup)
        folder=Path(temporary.name)
        for name in ('view_00.png','view_00_after.png'):
            Image.new('RGB',(32,32),(180,180,180)).save(folder/name)
        return folder

    def stream_client(self,replies):
        class Stream(list):
            def close(self):pass
        streams=[Stream([SimpleNamespace(choices=[SimpleNamespace(
            delta=SimpleNamespace(content=raw),finish_reason=finish)])]) for raw,finish in replies]
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=Mock(side_effect=streams))))

    def test_truncated_reply_is_archived_and_retried_without_inventing_its_tail(self):
        folder=self.request_fixture();target=folder/'response.json'
        complete={'views':[{'id':'view_00','protected_polygons':[]}]}
        client=self.stream_client([('{"views":[','length'),(json.dumps(complete),'stop')])
        data=sky_cleanup_review.request_review(client,'test-model',['view_00'],folder,folder,target)
        self.assertEqual(data,complete)
        self.assertEqual(client.chat.completions.create.call_count,2)
        self.assertEqual(json.loads(target.with_suffix('.txt').read_text()),complete)
        self.assertEqual(len(list(folder.glob('response.incomplete.*.txt'))),1)

    def test_completed_but_excessive_contour_is_reasked_instead_of_format_repair(self):
        folder=self.request_fixture();target=folder/'response.json'
        excessive={'views':[{'id':'view_00','protected_polygons':[[[10+i,20+i] for i in range(61)]]}]}
        complete={'views':[{'id':'view_00','protected_polygons':[]}]}
        client=self.stream_client([(json.dumps(excessive),'stop'),(json.dumps(complete),'stop')])
        with patch.object(sky_cleanup_review,'parse_or_repair_review') as repair:
            self.assertEqual(sky_cleanup_review.request_review(client,'test-model',['view_00'],folder,folder,target),complete)
            repair.assert_not_called()
        self.assertEqual(len(list(folder.glob('response.invalid.*.txt'))),1)

    def test_historical_unfinished_reply_is_replaced_with_fresh_visual_analysis(self):
        import hashlib
        import numpy as np
        folder=self.request_fixture()
        (folder/'views.json').write_text(json.dumps({'views':[{'id':'view_00'}]}),encoding='utf-8')
        digest=hashlib.sha256(b'test-model');digest.update(b'view_00')
        for _ in range(2):digest.update(str((32,32)).encode());digest.update(bytes([180])*(32*32*3))
        cache=folder/'protection_review_cache';cache.mkdir()
        target=cache/(digest.hexdigest()+'.json');target.with_suffix('.txt').write_text('{"views":[',encoding='utf-8')
        complete={'views':[{'id':'view_00','protected_polygons':[]}]}
        with patch('streetlab.sky_semantics.ensure'),patch('streetlab.sky_semantics.allowed',side_effect=lambda folder,name,shape:np.ones(shape,dtype=bool)), \
             patch.object(sky_cleanup_review,'hpc_settings',return_value=('fake','http://hpc.test/v1','test-model')), \
             patch('openai.OpenAI'),patch.object(sky_cleanup_review,'request_review',return_value=complete) as fetch:
            self.assertEqual(sky_cleanup_review.review(folder,model='test-model'),0)
            fetch.assert_called_once()
        self.assertEqual(json.loads(target.read_text()),complete)
        self.assertEqual(len(list(cache.glob('*.unvalidated.*.txt'))),1)

    def test_visible_loss_excludes_preexisting_empty_space(self):
        import numpy as np
        from PIL import Image
        parent=ROOT/'scratch/tests/review_loop';parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as temporary:
            folder=Path(temporary)
            before=np.full((32,32,3),180,dtype=np.uint8)
            before[2:8,2:8]=[255,0,255]
            after=before.copy();after[20:28,20:28]=[255,0,255]
            Image.fromarray(before).save(folder/'before.png')
            Image.fromarray(after).save(folder/'after.png')
            lost=sky_cleanup_review.newly_empty_pixels(folder/'before.png',folder/'after.png',(32,32))
            self.assertFalse(lost[4,4]);self.assertFalse(lost[12,12]);self.assertTrue(lost[24,24])

    def test_intact_wall_and_lost_sky_in_same_small_cell_are_not_a_wall_loss(self):
        import numpy as np
        from PIL import Image
        folder=self.request_fixture()
        before=np.full((4,4,3),180,dtype=np.uint8)
        after=before.copy();after[0,0]=[255,0,255]
        Image.fromarray(before).save(folder/'before.png');Image.fromarray(after).save(folder/'after.png')
        eligible=np.zeros((4,4),dtype=bool);eligible[1,1]=True
        ordinary=sky_cleanup_review.newly_empty_pixels(folder/'before.png',folder/'after.png',(2,2))
        self.assertTrue(ordinary[0,0])
        loss=sky_cleanup_review.newly_empty_pixels(folder/'before.png',folder/'after.png',(2,2),eligible=eligible)
        self.assertFalse(loss.any())

    def test_visible_loss_requires_matching_image_dimensions(self):
        from PIL import Image
        parent=ROOT/'scratch/tests/review_loop';parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as temporary:
            folder=Path(temporary)
            Image.new('RGB',(32,32)).save(folder/'before.png')
            Image.new('RGB',(16,16)).save(folder/'after.png')
            with self.assertRaises(ValueError):
                sky_cleanup_review.newly_empty_pixels(folder/'before.png',folder/'after.png',(32,32))

    def test_intact_wall_is_not_counted_as_new_repair(self):
        import hashlib
        import numpy as np
        from PIL import Image
        parent=ROOT/'scratch/tests/review_loop';parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as temporary:
            folder=Path(temporary)
            (folder/'views.json').write_text(json.dumps({'views':[{'id':'view_00'}]}),encoding='utf-8')
            rgb=np.full((32,32,3),180,dtype=np.uint8)
            for name in ('view_00.png','view_00_after.png'):
                Image.fromarray(rgb).save(folder/name)
            digest=hashlib.sha256(b'test-model');digest.update(b'view_00')
            for _ in range(2):digest.update(str((32,32)).encode());digest.update(rgb.tobytes())
            cache=folder/'protection_review_cache';cache.mkdir()
            (cache/(digest.hexdigest()+'.json')).write_text(json.dumps({'views':[{'id':'view_00',
                'protected_polygons':[[[100,100],[900,100],[900,900],[100,900]]]}]}),encoding='utf-8')
            with patch('streetlab.sky_semantics.ensure'),patch('streetlab.sky_semantics.allowed',side_effect=lambda folder,name,shape:np.ones(shape,dtype=bool)):
                self.assertEqual(sky_cleanup_review.review(folder,model='test-model'),0)
            self.assertFalse(json.loads((folder/'sky_protection_review.json').read_text())['views'][0]['protected_polygons'])

    def test_protection_requires_exact_views_and_bounded_polygons(self):
        data={'views':[{'id':'view_00','protected_polygons':[[[10,20],[100,20],[100,200]]]}]}
        self.assertEqual(validate(json.dumps(data),['view_00']),data)
        with self.assertRaises(ValueError):validate(json.dumps(data),['view_01'])
        data['views'][0]['protected_polygons'][0][0][0]=1001
        with self.assertRaises(ValueError):validate(json.dumps(data),['view_00'])

    def test_missing_protection_field_is_not_silently_accepted(self):
        with self.assertRaises(ValueError):validate('{"views":[{"id":"view_00"}]}',['view_00'])

    def test_minor_contour_overshoot_is_clipped_at_the_image_border(self):
        text=json.dumps({'views':[{'id':'v','protected_polygons':[[[300,900],[400,900],[400,1014],[300,1014]]]}]})
        result=validate_or_clip_border(text,['v'])
        polygon=result['views'][0]['protected_polygons'][0]
        self.assertEqual(max(p[1] for p in polygon),1000)
        self.assertEqual(min(p[1] for p in polygon),900)

    def test_flat_coordinate_list_is_paired_without_changing_the_shape(self):
        raw='{"views":[{"id":"v","protected_polygons":[[10,20,100,20,100,200]]}]}'
        result=validate_or_clip_border(raw,['v'])
        self.assertEqual(result['views'][0]['protected_polygons'],[[[10,20],[100,20],[100,200]]])

    def test_unrequested_empty_rows_do_not_hide_the_requested_views(self):
        raw=json.dumps({'views':[{'id':name,'protected_polygons':[]} for name in ('v0','v1','v2','v3')]})
        result=validate_or_clip_border(raw,['v2','v3'])
        self.assertEqual([v['id'] for v in result['views']],['v2','v3'])

    def test_review_loop_keeps_running_beyond_two_passes_until_no_new_protection(self):
        parent=ROOT/'scratch/tests/review_loop';parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as temporary:
            folder=Path(temporary)
            (folder/'street_without_sky.glb').write_bytes(b'fixture')
            (folder/'views.json').write_text(json.dumps({'views':[{'id':'view_00'}]}),encoding='utf-8')
            polished=folder/'finished'
            with patch.object(prepare_world.sky_cleanup,'main',return_value=folder) as clean, \
                    patch('streetlab.sky_cleanup_review.review',side_effect=[1,1,0,0]) as review, \
                    patch.object(prepare_world.sky_cleanup_polish,'main',return_value=polished) as polish, \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(prepare_world.main([str(folder/'source.glb')]),polished)
            self.assertEqual(clean.call_count,3)
            self.assertEqual(review.call_count,4)
            polish.assert_called_once()
            self.assertEqual(review.call_args.kwargs['after_folder'],polished)
            self.assertEqual(json.loads((folder/'preparation.json').read_text())['status'],'complete')


if __name__=='__main__':unittest.main()
