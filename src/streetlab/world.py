"""World APIで街並みを生成・取得する。実行はユーザーが行う。"""

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import shutil
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from streetlab.world_api import load_api_key


from streetlab.paths import ROOT
OUT = ROOT / "outputs" / "prague"
BASE = "https://api.worldlabs.ai/marble/v1/"
JST = timezone(timedelta(hours=9))
GENERATION_CREDITS = {"marble-1.1": (1580, 1580), "marble-1.1-plus": (1580, 3080)}


def generation_payload(model, name):
    text = (ROOT / "config" / "world_prompt.txt").read_text(encoding="utf-8-sig").strip()
    if not text:
        raise ValueError("config/world_prompt.txt に作りたい世界の説明文を入力してください。")
    return {
        "display_name": f"World {name}",
        "model": model,
        "permission": {"public": False},
        "seed": 42,
        "world_prompt": {"type": "text", "text_prompt": text},
    }


def latest_output():
    runs = ROOT / "outputs" / "worlds"
    candidates = [path for path in runs.iterdir()
                  if path.is_dir() and re.fullmatch(r"\d{8}_\d{6}(?:_\d+)?", path.name)] if runs.exists() else []
    if candidates:
        return max(candidates, key=lambda path: path.name)
    legacy = ROOT / "outputs" / "prague"
    return legacy if (legacy / "world.json").exists() else None


def unfinished_generation(path):
    if (path / "generate_submission_pending.json").exists():
        return True
    operation_path = path / "generate_operation.json"
    if operation_path.exists():
        job = json.loads(operation_path.read_text(encoding="utf-8"))
        return not job.get("done") or bool(job.get("error")) or not (path / "world.json").exists()
    return not (path / "world.json").exists()


def select_output(action, run_dir=None, resume=False):
    if run_dir is not None:
        path = Path(run_dir)
        path = path if path.is_absolute() else ROOT / path
        if action == "generate" and not resume:
            path.mkdir(parents=True, exist_ok=True)
        elif not path.is_dir():
            raise RuntimeError("指定した保存フォルダーが見つかりません。")
        return path.resolve()
    latest = latest_output()
    if action != "generate" or resume:
        if latest is None:
            raise RuntimeError("生成済みのフォルダーがありません。先に python run.py world generate を実行してください。")
        return latest
    # 中断や送信結果不明の記録がある場合は、別の有料生成を始めない。
    if latest is not None and unfinished_generation(latest):
        return latest
    runs = ROOT / "outputs" / "worlds"
    runs.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    index = 0
    while True:
        path = runs / (stamp if index == 0 else f"{stamp}_{index:03d}")
        try:
            path.mkdir()
            return path
        except FileExistsError:
            index += 1


def save(name, data):
    path = OUT / name
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def read(name):
    return json.loads((OUT / name).read_text(encoding="utf-8"))


def api(key, route, payload=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        BASE + route,
        data=body,
        headers={"WLT-Api-Key": key, "Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
    )
    with urlopen(request, timeout=120) as response:
        return json.load(response)


def operation(key, stage, route, payload):
    filename = stage + "_operation.json"
    marker = OUT / (stage + "_submission_pending.json")
    if (OUT / filename).exists():
        job = read(filename)
        print("保存済みの処理を再開します。新たな生成リクエストは送りません。", flush=True)
    else:
        if marker.exists():
            raise RuntimeError(
                "前回の送信結果が不明です。重複課金を避けるため停止しました。"
                "World Labs Platformで作成済みの処理を確認してください。"
            )
        minimum, required = GENERATION_CREDITS[payload["model"]] if stage == "generate" else (3500, 3500)
        balance = api(key, "credits")["remaining_credits"]
        price = f"{required:,}" if minimum == required else f"{minimum:,}〜{required:,}"
        print(f"API残高: {balance:,.0f} / この処理の掲載料金: {price} クレジット", flush=True)
        if balance < required:
            raise RuntimeError("この処理の掲載料金（変動料金では上限）に対してAPI残高が不足しています。")
        save(stage + "_request.json", payload)
        # POST送信後に通信が切れた場合は、自動的に再送しない。
        save(marker.name, {"route": route, "status": "submission_outcome_unknown"})
        job = api(key, route, payload)
        save(filename, job)
        marker.unlink()

    started = time.monotonic()
    while not job.get("done"):
        if time.monotonic() - started > 7200:
            raise RuntimeError("待機を終了しました。同じコマンドで保存済みの処理を再開できます。")
        operation_id = job.get("operation_id")
        if not operation_id:
            raise RuntimeError("operation_idがありません。保存された応答を確認してください。")
        print(f"{stage}: 処理中…（20秒ごとに確認）", flush=True)
        time.sleep(20)
        job = api(key, "operations/" + quote(operation_id, safe=""))
        save(filename, job)
    if job.get("error"):
        raise RuntimeError("APIの処理が失敗しました: " + json.dumps(job["error"], ensure_ascii=False))
    if job.get("cost"):
        print("確定した消費量:", json.dumps(job["cost"], ensure_ascii=False), flush=True)
    return job.get("response") or {}


def download(url, name, fallback):
    if not url:
        return
    parsed = urlsplit(url)
    if parsed.scheme != "https":
        raise RuntimeError("取得用URLがHTTPSではありません。")
    suffix = Path(parsed.path).suffix.lower()
    if suffix not in {".glb", ".ply", ".spz", ".png", ".jpg", ".jpeg", ".webp", ".zip"}:
        suffix = fallback
    name = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    target = OUT / (name + suffix)
    if target.exists() and target.stat().st_size:
        print("保存済み:", target.name, flush=True)
        return
    partial = target.with_suffix(target.suffix + ".part")
    print("ダウンロード:", target.name, flush=True)
    # 配布先へのダウンロードにはAPIキーを付けない。
    with urlopen(url, timeout=180) as source, partial.open("wb") as destination:
        shutil.copyfileobj(source, destination)
    partial.replace(target)


def get_world(key, world_id):
    world = api(key, "worlds/" + quote(world_id, safe=""))
    save("world.json", world)
    url = world.get("world_marble_url")
    if url:
        (OUT / "viewer_url.txt").write_text(url + "\n", encoding="utf-8")
        print("ブラウザーで見る:", url, flush=True)
    return world


def download_world(world, selection):
    assets = world.get("assets") or {}
    if selection == "panorama":
        download((assets.get("imagery") or {}).get("pano_url"), "panorama", ".jpg")
        return
    if selection == "thumbnail":
        download(assets.get("thumbnail_url"), "thumbnail", ".jpg")
        return
    if selection == "collider":
        download((assets.get("mesh") or {}).get("collider_mesh_url"), "collider_mesh", ".glb")
        return
    splats = (assets.get("splats") or {}).get("spz_urls") or {}
    # 全解像度があればそれを優先し、なければ返却された解像度を保存する。
    chosen = {"full_res": splats["full_res"]} if splats.get("full_res") else splats
    for resolution, url in chosen.items():
        download(url, "splats_" + resolution, ".spz")


def main(argv=None):
    global OUT
    parser = argparse.ArgumentParser(description="config/world_prompt.txt の説明文で世界を生成・取得")
    parser.add_argument("action", choices=["generate", "download", "mesh"])
    parser.add_argument("--asset", choices=["splats", "collider", "panorama", "thumbnail"])
    parser.add_argument("--run-dir", type=Path, help="以前の結果を指定する保存フォルダー（省略時は最新）")
    parser.add_argument("--resume", action="store_true", help="最新の生成を再開・確認する（新規生成しない）")
    parser.add_argument("--model", choices=GENERATION_CREDITS, default=None,
                        help="生成モデル（省略時はmarble-1.1、再開時は元のモデル）")
    parser.add_argument("--dry-run", action="store_true", help="生成内容を表示するだけ（API呼び出し・保存なし）")
    args = parser.parse_args(argv)
    if args.action == "download" and not args.asset:
        parser.error("downloadには --asset splats / collider / panorama / thumbnail の指定が必要です。")
    if args.action != "download" and args.asset:
        parser.error("--assetはdownloadで指定してください。")
    if args.resume and args.action != "generate":
        parser.error("--resumeはgenerateで指定してください。")
    if args.model is not None and args.action != "generate":
        parser.error("--modelはgenerateで指定してください。")
    if args.dry_run:
        if args.action != "generate" or args.resume or args.run_dir is not None:
            parser.error("--dry-runは新規generateの内容確認用です。--resume / --run-dirとは併用できません。")
        payload = generation_payload(args.model or "marble-1.1", datetime.now(JST).strftime("%Y%m%d_%H%M%S"))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        print("内容確認のみ：APIは呼ばず、フォルダーも作成していません。")
        return
    key = load_api_key()
    if not key:
        raise RuntimeError(".env に WORLD_LABS_API_KEY を設定してください。")
    OUT = select_output(args.action, args.run_dir, args.resume)
    print("保存先:", OUT, flush=True)
    if args.resume and not any((OUT / name).exists() for name in
                               ("generate_operation.json", "generate_submission_pending.json")):
        raise RuntimeError("再開できる送信記録がありません。新しく生成する場合は --resume を外してください。")

    if args.action == "generate":
        if (OUT / "generate_request.json").exists():
            # 再開時は編集中の文章ではなく、実際に送信した内容を使う。
            payload = read("generate_request.json")
            if args.model is not None and args.model != payload.get("model"):
                raise RuntimeError("再開する生成のモデルは変更できません。元のモデルで再開してください。")
        elif (OUT / "generate_operation.json").exists() or (OUT / "generate_submission_pending.json").exists():
            payload = {}  # 既存処理の確認のみ。新しいPOSTには使わない。
        else:
            payload = generation_payload(args.model or "marble-1.1", OUT.name)
        world = operation(key, "generate", "worlds:generate", payload)
        if not world.get("world_id"):
            raise RuntimeError("完了応答にworld_idがありません。generate_operation.jsonを確認してください。")
        save("world.json", world)
        world = get_world(key, world["world_id"])
        print("生成が完了しました。ワールド情報と閲覧URLを保存しました。", flush=True)
        print("3Dデータや画像の取得は、形式を選んでdownloadまたはmeshで行ってください。", flush=True)
    else:
        if not (OUT / "world.json").exists():
            raise RuntimeError("先に python run.py world generate で街並みを生成してください。")
        world = get_world(key, read("world.json")["world_id"])
        if args.action == "download":
            download_world(world, args.asset)
        else:
            # 見た目を持つHQメッシュの書き出し。衝突判定用メッシュとは別。
            result = operation(
                key, "mesh", "worlds/" + quote(world["world_id"], safe="") + ":export",
                {"asset_type": "mesh", "format": "glb", "mesh_variant": "textured"},
            )
            save("mesh_export.json", result)
            if not result.get("url"):
                raise RuntimeError("書き出し応答に取得URLがありません。mesh_export.jsonを確認してください。")
            download(result["url"], "street_textured", ".glb")
    print("保存先:", OUT, flush=True)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n待機を中断しました。開始済みの処理は同じコマンドで再開できます。")
        sys.exit(1)
    except HTTPError as error:
        print(f"HTTP {error.code}: World APIまたは取得先からエラーが返りました。")
        if error.code == 402:
            print("World Labs PlatformのAPI残高を確認してください。")
        sys.exit(1)
    except (URLError, OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        message = str(error)
        secret = load_api_key()
        if secret:
            message = message.replace(secret, "[API KEY]")
        # 取得URLには一時的なアクセス情報が含まれることがあるため、表示しない。
        message = re.sub(r"https?://\S+", "[URL]", message)
        print("停止:", message)
        sys.exit(1)
