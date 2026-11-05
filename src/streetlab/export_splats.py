"""生成済みワールドのスプラットを、Blender用にPLYで取得する。"""

import json
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote

from streetlab.world_api import load_api_key
from streetlab.world import OUT, api, download, read, save


def main():
    key = load_api_key()
    if not key:
        raise RuntimeError(".envにWorld APIのキーを設定してください。")
    if not (OUT / "world.json").exists():
        raise RuntimeError("生成済みのoutputs/prague/world.jsonが必要です。")
    world_id = read("world.json")["world_id"]
    operation_path = OUT / "ply_operation.json"
    pending = OUT / "ply_submission_pending.json"
    if operation_path.exists():
        job = read(operation_path.name)
        print("保存済みのPLY書き出し処理を確認します。", flush=True)
    else:
        if pending.exists():
            raise RuntimeError("前回の送信結果が不明です。Platformで既存の処理を確認してください。")
        payload = {"asset_type": "splats", "format": "ply", "resolution": "full_res"}
        save("ply_request.json", {"world_id": world_id, **payload})
        save(pending.name, {"world_id": world_id, "status": "submission_outcome_unknown"})
        print("生成済みワールドのスプラットをPLYで書き出します。", flush=True)
        job = api(key, "worlds/" + quote(world_id, safe="") + ":export", payload)
        save(operation_path.name, job)
        pending.unlink()
    started = time.monotonic()
    while not job.get("done"):
        if time.monotonic() - started > 7200:
            raise RuntimeError("待機を終了しました。同じコマンドで再開できます。")
        operation_id = job.get("operation_id")
        if not operation_id:
            raise RuntimeError("書き出し応答にoperation_idがありません。")
        print("PLY書き出し処理中…", flush=True)
        time.sleep(20)
        job = api(key, "operations/" + quote(operation_id, safe=""))
        save(operation_path.name, job)
    if job.get("error"):
        raise RuntimeError("PLY書き出しが失敗しました。ply_operation.jsonを確認してください。")
    result = job.get("response") or {}
    save("ply_export.json", result)
    if not result.get("url"):
        raise RuntimeError("書き出し結果に取得URLがありません。")
    if job.get("cost"):
        print("確定した消費量:", json.dumps(job["cost"], ensure_ascii=False), flush=True)
    download(result["url"], "splats_full_res", ".ply")
    print("保存先:", OUT / "splats_full_res.ply", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n中断しました。処理IDを保存済みなら同じコマンドで再開できます。")
        sys.exit(1)
    except HTTPError as error:
        print(f"HTTP {error.code}: PLY書き出しまたは取得でエラーが返りました。")
        sys.exit(1)
    except (URLError, OSError, ValueError, KeyError, TypeError, RuntimeError):
        print("PLYの取得を完了できませんでした。接続とoutputs/prague内の保存情報を確認してください。")
        sys.exit(1)
