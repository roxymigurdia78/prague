"""Conversation wiring, durable HTTP receipts and duplicate-generation protection."""
from contextlib import redirect_stdout
from http.server import HTTPServer
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler
from unittest.mock import patch

from streetlab import conversation as convo


REAL_EXTRACT = convo.extract
SCENE = ("A narrow cobblestone lane lined with tall stone houses, weathered plaster facades in muted pastel "
         "tones, arched wooden doors, wrought-iron balconies and hanging shop signs.")


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.messages = [{"role": "user", "content": "石畳の街。秋の夕方。"}]
        self.data = {"messages": self.messages}
        self.extraction = {"world_description": "石畳の街", "season": "autumn", "time_of_day": "evening",
                           "place_name": None, "place_query": None, "place_country": None, "place_relation": None,
                           "marble_scene": SCENE,
                           "evidence": {"world_description": "石畳の街", "season": "秋", "time_of_day": "夕方",
                                        "place_name": None},
                           "date_constraints": {}, "time_constraints": {}}
        self.extract = patch.object(convo, "extract", return_value=(self.extraction, {"model": convo.DEFAULT_MODEL})).start()
        self.search = patch.object(convo, "search_locations", side_effect=AssertionError("no place expected")).start()
        self.addCleanup(patch.stopall)

    def process(self, **kwargs):
        return convo.process(self.data, output=self.root, seed=42, **kwargs)

    def test_full_wiring_and_idempotency(self):
        with patch.object(convo.world, "main") as generate:
            first = self.process()
            second = self.process()
        self.assertEqual(first, second)
        self.extract.assert_called_once()
        generate.assert_not_called()
        run = Path(first["run_dir"])
        self.assertEqual(first["status"], "ready")
        chosen_date = convo.read_json(run / "date_selection.json")
        self.assertEqual(chosen_date["season"], "autumn")
        chosen_time = convo.read_json(run / "time_selection.json")
        self.assertEqual(chosen_time["period"], "evening")
        sun = convo.read_json(run / "render_settings.json")["sun"]
        self.assertEqual(sun["date"] + "T" + sun["time"], chosen_time["local_datetime"])
        prompt = convo.read_json(run / "marble_request.json")["world_prompt"]["text_prompt"]
        lighting = convo.MARBLE_LIGHTING.read_text(encoding="utf-8").strip()
        self.assertEqual(prompt, SCENE + "\n" + lighting)
        for word in ("autumn", "evening", "Season", "Time of day"):
            self.assertNotIn(word, prompt)
        self.assertEqual(prompt, (run / "world_prompt.txt").read_text(encoding="utf-8").strip())

    def test_generation_is_called_once_and_global_restored(self):
        old = convo.world.OUT
        with patch.object(convo.world, "main") as generate:
            result = self.process(generate=True)
            self.process(generate=True)
        self.assertEqual(result["status"], "generated")
        generate.assert_called_once()
        self.assertIn("--prompt-file", generate.call_args.args[0])
        self.assertEqual(convo.world.OUT, old)

    def place(self, text, name, query, country, relation="set_in"):
        self.messages[0]["content"] = text
        self.extraction.update(place_name=name, place_query=query, place_country=country, place_relation=relation)
        self.extraction["evidence"] |= {"world_description": text, "place_name": text}

    @staticmethod
    def search_result(*rows):
        return {"results": list(rows), "attribution": {"service_url": "https://open-meteo.com/en/docs/geocoding-api",
                                                        "license": "CC BY 4.0"}}

    def test_place_name_is_geocoded_and_passed_to_date_time_and_blender(self):
        self.place("秋の夕方の京都の石畳の街", "京都", "Kyoto", "JP")
        self.search.side_effect = None
        self.search.return_value = self.search_result(
            {"id": 1, "name": "Kyoto", "latitude": 35.02107, "longitude": 135.75385, "elevation": 50.0,
             "feature_code": "PPLA", "country_code": "JP", "timezone": "Asia/Tokyo", "population": 1459640},
            {"id": 2, "name": "Kyoto Station", "latitude": 34.98, "longitude": 135.75, "feature_code": "RSTN",
             "country_code": "JP", "timezone": "Asia/Tokyo"})
        result = self.process()
        self.assertEqual(result["status"], "ready")
        self.search.assert_called_once_with("Kyoto", country="JP", language="en", count=10)
        run = Path(result["run_dir"])
        chosen_time = convo.read_json(run / "time_selection.json")
        self.assertEqual(chosen_time["timezone"], "Asia/Tokyo")
        self.assertEqual(chosen_time["location"]["latitude"], 35.02107)
        self.assertEqual(chosen_time["period"], "evening")
        self.assertIn(chosen_time["date"][5:7], {"09", "10", "11"})
        sun = convo.read_json(run / "render_settings.json")["sun"]
        self.assertEqual((sun["latitude"], sun["longitude"], sun["altitude"]), (35.02107, 135.75385, 50.0))
        scene = convo.read_json(run / "scene_conditions.json")
        self.assertEqual(scene["location_source"], "conversation_place")
        self.assertEqual(scene["place"]["place_name"], "京都")
        self.assertEqual((scene["season"], scene["time_of_day"]), ("autumn", "evening"))
        self.assertEqual(scene["local_datetime"], chosen_time["local_datetime"])
        self.assertIn("--from-time", scene["commands"]["atmosphere"])
        self.assertEqual(convo.read_json(run / "location.json")["location"]["name"], "Kyoto")

    def test_southern_place_uses_southern_hemisphere_season(self):
        self.place("秋の夕方のシドニーの石畳", "シドニー", "Sydney", "AU")
        self.search.side_effect = None
        self.search.return_value = self.search_result(
            {"id": 3, "name": "Sydney", "latitude": -33.86785, "longitude": 151.20732, "elevation": 58.0,
             "feature_code": "PPLA", "country_code": "AU", "timezone": "Australia/Sydney", "population": 4627345})
        run = Path(self.process()["run_dir"])
        self.assertEqual(convo.read_json(run / "date_input.json")["hemisphere"], "south")
        self.assertIn(convo.read_json(run / "date_selection.json")["date"][5:7], {"03", "04", "05"})

    def test_no_place_uses_default_location(self):
        run = Path(self.process()["run_dir"])
        self.search.assert_not_called()
        record = convo.read_json(run / "location.json")
        self.assertEqual(record["source"], "config_default")
        self.assertEqual(record["location"]["name"], convo.default_location()["name"])

    def test_explicit_location_wins_over_conversation_place(self):
        self.place("秋の夕方の京都の石畳の街", "京都", "Kyoto", "JP")
        explicit = {"name": "Berlin", "latitude": 52.52437, "longitude": 13.41053, "timezone": "Europe/Berlin"}
        run = Path(self.process(location=explicit)["run_dir"])
        self.search.assert_not_called()
        self.assertEqual(convo.read_json(run / "time_selection.json")["timezone"], "Europe/Berlin")

    def test_unclear_or_missing_place_stops_for_review(self):
        self.place("秋の夕方のスプリングフィールドの石畳", "スプリングフィールド", "Springfield", None)
        self.search.side_effect = None
        self.search.return_value = self.search_result(
            {"name": "Springfield", "latitude": 39.8, "longitude": -89.6, "feature_code": "PPLA",
             "country_code": "US", "timezone": "America/Chicago", "population": 114230},
            {"name": "Springfield", "latitude": -27.6, "longitude": 152.9, "feature_code": "PPL",
             "country_code": "AU", "timezone": "Australia/Brisbane", "population": 100000})
        with patch.object(convo.world, "main") as generate:
            result = self.process(generate=True)
        self.assertEqual((result["status"], result["missing"]), ("needs_review", ["place"]))
        self.assertFalse((Path(result["run_dir"]) / "time_selection.json").exists())
        generate.assert_not_called()

    def test_place_quote_and_fields_are_validated(self):
        self.place("秋の夕方の京都の石畳の街", "京都", "Kyoto", "JP")
        bad = json.loads(json.dumps(self.extraction))
        bad["evidence"]["place_name"] = "パリに行きたい"
        with self.assertRaises(ValueError):
            convo.validate_extraction(bad, self.messages)
        for key, value in (("place_country", "Japan"), ("place_relation", "near"), ("place_query", None)):
            bad = json.loads(json.dumps(self.extraction))
            bad[key] = value
            with self.assertRaises(ValueError):
                convo.validate_extraction(bad, self.messages)

    def test_old_schema_extraction_is_redone_with_place(self):
        directory = self.root / convo.hashlib.sha256(json.dumps(self.messages, ensure_ascii=False, sort_keys=True,
                                                    separators=(",", ":")).encode()).hexdigest()
        directory.mkdir()
        old = {key: value for key, value in self.extraction.items() if key not in convo.PLACE_FIELDS}
        old["evidence"] = {key: value for key, value in old["evidence"].items() if key != "place_name"}
        convo.save_json(directory / "extraction.json", old)
        convo.save_json(directory / "context.json", {"location": convo.default_location(),
                                                     "location_source": "config_default", "seed": 7})
        convo.save_json(directory / "status.json", {"status": "error"})
        result = self.process()
        self.assertEqual(result["status"], "ready")
        self.extract.assert_called_once()
        self.assertTrue((directory / "v1_before_place" / "extraction.json").exists())
        self.assertEqual(convo.read_json(directory / "time_selection.json")["seed"], 7)

    def test_marble_lighting_suffix_is_fixed_verbatim(self):
        self.assertEqual(convo.MARBLE_LIGHTING.read_text(encoding="utf-8"),
                         "Photorealistic eye-level view, immersive, as if standing in the street.\n"
                         "No people, no cars. Unlit street lanterns.\n"
                         "Lighting: flat, uniform, overcast diffuse light, no direct sunlight,\n"
                         "no cast shadows, no specular highlights, no bloom, no color grading,\n"
                         "neutral white balance, evenly lit everywhere.\n")

    def test_marble_scene_rejects_light_time_weather_people_cars_and_japanese(self):
        for extra in (" Golden hour sunset.", " Soft evening light.", " Long shadows.", " Wet after rain.",
                      " A few tourists stroll.", " Parked cars line it.", " Lit lanterns.", " 秋の朝。",
                      " Creating a cozy, enchanting atmosphere.", " Warm golden facades."):
            bad = json.loads(json.dumps(self.extraction))
            bad["marble_scene"] = SCENE + extra
            with self.assertRaises(ValueError, msg=extra):
                convo.validate_extraction(bad, self.messages)
        bad = json.loads(json.dumps(self.extraction))
        bad["marble_scene"] = None
        with self.assertRaises(ValueError):
            convo.validate_extraction(bad, self.messages)
        ok = json.loads(json.dumps(self.extraction))
        ok["marble_scene"] = SCENE + " Dark oak doors, rendered plaster in shades of ochre, a stone sundial, a café."
        convo.validate_extraction(ok, self.messages)

    def test_rejected_reply_gets_one_corrective_retry(self):
        import sys, types
        bad = json.loads(json.dumps(self.extraction))
        bad["marble_scene"] = SCENE + " Soft morning light."
        replies = [json.dumps(bad), json.dumps(self.extraction)]
        calls = []

        class Stream(list):
            def close(self):
                pass

        def create(**kwargs):
            calls.append(kwargs["messages"])
            text = replies[len(calls) - 1]
            chunk = types.SimpleNamespace(choices=[types.SimpleNamespace(
                delta=types.SimpleNamespace(content=text), finish_reason="stop")])
            return Stream([chunk])

        class Client:
            def __init__(self, **_):
                self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=create))
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False

        with patch.dict(sys.modules, {"openai": types.SimpleNamespace(OpenAI=Client)}), \
                patch.object(convo, "hpc_settings", return_value=("k", "https://llm.kambehpc.com/v1", "m")):
            result, backend = REAL_EXTRACT(self.messages)
        self.assertEqual(result["marble_scene"], SCENE)
        self.assertTrue(backend["corrective_retry"])
        self.assertEqual(len(calls), 2)
        self.assertIn("morning", calls[1][-1]["content"])

    def test_conditions_only_extraction_ignores_unused_scene_but_preserves_evidence_validation(self):
        self.place('A street in Vienna with light gray facades.', 'Vienna', 'Vienna', 'AT')
        self.extraction.update(season=None,time_of_day=None,marble_scene=SCENE+' Light gray facades.')
        self.extraction['evidence'].update(season=None,time_of_day=None)
        with self.assertRaisesRegex(ValueError,'light'):
            convo.validate_extraction(self.extraction,self.messages)
        result=convo.validate_extraction(self.extraction,self.messages,require_marble_scene=False)
        self.assertEqual(result['place_query'],'Vienna')
        self.assertIsNone(result['marble_scene'])
        self.extraction['evidence']['place_name']='Prague'
        with self.assertRaisesRegex(ValueError,'抽出根拠'):
            convo.validate_extraction(self.extraction,self.messages,require_marble_scene=False)

    def test_conditions_only_hpc_call_accepts_null_scene_without_rewriting_the_prompt(self):
        from unittest.mock import MagicMock
        original=json.loads(json.dumps(self.extraction))
        original['marble_scene']=None
        client=MagicMock()
        with patch('openai.OpenAI',return_value=client), \
                patch.object(convo,'hpc_settings',return_value=('k','https://llm.kambehpc.com/v1','m')), \
                patch.object(convo,'_complete',return_value=json.dumps(original)) as complete:
            result,backend=REAL_EXTRACT(self.messages,require_marble_scene=False)
        self.assertIsNone(result['marble_scene'])
        self.assertEqual(backend['mode'],'conditions_only')
        complete.assert_called_once()
        self.assertIn('conditions-only',complete.call_args.args[2][0]['content'])

    def test_missing_conditions_never_randomly_generate(self):
        self.extraction["season"] = None
        self.extraction["time_of_day"] = None
        with patch.object(convo.world, "main") as generate:
            result = self.process(generate=True)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(set(result["missing"]), {"season", "time_of_day"})
        self.assertFalse((Path(result["run_dir"]) / "world_prompt.txt").exists())
        generate.assert_not_called()

    def test_conflicting_date_stops_before_marble(self):
        self.extraction["date_constraints"] = {"month": 7}
        with patch.object(convo.world, "main") as generate:
            with self.assertRaises(ValueError):
                self.process(generate=True)
        generate.assert_not_called()
        status = next(self.root.glob("*/status.json"))
        self.assertEqual(convo.read_json(status)["stage"], "date_time_selection")

    def test_quotes_cannot_come_from_assistant(self):
        self.extraction["evidence"]["season"] = "summer"
        with self.assertRaises(ValueError):
            convo.validate_extraction(self.extraction, self.messages + [{"role": "assistant", "content": "summer"}])

    def test_entire_conversation_and_text_blocks_preserved(self):
        source = [{"role": "user", "content": [{"type": "text", "text": "春"}]},
                  {"role": "assistant", "content": "昼ですか？"}, {"role": "user", "content": "秋の夕方に変更"}]
        result = convo.normalize_conversation(source)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["content"], "春")
        self.assertEqual(result[-1]["content"], "秋の夕方に変更")
        with self.assertRaises(ValueError):
            convo.normalize_conversation({"season": "autumn"})

    def test_conversation_id_cannot_be_a_file_path(self):
        for identifier in ("../outside", "C:/outside", "not-a-uuid", None):
            with self.assertRaises(ValueError):
                convo.normalize_conversation(self.data | {"conversation_id": identifier})

    def test_scan_does_not_retry_errors_automatically(self):
        inbox = self.root / "inbox"
        inbox.mkdir()
        convo.save_json(inbox / "bad.json", self.data)
        self.extract.side_effect = RuntimeError("secret or private transcript")
        with redirect_stdout(io.StringIO()):
            convo.scan(inbox, output=self.root / "results")
            convo.scan(inbox, output=self.root / "results")
        self.extract.assert_called_once()
        self.assertNotIn("secret", "".join(p.read_text() for p in (self.root / "results").rglob("status.json")))

    def test_marble_saved_submission_is_resumed_after_interruption(self):
        def interrupted(argv):
            out = Path(argv[argv.index("--run-dir") + 1])
            out.mkdir(parents=True, exist_ok=True)
            convo.save_json(out / "generate_submission_pending.json", {})
            raise RuntimeError("unknown submission")
        with patch.object(convo.world, "main", side_effect=interrupted):
            with self.assertRaises(RuntimeError):
                self.process(generate=True)
        with patch.object(convo.world, "api") as api, patch.object(convo.world, "load_api_key", return_value="test"):
            with redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, "送信結果が不明"):
                self.process(generate=True)
        api.assert_not_called()

    def test_http_requires_token_saves_once_and_status_is_queryable(self):
        self.data["conversation_id"] = "854900a7-14f8-424e-b7dc-6561f85e81a6"
        inbox = self.root / "inbox"
        token = "test-token-abcdefghijklmnopqrstuvwxyz"
        audit_path = self.root / "auth_audit.jsonl"
        server = HTTPServer(("127.0.0.1", 0), convo.receiver_handler(inbox, token,
                            {"output": self.root / "output", "auth_audit": audit_path}))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}/conversations"
        opener = build_opener(ProxyHandler({}))
        with opener.open(url.rsplit("/", 1)[0] + "/health", timeout=5) as response:
            self.assertIs(json.load(response)["automatic_generation"], False)
        body = json.dumps(self.data).encode()
        with self.assertRaises(HTTPError) as rejected:
            opener.open(Request(url, data=body), timeout=5)
        self.assertEqual(rejected.exception.code, 401)
        wrong_token = "different-client-token-abcdefghijklmnopqrstuvwxyz"
        with self.assertRaises(HTTPError) as rejected:
            opener.open(Request(url, data=body, headers={"Authorization": "Bearer " + wrong_token}), timeout=5)
        self.assertEqual(rejected.exception.code, 401)
        headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
        for _ in range(2):
            with opener.open(Request(url, data=body, headers=headers), timeout=5) as response:
                self.assertEqual(response.status, 202)
                receipt = json.load(response)
        self.assertEqual(len(list(inbox.glob("*.json"))), 1)
        self.assertTrue((inbox / (self.data["conversation_id"] + ".json")).exists())
        self.extract.assert_not_called()
        with opener.open(Request(url + "/" + self.data["conversation_id"], headers=headers), timeout=5) as response:
            self.assertEqual(json.load(response)["status"], "received_or_processing")
        events = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
        import hashlib
        runtime_hash = hashlib.sha256(token.encode()).hexdigest()
        self.assertTrue(all(event["runtime_token_sha256"] == runtime_hash for event in events))
        self.assertIsNone(events[0]["received_bearer_sha256"])
        self.assertEqual(events[1]["received_bearer_sha256"], hashlib.sha256(wrong_token.encode()).hexdigest())
        self.assertFalse(events[1]["accepted"])
        self.assertEqual(events[-1]["received_bearer_sha256"], runtime_hash)
        self.assertTrue(events[-1]["accepted"])
        self.assertNotIn(token, audit_path.read_text())
        self.assertNotIn(wrong_token, audit_path.read_text())


if __name__ == "__main__":
    unittest.main()
