"""Offline checks for submission deduplication and GLB verification."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from unittest.mock import patch

from PIL import Image
from streetlab import trellis_hpc as hpc3d


class HPCGenerationTests(unittest.TestCase):
    def setUp(self):
        root = hpc3d.ROOT / "scratch" / "tests" / "_hpc_test_tmp"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_same_image_and_settings_never_launch_twice(self):
        root = self.root

        class Job:
            def __init__(self, name):
                self.name = name
                self.dir = root / name
                self.path = self.dir / "job.json"
                self.data = json.loads(self.path.read_text()) if self.path.exists() else {"steps": {}}

            def step(self, key):
                return self.data["steps"].setdefault(key, {})

            def save(self):
                self.dir.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(self.data))

        image = self.root / "door.png"
        Image.new("RGB", (8, 8), "brown").save(image)
        paths = {"job": "/home/u/jobs/test", "env": "/home/u/trellis2_env.sh",
                 "images": "/home/u/jobs/test/images", "glbs": "/home/u/jobs/test/GLBs",
                 "done": "/home/u/jobs/test/DONE", "log": "/home/u/jobs/test/log.txt"}
        module = SimpleNamespace(Job=Job, SIF="/opt/images/jupyter-cpu.sif",
                                 upload=Mock(), remote_paths=lambda *_: paths)
        hpc = Mock(home="/home/u")
        hpc.run.side_effect = [(0, "VERIFIED", ""), (0, "12345", "")]
        sample_source = ('import os\nIMAGE_EXTS = set()\n'
                         'def make(input_dir):\n    manifest = {"input_dir": str(input_dir),}\n'
                         '    return pipeline.run(None, seed=42, pipeline_type="1536_cascade")\n')
        hpc.get.side_effect = lambda _paths, directory: (directory / "batch_trellis_glb.py").write_text(sample_source)
        args = SimpleNamespace(image=image, texture_size=4096, decimation_target=150000,
                               normal_map=True, save_highres=True)
        with redirect_stdout(io.StringIO()):
            first = hpc3d.submit(module, hpc, args)
            second = hpc3d.submit(module, hpc, args)
        self.assertEqual(first.name, second.name)
        self.assertEqual(hpc.run.call_count, 2)  # preflight + exactly one launch
        module.upload.assert_called_once()
        self.assertEqual(second.step("generate")["state"], "running")
        command = hpc.run.call_args.args[0]
        self.assertIn("--normal-map --save-highres", command)
        self.assertIn("--texture-size 4096 --decimation-target 150000", command)

    def test_random_seed_is_chosen_at_execution_and_recorded(self):
        source = ('import os\nIMAGE_EXTS = set()\n'
                  'def make(input_dir):\n    manifest = {"input_dir": str(input_dir),}\n'
                  '    return pipeline.run(None, seed=42, pipeline_type="1536_cascade")\n')
        result = hpc3d.job_runner(source, None)
        self.assertIn("os.urandom(4)", result)
        self.assertIn("seed=GENERATION_SEED", result)
        self.assertIn('"seed": GENERATION_SEED', result)
        self.assertNotIn("seed=42,", result)
        fixed = hpc3d.job_runner(source, 43)
        self.assertIn("GENERATION_SEED = 43", fixed)

    def test_glb_verifier_checks_geometry_material_and_normal_map(self):
        data = {"asset": {"version": "2.0"}, "accessors": [{"count": 450000}],
                "meshes": [{"primitives": [{"indices": 0}]}],
                "materials": [{"pbrMetallicRoughness": {}, "normalTexture": {"index": 0}}],
                "images": [{"uri": "test.png"}]}
        chunk = json.dumps(data).encode()
        chunk += b" " * (-len(chunk) % 4)
        payload = struct.pack("<4sII", b"glTF", 2, 20 + len(chunk))
        payload += struct.pack("<II", len(chunk), 0x4E4F534A) + chunk
        path = self.root / "object.glb"
        path.write_bytes(payload)
        result = hpc3d.validate_glb(path)
        self.assertEqual(result["faces"], 150000)
        self.assertEqual(result["normal_maps"], 1)
        self.assertEqual(result["pbr_materials"], 1)
        path.write_bytes(payload[:-4])
        with self.assertRaises(ValueError):
            hpc3d.validate_glb(path)

    def test_cpu_offload_is_disabled_before_cuda_initialization(self):
        source = ('import os\nIMAGE_EXTS = set()\n'
                  'def make(input_dir):\n    pipeline.cuda()\n'
                  '    manifest = {"input_dir": str(input_dir),}\n'
                  '    return pipeline.run(None, seed=42, pipeline_type="1536_cascade")\n')
        result = hpc3d.job_runner(source, None, keep_models_on_gpu=True)
        self.assertLess(result.index("pipeline.low_vram = False"), result.index("pipeline.cuda()"))
        self.assertIn('"low_vram": False', result)
        self.assertIn('pipeline_type="1536_cascade"', result)
        self.assertIn("os.urandom(4)", result)

    def make_queued_job(self, number=1, verified=False):
        module = hpc3d.transport()
        module.LOCAL_JOBS = self.root
        job = module.Job("trellis_" + f"{number:024x}")
        job.data["remote_dir"] = "/home/u/jobs/" + job.name
        if verified:
            job.step("verify")["done"] = True
        job.save()
        return module, job

    def test_receiver_waits_without_starting_generation(self):
        module, job = self.make_queued_job()
        hpc = Mock()
        with patch.object(hpc3d, "status", return_value={"done": None}), \
             patch.object(hpc3d, "fetch") as fetch, \
             patch.object(hpc3d, "submit", side_effect=AssertionError("Must not generate")):
            hpc3d.watch_results(module, hpc, once=True)
        fetch.assert_not_called()
        hpc.run.assert_not_called()

    def test_receiver_downloads_completed_job_and_skips_it_next_time(self):
        module, job = self.make_queued_job()
        def complete_fetch(_module, _hpc, current):
            current.step("verify")["done"] = True
            current.save()
            return True
        with patch.object(hpc3d, "status", return_value={"done": "0"}) as status, \
             patch.object(hpc3d, "fetch", side_effect=complete_fetch) as fetch, \
             redirect_stdout(io.StringIO()):
            hpc3d.watch_results(module, Mock(), once=True)
            hpc3d.watch_results(module, Mock(), once=True)
        fetch.assert_called_once()
        status.assert_called_once()
        self.assertEqual(module.Job(job.name).step("auto_receive")["state"], "success")

    def test_receiver_does_not_download_or_restart_failed_generation(self):
        module, job = self.make_queued_job()
        with patch.object(hpc3d, "status", return_value={"done": "143"}), \
             patch.object(hpc3d, "fetch") as fetch, redirect_stdout(io.StringIO()):
            hpc3d.watch_results(module, Mock(), once=True)
        fetch.assert_not_called()
        self.assertEqual(module.Job(job.name).step("auto_receive")["reason"], "generation_failed")

    def test_receiver_lock_rejects_second_receiver(self):
        with hpc3d.receiver_lock(self.root):
            with self.assertRaises(OSError):
                with hpc3d.receiver_lock(self.root):
                    self.fail("Second receiver acquired the lock")


if __name__ == "__main__":
    unittest.main()
