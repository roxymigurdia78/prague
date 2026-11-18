import tempfile
from pathlib import Path
import unittest

import numpy as np
from PIL import Image

from streetlab.sky_semantics import allowed,confirmed_sky


class SemanticGuardTests(unittest.TestCase):
    def test_uncertain_boundary_is_the_same_physical_region_at_each_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);masks=folder/'semantic_guard/masks/output/v00';masks.mkdir(parents=True)
            sky=np.zeros((40,50),dtype=np.uint8);sky[5:30,5:45]=255
            Image.fromarray(sky).save(masks/'sky_mask.png')
            native=allowed(folder,'v00',sky.shape)
            for shape in ((20,25),(80,100)):
                with self.subTest(shape=shape):
                    y=np.minimum(((np.arange(shape[0])+.5)*40/shape[0]).astype(int),39)
                    x=np.minimum(((np.arange(shape[1])+.5)*50/shape[1]).astype(int),49)
                    np.testing.assert_array_equal(allowed(folder,'v00',shape),native[y[:,None],x[None,:]])

    def test_seeded_sky_color_does_not_spread_to_beige_wall_or_disconnected_patch(self):
        rgb=np.full((20,30,3),[.52,.45,.30],dtype=float)
        rgb[:10,:10]=[.65,.65,.61]
        rgb[15:,25:]=[.65,.65,.61]
        result=confirmed_sky(rgb,[[100,100]],np.zeros((20,30),dtype=bool))
        self.assertTrue(result[4,4])
        self.assertFalse(result[12,12])
        self.assertFalse(result[17,27])

    def test_confirmed_sky_is_excluded_but_roof_boundary_and_building_remain(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            masks=folder/'semantic_guard/masks/output/v00'
            masks.mkdir(parents=True)
            sky=np.zeros((40,50),dtype=np.uint8)
            sky[:20,:]=255
            Image.fromarray(sky).save(masks/'sky_mask.png')
            result=allowed(folder,'v00',sky.shape)
            self.assertFalse(result[10,25])
            self.assertTrue(result[19,25])
            self.assertTrue(result[30,25])


if __name__=='__main__':unittest.main()
