"""No network; test lower-face preservation and VLM/settings validation."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from streetlab import sky_cleanup as sky


class SkyCleanupTests(unittest.TestCase):
    def test_same_sky_color_below_height_is_preserved(self):
        colors = np.full((3, 7, 3), (0.6, 0.7, 0.8), dtype=np.float32)
        z = np.array([[8, 8, 8], [1, 1, 1], [3, 8, 8]])
        selected = sky.classify_upper(z, colors, [[0.6, 0.7, 0.8]], 5, 0.1)
        self.assertEqual(selected.tolist(), [True, False, False])

    def test_upper_roof_color_and_mixed_boundaries_are_preserved(self):
        colors = np.full((3, 7, 3), (0.6, 0.7, 0.8), dtype=np.float32)
        colors[0] = (0.7, 0.2, 0.1)
        colors[1, :2] = (0.7, 0.2, 0.1)
        colors[2, :1] = (0.7, 0.2, 0.1)
        selected = sky.classify_upper(np.full((3, 3), 8), colors, [[0.6, 0.7, 0.8]], 5, 0.1)
        self.assertEqual(selected.tolist(), [False, False, True])

    def test_empty_palette_selects_nothing(self):
        self.assertFalse(sky.classify_upper(np.ones((1, 3)), np.ones((1, 7, 3)), [], 0, .1).any())

    def test_connected_region_excludes_separate_same_color_wall(self):
        rgb = np.full((10,10,3), (0.6,0.7,0.8), dtype=np.float32)
        rgb[4:6,:,:] = (0.8,0.1,0.1)
        mask = sky.connected_sky(rgb, [[200,200]], .05)
        self.assertTrue(mask[:4].all())
        self.assertFalse(mask[4:].any())

    def test_concave_outline_does_not_cut_out_building_notch(self):
        polygon = [[0,0],[1000,0],[1000,1000],[700,1000],[700,300],[300,300],[300,1000],[0,1000]]
        mask = sky.polygon_sky_mask((10,10), [polygon])
        self.assertTrue(mask[:3].all())
        self.assertTrue(mask[:, :3].all())
        self.assertFalse(mask[3:,3:7].any())

    def test_bad_sky_polygon_rejected(self):
        data = {"views":[{"id":"view_00","points":[],"sky_polygons":[[[0,0],[1001,0],[0,1000]]]}]}
        with self.assertRaises(ValueError):
            sky.parse_analysis(json.dumps(data), ["view_00"])

    def test_dark_wall_and_black_seeds_are_not_accepted_as_sky(self):
        image=np.zeros((10,10,3),dtype=np.float32)
        image[:]=(0.5,.56,.59)
        image[8,8]=(.42,.43,.38)
        image[7,7]=(0,0,0)
        points=[[100,100],[200,200],[300,300],[400,400],[800,800],[700,700]]
        filtered=sky.filter_sky_points([image],[{"id":"v","points":points}])
        self.assertEqual(filtered["v"],points[:4])

    def test_small_sky_floater_hole_is_filled_but_exterior_is_not(self):
        mask=np.zeros((10,10),dtype=bool)
        mask[1:9,1:9]=True
        mask[4,4]=False
        result=sky.fill_small_holes(mask,maximum=2)
        self.assertTrue(result[4,4])
        self.assertFalse(result[0].any())

    def test_adaptive_growth_follows_sky_brightness_gradient(self):
        image=np.zeros((10,20,3),dtype=np.float32)
        for x in range(15):
            image[:,x]=np.array([.4,.46,.49])+x*.025
        image[:,15:]=(.4,.1,.1)
        result=sky.connected_sky(image,[[100,500]],tolerance=.08,adaptive=True)
        self.assertTrue(result[:,:15].all())
        self.assertFalse(result[:,15:].any())

    def test_component_analysis_welds_uv_seams_and_keeps_detached_fragment_separate(self):
        xyz=np.array([[0,0,0],[1,0,0],[0,1,0],
                      [1,0,0],[1,1,0],[0,1,0],
                      [0,0,3],[.1,0,3],[0,.1,3]],dtype=float)
        triangles=np.array([[0,1,2],[3,4,5],[6,7,8]])
        groups=sky.mesh_components(xyz,triangles,np.array([True,True,True]))
        self.assertEqual(sorted(sorted(g.tolist()) for g in groups),[[0,1],[2]])

    def test_component_detaches_only_after_connecting_sky_faces_are_removed(self):
        xyz=np.array([[0,0,0],[1,0,0],[0,1,0],[0,1,3],[1,1,3],[1,2,3]],dtype=float)
        triangles=np.array([[0,1,2],[1,2,3],[3,4,5]])
        self.assertEqual(len(sky.mesh_components(xyz,triangles,np.array([True,True,True]))),1)
        groups=sky.mesh_components(xyz,triangles,np.array([True,False,True]))
        self.assertEqual(sorted(sorted(g.tolist()) for g in groups),[[0],[2]])

    def test_thin_crack_can_be_closed_then_enclosed_fragment_removed(self):
        mask=np.ones((30,30),dtype=bool)
        mask[10:20,10:20]=False
        mask[:10,14]=False
        result=sky.fill_small_holes(sky.close_sky_mask(mask,2),maximum=150)
        self.assertTrue(result[12:18,12:18].all())

    def test_large_building_region_at_image_edge_is_preserved(self):
        mask=np.ones((30,30),dtype=bool)
        mask[:,:12]=False
        result=sky.fill_small_holes(sky.close_sky_mask(mask,2),maximum=150)
        self.assertFalse(result[:,:10].any())

    def test_cut_boundary_refinement_preserves_unrelated_vertices_and_caps_motion(self):
        xyz=np.array([[0,0,2],[1,.2,2],[2,0,2],[0,-1,2],[1,-1,2],[2,-1,2],
                      [0,1,2],[1,1,2],[2,1,2],[0,0,0]],dtype=float)
        triangles=np.array([[0,1,3],[1,4,3],[1,2,4],[2,5,4],
                            [0,6,1],[1,6,7],[1,7,2],[2,7,8]])
        removed=np.array([False]*4+[True]*4)
        result,summary=sky.refine_cut_boundary(xyz,triangles,removed,1,.05)
        self.assertGreater(summary['vertices'],0)
        self.assertLessEqual(summary['maximum_displacement'],.0500001)
        np.testing.assert_array_equal(result[3:6],xyz[3:6])
        np.testing.assert_array_equal(result[9],xyz[9])

    def test_vlm_requires_exact_views_and_valid_coordinates(self):
        good = {"views": [{"id": "view_00", "points": [[500, 100]], "reason": "sky"}]}
        self.assertEqual(sky.parse_analysis(json.dumps(good), ["view_00"]), good)
        for data in ({"views": []}, {"views": good["views"] * 2},
                     {"views": [{"id": "view_00", "points": [[-1, 100]]}]},
                     {"views": [{"id": "view_00", "points": [[True, 100]]}]}):
            with self.assertRaises(ValueError):
                sky.parse_analysis(json.dumps(data), ["view_00"])

    def test_explanatory_prefix_can_be_read_but_multiple_answers_are_rejected(self):
        value={"views":[{"id":"view_00","points":[[500,100]],"reason":"sky"}]}
        self.assertEqual(sky.parse_analysis('Analysis follows:\n'+json.dumps(value),['view_00']),value)
        with self.assertRaises(ValueError):
            sky.parse_analysis(json.dumps(value)+'\n'+json.dumps(value),['view_00'])

    def test_local_token_alias_wins_over_fallback_key(self):
        root = (sky.ROOT / "scratch" / "tests" / "_sky_test_tmp").resolve()
        if not root.is_relative_to(sky.ROOT.resolve()):
            raise RuntimeError("Test path outside workspace")
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as folder:
            folder = Path(folder)
            (folder / "config").mkdir()
            fallback = folder / "old.env"
            fallback.write_text("LLM_KEY=old-placeholder\nLLM_MODEL=qwen3.5:35b\n", encoding="utf-8")
            (folder / "config" / "trellis.local.json").write_text(json.dumps({"hpc_env_file": str(fallback)}), encoding="utf-8")
            (folder / ".env").write_text("LITELLM_API_KEY=new-placeholder\nOPENAI_API_KEY=do-not-use\n", encoding="utf-8")
            with patch.object(sky, "ROOT", folder), patch.dict(os.environ, {}, clear=True):
                key, base, model = sky.hpc_settings()
                self.assertEqual((key, base, model), ("new-placeholder", sky.HPC_URL, "qwen3.5:35b"))
                with patch.dict(os.environ, {"LITELLM_API_KEY": "env-placeholder"}):
                    self.assertEqual(sky.hpc_settings()[0], "env-placeholder")
                (folder / ".env").write_text("LLM_KEY=key-placeholder\nLLM_BASE_URL=https://api.openai.com/v1", encoding="utf-8")
                with self.assertRaises(ValueError):
                    sky.hpc_settings()


if __name__ == "__main__":
    unittest.main()
