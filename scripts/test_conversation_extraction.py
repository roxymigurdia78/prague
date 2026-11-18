"""Exercise the running receiver and real HPC extraction; refuse automatic Marble generation."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import os
import sys
import time
from urllib.request import Request, build_opener, ProxyHandler
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from streetlab.conversation import read_json, save_json

CASES = [
    {"name": "explicit_preferences", "messages": [
        {"role": "assistant", "content": "どんな世界に行きたい？"},
        {"role": "user", "content": "秋の夕暮れのプラハ旧市街を歩きたい。石畳と古い建物が並ぶ路地がいい。"}],
     "season": "autumn", "time_of_day": "evening", "world_contains": ["プラハ", "prague"],
     "place": "prague", "timezone": "Europe/Prague"},
    {"name": "latest_user_correction", "messages": [
        {"role": "user", "content": "夏の昼の京都の商店街に行きたい。"},
        {"role": "assistant", "content": "夏の昼の京都の商店街ですね。"},
        {"role": "user", "content": "やっぱり変更します。京都ではなく札幌の雪の積もった街並み。季節は冬、時間帯は深夜にしてください。"}],
     "season": "winter", "time_of_day": "midnight", "world_contains": ["札幌", "sapporo"],
     "place": "sapporo", "timezone": "Asia/Tokyo"},
    {"name": "unaccepted_assistant_proposal", "messages": [
        {"role": "user", "content": "石畳の路地と古い街灯がある街に行きたい。"},
        {"role": "assistant", "content": "春の朝にしましょうか？"},
        {"role": "user", "content": "季節と時間帯はまだ決めません。提案はまだ採用しないでください。"}],
     "season": None, "time_of_day": None, "world_contains": None, "place": None},
    {"name": "missing_season", "messages": [
        {"role": "user", "content": "空中に浮かぶ架空の都市を夜に歩きたい。季節はまだ決めていません。"}],
     "season": None, "time_of_day": "night", "world_contains": None, "place": None},
    {"name": "inspired_by_place", "messages": [
        {"role": "assistant", "content": "どんな世界に行ってみたい？"},
        {"role": "user", "content": "プラハみたいな、かわいいファンタジーな街並みのとこに行きたい"},
        {"role": "assistant", "content": "朝と夕方、どっちがいい？"},
        {"role": "user", "content": "朝のすっきりした感じでいいや"},
        {"role": "assistant", "content": "季節はどうしようか？"},
        {"role": "user", "content": "まあ、秋でいいや"}],
     "season": "autumn", "time_of_day": "morning", "world_contains": ["プラハ", "prague"],
     "place": "prague", "timezone": "Europe/Prague"},
    {"name": "explicit_calendar_and_clock", "messages": [
        {"role": "user", "content": "プラハ旧市街を2026年10月15日の現地時刻13:00に歩きたい。季節は秋、時間帯は昼です。"}],
     "season": "autumn", "time_of_day": "noon", "world_contains": ["プラハ", "prague"],
     "date": "2026-10-15", "clock": "13:00", "place": "prague", "timezone": "Europe/Prague"},
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    config = read_json(ROOT / "config" / "conversation_receiver.local.json")
    base = f"http://{config['host']}:{config['port']}"
    token = os.environ.get("CONVERSATION_RECEIVER_TOKEN") or config["token"]
    opener = build_opener(ProxyHandler({}))

    def request(path, payload=None):
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = Request(base + path, data=data, headers={"Authorization": "Bearer " + token,
                      "Content-Type": "application/json; charset=utf-8"})
        with opener.open(req, timeout=10) as response:
            return response.status, json.load(response)

    _, health = request("/health")
    if health.get("automatic_generation") is not False:
        raise RuntimeError("Receiver must explicitly report automatic_generation=false before running tests")
    output = args.output or ROOT / "outputs" / "conversation_tests" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    output.mkdir(parents=True, exist_ok=False)
    report = {"receiver": base, "health": health, "cases": []}
    for case in CASES:
        print(f"Testing: {case['name']}", flush=True)
        # Change transcript identity on each test run so the live HPC is exercised instead of cached extraction.
        payload = {"conversation_id": str(uuid.uuid4()), "messages": [
            {"role": "system", "content": "Extraction verification label: " + output.name + ". This label is not a scene preference."},
            *case["messages"]]}
        code, receipt = request("/conversations", payload)
        assert code == 202, code
        deadline = time.monotonic() + 120
        while True:
            _, status = request("/conversations/" + payload["conversation_id"])
            if status["status"] != "received_or_processing":
                break
            if time.monotonic() > deadline:
                raise TimeoutError("Extraction test did not finish within 120 seconds")
            time.sleep(1)
        directory = ROOT / "outputs" / "conversations" / receipt["id"]
        extraction = read_json(directory / "extraction.json") if (directory / "extraction.json").exists() else {}
        failures = []
        for key in ("season", "time_of_day"):
            if extraction.get(key) != case[key]:
                failures.append(f"{key}: expected={case[key]!r}, actual={extraction.get(key)!r}")
        description = extraction.get("world_description")
        if not isinstance(description, str) or not description.strip():
            failures.append("Missing world description")
        elif case["world_contains"] and not any(part in description.lower() for part in case["world_contains"]):
            failures.append("Expected city not preserved in world description")
        query = extraction.get("place_query")
        if (query.casefold() if isinstance(query, str) else None) != case["place"]:
            failures.append(f"place_query: expected={case['place']!r}, actual={query!r}")
        expected_status = "ready" if case["season"] and case["time_of_day"] else "needs_review"
        if status["status"] != expected_status:
            failures.append(f"status: expected={expected_status}, actual={status['status']}")
        if (directory / "world").exists():
            failures.append("Unexpected world generation directory")
        selected_time = None
        if expected_status == "ready" and status["status"] == "ready":
            selected_time = read_json(directory / "time_selection.json")
            if case.get("timezone") and selected_time["timezone"] != case["timezone"]:
                failures.append(f"timezone: expected={case['timezone']}, actual={selected_time['timezone']}")
            if not (directory / "scene_conditions.json").exists():
                failures.append("Missing scene_conditions.json")
            lighting = (ROOT / "config" / "marble_lighting.txt").read_text(encoding="utf-8").strip()
            prompt = (directory / "world_prompt.txt").read_text(encoding="utf-8").strip()
            if prompt != str(extraction.get("marble_scene", "")).strip() + "\n" + lighting:
                failures.append("Marble prompt is not scene + fixed lighting")
            if "date" in case and selected_time["date"] != case["date"]:
                failures.append("Explicit calendar date changed")
            if "clock" in case and selected_time["local_datetime"][11:16] != case["clock"]:
                failures.append("Explicit local clock changed")
        result = {"name": case["name"], "passed": not failures, "failures": failures,
                  "input": payload, "status": status, "extraction": extraction,
                  "selected_datetime": selected_time["local_datetime"] if selected_time else None,
                  "marble_generation_started": (directory / "world").exists(), "run_dir": str(directory)}
        report["cases"].append(result)
        save_json(output / (case["name"] + ".json"), result)
        save_json(output / "report.json", report)
        print(json.dumps({"case": case["name"], "passed": result["passed"],
                          "season": extraction.get("season"), "time_of_day": extraction.get("time_of_day"),
                          "place": extraction.get("place_query"), "relation": extraction.get("place_relation"),
                          "marble_scene": extraction.get("marble_scene"),
                          "status": status["status"], "failures": failures}, ensure_ascii=False), flush=True)
    passed = sum(case["passed"] for case in report["cases"])
    lines = ["# 会話抽出の実機テスト", "", f"結果: {passed}/{len(CASES)}件成功。Marble自動生成: 無効。", "",
             "HTTP受信 → UUID名での保存 → 実際のHPC抽出 → 日時設定までを確認。", "",
             "| ケース | 世界の説明（HPC抽出） | Marble用の場所の説明 | 地名 | 季節 | 時間帯 | 状態 | 結果 |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for result in report["cases"]:
        extracted = result["extraction"]
        description = str(extracted.get("world_description", "")).replace("|", "\\|").replace("\n", " ")
        scene = str(extracted.get("marble_scene", "")).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {result['name']} | {description} | {scene} | {extracted.get('place_query')} ({extracted.get('place_relation')}) | {extracted.get('season')} | {extracted.get('time_of_day')} | {result['status']['status']} | {'成功' if result['passed'] else '失敗'} |")
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Report: {output / 'report.md'}", flush=True)
    return 0 if passed == len(CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
