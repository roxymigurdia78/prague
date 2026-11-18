from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from streetlab import sky_fragment_review as review
from streetlab.cleanup_convergence import read,save
from streetlab.paths import ROOT


class FragmentReviewTests(unittest.TestCase):
    def setUp(self):
        parent=ROOT/'scratch/tests/fragment_review';parent.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=parent);self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)
        self.rgb=np.full((100,100,3),(255,0,255),dtype=np.uint8)
        self.rgb[80:]=[100,70,30];self.rgb[20:23,20:23]=0
        masks={};negative={}
        for name in ('v0','v1'):
            Image.fromarray(self.rgb).save(self.folder/(name+'_after.png'))
            positive=np.zeros((100,100),dtype=np.uint8);positive[:75]=255
            p=self.folder/(name+'_mask.png');Image.fromarray(positive).save(p);masks[name]=str(p)
            p=self.folder/(name+'_negative.png');Image.new('L',(100,100)).save(p);negative[name]=str(p)
        self.metrics={'mask_files':masks,'negative_mask_files':negative}

    def inspect(self,verdict):
        with patch.object(review,'confirm',return_value={'verdict':verdict,'reason':'test evidence'}) as confirm:
            result=review.review_fragments(self.folder,self.metrics,self.folder/'review','model',None,client=object())
        self.assertEqual(confirm.call_count,2)
        self.assertTrue(all(call.kwargs['context'] for call in confirm.call_args_list))
        return result

    def test_black_scraps_remain_candidates_and_are_precisely_selected(self):
        result=self.inspect('sky')
        self.assertEqual(result['fragment_pixels'],18)
        self.assertEqual(result['proposal_pixels'],18)
        self.assertEqual(result['uncertain_fragment_pixels'],0)
        with Image.open(result['artifact_masks']['v0']) as image:
            self.assertTrue((np.asarray(image)[20:23,20:23]>0).all())

    def test_recognized_real_structures_are_preserved_and_do_not_force_endless_cleaning(self):
        result=self.inspect('building')
        self.assertEqual(result['fragment_pixels'],0)
        self.assertEqual(result['proposal_pixels'],0)
        self.assertEqual(result['retained_structure_pixels'],18)
        with Image.open(self.metrics['negative_mask_files']['v0']) as image:
            self.assertTrue((np.asarray(image)[20:23,20:23]>0).all())

    def test_uncertain_scraps_are_neither_removed_nor_declared_absent(self):
        result=self.inspect('uncertain')
        self.assertEqual(result['uncertain_fragment_pixels'],18)
        self.assertEqual(result['fragment_pixels'],18)
        self.assertEqual(result['proposal_pixels'],0)

    def test_coarse_facade_proposals_are_reviewed_separately_from_tiny_black_scraps(self):
        rgb=self.rgb.copy();rgb[35:60,35:65]=[120,90,50]
        for view in ('v0','v1'):Image.fromarray(rgb).save(self.folder/(view+'_after.png'))
        verdicts=[{'verdict':v} for v in ('sky','building','sky','building')]
        with patch.object(review,'confirm',side_effect=verdicts) as inspect:
            result=review.review_fragments(self.folder,self.metrics,self.folder/'review','model',None,client=object())
        self.assertEqual(inspect.call_count,4)
        self.assertEqual(result['proposal_pixels'],18)
        self.assertEqual(result['retained_structure_pixels'],2*25*30)

    def test_old_protection_releases_only_fully_verified_scraps_and_keeps_roof_regions(self):
        result=self.inspect('sky')
        protection=self.folder/'old_guard';protection.mkdir()
        scrap=[[190,190],[240,190],[240,240],[190,240]]
        roof=[[100,820],[400,820],[400,950],[100,950]]
        save(protection/'sky_protection_review.json',{'views':[{'id':'v0','protected_polygons':[scrap,roof]}]})
        before=(protection/'sky_protection_review.json').read_bytes()
        updated=review.refine_protection(self.folder,protection,result,self.folder/'new_guard')
        self.assertEqual(read(Path(updated)/'sky_protection_review.json')['views'][0]['protected_polygons'],[roof])
        self.assertEqual((protection/'sky_protection_review.json').read_bytes(),before)

    def test_no_masks_cannot_masquerade_as_a_successful_fragment_inspection(self):
        with self.assertRaises(ValueError):
            review.review_fragments(self.folder,{},self.folder/'review','model',None,client=object())


if __name__=='__main__':unittest.main()
