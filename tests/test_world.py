"""Output selection checks without API calls or credentials."""

from contextlib import redirect_stdout
from datetime import datetime
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from streetlab import world


class WorldOutputTests(unittest.TestCase):
    def setUp(self):
        temporary_root = (world.ROOT / "scratch" / "tests" / "_world_test_tmp").resolve()
        if not temporary_root.is_relative_to(world.ROOT.resolve()):
            raise RuntimeError("Test files must stay inside the workspace")
        temporary_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temporary_root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "config").mkdir()
        self.root_patch = patch.object(world, "ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.out_patch = patch.object(world, "OUT", self.root / "outputs" / "prague")
        self.out_patch.start()
        self.addCleanup(self.out_patch.stop)

    def completed(self, path):
        path.mkdir(parents=True, exist_ok=True)
        (path / "world.json").write_text('{"world_id": "saved-world"}', encoding="utf-8")
        (path / "generate_operation.json").write_text(
            '{"done": true, "response": {"world_id": "saved-world"}}', encoding="utf-8")

    def test_japan_time_and_collision_preserve_previous_results(self):
        clock = Mock()
        clock.now.return_value = datetime(2026, 11, 4, 15, 30, tzinfo=world.JST)
        with patch.object(world, "datetime", clock):
            first = world.select_output("generate")
            self.completed(first)
            second = world.select_output("generate")
        self.assertEqual(first.name, "20261104_153000")
        self.assertEqual(second.name, "20261104_153000_001")
        self.assertTrue((first / "world.json").exists())
        clock.now.assert_called_with(world.JST)
        self.assertEqual(world.select_output("download"), second)
        self.assertEqual(world.select_output("mesh"), second)

    def test_interrupted_and_unknown_submissions_reuse_folder(self):
        path = self.root / "outputs" / "worlds" / "20261104_153000"
        path.mkdir(parents=True)
        (path / "generate_operation.json").write_text('{"done": false}', encoding="utf-8")
        self.assertEqual(world.select_output("generate"), path)
        self.completed(path)
        (path / "generate_submission_pending.json").write_text("{}", encoding="utf-8")
        self.assertEqual(world.select_output("generate"), path)

    def test_legacy_results_and_explicit_selection(self):
        legacy = self.root / "outputs" / "prague"
        self.completed(legacy)
        self.assertEqual(world.select_output("download"), legacy)
        new = world.select_output("generate")
        self.completed(new)
        self.assertEqual(world.select_output("download"), new)
        self.assertEqual(world.select_output("download", Path("outputs/prague")), legacy.resolve())
        self.assertEqual(world.select_output("generate", resume=True), new)

    def test_no_results_does_not_create_folder_for_download_or_resume(self):
        for action, resume in (("download", False), ("generate", True)):
            with self.assertRaises(RuntimeError):
                world.select_output(action, resume=resume)
        self.assertFalse((self.root / "outputs").exists())

    def test_generate_then_download_uses_same_folder(self):
        text = 'A street with "unlit" lanterns.\nNeutral light.'
        (self.root / "config" / "world_prompt.txt").write_text(text, encoding="utf-8")
        result = {"world_id": "offline-world", "assets": {}}

        def fake_operation(*args):
            payload = args[3]
            self.assertEqual(payload["world_prompt"]["text_prompt"], text)
            self.assertEqual(payload["model"], "marble-1.1")
            self.assertEqual(payload["display_name"], f"World {world.OUT.name}")
            self.assertEqual(payload["permission"], {"public": False})
            world.save("generate_request.json", payload)
            world.save("generate_operation.json", {"done": True, "response": result})
            return result

        with patch.object(world, "load_api_key", return_value="offline-key"), \
                patch.object(world, "operation", side_effect=fake_operation), \
                patch.object(world, "api", return_value=result), \
                patch.object(world, "download_world") as download, \
                redirect_stdout(io.StringIO()):
            world.main(["generate"])
            generated = world.OUT
            world.main(["download", "--asset", "splats"])
        self.assertEqual(world.OUT, generated)
        download.assert_called_once_with(result, "splats")
        self.assertTrue((generated / "world.json").exists())

    def test_plain_text_preview_needs_no_key_and_creates_no_outputs(self):
        text = 'Prague\'s street.\n"No people, no cars."'
        (self.root / "config" / "world_prompt.txt").write_text("\ufeff" + text, encoding="utf-8")
        (self.root / "world_prompt.json").write_text("old unused settings", encoding="utf-8")
        with patch.object(world, "load_api_key") as key, \
                patch.object(world, "api") as api, redirect_stdout(io.StringIO()) as stream:
            world.main(["generate", "--dry-run", "--model", "marble-1.1-plus"])
        key.assert_not_called()
        api.assert_not_called()
        decoder = json.JSONDecoder()
        payload, _ = decoder.raw_decode(stream.getvalue())
        self.assertEqual(payload["world_prompt"]["text_prompt"], text)
        self.assertEqual(payload["model"], "marble-1.1-plus")
        self.assertFalse((self.root / "outputs").exists())

    def test_resume_keeps_original_prompt_and_model_without_text_file(self):
        path = self.root / "outputs" / "worlds" / "20261104_153000"
        self.completed(path)
        payload = {"model": "marble-1.1-plus", "world_prompt": {"type": "text", "text_prompt": "original"}}
        (path / "generate_request.json").write_text(json.dumps(payload), encoding="utf-8")
        result = {"world_id": "offline-world"}
        with patch.object(world, "load_api_key", return_value="offline-key"), \
                patch.object(world, "operation", return_value=result) as operation, \
                patch.object(world, "api", return_value=result), redirect_stdout(io.StringIO()):
            world.main(["generate", "--resume"])
            self.assertEqual(operation.call_args.args[3], payload)
            with self.assertRaisesRegex(RuntimeError, "モデルは変更できません"):
                world.main(["generate", "--resume", "--model", "marble-1.1"])
        self.assertEqual(operation.call_count, 1)

    def test_plus_credit_check_covers_upper_price_before_submission(self):
        world.OUT = self.root
        payload = {"model": "marble-1.1-plus"}
        with patch.object(world, "api", return_value={"remaining_credits": 3000}) as api, \
                redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "上限"):
                world.operation("offline-key", "generate", "worlds:generate", payload)
        api.assert_called_once_with("offline-key", "credits")
        self.assertFalse((self.root / "generate_request.json").exists())

    def test_blank_prompt_is_rejected_before_any_api_call(self):
        (self.root / "config" / "world_prompt.txt").write_text(" \n ", encoding="utf-8")
        with patch.object(world, "api") as api:
            with self.assertRaisesRegex(ValueError, "説明文"):
                world.main(["generate", "--dry-run"])
        api.assert_not_called()

    def test_resume_without_submission_cannot_start_paid_generation(self):
        empty = self.root / "outputs" / "worlds" / "20261104_153000"
        empty.mkdir(parents=True)
        (self.root / "world_prompt.json").write_text(
            '{"model": "marble-1.1", "world_prompt": {"type": "text"}}', encoding="utf-8")
        with patch.object(world, "load_api_key", return_value="offline-key"), \
                patch.object(world, "api") as api, redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "再開できる送信記録"):
                world.main(["generate", "--resume"])
        api.assert_not_called()


if __name__ == "__main__":
    unittest.main()
