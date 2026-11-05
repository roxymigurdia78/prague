"""Offline pipeline tests; no external requests or charges."""
import base64
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image
from streetlab import trellis_watch as watch


def png(color="brown", mode="RGB"):
    stream = io.BytesIO()
    Image.new(mode, (24, 32), color).save(stream, "PNG")
    return stream.getvalue()


def analysis(primary=1, clear=True):
    return dict(target_clear=clear, target="arched wood door", primary_image=primary,
                observations=["vertical boards"], material_hypotheses=["weathered wood"],
                lighting_hypotheses=["warm cast"], preserve=["board layout"],
                uncertainties=["back not visible"])


def client_for(payload=None, clear=True):
    client = Mock()
    client.vision.chat.completions.create.side_effect = lambda **_: stream_response(analysis(2, clear))
    client.image.images.edit.return_value = SimpleNamespace(
        data=[SimpleNamespace(b64_json=base64.b64encode(payload or png()).decode())],
        usage=None, _request_id="edit")
    return client


class StreamResponse:
    response = SimpleNamespace(headers={"x-request-id": "hpc-test"})

    def __init__(self, value, finish="stop"):
        self.value, self.finish, self.closed = value, finish, False

    def __iter__(self):
        yield SimpleNamespace(usage=None, choices=[SimpleNamespace(
            finish_reason=None, delta=SimpleNamespace(content=json.dumps(self.value)))])
        yield SimpleNamespace(usage=None, choices=[SimpleNamespace(
            finish_reason=self.finish, delta=SimpleNamespace(content=None))])

    def close(self):
        self.closed = True


def stream_response(value):
    return StreamResponse(value)


class FolderPipelineTests(unittest.TestCase):
    def setUp(self):
        root = watch.ROOT / "scratch" / "tests" / "_watch_test_tmp"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input = self.root / "input"
        self.input.mkdir()
        self.output = self.root / "results"
        self.files = [self.input / "wide.png", self.input / "close.png"]
        for path, color in zip(self.files, ["blue", "brown"]):
            path.write_bytes(png(color))

    def test_groups_root_and_subfolders_do_not_mix(self):
        child = self.input / "lamp"
        child.mkdir()
        (child / "one.png").write_bytes(png())
        (child / "ignored.txt").write_text("not an image")
        nested = child / "nested"
        nested.mkdir()
        (nested / "unrelated.png").write_bytes(png())
        groups = dict(watch.discover(self.input))
        self.assertEqual(set(groups), {"_root", "lamp"})
        self.assertEqual(len(groups["_root"]), 2)
        self.assertEqual(len(groups["lamp"]), 1)

    def test_full_pipeline_reorders_anchor_preserves_sources_and_deduplicates(self):
        refs = watch.snapshot(self.files)
        original = [p.read_bytes() for p in self.files]
        client = client_for()

        def fake_edit(**kwargs):
            self.assertEqual([f.read() for f in kwargs["image"]], [refs[1]["bytes"], refs[0]["bytes"]])
            self.assertIn('"primary_image": 1', kwargs["prompt"])
            self.assertIn("neutral diffuse", kwargs["prompt"])
            self.assertEqual(kwargs["background"], "opaque")
            return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(png()).decode())],
                                   usage=None, _request_id="edit")

        client.image.images.edit.side_effect = fake_edit
        with redirect_stdout(io.StringIO()):
            self.assertEqual(watch.process_group(client, "door", refs, self.output, watch.DEFAULTS), "success")
            # Another watcher/restart, renamed files and reordered references still skip.
            refs[0]["name"] = "renamed.png"
            self.assertEqual(watch.process_group(client, "another", refs[::-1], self.output, watch.DEFAULTS), "skipped")
        client.image.images.edit.assert_called_once()
        client.vision.chat.completions.create.assert_called_once()
        client.vision.responses.create.assert_not_called()
        request = client.vision.chat.completions.create.call_args.kwargs
        self.assertEqual(request["reasoning_effort"], "none")
        self.assertEqual(len([c for c in request["messages"][1]["content"] if c["type"] == "image_url"]), 2)
        manifest = json.loads(next(self.output.glob("*/manifest.json")).read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "success")
        self.assertTrue(Path(manifest["output"]).is_file())
        self.assertEqual([p.read_bytes() for p in self.files], original)
        self.assertEqual(manifest["analysis_api"]["backend"], "hpc")
        self.assertTrue(all(s.closed for s in client.image.images.edit.call_args.kwargs["image"]))

    def test_unclear_target_does_not_spend_on_image_edit(self):
        client = client_for(clear=False)
        with redirect_stdout(io.StringIO()):
            status = watch.process_group(client, "door", watch.snapshot(self.files), self.output, watch.DEFAULTS)
        self.assertEqual(status, "needs_review")
        client.image.images.edit.assert_not_called()

    def test_failure_is_persistent_and_does_not_leak_exception_body(self):
        client = client_for()
        client.image.images.edit.side_effect = TimeoutError("secret-request-body")
        refs = watch.snapshot(self.files)
        with self.assertRaises(TimeoutError):
            watch.process_group(client, "door", refs, self.output, watch.DEFAULTS)
        self.assertEqual(watch.process_group(client, "door", refs, self.output, watch.DEFAULTS), "skipped")
        client.image.images.edit.assert_called_once()
        text = next(self.output.glob("*/manifest.json")).read_text(encoding="utf-8")
        self.assertNotIn("secret-request-body", text)
        self.assertEqual(json.loads(text)["status"], "stopped_no_retry")

    def test_single_image_analysis_and_edit_are_supported(self):
        client = client_for()
        client.vision.chat.completions.create.side_effect = lambda **_: stream_response(analysis())
        with redirect_stdout(io.StringIO()):
            self.assertEqual(watch.process_group(client, "one", watch.snapshot(self.files[:1]), self.output, watch.DEFAULTS), "success")
        self.assertEqual(len(client.image.images.edit.call_args.kwargs["image"]), 1)

    def test_transparent_request_requires_real_alpha(self):
        client = client_for()
        refs = watch.snapshot(self.files)
        config = dict(watch.DEFAULTS, background="transparent")
        with self.assertRaises(ValueError):
            watch.edit_image(client.image, refs, analysis(2), config, "test")
        client = client_for(png((0, 0, 0, 0), "RGBA"))
        payload, _ = watch.edit_image(client.image, refs, analysis(2), config, "test")
        self.assertTrue(payload.startswith(b"\x89PNG"))

    def test_once_uses_mock_api_and_second_run_skips_paid_calls(self):
        client = client_for()
        with patch.object(watch, "create_clients", return_value=(client, watch.DEFAULTS)), patch.object(watch, "load_env_file"), \
             patch.dict("os.environ", {"OPENAI_API_KEY": "test-placeholder"}), \
             patch.object(watch.time, "sleep"), redirect_stdout(io.StringIO()):
            args = ["--once", "--input-dir", str(self.input), "--output-dir", str(self.output)]
            self.assertEqual(watch.main(args), 0)
            self.assertEqual(watch.main(args), 0)
        self.assertEqual(client.image.images.edit.call_count, 1)

    def test_copy_in_progress_is_not_sent(self):
        client = client_for()
        def add_file(_):
            (self.input / "later.png").write_bytes(png("green"))
        with patch.object(watch, "create_clients", return_value=(client, watch.DEFAULTS)), patch.object(watch, "load_env_file"), \
             patch.dict("os.environ", {"OPENAI_API_KEY": "test-placeholder"}), \
             patch.object(watch.time, "sleep", side_effect=add_file), redirect_stdout(io.StringIO()):
            self.assertEqual(watch.main(["--once", "--input-dir", str(self.input), "--output-dir", str(self.output)]), 1)
        client.vision.chat.completions.create.assert_not_called()

    def test_hpc_and_image_credentials_and_endpoints_are_separate(self):
        env_file = self.root / "hpc.env"
        env_file.write_text('LLM_KEY=hpc-placeholder\nLLM_BASE_URL=https://llm.kambehpc.com/v1\n'
                            'LLM_MODEL=qwen3.5:35b\nOPENAI_API_KEY=must-not-use\n', encoding="utf-8")
        config = dict(watch.DEFAULTS, hpc_env_file=str(env_file))
        with patch.dict("os.environ", {"OPENAI_API_KEY": "image-placeholder",
                                       "OPENAI_BASE_URL": "https://wrong-endpoint.invalid"}, clear=True), \
             patch("openai.OpenAI") as factory:
            _, resolved = watch.create_clients(config)
        hpc_call, image_call = factory.call_args_list
        self.assertEqual(hpc_call.kwargs["api_key"], "hpc-placeholder")
        self.assertEqual(hpc_call.kwargs["base_url"], "https://llm.kambehpc.com/v1")
        self.assertEqual(image_call.kwargs["api_key"], "image-placeholder")
        self.assertEqual(image_call.kwargs["base_url"], "https://api.openai.com/v1")
        self.assertEqual(hpc_call.kwargs["max_retries"], 0)
        self.assertEqual(resolved["vision_model"], "qwen3.5:35b")

    def test_missing_hpc_key_never_falls_back_to_openai_key(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "image-placeholder"}, clear=True):
            with self.assertRaises(ValueError):
                watch.hpc_settings(watch.DEFAULTS)

    def test_hpc_connection_check_does_not_need_image_key_or_send_images(self):
        client = Mock()
        client.models.list.return_value = [SimpleNamespace(id="qwen3.5:35b")]
        with patch.object(watch, "load_env_file"), patch.dict("os.environ", {}, clear=True), \
             patch.object(watch, "hpc_settings", return_value=("hpc-placeholder", "https://llm.kambehpc.com/v1", "qwen3.5:35b")), \
             patch("openai.OpenAI", return_value=client), redirect_stdout(io.StringIO()):
            self.assertEqual(watch.main(["--check-hpc"]), 0)
        client.models.list.assert_called_once()
        client.chat.completions.create.assert_not_called()
        client.images.edit.assert_not_called()

    def test_hpc_json_fences_and_reasoning_are_handled_without_extra_request(self):
        expected = analysis()
        text = '<think>{"do not use":true}</think>\n```json\n' + json.dumps(expected) + '\n```'
        self.assertEqual(watch.parse_analysis(text, 1), expected)
        with self.assertRaises(ValueError):
            watch.parse_analysis(json.dumps(expected) + json.dumps(expected), 1)
        with self.assertRaises(ValueError):
            watch.parse_analysis(json.dumps(analysis(primary=2)), 1)

    def test_incomplete_hpc_output_is_closed_and_stops_before_image_edit(self):
        client = client_for()
        stream = StreamResponse(analysis(), finish="length")
        client.vision.chat.completions.create.side_effect = None
        client.vision.chat.completions.create.return_value = stream
        with self.assertRaises(ValueError):
            watch.process_group(client, "door", watch.snapshot(self.files), self.output, watch.DEFAULTS)
        self.assertTrue(stream.closed)
        client.image.images.edit.assert_not_called()

    def test_dry_run_never_loads_key_or_creates_output(self):
        with patch.object(watch, "load_env_file", side_effect=AssertionError("keys must not load")), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(watch.main(["--dry-run", "--input-dir", str(self.input), "--output-dir", str(self.output)]), 0)
        self.assertFalse(self.output.exists())

    def test_invalid_files_and_too_many_references_rejected_before_api(self):
        self.files[0].write_bytes(b"incomplete")
        with self.assertRaises(OSError):
            watch.snapshot(self.files)
        with self.assertRaises(ValueError):
            watch.snapshot([self.files[1]] * 17)


if __name__ == "__main__":
    unittest.main()
