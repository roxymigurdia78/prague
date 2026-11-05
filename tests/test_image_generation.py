"""Offline checks: no network or paid image generation."""

import base64
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from streetlab import generate_material_variants as images


# A tiny valid PNG; used only as a stand-in API response.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5t8AAAAASUVORK5CYII="
)


def response(payload=PNG):
    return SimpleNamespace(
        data=[SimpleNamespace(b64_json=base64.b64encode(payload).decode())],
        usage=None,
        _request_id="offline-request",
    )


class ImageGenerationTests(unittest.TestCase):
    @staticmethod
    def temporary_directory():
        # Keep temporary writes and their automatic cleanup inside this workspace.
        root = (images.ROOT / "scratch" / "tests" / "_image_test_tmp").resolve()
        if not root.is_relative_to(images.ROOT):
            raise RuntimeError("Test temporary directory is outside the workspace")
        root.mkdir(parents=True, exist_ok=True)
        return tempfile.TemporaryDirectory(dir=root)

    def test_original_six_image_command_dry_run_writes_nothing(self):
        with self.temporary_directory() as directory:
            output = Path(directory) / "not-created"
            with patch.object(images, "load_env_file", side_effect=AssertionError("must not load keys")):
                with redirect_stdout(io.StringIO()) as stream:
                    code = images.main([
                        "--classes", "window", "ceiling_light", "--start-variant", "1",
                        "--variants", "3", "--output-root", str(output), "--dry-run",
                    ])
            self.assertEqual(code, 0)
            self.assertIn("Plan: 6 images", stream.getvalue())
            self.assertIn("ceiling_light_v3.png", stream.getvalue())
            self.assertFalse(output.exists())

    def test_edit_sends_reference_and_keeps_original_file(self):
        with self.temporary_directory() as directory:
            source = Path(directory) / "reference.png"
            target = Path(directory) / "edited.png"
            source.write_bytes(PNG)
            args = images.parse_args([
                "--input", str(source), "--prompt", "make this vase blue",
                "--background", "transparent",
            ])
            client = Mock()

            def fake_edit(**kwargs):
                self.assertEqual(kwargs["image"].read(), PNG)
                self.assertEqual(kwargs["background"], "transparent")
                self.assertEqual(kwargs["output_format"], "png")
                self.assertEqual(kwargs["n"], 1)
                return response()

            client.images.edit.side_effect = fake_edit
            prompt = images.build_plan(args, Path(directory))[0]["prompt"]
            result = images.generate_one(client, args, prompt, target)
            client.images.generate.assert_not_called()
            self.assertIn("make this vase blue", prompt)
            self.assertNotIn("front three-quarter view", prompt)
            self.assertEqual(source.read_bytes(), PNG)
            self.assertEqual(target.read_bytes(), PNG)
            self.assertEqual(result["request_id"], "offline-request")
            self.assertTrue(client.images.edit.call_args.kwargs["image"].closed)

    def test_new_generation_uses_generate_endpoint(self):
        with self.temporary_directory() as directory:
            args = images.parse_args(["--classes", "vase"])
            client = Mock()
            client.images.generate.return_value = response()
            images.generate_one(client, args, "test prompt", Path(directory) / "vase.png")
            client.images.edit.assert_not_called()
            self.assertEqual(client.images.generate.call_args.kwargs["model"], "gpt-image-2")

    def test_reference_only_runs_enhancement_and_records_full_prompt(self):
        with self.temporary_directory() as directory:
            source = Path(directory) / "reference.png"
            source.write_bytes(PNG)
            client = Mock()
            client.images.edit.return_value = response()
            with patch.dict(os.environ, {"OPENAI_API_KEY": "offline-placeholder"}):
                with patch("openai.OpenAI", return_value=client):
                    with redirect_stdout(io.StringIO()):
                        code = images.main([
                            "--input", str(source), "--quality", "high",
                            "--output-root", str(Path(directory) / "results"),
                            "--env-file", str(Path(directory) / "missing.env"),
                        ])
            self.assertEqual(code, 0)
            client.images.generate.assert_not_called()
            client.images.edit.assert_called_once()
            sent = client.images.edit.call_args.kwargs
            self.assertEqual(sent["quality"], "high")
            self.assertIn("SAME single dominant object", sent["prompt"])
            self.assertIn("Do not mirror", sent["prompt"])
            manifest_path = next((Path(directory) / "results").glob("*/manifest.json"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["mode"], "enhance")
            self.assertEqual(manifest["assets"][0]["prompt"], sent["prompt"])
            self.assertEqual(manifest["assets"][0]["name"], "enhanced_object_v1")
            self.assertEqual(manifest["assets"][0]["status"], "success")
            self.assertEqual(source.read_bytes(), PNG)

    def test_image_input_cannot_silently_use_unrelated_class_prompt(self):
        with self.temporary_directory() as directory:
            source = Path(directory) / "reference.png"
            source.write_bytes(PNG)
            with redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    images.parse_args(["--input", str(source), "--classes", "vase"])
            self.assertEqual(error.exception.code, 2)

    def test_invalid_response_is_not_saved(self):
        with self.temporary_directory() as directory:
            target = Path(directory) / "bad.png"
            client = Mock()
            client.images.generate.return_value = response(b"not an image")
            with self.assertRaises(ValueError):
                images.generate_one(client, images.parse_args([]), "test", target)
            self.assertFalse(target.exists())

    def test_failed_request_stops_batch_without_retry_or_secret_logging(self):
        with self.temporary_directory() as directory:
            client = Mock()
            client.images.generate.side_effect = TimeoutError("private-input-body")
            with patch.dict(os.environ, {"OPENAI_API_KEY": "offline-placeholder"}):
                with patch("openai.OpenAI", return_value=client) as factory:
                    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as errors:
                        code = images.main([
                            "--classes", "vase", "--variants", "3", "--delay", "0",
                            "--output-root", directory, "--env-file", str(Path(directory) / "missing.env"),
                        ])
            self.assertEqual(code, 1)
            factory.assert_called_once_with(max_retries=0, timeout=600.0)
            self.assertEqual(client.images.generate.call_count, 1)
            manifest_text = next(Path(directory).glob("*/manifest.json")).read_text(encoding="utf-8")
            manifest = json.loads(manifest_text)
            self.assertEqual([asset["status"] for asset in manifest["assets"]], ["outcome_unknown", "planned", "planned"])
            for secret in ["offline-placeholder", "private-input-body"]:
                self.assertNotIn(secret, manifest_text + errors.getvalue())

    def test_env_handles_bom_and_preserves_existing_environment(self):
        with self.temporary_directory() as directory:
            env = Path(directory) / ".env"
            env.write_text('OPENAI_API_KEY="file-key"\n', encoding="utf-8-sig")
            with patch.dict(os.environ, {"OPENAI_API_KEY": "environment-key"}):
                images.load_env_file(env)
                self.assertEqual(os.environ["OPENAI_API_KEY"], "environment-key")
            with patch.dict(os.environ, {}, clear=True):
                images.load_env_file(env)
                self.assertEqual(os.environ["OPENAI_API_KEY"], "file-key")


if __name__ == "__main__":
    unittest.main()
