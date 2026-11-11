import tempfile
from pathlib import Path
import unittest

import numpy as np
from PIL import Image

from streetlab.sky_opening import refine_opening,architecture_evidence
from streetlab.sky_residual_loop import measure
from streetlab.paths import ROOT


class SkyOpeningTests(unittest.TestCase):
    def scene(self):
        rgb=np.full((180,240,3),(115,82,52),dtype=np.uint8)
        rgb[:,65:175]=[160,162,151]
        rgb[70:100,65:85]=[115,82,52] # a real projecting eave
        return rgb

    def polygon(self):
        # Deliberately crosses the facade on the left and misses sky on the right.
        return [[[200,0],[650,0],[650,1000],[200,1000]]]

    def test_gray_sky_is_found_without_existing_magenta_holes(self):
        rgb=self.scene();mask=refine_opening(rgb,self.polygon(),rgb)
        self.assertGreater(mask[:,95:160].mean(),.9)
        self.assertFalse(mask[:,:60].any());self.assertFalse(mask[:,180:].any())
        self.assertFalse(mask[72:98,65:83].any())

    def test_known_empty_hole_does_not_turn_facades_into_sky(self):
        original=self.scene();rgb=original.copy();rgb[30:60,105:135]=[255,0,255]
        mask=refine_opening(rgb,self.polygon(),original)
        self.assertTrue(mask[40,110]);self.assertFalse(mask[:,:60].any())

    def test_small_floaters_inside_opening_are_included(self):
        rgb=self.scene();rgb[30:33,110:114]=[0,0,0]
        self.assertTrue(refine_opening(rgb,self.polygon(),rgb)[31,111])

    def test_no_semantic_opening_never_selects_geometry(self):
        self.assertFalse(refine_opening(self.scene(),[]).any())

    def test_gray_sky_missed_by_one_view_is_unknown_not_a_building_vote(self):
        rgb=np.full((80,100,3),(150,152,145),dtype=np.uint8)
        rgb[20:60,70:90]=[110,60,25]
        sky=np.zeros((80,100),dtype=bool);sky[:,10:50]=True
        negative=architecture_evidence(rgb,sky,sky_color=(150,152,145))
        self.assertFalse(negative[:,50:65].any())
        self.assertTrue(negative[30:50,75:85].all())

    def test_large_gray_cap_is_measured_instead_of_rejected_for_missing_magenta(self):
        parent=ROOT/'scratch/tests/sky_opening';parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as temp:
            folder=Path(temp);rgb=self.scene()
            Image.fromarray(rgb).save(folder/'v_after.png');Image.fromarray(rgb).save(folder/'v.png')
            result=measure(folder,{'views':[{'id':'v','polygons':self.polygon()}]},
                reference_folder=folder,masks_folder=folder/'masks')
            self.assertGreater(result['proposal_pixels'],10000)
            self.assertFalse(result['excessive'])
            self.assertTrue(Path(result['mask_files']['v']).exists())


if __name__=='__main__':unittest.main()
